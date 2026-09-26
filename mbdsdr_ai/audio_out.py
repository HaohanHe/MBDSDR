"""
MBDSDR AI 内核 - 实时音频输出
================================
把解调后的音频块（float32 numpy 数组）实时播放到系统默认声卡。

依赖：
- sounddevice（可选）：通过 PortAudio 直接输出音频。
  未安装或音频设备不可用时，AudioPlayer.available 置为 False，
  所有方法安全降级为空操作（write 返回 0、start 返回 False），
  主程序不会因为缺少音频设备而崩溃。
- numpy：必需（DSP 输出本身就是 numpy 数组）。

典型用法：
    player = AudioPlayer(sample_rate=48000, gain=1.0)
    player.start()
    # 解调循环中：
    player.write(audio_block_float32)
    # 退出时：
    player.stop()

线程安全：
    write() / stop() / start() / set_gain() 都可以从不同线程调用，
    内部用 threading.Lock 保护内部队列与流句柄。stop() 可重复调用。
"""

import collections
import threading
from typing import Optional

import numpy as np

# sounddevice 是可选依赖：导入失败时整体降级，不抛异常
try:
    import sounddevice as sd  # type: ignore
    _SD_AVAILABLE = True
except Exception:  # pragma: no cover - 取决于运行环境
    sd = None  # type: ignore
    _SD_AVAILABLE = False


