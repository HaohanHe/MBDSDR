# SPDX-License-Identifier: MIT
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
import time
from typing import Callable, Dict, Optional

import numpy as np

from PySide6.QtCore import QObject, QThread, Signal, Slot

from mbdsdr_ai.dsp_stream import PingPongStream
from mbdsdr_ai.dsp import StreamSplitter, AGC
from mbdsdr_ai.squelch import BaselineSquelch, DEFAULT_SQUELCH_DBFS
from mode_registry import get as get_mode, demodulate

log = logging.getLogger(__name__)


# 每块读取样点数：与 spectrum_widget FFT 16384 档位对齐（原 _poll_sdr_iq 同值）
READ_BLOCK_SAMPLES = 16384


class SharedDemodConfig:
    """UI 线程写 / 解调工作线程读的共享解调参数。

    CPython GIL 下对简单标量（str/float/bool）的属性读写是原子的，
    这里不加锁：最坏情况是某一帧读到刚跨边界的旧/新值，听感无差。
    """

    def __init__(self, mode: str = "FM",
                 squelch_db: float = DEFAULT_SQUELCH_DBFS,
                 auto_squelch: bool = True, agc_speed: str = "medium",
                 gain_db: float = 0.0):
        self.mode: str = str(mode).upper()
        self.squelch_db: float = float(squelch_db)
        # 自动静噪：True = 持续跟踪噪声底；False = 基线锁定在最近一次标定。
        # 两种情况下滑杆值都是"相对噪声基线的余量"。
        self.auto_squelch: bool = bool(auto_squelch)
        # 解调后音频 AGC 档位：fast / medium / slow / off（gqrx 式三档+关）。
        self.agc_speed: str = str(agc_speed).lower()
        # 前端增益（dB）：变化时重打噪声基线（设置线/操作逻辑线负责写入）。
        self.gain_db: float = float(gain_db)


