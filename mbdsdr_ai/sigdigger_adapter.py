"""
MBDSDR AI - SigDigger / sigutils 真实源码移植适配器
=====================================================
sigdigger_adapter.py

把 SigDigger (https://github.com/BatchDrake/SigDigger) 的底层库 sigutils
(https://github.com/BatchDrake/sigutils) 的「信号检测 + 自动调制识别」核心
从 C 移植为纯 numpy。不依赖 libsigutils / suscan。

覆盖：
  1. 运行峰值检测器（滑动窗均值/方差，阈值用 sigma 倍数）
     - src/sigutils/detect.c:48  su_peak_detector_feed
  2. 信道发现（能量检测 + SNR 门限 + 最小带宽）
     - src/include/sigutils/detect.h:37-47  常量
       MIN_SNR=6dB, MIN_BW=10Hz, ALPHA=1e-2, BETA=1e-3, GAMMA=0.5,
       pd_thres=2(sigmas), pd_signif=10dB
  3. 自动调制识别（基于瞬时幅度/相位/频率特征的决策树）
     - SigDigger UIMediator / suscan 调制分类思路
       （常包络 vs 变包络 → FM/PSK vs AM/ASK；频偏大小 → FSK vs CW）

纯 numpy。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# ── sigutils 信道检测器常量（detect.h:37-47）─────────────────────────────
SU_CHANNEL_DETECTOR_MIN_SNR_DB = 6.0    # detect.h:38  SU_CHANNEL_DETECTOR_MIN_SNR
SU_CHANNEL_DETECTOR_MIN_BW_HZ = 10.0    # detect.h:39  SU_CHANNEL_DETECTOR_MIN_BW
SU_CHANNEL_DETECTOR_ALPHA = 1e-2        # detect.h:41  PSD 平均系数
SU_CHANNEL_DETECTOR_BETA = 1e-3         # detect.h:42  spmax/spmin 平均系数
SU_CHANNEL_DETECTOR_GAMMA = 0.5         # detect.h:43  峰值跟踪系数
SU_PD_THRES_SIGMAS = 2.0               # detect.h:141 pd_thres = 2
SU_PD_SIGNIF_DB = 10.0                 # detect.h:142 pd_signif = 10 dB


# ═══════════════════════════════════════════════════════════════════════
# 1. 运行峰值检测器 —— 移植 detect.c:48 su_peak_detector_feed
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class SigutilsPeakDetector:
    """滑动窗峰值检测器。

    维护长度 size 的历史缓冲与累加器 accum。缓冲填满后，
    用窗内均值/方差算 threshold = thr2 * variance（thr2 = thres^2），
    若 (x-mean)^2 > threshold 则判峰：x>mean → +1（上峰），否则 -1（下峰）。

    移植 detect.c:48-100。
    """

    size: int = 10
    thres_sigmas: float = SU_PD_THRES_SIGMAS

    def __post_init__(self):
        self.thr2 = self.thres_sigmas ** 2       # detect.c:38
        self.history = np.zeros(self.size, dtype=np.float64)
        self.p = 0
        self.count = 0
        self.accum = 0.0
        self.inv_size = 1.0 / self.size          # detect.c:43

    def feed(self, x: float) -> int:
        """喂入一个样本，返回 0（非峰）/ +1（上峰）/ -1（下峰）。detect.c:48。"""
        if self.count < self.size:
            # 填充阶段，不判决（detect.c:67-69）
            self.history[self.count] = x
            self.count += 1
            self.accum += x
            return 0

        mean = self.inv_size * self.accum                 # detect.c:71
        d = self.history - mean
        variance = float(np.sum(d * d)) * self.inv_size    # detect.c:74-79
        x2 = (x - mean) ** 2                               # detect.c:81-82
        threshold = self.thr2 * variance                   # detect.c:83

        peak = 0
        if x2 > threshold:                                 # detect.c:85
            peak = 1 if x > mean else -1                   # detect.c:86

        # 滑动：弹出最老样本，压入新样本
        self.accum -= self.history[self.p]                 # detect.c:90
        self.history[self.p] = x                           # detect.c:91
        self.p += 1
        if self.p == self.size:                            # detect.c:93-94
            self.p = 0
        self.accum += x                                    # detect.c:97
        return peak


# ═══════════════════════════════════════════════════════════════════════
# 2. 信道发现 —— 移植 detect.c 能量/SNR 检测
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class SigutilsChannel:
    """一个检测到的信道。对应 sigutils_channel（detect.h:145）。"""
    fc_hz: float          # 中心频率
    f_lo_hz: float        # 下界
    f_hi_hz: float        # 上界
    bw_hz: float          # 带宽
    snr_db: float         # 信噪比
    present: bool = True


class SigutilsChannelDetector:
    """基于 PSD 的信道发现器。

    用平滑后的功率谱，估计噪声底 N0，把超过 N0*10^(MIN_SNR/10) 的连续段
    作为信道，要求带宽 > MIN_BW。对应 detect.c 的 DISCOVERY 模式。
    """

    def __init__(self, sample_rate: float,
                 min_snr_db: float = SU_CHANNEL_DETECTOR_MIN_SNR_DB,
                 min_bw_hz: float = SU_CHANNEL_DETECTOR_MIN_BW_HZ):
        self.sample_rate = float(sample_rate)
        self.min_snr_db = min_snr_db
        self.min_bw_hz = min_bw_hz

    def detect(self, freqs_hz: np.ndarray, powers_db: np.ndarray) -> List[SigutilsChannel]:
        """在功率谱上发现信道。detect.c DISCOVERY 模式。"""
        freqs = np.asarray(freqs_hz, dtype=float)
        p = np.asarray(powers_db, dtype=float)
        if len(p) == 0:
            return []

        # 噪声底：取最低 10% bin 的均值（detect.c:706-712 用最小值近似，这里更稳）
        sorted_p = np.sort(p)
        n_low = max(1, int(len(p) * 0.1))
        n0_db = float(np.mean(sorted_p[:n_low]))

        # SNR 门限（detect.h:51  cp->snr > MIN_SNR）
        threshold_db = n0_db + self.min_snr_db
        above = p > threshold_db

        channels: List[SigutilsChannel] = []
        # 找连续超阈段
        idx = 0
        n = len(above)
        while idx < n:
            if not above[idx]:
                idx += 1
                continue
            start = idx
            while idx < n and above[idx]:
                idx += 1
            end = idx - 1
            f_lo = float(freqs[start])
            f_hi = float(freqs[end])
            bw = abs(f_hi - f_lo)
            if bw < self.min_bw_hz:
                continue
            seg = p[start:end + 1]
            peak = float(seg.max())
            peak_bin = start + int(np.argmax(seg))
            channels.append(SigutilsChannel(
                fc_hz=float(freqs[peak_bin]),
                f_lo_hz=f_lo, f_hi_hz=f_hi, bw_hz=bw,
                snr_db=peak - n0_db,
            ))
        channels.sort(key=lambda c: c.snr_db, reverse=True)
        return channels


# ═══════════════════════════════════════════════════════════════════════
# 3. 自动调制识别 —— 基于瞬时特征的决策树
# ═══════════════════════════════════════════════════════════════════════
def identify_modulation(samples: np.ndarray, sample_rate: float) -> Dict[str, Any]:
    """自动调制识别（AMR）。

    思路来自 SigDigger/suscan 的调制分析：用归一化瞬时幅度/相位/频率的
    统计量区分 CW/AM/ASK/FM/FSK/PSK。
      * 包络几乎恒定（amp_cv 小）→ 恒包络调制（FM/PSK/CW）
      * 包络变化大（amp_cv 大）→ AM/ASK
      * 频偏小且幅度恒定 → CW/窄带FM
      * 频偏大 → FSK/FM
    """
    samples = np.asarray(samples, dtype=complex)
    if len(samples) < 64:
        return {"modulation": "unknown", "reason": "样本太少(<64)"}

    amp = np.abs(samples)
    amp_cv = float(np.std(amp) / (np.mean(amp) + 1e-12))  # 幅度变异系数

    phase = np.unwrap(np.angle(samples))
    inst_freq = np.diff(phase) * sample_rate / (2 * np.pi)
    freq_std = float(np.std(inst_freq))
    freq_range = float(np.max(np.abs(inst_freq)))

    # 频谱平坦度（白噪声=1）
    sp = np.abs(np.fft.fft(samples)) + 1e-12
    flat = float(np.exp(np.mean(np.log(sp))) / np.mean(sp))

    if flat > 0.8:
        mod = "noise/unknown"
    elif amp_cv < 0.05 and freq_std < sample_rate * 0.005:
        mod = "CW (等幅单音)"
    elif amp_cv < 0.1 and freq_std > sample_rate * 0.01:
        # 恒包络 + 频偏大 → FM/FSK
        mod = "FM/FSK (恒包络角调制)"
    elif amp_cv > 0.3 and freq_std < sample_rate * 0.01:
        mod = "AM/ASK (变包络幅度调制)"
    elif amp_cv > 0.1 and freq_std > sample_rate * 0.01:
        mod = "QAM/复合 (幅度+相位联合)"
    else:
        mod = "PSK (恒包络相位调制)"

    return {
        "modulation": mod,
        "amp_cv": amp_cv,
        "freq_std_hz": freq_std,
        "freq_range_hz": freq_range,
        "spectral_flatness": flat,
        "sample_rate": sample_rate,
        "source": "SigDigger UIMediator / sigutils detect.h:37-47",
    }


# ═══════════════════════════════════════════════════════════════════════
# ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_sigdigger_tools(registry) -> None:
    """把 sigutils 信号检测 + AMR 工具注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    def _detect_channels(args):
        """在功率谱上发现信道。"""
        freqs = args.get("frequencies")
        powers = args.get("powers_db")
        sr = float(args.get("sample_rate", 1e6))
        if not isinstance(freqs, list) or not isinstance(powers, list):
            return ToolResult(False, "frequencies 和 powers_db 都必须是列表")
        try:
            det = SigutilsChannelDetector(
                sr,
                min_snr_db=float(args.get("min_snr_db", SU_CHANNEL_DETECTOR_MIN_SNR_DB)),
                min_bw_hz=float(args.get("min_bw_hz", SU_CHANNEL_DETECTOR_MIN_BW_HZ)),
            )
            chans = det.detect(np.asarray(freqs), np.asarray(powers))
            data = {
                "channels": [c.__dict__ for c in chans],
                "num_channels": len(chans),
                "defaults": {"min_snr_db": SU_CHANNEL_DETECTOR_MIN_SNR_DB,
                             "min_bw_hz": SU_CHANNEL_DETECTOR_MIN_BW_HZ},
                "source": "sigutils detect.h:37-47 / detect.c DISCOVERY",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"信道检测失败: {e}")

    def _identify_modulation(args):
        """自动调制识别。"""
        iq = args.get("iq")
        if not isinstance(iq, list):
            return ToolResult(False, "iq 必须是复数采样列表（交错 [i0,q0,...]）")
        try:
            arr = np.asarray(iq, dtype=float)
            c = arr[0::2] + 1j * arr[1::2] if arr.size % 2 == 0 else arr.astype(complex)
            sr = float(args.get("sample_rate", 1e6))
            out = identify_modulation(c, sr)
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"调制识别失败: {e}")

    def _peak_detector_feed(args):
        """滑动窗峰值检测器：喂入功率值序列，返回峰位置。"""
        values = args.get("values")
        if not isinstance(values, list):
            return ToolResult(False, "values 必须是数值列表")
        try:
            pd = SigutilsPeakDetector(size=int(args.get("size", 10)),
                                      thres_sigmas=float(args.get("thres_sigmas", SU_PD_THRES_SIGMAS)))
            peaks = []
            for i, v in enumerate(values):
                r = pd.feed(float(v))
                if r != 0:
                    peaks.append({"index": i, "direction": "up" if r > 0 else "down"})
            data = {"peaks": peaks, "num_peaks": len(peaks),
                    "source": "sigutils detect.c:48 su_peak_detector_feed"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"峰值检测失败: {e}")

    registry.register(
        name="sigutils_detect_channels",
        description=("sigutils 信道发现：给频率轴+功率(dB)，按 SNR>6dB、带宽>10Hz "
                     "列出所有信道（中心频/带宽/SNR）。"),
        parameters={
            "type": "object",
            "properties": {
                "frequencies": {"type": "array", "items": {"type": "number"}},
                "powers_db": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number", "default": 1e6},
                "min_snr_db": {"type": "number", "default": SU_CHANNEL_DETECTOR_MIN_SNR_DB},
                "min_bw_hz": {"type": "number", "default": SU_CHANNEL_DETECTOR_MIN_BW_HZ},
            },
            "required": ["frequencies", "powers_db"],
        },
        handler=_detect_channels,
        category="sdr_spectrum",
    )
    registry.register(
        name="sigutils_identify_modulation",
        description=("SigDigger/sigutils 自动调制识别：复 IQ → CW/AM/ASK/FM/FSK/PSK 判别。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number", "default": 1e6},
            },
            "required": ["iq"],
        },
        handler=_identify_modulation,
        category="sdr_spectrum",
    )
    registry.register(
        name="sigutils_peak_detector",
        description=("sigutils 滑动窗峰值检测器：在功率序列上用 mean±2σ 找峰。"
                     "移植 detect.c:48。"),
        parameters={
            "type": "object",
            "properties": {
                "values": {"type": "array", "items": {"type": "number"}},
                "size": {"type": "integer", "default": 10},
                "thres_sigmas": {"type": "number", "default": SU_PD_THRES_SIGMAS},
            },
            "required": ["values"],
        },
        handler=_peak_detector_feed,
        category="sdr_spectrum",
    )
