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
        播放增益，范围 0.0-5.0，默认 1.0。
    """

    #: 单块最大缓存样本数，防止上游喂得太快导致无界增长
    _MAX_QUEUED_SAMPLES = 48000 * 2  # ≈2 秒 @48k

    def __init__(self, sample_rate: int = 48000, channels: int = 1, gain: float = 1.0):
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

    # ------------------------------------------------------------------
    # sounddevice 回调（由 PortAudio 后台线程调用）
    # ------------------------------------------------------------------
    def _callback(self, outdata: np.ndarray, frames: int,
                  time_info, status) -> None:  # noqa: D401 - sd 回调签名
        """PortAudio 回调：从内部队列取数填到 outdata，不够就补零。"""
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
                    # 把尾部留在队首下次继续播
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
                self._stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="float32",
                    callback=self._callback,
                )
                self._stream.start()
                return True
            except Exception:
                # 打开失败（无设备 / 采样率不支持等）：降级为不可用
                self._stream = None
                self.available = False
                return False

    def write(self, block: np.ndarray) -> int:
        """写入一个音频块。

        Parameters
        ----------
        block : np.ndarray
            float32 numpy 数组，单声道 shape=(N,) 或立体声 shape=(N, 2)。
            内部会乘以 gain、clip 到 [-1, 1]，再扁平化为交错样本入队。

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

    @property
    def is_playing(self) -> bool:
        """是否正在播放（流已启动且 sounddevice 可用）。"""
        return self.available and self._stream is not None