# ======================================================================
# 线程 1：IQ Reader（从后端读真实基带，写入 input 乒乓流）
# ======================================================================
class IqReaderThread(QThread):
    """后台线程循环调 backend.read_samples(n)，写入 input PingPongStream。

    对照 SDR++ source 模块的 read 线程：阻塞读硬件 → 零拷贝 swap 交给 splitter。
    read_samples 抛异常/返回 None 时本帧跳过，不崩；stop() 唤醒后退出。
    连续失败超过 STALL_TIMEOUT_S 时发 read_failed：上层据此把 UI 如实置为
    "已断开"（S8/S9）。按时间判定而非固定次数：Windows 上 msleep 粒度约 15ms，
    按次数会拖到数秒才报。
    """

    #: 连续无数据达到该秒数才报（空读退避在 2ms 量级，1s 足够排除瞬时抖动）
    STALL_TIMEOUT_S = 1.0

    read_failed = Signal(str)  # 连续无数据超过阈值（设备可能已断开）

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
        last_ok = time.monotonic()
        last_report = 0.0
        last_reason = "read_samples 连续无数据"
        while self._running:
            try:
                iq = self._read_fn(self._block)
            except Exception as e:  # noqa: BLE001
                iq = None
                last_reason = str(e) or e.__class__.__name__
            if iq is None:
                now = time.monotonic()
                # 每个阈值周期报一次：持续掉线时上层能反复核查并升级处置
                if now - last_ok >= self.STALL_TIMEOUT_S and \
                        now - last_report >= self.STALL_TIMEOUT_S:
                    last_report = now
                    try:
                        self.read_failed.emit(last_reason)
                    except Exception:
                        pass
                self.msleep(2)
                continue
            last_ok = time.monotonic()
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

    #: 单帧解调失败（可预期数据异常）：供状态栏/日志如实提示，不静默吞掉
    demod_failed = Signal(str)
    #: 静噪基线标定状态（标定中/完成/失败）：失败必须用户可见
    squelch_state = Signal(str)

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
        # 主窗口统一音频后处理缝（ANR 降噪/tap 分发）；None=不处理后直接写声卡
        self.audio_tap = None
        # 记录本次实际用的解调模式（调试/测试断言）
        self.last_demod_mode: str = "FM"
        # 解调失败日志限流时间戳（monotonic）
        self._last_demod_fail_ts = 0.0
        # 有状态 WFM 接收器（WFM 模式下懒创建一次）
        self._wfm_rx = None
        # 静噪：唯一滑杆刻度（-100..-20，默认 -50）= 相对该模式噪声基线的余量。
        # 基线自动标定（模式/增益/采样率变化时重置），测法见 mbdsdr_ai/squelch.py。
        self._squelch = BaselineSquelch(squelch_db=self._cfg.squelch_db,
                                        mode=self._cfg.mode,
                                        sample_rate=48000.0)
        self._last_squelch_label = ""
        self._last_gain_db = float(self._cfg.gain_db)
        # 解调后音频 AGC（gqrx 式三档+关）：每档一个实例，档位各自保留
        # 增益状态，来回切换不跳变；off 直通。
        self._audio_agc: Dict[str, AGC] = {}

    # 档位 → (attack, release)：attack 控制增益上爬（信号变强时快速回落），
    # release 控制增益下放（信号变弱时缓慢拉升）。数值为一阶平滑系数。
    _AGC_SPEED_PARAMS = {
        "fast": (0.10, 0.01),
        "medium": (0.02, 0.002),
        "slow": (0.005, 0.0005),
    }

    def _apply_audio_agc(self, audio: np.ndarray) -> np.ndarray:
        """按当前档位对解调后音频施加 AGC；off/未知档直通。"""
        speed = str(self._cfg.agc_speed).lower()
        if speed in ("off", "", "none"):
            return audio
        if speed not in self._AGC_SPEED_PARAMS:
            speed = "medium"
        agc = self._audio_agc.get(speed)
        if agc is None:
            attack, release = self._AGC_SPEED_PARAMS[speed]
            agc = AGC(target_level=0.5, attack=attack, release=release,
                      max_gain=60.0)
            self._audio_agc[speed] = agc
        if audio.ndim == 2:
            # WFM 立体声：各声道共用同一 AGC 实例顺序处理（增益状态连续）
            return np.stack([agc.process(audio[:, c])
                             for c in range(audio.shape[1])], axis=1)
        return agc.process(audio)

    def _squelch_open(self, audio: np.ndarray) -> bool:
        """静噪门决策的唯一入口：相对噪声基线的余量（不是绝对电平）。

        滑杆值统一由 ``margin_threshold_db`` 归一化为 dB 余量；噪声基线按
        当前模式自动标定，模式/增益/采样率变化时重测。标定状态变化会发
        ``squelch_state``（失败可见，不默默套默认值）。
        """
        self._squelch.set_threshold(self._cfg.squelch_db)
        self._squelch.track = bool(self._cfg.auto_squelch)
        open_g = bool(self._squelch.update(audio, int(audio.shape[0])))
        label = self._squelch.state_label()
        if label != self._last_squelch_label:
            self._last_squelch_label = label
            log.info("%s", label)
            self.squelch_state.emit(label)
        return open_g

    def recalibrate(self) -> None:
        """重打噪声基线：增益变化 / 采样率变化 / 用户手动重标时调用。"""
        self._squelch.reset()

    def _report_demod_failure(self, mode: str, exc: BaseException,
                              stage: str = "解调") -> None:
        """单帧可预期失败：限流记录 + 发信号，不静默跳过。

        限流 2 秒：坏块通常成串出现，逐帧刷日志既淹没证据又拖慢线程。
        解调 / VFO / 静噪 / 写卡共用一个上报通道（同一时刻只留最近一条）。
        """
        now = time.monotonic()
        if now - self._last_demod_fail_ts < 2.0:
            return
        self._last_demod_fail_ts = now
        detail = f"{exc.__class__.__name__}: {exc}"
        log.warning("%s失败（mode=%s）：%s", stage, mode, detail)
        self.demod_failed.emit(f"{stage}失败（{mode}）：{detail}")

    #: 单帧可预期异常：坏数据/边界，不是编程错误（TypeError/AttributeError 等上抛）
    _FRAME_ERRORS = (ValueError, IndexError, FloatingPointError)
    #: 外部设备类异常：声卡拔出/驱动拒绝，属可预期边界
    _DEVICE_ERRORS = (OSError, RuntimeError)

    def _vfo_ddc(self, iq):
        """VFO DDC：单帧坏数据限流上报后返回 None；编程错误原样上抛。"""
        try:
            return self._vfo.process(iq)
        except self._FRAME_ERRORS as exc:
            self._report_demod_failure(f"VFO/{self._cfg.mode}", exc, stage="VFO 处理")
            return None

    def _wfm_audio(self, iq):
        """WFM 鉴频：单帧坏数据限流上报后返回 None；编程错误原样上抛。"""
        try:
            return self._wfm_rx.process(iq)
        except self._FRAME_ERRORS as exc:
            self._report_demod_failure("WFM", exc, stage="WFM 鉴频")
            return None

    def _squelch_gate(self, audio, player, mode: str) -> None:
        """静噪门控：判定失败限流上报（保持上次静音状态）；编程错误上抛。"""
        try:
            open_g = self._squelch_open(audio)
            player.set_muted(not open_g)
        except self._FRAME_ERRORS as exc:
            self._report_demod_failure(mode, exc, stage="静噪判定")

    def _push_audio(self, audio, player, mode: str) -> None:
        """AGC → tap → 写卡：设备/数据异常限流上报；编程错误原样上抛。"""
        try:
            audio = self._apply_audio_agc(audio)
            if self.audio_tap is not None:
                audio = self.audio_tap(audio)
            player.write(audio)
        except self._DEVICE_ERRORS + self._FRAME_ERRORS as exc:
            self._report_demod_failure(mode, exc, stage="音频处理/写卡")

    def _report_unexpected(self, exc: BaseException, mode: str) -> None:
        """编程错误：保留完整栈并上报，然后停止本 VFO（不在 QThread.run 边界外抛）。"""
        log.exception("解调线程未预期异常（mode=%s）：%s", mode, exc)
        self.demod_failed.emit(
            f"解调线程内部错误（{mode}）：{exc.__class__.__name__}: {exc}")
        self._running = False

    def set_audio_enabled(self, on: bool) -> None:
        """运行时切换本 VFO 是否把解调结果写声卡（主听/次听切换）。

        主听 VFO 置 True（写声卡），次听 VFO 置 False（仍跑 DDC+解调验证并行
        链路，但不写声卡）。GIL 下 bool 赋值原子，worker 循环每帧读到新值。
        多 VFO 各自独立 DDC 真解调；当前共享单一声卡，只允许一个 VFO 出声。
        """
        self._audio_enabled = bool(on)

    def _demod_48k(self, dsp, mode: str, vfo_out: np.ndarray) -> np.ndarray:
        """VFO 输出已是 48k complex IQ，按模式解调。

        模式→函数映射已收口到 mode_registry（与 main_window._demod_at_48k 同源）。
        WFM 在 run() 里走专用广播路径并 continue，不会进到这里。
        """
        desc = get_mode(mode)
        if desc is not None and desc.outputs_audio:
            result = demodulate(mode, vfo_out, sample_rate=48000)
            if result is not None:
                return result
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
            if mode != self.last_demod_mode:
                # 切模式：分析带与噪声基线都换，重新标定
                self._squelch.set_mode(mode)
            self.last_demod_mode = mode
            # 增益变化：噪声底整体平移，必须重打基线
            gain_now = float(self._cfg.gain_db)
            if gain_now != self._last_gain_db:
                self._last_gain_db = gain_now
                self.recalibrate()
            if mode in ("RAW", "DIG"):
                continue

            # ===== WFM 广播路径：信道就是全带宽，先鉴频解调，再据音频形态静噪 =====
            if mode == "WFM":
                if self._wfm_rx is None:
                    self._wfm_rx = _dsp.WFMReceiver(
                        sample_rate=self._sr, audio_sr=48000)
                if not player_ok:
                    # 无设备 / 静音 VFO：仍跑鉴频验证链路，但不写声卡、不做静噪门控
                    try:
                        self._wfm_audio(iq)
                    except Exception as exc:  # noqa: BLE001
                        self._report_unexpected(exc, mode)
                        break
                    continue
                try:
                    audio = self._wfm_audio(iq)
                except Exception as exc:  # noqa: BLE001
                    self._report_unexpected(exc, mode)
                    break
                if audio is None or audio.size == 0:
                    continue
                # 静噪门控（唯一刻度）：相对该模式噪声基线的余量。
                # WFM 与窄带用同一判定，差别只在分析带/标定（见 squelch.py）。
                try:
                    self._squelch_gate(audio, player, mode)
                except Exception as exc:  # noqa: BLE001
                    self._report_unexpected(exc, mode)
                    break
                try:
                    self._push_audio(audio, player, mode)
                except Exception as exc:  # noqa: BLE001
                    self._report_unexpected(exc, mode)
                    break
                continue

            # ===== 窄带路径（FM/NFM/AM/USB/LSB/CW）=====
            # 先 VFO DDC（NCO 搬频 + 有理重采样 + Nuttall LPF）取出 48k 信道 IQ，
            # 再解调；静噪门控放在**解调后音频**上（统一口径，见 squelch.py 常量段）。
            if self._vfo is None:
                # VFO 不可用：窄带无法解调，跳过（不造假音频）
                continue
            try:
                vfo_out = self._vfo_ddc(iq)
            except Exception as exc:  # noqa: BLE001
                self._report_unexpected(exc, mode)
                break
            if vfo_out is None or len(vfo_out) <= 16:
                continue

            if not player_ok:
                # 无设备 / 静音 VFO：上面已跑完 VFO.process 验证 DDC 链路，
                # 不写声卡、也不做静噪门控。
                continue

            try:
                audio = self._demod_48k(_dsp, mode, vfo_out)
            except (ValueError, IndexError, FloatingPointError) as exc:
                # 单帧数据可预期异常（形状/索引/数值边界）：记录并上报，
                # 本帧跳过继续收；编程错误（TypeError/AttributeError 等）不在此列，
                # 必须上抛，否则"频谱有信号、没有声音、没有提示"。
                self._report_demod_failure(mode, exc)
                continue
            except Exception as exc:  # noqa: BLE001
                self._report_unexpected(exc, mode)
                break
            if audio is None or audio.size == 0:
                continue

            # 静噪门控（唯一刻度）：相对该模式噪声基线的余量（与 WFM 同口径）。
            try:
                self._squelch_gate(audio, player, mode)
            except Exception as exc:  # noqa: BLE001
                self._report_unexpected(exc, mode)
                break

            try:
                self._push_audio(audio, player, mode)
            except Exception as exc:  # noqa: BLE001
                self._report_unexpected(exc, mode)
                break
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
    #: 读线程连续无数据（≈1s）：上层应查询后端 status 并如实更新 UI
    read_failed = Signal(str)
    #: 单帧解调失败（限流后上报）：状态栏/日志据此给用户提示，不静默
    demod_failed = Signal(str)
    #: 静噪基线标定状态（标定中/完成/失败）：失败必须可见
    squelch_state = Signal(str)

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
        self._vfo2_stream = (PingPongStream(np.complex64, READ_BLOCK_SAMPLES * 4)
                             if vfo2_dsp is not None else None)
        self._record_stream = PingPongStream(np.complex64, READ_BLOCK_SAMPLES * 4)

        self._reader = IqReaderThread(read_fn, self._input, READ_BLOCK_SAMPLES)
        self._reader.read_failed.connect(self.read_failed)
        self._fft = FftWorker(self._spectrum_stream, gen, self._sr, center_getter)
        self._fft.frame_ready.connect(self._on_fft_frame)
        self._demod1 = DemodWorker(self._vfo1_stream, main_vfo_dsp, player,
                                   config, self._sr, audio_enabled=True)
        self._demod2 = (DemodWorker(self._vfo2_stream, vfo2_dsp, player,
                                    config, self._sr, audio_enabled=False)
                        if vfo2_dsp is not None else None)
        # 解调失败 / 静噪标定失败：如实转发布线（UI 侧可选择接状态栏）
        for w in (x for x in (self._demod1, self._demod2) if x is not None):
            w.demod_failed.connect(self.demod_failed)
            w.squelch_state.connect(self.squelch_state)
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
        if self._vfo2_stream is not None:
            self._splitter.bind(self._vfo2_stream)
        self._splitter.bind(self._record_stream)
        self._splitter.start()
        self._fft.start()
        self._demod1.start()
        if self._demod2 is not None:
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
        for w in (x for x in (self._fft, self._demod1, self._demod2,
                              self._record) if x is not None):
            try:
                w.shutdown()
            except Exception:
                pass
        # 4) join 所有线程
        for t in (x for x in (self._reader, self._fft, self._demod1,
                              self._demod2, self._record) if x is not None):
            try:
                t.wait(2000)
            except Exception:
                pass
        # 5) 清停止标志，便于下次重建流水线时复用对象（我们这里每次都新建，防御性调用）
        for s in (x for x in (self._input, self._spectrum_stream,
                              self._vfo1_stream, self._vfo2_stream,
                              self._record_stream) if x is not None):
            try:
                s.stop_reader()
                s.stop_writer()
            except Exception:
                pass