class AudioPlayer:
    """把解调后的 float32 音频块实时输出到默认声卡。

    Parameters
    ----------
    sample_rate : int
        采样率 Hz，默认 48000。
    channels : int
        声道数，1=单声道，2=立体声。
    gain : float
        播放增益，范围 0.0-5.0，默认 0.5（≈ -6 dB）。
        来源: gqrx/src/applications/gqrx/receiver.cpp:49,124 DEFAULT_AUDIO_GAIN=-6.0。
    """

    #: 单块最大缓存样本数，防止上游喂得太快导致无界增长
    #: 对照 SDR++ new_portaudio_sink:16-17 AUDIO_LATENCY=1/60≈16.7ms，
    #: 留 150ms 余量吸收 GUI 线程抖动（原 2s 过大且长期欠载用不上）。
    _MAX_QUEUED_SAMPLES = int(48000 * 0.15)  # ≈150ms @48k

    #: 淡入淡出斜坡长度（样本），约 5ms @48k，消除静噪开关/丢块/启停咔哒
    _RAMP_SAMPLES = 256

    def __init__(self, sample_rate: int = 48000, channels: int = 1, gain: float = 0.5):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.gain = float(gain)

        #: sounddevice 是否可用（False 时所有方法安全降级）
        self.available = _SD_AVAILABLE

        self._stream: Optional["sd.OutputStream"] = None
        self._lock = threading.Lock()
        # 待播放的 float32 块队列（每个元素是一维 float32 数组）
        self._queue: "collections.deque[np.ndarray]" = collections.deque()
        self._queued_samples = 0

        # ── 淡入淡出斜坡状态机（消除静噪/丢块/启停咔哒）──
        # 对照 SDR++ power_squelch.h 门在 IF 域 + 后级 LPF 平滑的思路；
        # 我们在音频域用 5ms 线性 ramp 等效实现，ramp 在 PortAudio 回调里算。
        self._ramp_gain = 1.0       # 当前平滑增益 [0,1]
        self._ramp_target = 1.0     # 目标增益（mute 时 0，unmute 时 1）
        self._muted = False
        # 可选内部重采样器（write 传入非原生 sr 时 lazy 构建）
        self._resamp_cache: dict = {}

    # ------------------------------------------------------------------
    # sounddevice 回调（由 PortAudio 后台线程调用）
    # ------------------------------------------------------------------
    def _callback(self, outdata: np.ndarray, frames: int,
                  time_info, status) -> None:  # noqa: D401 - sd 回调签名
        """PortAudio 回调：从内部队列取数填到 outdata，不够补零，全程带淡入淡出。

        对照 SDR++ new_portaudio_sink:330-346：回调阻塞读满块、不半真半零拼接。
        Python 回调里不能真阻塞（PortAudio 会警告），所以保留补零，但补零段
        被 ramp_gain 压到 0，听不见咔哒；underrun 后下一块有数据时从 0 渐入。
        """
        needed = frames * self.channels
        chunks = []
        have = 0
        with self._lock:
            while have < needed and self._queue:
                blk = self._queue[0]
                n = blk.size
                take = min(n, needed - have)
                if take == n:
                    chunks.append(self._queue.popleft())
                    self._queued_samples -= n
                else:
                    chunks.append(blk[:take])
                    self._queue[0] = blk[take:]
                    self._queued_samples -= take
                have += take
        if chunks:
            data = np.concatenate(chunks)
        else:
            data = np.zeros(0, dtype=np.float32)
        if data.size < needed:
            data = np.concatenate(
                [data, np.zeros(needed - data.size, dtype=np.float32)]
            )

        # ── 淡入淡出斜坡（5ms 线性，消除静噪开关/丢块/启停咔哒）──
        underrun = have < needed
        # underrun 或被 mute 时目标增益=0；否则目标=1
        if underrun or self._muted:
            self._ramp_target = 0.0
        else:
            self._ramp_target = 1.0
        n = data.size
        if n > 0:
            step = (self._ramp_target - self._ramp_gain) / max(self._RAMP_SAMPLES, 1)
            # 每样本步进，但不超过 target（clip 到 [0,1]）
            ramp_end = self._ramp_gain + step * n
            if step > 0:
                ramp_end = min(ramp_end, self._ramp_target)
            else:
                ramp_end = max(ramp_end, self._ramp_target)
            ramp = np.linspace(self._ramp_gain, ramp_end, n,
                               endpoint=False, dtype=np.float32)
            data = data * ramp
            self._ramp_gain = float(ramp_end)

        # outdata shape: (frames, channels)
        outdata[:] = data.reshape(frames, self.channels)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """启动音频流。返回是否成功。

        已启动时幂等返回 True；sounddevice 不可用或打开设备失败时返回 False。
        """
        if not self.available:
            return False
        with self._lock:
            if self._stream is not None:
                return True
            try:
                # 对照 SDR++ new_portaudio_sink:79 blockSize=sampleRate/60 ≈16.7ms
                self._stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="float32",
                    callback=self._callback,
                    blocksize=max(1, int(self.sample_rate / 60)),
                )
                self._stream.start()
                return True
            except Exception:
                # 打开失败（无设备 / 采样率不支持等）：降级为不可用
                self._stream = None
                self.available = False
                return False

    def write(self, block: np.ndarray, sr: Optional[float] = None) -> int:
        """写入一个音频块。

        Parameters
        ----------
        block : np.ndarray
            float32 numpy 数组，单声道 shape=(N,) 或立体声 shape=(N, 2)。
            内部会乘以 gain、clip 到 [-1, 1]，再扁平化为交错样本入队。
        sr : float, optional
            该块的实际采样率(Hz)。默认 None = 已等于 self.sample_rate。
            若传入且与 self.sample_rate 不同，内部用 scipy resample_poly
            重采样到原生率再入队（对照 SDR++ radio_module.h:591-603
            resamp.setOutSamplerate，把重采样职责下沉到 sink）。

        Returns
        -------
        int
            实际入队的样本数（按 float32 标量计）；无设备/流未启动时返回 0。
        """
        if not self.available or self._stream is None:
            return 0
        if block is None:
            return 0
        try:
            data = np.asarray(block, dtype=np.float32)
            if data.size == 0:
                return 0
            # sink 侧重采样：输入 sr 与原生不同时自动对齐
            if sr is not None and int(round(sr)) != self.sample_rate:
                data = self._resample_to_native(data, float(sr))
                if data.size == 0:
                    return 0
            if self.gain != 1.0:
                data = data * self.gain
            np.clip(data, -1.0, 1.0, out=data)
            # 立体声 (N, C) → 交错 (N*C,)
            if data.ndim == 2:
                data = data.reshape(-1)
            elif data.ndim > 2:
                data = data.reshape(-1)
            n = int(data.size)
            with self._lock:
                # 队列过长时丢弃最老的块，避免延迟无限累积
                while self._queued_samples + n > self._MAX_QUEUED_SAMPLES and self._queue:
                    old = self._queue.popleft()
                    self._queued_samples -= int(old.size)
                self._queue.append(data)
                self._queued_samples += n
            return n
        except Exception:
            return 0

    def _resample_to_native(self, data: np.ndarray, in_sr: float) -> np.ndarray:
        """把任意采样率的音频块重采样到 self.sample_rate（多相 FIR）。

        对照 SDR++ radio_module.h:594 resamp.setOutSamplerate +
        rational_resampler.h:120-165 多相滤波。系数按 (in_sr, out_sr) 缓存。
        """
        from math import gcd
        key = (int(round(in_sr)), self.sample_rate)
        cached = self._resamp_cache.get(key)
        if cached is None:
            g = gcd(key[0], key[1])
            up = key[1] // g
            down = key[0] // g
            self._resamp_cache[key] = (up, down)
            cached = (up, down)
        up, down = cached
        try:
            from scipy.signal import resample_poly
            return resample_poly(data, up, down).astype(np.float32)
        except Exception:
            # scipy 不可用时线性插值兜底
            n_new = int(round(data.size * self.sample_rate / in_sr))
            if n_new <= 0:
                return np.zeros(0, dtype=np.float32)
            t_old = np.linspace(0.0, 1.0, data.size, endpoint=False)
            t_new = np.linspace(0.0, 1.0, n_new, endpoint=False)
            return np.interp(t_new, t_old, data).astype(np.float32)

    def stop(self) -> None:
        """停止并关闭音频流。可重复调用，安全。"""
        with self._lock:
            s = self._stream
            self._stream = None
            self._queue.clear()
            self._queued_samples = 0
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass

    def set_gain(self, gain: float) -> None:
        """设置播放增益（0.0-5.0），越界会被 clip 到合法范围。"""
        try:
            g = float(gain)
        except Exception:
            return
        self.gain = max(0.0, min(5.0, g))

    def set_muted(self, muted: bool) -> None:
        """静音/取消静音。不立即清队列，而是让回调里的 5ms ramp 渐出/渐入，
        避免硬切零导致咔哒。对照 SDR++ power_squelch.h 门控思路（我们在音频域实现）。"""
        self._muted = bool(muted)

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def is_playing(self) -> bool:
        """是否正在播放（流已启动且 sounddevice 可用）。"""
        return self.available and self._stream is not None
