"""
MBDSDR 接收流水线（多线程）
============================

把原来 UI 线程上串行的 IQ 主循环（QTimer 50ms 读一块 → 频谱 FFT → 录制 →
VFO 解调 → 声卡）重构为 SDR++ 风格的多线程流水线：

    [IQ Reader QThread]
        read_samples(16384) → 写 input PingPongStream → swap
              │
              ▼
        [StreamSplitter] 一进多出（mbdsdr_ai.dsp.StreamSplitter）
          ├─→ spectrum stream → [FftWorker QThread] 窗×FFT×fftshift×dBFS×IIR
          │                     → Signal frame_ready(iq, sr) → UI 只 repaint
          ├─→ vfo1 stream     → [DemodWorker QThread 1] VFO.process(DDC) → 解调 → 声卡
          ├─→ vfo2 stream     → [DemodWorker QThread 2] 并行解调（音频静音，验证独立链路）
          └─→ record stream   → [RecordTap QThread] append 到录制 buffer

设计要点：
  - UI 线程绝不做 FFT / 解调重活；FftWorker 直接调 spectrum.generator.push_iq
    （纯 numpy，不碰 Qt 控件），完成后发 Qt 信号，UI 只做轻量 repaint + doppler tap。
  - 每个下游各持独立 PingPongStream，splitter 逐路拷贝，不再共用同一块 IQ buffer。
  - 线程全部 daemon + 显式 stop/join；断连时 stop_writer/stop_reader 唤醒阻塞点。
  - 本模块只依赖 PySide6（QThread/Signal）+ mbdsdr_ai 内核（PingPongStream/
    StreamSplitter/VFO/dsp 解调函数），不 import 任何 tests/ 或合成源。
"""

import logging
import threading
from typing import Callable, Optional

import numpy as np

from PySide6.QtCore import QObject, QThread, Signal, Slot

from mbdsdr_ai.dsp_stream import PingPongStream
from mbdsdr_ai.dsp import StreamSplitter

log = logging.getLogger(__name__)


# 每块读取样点数：与 spectrum_widget FFT 16384 档位对齐（原 _poll_sdr_iq 同值）
READ_BLOCK_SAMPLES = 16384


class SharedDemodConfig:
    """UI 线程写 / 解调工作线程读的共享解调参数。

    CPython GIL 下对简单标量（str/float/bool）的属性读写是原子的，
    这里不加锁：最坏情况是某一帧读到刚跨边界的旧/新值，听感无差。
    """

    def __init__(self, mode: str = "FM", squelch_db: float = -80.0):
        self.mode: str = str(mode).upper()
        self.squelch_db: float = float(squelch_db)


