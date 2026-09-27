"""
receive_chain.py — 实时接收管线（ReceiveChain）
=================================================

把 15 个已单测通过的 DSP 模块端到端串成一条状态化、可动态调参的实时
接收链。一个 :class:`ReceiveChain` 实例 = 一个 VFO；多个 VFO 各持一条链，
共享同一份前端 IQ，互不干扰。

管线（数据流，左→右）::

    device IQ (complex64, fs_in)
      │
      ├─ DCBlocker        I/Q 独立一阶 IIR 去直流（dc_blocker.DCBlocker）
      ├─ IQCorrector      g/φ 校正矩阵，默认单位阵（iq_correction.IQCorrector）
      ├─ DecimatingFIR    抗混叠整数抽取 fs_in → fs_stage（decimating_fir）
      ├─ XlatingFIR       NCO 频移(±offset) → 有理重采样 → 信道 LPF
      │                   输出复基带（complex128, if_sr）（channelizer.XlatingFIR）
      ├─ Squelch          RMS 门控（attack/decay/hang），AGC/解调前（squelch.Squelch）
      ├─ Demodulator      AM / USB / LSB / NFM / WFM(立体声) / CW
      │                   输出 float32 单声道，WFM 输出 (L, R) 双通道（if_sr）
      ├─ AudioResampler   有状态多相 FIR 重采样 if_sr → 48 kHz（audio_resampler）
      └─ 输出 float32（单声道 (N,) 或立体声 (N, 2)），采样率 48000 Hz

阶段与默认参数（随模式切换，见 ``_MODE_TABLE``）：

    模式   if_sr(Hz)  信道BW(Hz)  kind   输出
    AM     15000     10000       am     单声道包络
    FM/NFM 50000     12500       nfm    单声道相位鉴频+去加重
    WFM    250000    150000      wfm    立体声 pilot19k→38k 再生
    USB    24000     2800        usb    乘积检波（+BW/2）
    LSB    24000     2800        lsb    乘积检波（-BW/2）
    CW     3000      200         cw     BFO 800Hz 差拍

────────────────────────────────────────────────────────────────────────
接口契约（供 UI / 录音 / 声卡消费，红线）
────────────────────────────────────────────────────────────────────────

输入
    ``process(iq_chunk)``：
      * iq_chunk : complex64/complex128 ndarray，shape (N,)，N≥0。
        设备未连接 / 无新样本时调用方应传 ``None`` 或空数组。
      * 本模块**绝不伪造数据**：输入为空/None 时返回空 float32，不补零假象。

输出
      * 单声道模式：np.float32，shape (N,)。
      * WFM 立体声：np.float32，shape (N, 2)（左/右交错为列）。
      * 输出采样率恒为 ``audio_out_sr``（默认 48000 Hz）。
      * 块长度随重采样比例逐块变化（不固定），消费方不得假设定长。

状态
      * 所有滤波器/相位累加器/门控状态跨块连续，**不**在每块之间重置。
      * 仅当调用 ``set_mode / set_frequency_offset / set_bandwidth / reset``
        等真正改变物理配置时才重建并清空状态（避免旧频/旧模式串音）。

线程安全
      * ``process()`` 本身**非线程安全**：应在单条音频消费线程里顺序调用。
      * ``set_*()`` 是控制面操作；与 ``process()`` 并发时，控制面可能在
        块边界重建滤波器——最坏情况是一次瞬态，不会崩溃。实时消费线程
        应由调用方（SDRBackend 音频线程）独占驱动 ``process``。

无设备行为
      * ReceiveChain 本身不碰硬件。无 IQ 输入时 process 返回空数组。
      * 配合 SDRBackend：未连接时 ``read_audio()`` 返回 None、
        ``start_audio()`` 返回 False（见 sdr_backend.py）。

License: GPL-3.0-or-later
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple, Union

import numpy as np

from .dc_blocker import DCBlocker
from .iq_correction import IQCorrector
from .decimating_fir import DecimatingFIR, design_decimation_taps
from .channelizer import XlatingFIR
from .squelch import Squelch
from .audio_resampler import AudioResampler

# 解调器（保持与各自构造器默认值一致）
from .demod_am import DemodAM
from .demod_ssb import DemodSSB
from .demod_nfm import DemodNFM
from .demod_wfm import DemodWFM
from .demod_cw import DemodCW

logger = logging.getLogger(__name__)

Array1d = np.ndarray


# ─────────────────────────────────────────────────────────────────────────
# 模式 → (if_sr, 信道双边带宽, 解调类型)。带宽对齐 SDR++ getDefaultBandwidth：
#   WFM=150k, NFM=12.5k, AM=10k, USB/LSB=2.8k, CW=200。
# ─────────────────────────────────────────────────────────────────────────
_MODE_TABLE = {
    "AM":  dict(if_sr=15000.0,  bandwidth=10000.0, kind="am"),
    "FM":  dict(if_sr=50000.0,  bandwidth=12500.0, kind="nfm"),
    "NFM": dict(if_sr=50000.0,  bandwidth=12500.0, kind="nfm"),
    "WFM": dict(if_sr=250000.0, bandwidth=150000.0, kind="wfm"),
    "USB": dict(if_sr=24000.0,  bandwidth=2800.0,  kind="usb"),
    "LSB": dict(if_sr=24000.0,  bandwidth=2800.0,  kind="lsb"),
    "SSB": dict(if_sr=24000.0,  bandwidth=2800.0,  kind="usb"),
    "CW":  dict(if_sr=3000.0,   bandwidth=200.0,   kind="cw"),
}

# NFM 去加重时间常数：欧/中 50μs（demod_nfm 默认 0=不去加重，这里给标准值）
_NFM_DEEMPH_TAU = 50e-6


class ReceiveChain:
    """一条状态化实时接收链（= 一个 VFO）。

    Parameters
    ----------
    fs_in : float
        前端复采样率 Hz（设备 IQ 采样率，如 2.048e6）。
    mode : str
        初始解调模式（AM/FM/NFM/WFM/USB/LSB/SSB/CW）。
    bandwidth : float, optional
        信道双边带宽 Hz；None 时按模式表取默认。
    frequency_offset : float
        VFO 相对中心频率的偏移 Hz：把 +frequency_offset 处的信号搬到基带 0Hz。
    squelch_db : float
        RMS 静噪门限 dBFS；-150 表示完全开门。
    gain_db : float
        链后数字音频增益 dB（硬件增益由设备端/GainStager 负责）。
    audio_out_sr : float
        输出音频采样率，默认 48000 Hz。
    """

    def __init__(self,
                 fs_in: float,
                 mode: str = "FM",
                 bandwidth: Optional[float] = None,
                 frequency_offset: float = 0.0,
                 squelch_db: float = -150.0,
                 gain_db: float = 0.0,
                 audio_out_sr: float = 48000.0) -> None:
        self.fs_in = float(fs_in)
        self.audio_out_sr = float(audio_out_sr)
        self._mode = (mode or "FM").upper()
        self._frequency_offset = float(frequency_offset)
        self._squelch_db = float(squelch_db)
        self._gain_db = float(gain_db)
        self._bandwidth = float(bandwidth) if bandwidth else None

        # ── 前端（fs_in，与模式无关，只建一次）──
        self._dc = DCBlocker(r=0.999)
        self._iqcorr = IQCorrector()

        # ── 后级（随模式/带宽重建）──
        self._decim: Optional[DecimatingFIR] = None
        self._channel: Optional[XlatingFIR] = None
        self._squelch: Optional[Squelch] = None
        self._demod = None
        self._rs_mono: Optional[AudioResampler] = None
        self._rs_l: Optional[AudioResampler] = None
        self._rs_r: Optional[AudioResampler] = None
        self._cfg = _MODE_TABLE[self._mode].copy()
        if self._bandwidth is not None:
            self._cfg["bandwidth"] = self._bandwidth

        self._build_backend()

    # ------------------------------------------------------------------
    # 配置构建
    # ------------------------------------------------------------------
    def _lookup(self, mode: str) -> dict:
        key = (mode or "FM").upper()
        if key not in _MODE_TABLE:
            logger.warning("未知模式 %r，回退 FM", mode)
            key = "FM"
        return _MODE_TABLE[key].copy()

    def _build_backend(self) -> None:
        """按当前 _cfg 重建后级（抽取/信道化/静噪/解调/重采样），并清空状态。"""
        if_sr = self._cfg["if_sr"]
        bw = self._cfg["bandwidth"]
        kind = self._cfg["kind"]
        offset = self._frequency_offset

        # 1) 粗抗混叠整数抽取（DecimatingFIR）。
        #
        # 关键正确性：绝不能一路抽到 if_sr——那样抗混叠低通截止=fs_stage/2
        # 会在 NCO 之前就把 offset 处的目标信道滤掉。正确做法是只粗抽到一个
        # “宽带 IF” fs_stage，使它的 Nyquist 仍能包住目标信道（|offset|+BW），
        # 再由 Channelizer 的 NCO 把该信道搬到基带、精抽到 if_sr。
        #
        # 约束：fs_stage/2 是抗混叠低通截止，必须比 |offset|+BW 多出过渡带余量
        # （Nuttall 过渡带 ≈ 0.1·cutoff），否则目标信道落在过渡带被压掉。
        # 取 2.5× 留出约一个过渡带的余量；fs_stage ≥ 2·if_sr。
        fs_stage_target = max(2.0 * if_sr, 2.5 * (abs(offset) + bw))
        if self.fs_in > fs_stage_target:
            d_front = max(1, int(self.fs_in // fs_stage_target))
        else:
            d_front = 1
        try:
            taps = design_decimation_taps(d_front, self.fs_in)
            self._decim = DecimatingFIR(taps, decimation=d_front)
        except Exception as e:  # pragma: no cover - 抽头设计兜底
            logger.warning("DecimatingFIR 构建失败，退化为直通: %s", e)
            self._decim = DecimatingFIR(np.ones(1, dtype=np.float64), decimation=1)

        fs_stage = self.fs_in / d_front

        # 2) 信道化（Xlating FIR）：NCO 把 +offset 搬到 0，再精抽到 if_sr，信道 LPF。
        self._channel = XlatingFIR(
            in_sr=fs_stage,
            out_sr=if_sr,
            bandwidth=bw,
            offset=offset,
        )

        # 3) 静噪门控（在解调前）。时间常数按 if_sr 换算。
        self._squelch = Squelch(
            threshold_db=self._squelch_db,
            hang_ms=200.0, attack_ms=5.0, decay_ms=50.0,
            sample_rate=if_sr,
        )

        # 4) 解调器
        if kind == "am":
            self._demod = DemodAM(if_sr=if_sr, bandwidth=bw)
        elif kind == "nfm":
            self._demod = DemodNFM(if_sr=if_sr, bandwidth=bw,
                                   deemph_tau=_NFM_DEEMPH_TAU)
        elif kind == "wfm":
            self._demod = DemodWFM(if_sr=if_sr, bandwidth=bw,
                                   stereo=True, deemph_tau=50e-6)
        elif kind == "usb":
            self._demod = DemodSSB(mode="usb", if_sr=if_sr, bandwidth=bw)
        elif kind == "lsb":
            self._demod = DemodSSB(mode="lsb", if_sr=if_sr, bandwidth=bw)
        elif kind == "cw":
            self._demod = DemodCW(if_sr=if_sr, tone=800.0, bandwidth=bw)
        else:  # pragma: no cover
            raise ValueError(f"未知解调类型 {kind!r}")

        # 5) 音频重采样器（if_sr → 48k）。立体声用两个独立实例，避免 L/R 串相位。
        self._rs_mono = AudioResampler(if_sr, self.audio_out_sr)
        if kind == "wfm":
            self._rs_l = AudioResampler(if_sr, self.audio_out_sr)
            self._rs_r = AudioResampler(if_sr, self.audio_out_sr)
        else:
            self._rs_l = self._rs_r = None

    # ------------------------------------------------------------------
    # 控制面（动态可调）
    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def if_sr(self) -> float:
        return self._cfg["if_sr"]

    @property
    def is_stereo(self) -> bool:
        return self._cfg["kind"] == "wfm"

    def set_mode(self, mode: str) -> None:
        """切换解调模式（重建后级并清空状态）。"""
        new = self._lookup(mode)
        self._mode = (mode or "FM").upper()
        self._cfg = new
        if self._bandwidth is not None:
            self._cfg["bandwidth"] = self._bandwidth
        self._build_backend()

    def set_frequency_offset(self, hz: float) -> None:
        """设置 VFO 频偏（把 +hz 处信号搬到基带）。只更新 NCO，重建信道。"""
        self._frequency_offset = float(hz)
        # offset 改变 NCO 相位增量与目标信号位置，重建信道化器最稳妥
        self._build_backend()

    def set_bandwidth(self, hz: float) -> None:
        """设置信道双边带宽 Hz。"""
        self._bandwidth = float(hz)
        self._cfg["bandwidth"] = self._bandwidth
        self._build_backend()

    def set_squelch(self, db: float) -> None:
        """设置 RMS 静噪门限 dBFS（-150=完全开门）。"""
        self._squelch_db = float(db)
        if self._squelch is not None:
            self._squelch.threshold_db = float(db)

    def set_gain(self, db: float) -> None:
        """设置链后数字音频增益 dB。"""
        self._gain_db = float(db)

    def set_iq_imbalance(self, gain_ratio: float, phase_error_deg: float) -> None:
        """注入已知 I/Q 不平衡参数（默认单位阵直通）。"""
        self._iqcorr.set_imbalance(gain_ratio, phase_error_deg)

    def reset(self) -> None:
        """清空所有内部状态（换源/换频后调用）。"""
        self._dc.reset()
        self._iqcorr.reset()
        if self._decim is not None:
            self._decim.reset()
        if self._channel is not None:
            self._channel.reset()
        if self._squelch is not None:
            self._squelch.reset()
        if self._demod is not None:
            self._demod.reset()
        if self._rs_mono is not None:
            self._rs_mono.reset()
        if self._rs_l is not None:
            self._rs_l.reset()
        if self._rs_r is not None:
            self._rs_r.reset()

    # ------------------------------------------------------------------
    # 处理面
    # ------------------------------------------------------------------
    def process(self, iq_chunk: Optional[np.ndarray]) -> np.ndarray:
        """处理一块复 IQ，输出一块音频（float32，48kHz）。

        无输入（None/空）时返回空 float32，绝不补假数据。
        """
        if iq_chunk is None:
            return np.zeros(0, dtype=np.float32)
        x = np.asarray(iq_chunk)
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)

        # 1) DC 阻断（I/Q 独立）
        x = self._dc.process(x)
        # 2) I/Q 校正（默认单位阵，数值上近似直通）
        x = self._iqcorr.process(x)

        # 3) 抗混叠抽取 fs_in → fs_stage
        if self._decim is not None:
            x = self._decim.process(x)
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)

        # 4) 信道化：NCO → 有理重采样 → 信道 LPF，得复基带（complex128, if_sr）
        if self._channel is not None:
            x = self._channel.process(x)
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)

        # 5) 静噪门控（RMS，解调前）：门关时整块置零
        if self._squelch is not None:
            x = self._squelch.apply(x)
            if x.size == 0:
                return np.zeros(0, dtype=np.float32)

        # 6) 解调
        kind = self._cfg["kind"]
        if kind == "wfm":
            left, right = self._demod.process(x)
            left = np.asarray(left, dtype=np.float32)
            right = np.asarray(right, dtype=np.float32)
            # 7a) 立体声重采样（L/R 各一个有状态重采样器）
            if self._rs_l is not None and self._rs_r is not None:
                left = self._rs_l.process(left)
                right = self._rs_r.process(right)
            n = min(len(left), len(right))
            out = np.column_stack((left[:n], right[:n])).astype(np.float32)
        else:
            audio = self._demod.process(x)
            audio = np.asarray(audio, dtype=np.float32)
            # 7b) 单声道重采样 if_sr → 48k
            if self._rs_mono is not None:
                audio = self._rs_mono.process(audio)
            out = audio.astype(np.float32)

        # 8) 数字增益
        if self._gain_db != 0.0 and out.size:
            out = out * (10.0 ** (self._gain_db / 20.0))
        # 9) 软限幅：阈值以下完全线性（不染色正常电平），超过阈值用 tanh
        # 软压到 ±1 以内，避免广播高峰被声卡硬削波产生刺耳失真。
        if out.size:
            thr = 0.9
            mag = np.abs(out)
            hard = mag > thr
            if hard.any():
                comp = thr + (1.0 - thr) * np.tanh((mag - thr) / (1.0 - thr))
                out = np.where(hard, np.sign(out) * comp, out)
            out = np.clip(out, -1.0, 1.0).astype(np.float32)
        return out


__all__ = ["ReceiveChain"]