# ======================================================================
# 线程 1：IQ Reader（从后端读真实基带，写入 input 乒乓流）
# ======================================================================
class IqReaderThread(QThread):
    """后台线程循环调 backend.read_samples(n)，写入 input PingPongStream。

    对照 SDR++ source 模块的 read 线程：阻塞读硬件 → 零拷贝 swap 交给 splitter。
    read_samples 抛异常/返回 None 时本帧跳过，不崩；stop() 唤醒后退出。
    """

    def __init__(self, read_fn: Callable[[int], Optional[np.ndarray]],
                 out_stream: PingPongStream, block_size: int = READ_BLOCK_SAMPLES,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._read_fn = read_fn
        self._out = out_stream
        self._block = int(block_size)
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            try:
                iq = self._read_fn(self._block)
            except Exception:
                iq = None
            if iq is None:
                self.msleep(2)
                continue
            iq = np.asarray(iq, dtype=np.complex64)
            n = int(iq.size)
            if n < 64:
                continue
            # 写入 write_buf（前 n 个样本），然后 swap 交给 splitter 消费
            wb = self._out.write_buf
            m = min(n, wb.size)
            wb[:m] = iq[:m]
            if not self._out.swap(m):
                # writer 被 stop() 唤醒
                break
        self._running = False

    def shutdown(self):
        """请求退出：标记 + 唤醒阻塞在 swap() 的本线程。"""
        self._running = False
        try:
            self._out.stop_writer()
        except Exception:
            pass


# ======================================================================
# 线程 2：频谱 FFT worker（重活全在这，UI 只 repaint）
# ======================================================================
class FftWorker(QThread):
    """从 spectrum stream 读 IQ，调 spectrum.generator.push_iq 做窗×FFT×dBFS。

    push_iq 只动 numpy 数组与 Python 属性，不碰 Qt 控件，可安全在本线程跑。
    完成后发 frame_ready(iq_copy, sr)：UI 槽里只做轻量 repaint + doppler/RSSI。
    """

    frame_ready = Signal(object, float)  # (iq copy: np.ndarray complex64, sr)

    def __init__(self, in_stream: PingPongStream,
                 gen, sr: float,
                 center_getter: Callable[[], float],
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._in = in_stream
        self._gen = gen
        self._sr = float(sr)
        self._center_getter = center_getter
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            buf, n = self._in.read()
            if n < 0:
                break
            if n <= 0:
                continue
            # 复制一份：push_iq 与发给 UI 的信号共用这份稳定副本，
            # flush 后底层 read_buf 会被 splitter 覆写，绝不能再引用视图。
            iq = np.array(buf[:n], dtype=np.complex64)
            try:
                center = float(self._center_getter())
            except Exception:
                center = 0.0
            try:
                self._gen.push_iq(iq, center, self._sr)
            except Exception:
                pass
            # 交给 UI 线程做轻量 tap（doppler feed_iq 碰 Qt，必须回 UI 线程）
            try:
                self.frame_ready.emit(iq, self._sr)
            except Exception:
                pass
            self._in.flush()
        self._running = False

    def shutdown(self):
        self._running = False
        try:
            self._in.stop_reader()
        except Exception:
            pass


# ======================================================================
# 线程 3..N：每 VFO 一个解调 worker
# ======================================================================
class DemodWorker(QThread):
    """从 VFO stream 读原生率 IQ → VFO.process(DDC 搬频+重采样+LPF) → 按模式解调 → 声卡。

    复用 dsp 包的模式映射（与原 _demod_at_48k 一致）。WFM 广播走特殊路径：
    跳过 VFO，直接对全带宽原生 IQ 鉴频（内部已重采样到 48k）。

    静噪门控位置：
      - 窄带模式（FM/NFM/AM/USB/LSB/CW）：先 VFO.process 取出 48k 信道 IQ，
        对 vfo_out 算功率 dBFS 做门控——真在收听带宽内判信号，不被邻道杂散误触发。
      - WFM：信道就是全带宽，对原始 IQ 算功率做门控。
      - AudioPlayer 内部 5ms ramp 消咔哒。

    audio_enabled=False 时仍完整跑 DDC+解调（验证并行链路），但不写声卡、
    不做静噪门控——供第二 VFO 静音并行解调使用。
    """

    def __init__(self, in_stream: PingPongStream,
                 vfo_dsp, player,
                 config: SharedDemodConfig,
                 sr: float,
                 audio_enabled: bool = True,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._in = in_stream
        self._vfo = vfo_dsp          # mbdsdr_ai.dsp.VFO（UI 线程可 set_offset）
        self._player = player
        self._cfg = config
        self._sr = float(sr)
        self._audio_enabled = bool(audio_enabled)
        self._running = False
        # 记录本次实际用的解调模式（调试/测试断言）
        self.last_demod_mode: str = "FM"

    def set_audio_enabled(self, on: bool) -> None:
        """运行时切换本 VFO 是否把解调结果写声卡（主听/次听切换）。

        主听 VFO 置 True（写声卡），次听 VFO 置 False（仍跑 DDC+解调验证并行
        链路，但不写声卡）。GIL 下 bool 赋值原子，worker 循环每帧读到新值。
        多 VFO 各自独立 DDC 真解调；当前共享单一声卡，只允许一个 VFO 出声。
        """
        self._audio_enabled = bool(on)

    def _demod_48k(self, dsp, mode: str, vfo_out: np.ndarray) -> np.ndarray:
        """VFO 输出已是 48k complex IQ，按模式解调（沿用原 main_window 映射）。"""
        if mode == "NFM":
            return dsp.fm_demod(vfo_out, deviation=5000.0, sample_rate=48000)
        if mode == "FM":
            return dsp.fm_demod(vfo_out, deviation=75000.0, sample_rate=48000)
        if mode == "AM":
            return dsp.am_demod(vfo_out)
        if mode == "USB":
            return dsp.ssb_demod(vfo_out, mode="USB", sample_rate=48000)
        if mode == "LSB":
            return dsp.ssb_demod(vfo_out, mode="LSB", sample_rate=48000)
        if mode == "CW":
            return dsp.cw_demod(vfo_out, tone_freq=700.0, sample_rate=48000)
        return dsp.fm_demod(vfo_out, deviation=75000.0, sample_rate=48000)

    def run(self):
        self._running = True
        from mbdsdr_ai import dsp as _dsp
        player = self._player
        player_ok = (player is not None and getattr(player, "available", False)
                     and self._audio_enabled)

        while self._running:
            buf, n = self._in.read()
            if n < 0:
                break
            if n <= 0:
                continue
            iq = np.asarray(buf[:n], dtype=np.complex64)
            self._in.flush()

            mode = self._cfg.mode
            self.last_demod_mode = mode
            if mode in ("RAW", "DIG"):
                continue

            # ===== WFM 广播路径：信道就是全带宽，静噪/鉴频都在原生率 IQ 上做 =====
            if mode == "WFM":
                # 静噪门控：WFM 信道 = 全带宽，对原始全带宽 IQ 算功率
                if player_ok:
                    try:
                        p = float(np.mean(np.abs(iq) ** 2))
                        dbfs = 10.0 * np.log10(p + 1e-12)
                        # AudioPlayer 内部 5ms ramp 消咔哒
                        player.set_muted(dbfs < self._cfg.squelch_db)
                    except Exception:
                        pass
                if not player_ok:
                    # 无设备 / 静音 VFO：仍跑鉴频验证链路，但不写声卡、不做静噪门控
                    try:
                        _dsp.wfm_broadcast_demod(iq, sample_rate=self._sr,
                                                 audio_sr=48000)
                    except Exception:
                        pass
                    continue
                try:
                    audio = _dsp.wfm_broadcast_demod(iq, sample_rate=self._sr,
                                                     audio_sr=48000)
                    if audio is not None and audio.size > 0:
                        player.write(audio)
                except Exception:
                    pass
                continue

            # ===== 窄带路径（FM/NFM/AM/USB/LSB/CW）=====
            # 先 VFO DDC（NCO 搬频 + 有理重采样 + Nuttall LPF）取出 48k 信道 IQ，
            # 静噪门控在 VFO 信道输出上算——真在收听带宽内判信号，而不是被全带宽
            # 邻道噪声/杂散误触发。
            if self._vfo is None:
                # VFO 不可用：窄带无法解调，跳过（不造假音频）
                continue
            try:
                vfo_out = self._vfo.process(iq)
            except Exception:
                continue
            if vfo_out is None or len(vfo_out) <= 16:
                continue

            # 静噪门控：对 VFO 信道输出（48k 窄带 IQ）算功率 dBFS，
            # 低于门限则 player.set_muted(True)，信号回来后 set_muted(False)。
            if player_ok:
                try:
                    p = float(np.mean(np.abs(vfo_out) ** 2))
                    dbfs = 10.0 * np.log10(p + 1e-12)
                    player.set_muted(dbfs < self._cfg.squelch_db)
                except Exception:
                    pass

            if not player_ok:
                # 无设备 / 静音 VFO：上面已跑完 VFO.process 验证 DDC 链路，
                # 这里不写声卡、也不做静噪门控。
                continue

            try:
                audio = self._demod_48k(_dsp, mode, vfo_out)
                if audio is not None and audio.size > 0:
                    player.write(audio)
            except Exception:
                pass
        self._running = False

    def shutdown(self):
        self._running = False
        try:
            self._in.stop_reader()
        except Exception:
            pass


# ======================================================================
# 线程 N+1：录制 tap（独立于解调路径，累积 baseband）
# ======================================================================
class RecordTap(QThread):
    """从 record stream 读 IQ，回调交给 main_window 累积（录制开关在 UI 侧判定）。

    与解调解耦：即使解调 worker 卡顿/被静噪门住，录制也不被阻塞。
    """

    def __init__(self, in_stream: PingPongStream,
                 on_iq: Callable[[np.ndarray], None],
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._in = in_stream
        self._on_iq = on_iq
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            buf, n = self._in.read()
            if n < 0:
                break
            if n <= 0:
                continue
            iq = np.array(buf[:n], dtype=np.complex64)
            self._in.flush()
            try:
                self._on_iq(iq)
            except Exception:
                pass
        self._running = False

    def shutdown(self):
        self._running = False
        try:
            self._in.stop_reader()
        except Exception:
            pass


# ======================================================================
# 编排器：组装全部乒乓流 + splitter + 线程，统一 start/stop
# ======================================================================
class ReceivePipeline(QObject):
    """组装并启停整条接收流水线。

    用法（main_window 连接成功后）::

        pipe = ReceivePipeline(
            read_fn=backend.read_samples, sr=sr,
            gen=self.spectrum.generator,
            center_getter=lambda: backend.get_frequency(),
            main_vfo_dsp=vfo1, vfo2_dsp=vfo2,
            player=self._audio_player, config=self._demod_cfg,
            on_record_iq=self._on_pipeline_record_iq,
        )
        pipe.start()
        # ...
        pipe.shutdown()

    frame_ready 信号跨线程到 UI：UI 槽里做 repaint + doppler tap + RSSI。
    """

    frame_ready = Signal(object, float)

    def __init__(self, read_fn: Callable[[int], Optional[np.ndarray]],
                 sr: float,
                 gen,
                 center_getter: Callable[[], float],
                 main_vfo_dsp,
                 vfo2_dsp,
                 player,
                 config: SharedDemodConfig,
                 on_record_iq: Callable[[np.ndarray], None],
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._read_fn = read_fn
        self._sr = float(sr)
        self._gen = gen
        self._center_getter = center_getter
        self._player = player
        self._cfg = config
        self._on_record_iq = on_record_iq

        # 输入乒乓流（reader 写，splitter 读）
        self._input = PingPongStream(dtype=np.complex64, buffer_size=READ_BLOCK_SAMPLES * 4)
        self._splitter = StreamSplitter(self._input)

        # 各路下游独立乒乓流
        self._spectrum_stream = PingPongStream(np.complex64, READ_BLOCK_SAMPLES * 4)
        self._vfo1_stream = PingPongStream(np.complex64, READ_BLOCK_SAMPLES * 4)
        self._vfo2_stream = PingPongStream(np.complex64, READ_BLOCK_SAMPLES * 4)
        self._record_stream = PingPongStream(np.complex64, READ_BLOCK_SAMPLES * 4)

        self._reader = IqReaderThread(read_fn, self._input, READ_BLOCK_SAMPLES)
        self._fft = FftWorker(self._spectrum_stream, gen, self._sr, center_getter)
        self._fft.frame_ready.connect(self._on_fft_frame)
        self._demod1 = DemodWorker(self._vfo1_stream, main_vfo_dsp, player,
                                   config, self._sr, audio_enabled=True)
        self._demod2 = DemodWorker(self._vfo2_stream, vfo2_dsp, player,
                                   config, self._sr, audio_enabled=False)
        self._record = RecordTap(self._record_stream, on_record_iq)

        self._started = False

    @Slot(object, float)
    def _on_fft_frame(self, iq: np.ndarray, sr: float):
        # 转发给 UI（跨线程 queued 连接自动 marshal）
        self.frame_ready.emit(iq, sr)

    def start(self):
        if self._started:
            return
        # 先绑下游，再启动 splitter，最后启动 reader（数据自顶向下流）
        self._splitter.bind(self._spectrum_stream)
        self._splitter.bind(self._vfo1_stream)
        self._splitter.bind(self._vfo2_stream)
        self._splitter.bind(self._record_stream)
        self._splitter.start()
        self._fft.start()
        self._demod1.start()
        self._demod2.start()
        self._record.start()
        self._reader.start()
        self._started = True

    def shutdown(self):
        """停全部线程 + 停乒乓流，join 回收。幂等。"""
        if not self._started:
            return
        self._started = False

        # 1) 先停生产者（reader），唤醒其 swap()
        try:
            self._reader.shutdown()
        except Exception:
            pass
        # 2) splitter 停读输入流（唤醒其 read()）
        try:
            self._splitter.stop()
        except Exception:
            pass
        # 3) 停各下游消费者（唤醒各自 read()）
        for w in (self._fft, self._demod1, self._demod2, self._record):
            try:
                w.shutdown()
            except Exception:
                pass
        # 4) join 所有线程
        for t in (self._reader, self._fft, self._demod1, self._demod2, self._record):
            try:
                t.wait(2000)
            except Exception:
                pass
        # 5) 清停止标志，便于下次重建流水线时复用对象（我们这里每次都新建，防御性调用）
        for s in (self._input, self._spectrum_stream, self._vfo1_stream,
                  self._vfo2_stream, self._record_stream):
            try:
                s.stop_reader()
                s.stop_writer()
            except Exception:
                pass
