"""
自动调制识别（AMR）
====================

对照 URH ``src/urh/ainterpretation/AutoInterpretation.py::detect_modulation``
（``:151-207``）与 ``Modulator.py:20`` 的调制类型枚举。

URH 的核心思路：
- 对 |z| 与 |z|/|z|（归一化）做 Haar 小波，比对方差 → 区分 ASK 与 FSK/PSK。
- 对 FFT 看是否有两个峰 → FSK。
- 中值滤波后方差骤降 → PSK（相位跳变是高频尖峰）。

本模块在保留这一思路的基础上扩展：
- 提取瞬时幅度 / 相位 / 频率的统计矩（均值、方差、峰度）。
- 加频谱平坦度、零交叉率、幅度零样本占比、瞬时频率离群比。
- 用规则决策树输出置信度，并给建议解调参数（中心频率、符号率估计）。

AI 增强：在规则之上加启发式——对低置信信号给「建议尝试的解调链」列表，
供 MBDSDR 接收链自动试解调。
"""

from __future__ import annotations

import dataclasses
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ModulationResult:
    """调制识别结果。"""

    modulation: str               # AM / FM / ASK / FSK / PSK / QAM / OOK / NOISE
    confidence: float             # 0..1
    center_freq: float            # Hz（基带相对中心）
    bandwidth: float              # Hz 估计占用带宽
    symbol_rate_hint: Optional[float] = None
    features: dict = dataclasses.field(default_factory=dict)
    suggestions: list = dataclasses.field(default_factory=list)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ModulationResult({self.modulation!r}, conf={self.confidence:.2f}, "
            f"fc={self.center_freq/1e3:.1f}kHz, bw={self.bandwidth/1e3:.1f}kHz)"
        )


# ---------------------------------------------------------------------------
# 特征提取
# ---------------------------------------------------------------------------

def _kurtosis(x: np.ndarray) -> float:
    """超额峰度（Fisher）。正态分布为 0。"""
    x = x - np.mean(x)
    var = np.var(x)
    if var < 1e-12:
        return 0.0
    return float(np.mean(x ** 4) / var ** 2 - 3.0)


def extract_features(iq: np.ndarray, sample_rate: float) -> dict:
    """从复基带信号提取一组统计特征。"""
    iq = np.asarray(iq, dtype=np.complex128)
    n = len(iq)
    if n < 64:
        raise ValueError(f"信号太短 ({n} samples)，至少需要 64 个样本")

    mag = np.abs(iq)
    phase = np.angle(iq)

    # 瞬时频率：unwrap 相位差分
    dphase = np.diff(np.unwrap(phase))
    inst_freq = dphase * sample_rate / (2.0 * np.pi)

    # 归一化幅度
    mag_norm = mag / (np.mean(mag) + 1e-12)

    # 幅度零样本占比（OOK 特征）
    threshold = 0.15 * (np.max(mag) + 1e-12)
    zero_frac = float(np.mean(mag < threshold))

    # 幅度统计
    mag_var = float(np.var(mag))
    mag_norm_var = float(np.var(mag_norm))
    mag_cv = float(np.std(mag) / (np.mean(mag) + 1e-12))   # 幅度变异系数

    # 瞬时频率统计
    freq_var = float(np.var(inst_freq))
    freq_std = float(np.sqrt(freq_var))
    freq_mean = float(np.mean(inst_freq))

    # 瞬时频率离群比（PSK 特征：相位跳变在 dphase 上产生大尖峰）
    med = np.median(inst_freq)
    mad = float(np.median(np.abs(inst_freq - med)))
    # 阈值取 MAD 与 0.3*freq_std 的较大者；频率恒定时 MAD≈0 则不视为离群
    outlier_thresh = max(3.0 * mad, 0.3 * freq_std)
    if freq_std < 100.0 or outlier_thresh < 1.0:
        outlier_ratio = 0.0
    else:
        outlier_ratio = float(np.mean(np.abs(inst_freq - med) > outlier_thresh))

    # FFT
    nfft = min(4096, 1 << max(8, int(np.ceil(np.log2(n)))))
    spectrum = np.abs(np.fft.fftshift(np.fft.fft(iq, nfft)))
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / sample_rate))
    spectrum = spectrum / (np.max(spectrum) + 1e-12)

    # 找前 10 个局部峰
    peak_idx = []
    for i in range(2, len(spectrum) - 2):
        if spectrum[i] > spectrum[i - 1] and spectrum[i] > spectrum[i + 1] \
                and spectrum[i] > 0.1:
            peak_idx.append(i)
    peak_idx.sort(key=lambda k: -spectrum[k])
    top_peaks = peak_idx[:10]

    if len(top_peaks) > 0:
        main_peak_bin = top_peaks[0]
        center_freq = float(freqs[main_peak_bin])
        peak_spacing = 0.0
        second_peak_amp = 0.0
        if len(top_peaks) > 1:
            peak_spacing = float(abs(freqs[top_peaks[1]] - freqs[main_peak_bin]))
            second_peak_amp = float(spectrum[top_peaks[1]])
    else:
        center_freq = 0.0
        peak_spacing = 0.0
        second_peak_amp = 0.0

    # 频谱平坦度
    p = spectrum[spectrum > 1e-6]
    if p.size >= 8:
        flatness = float(np.exp(np.mean(np.log(p))) / np.mean(p))
    else:
        flatness = 0.0

    # -20dB 带宽
    db = 20 * np.log10(spectrum + 1e-12)
    above = db > (db.max() - 20.0)
    if np.any(above):
        bw = float(freqs[above][-1] - freqs[above][0])
    else:
        bw = 0.0

    # 能量
    energy = float(np.mean(mag ** 2))

    return {
        "n_samples": n,
        "mag_cv": mag_cv,
        "mag_norm_var": mag_norm_var,
        "zero_frac": zero_frac,
        "freq_std": freq_std,
        "freq_var": freq_var,
        "outlier_ratio": outlier_ratio,
        "peak_spacing": peak_spacing,
        "center_freq": center_freq,
        "spectral_flatness": flatness,
        "bandwidth": bw,
        "energy": energy,
        "freq_mean": freq_mean,
        "second_peak_amp": second_peak_amp,
    }


# ---------------------------------------------------------------------------
# 分类器（规则决策树）
# ---------------------------------------------------------------------------

class ModulationClassifier:
    """基于规则的自动调制识别。

    决策树（按顺序判定）::

        energy 低                → NOISE
        zero_frac 高             → OOK
        mag_cv 高 + 频率稳定      → AM / ASK
        mag_cv 低 + 频率双峰      → FSK
        mag_cv 低 + 频率离群多    → PSK
        mag_cv 低 + 频率连续变化  → FM
        mag_cv 中 + 相位也变      → QAM
    """

    def __init__(self, noise_energy_floor: float = 1e-4):
        self.noise_energy_floor = noise_energy_floor

    def classify(self, iq: np.ndarray, sample_rate: float) -> ModulationResult:
        feat = extract_features(iq, sample_rate)

        # 1. 噪声
        if feat["energy"] < self.noise_energy_floor:
            return ModulationResult(
                modulation="NOISE", confidence=0.95,
                center_freq=0.0, bandwidth=0.0,
                features=feat,
                suggestions=["提高增益", "检查频谱是否有信号"],
            )

        # 2. 规则决策
        mod, conf = self._decide(feat)

        # 3. 符号率提示
        symbol_rate_hint = None
        if mod in ("FSK", "PSK", "ASK", "OOK", "QAM"):
            symbol_rate_hint = max(1.0, feat["bandwidth"] / 2.0)

        suggestions = self._suggest(mod, feat)

        return ModulationResult(
            modulation=mod,
            confidence=float(np.clip(conf, 0.0, 1.0)),
            center_freq=feat["center_freq"],
            bandwidth=feat["bandwidth"],
            symbol_rate_hint=symbol_rate_hint,
            features=feat,
            suggestions=suggestions,
        )

    # ------------------------------------------------------------------

    def _decide(self, f: dict) -> tuple:
        """返回 (modulation, confidence)。"""
        mag_cv = f["mag_cv"]
        zero = f["zero_frac"]
        outlier = f["outlier_ratio"]
        peak_spacing = f["peak_spacing"]
        second_peak_amp = f.get("second_peak_amp", 0.0)
        bw = f["bandwidth"]
        freq_std = f["freq_std"]

        # OOK：幅度有明显近零期
        if zero > 0.2:
            return "OOK", 0.85

        # FSK：频域有两个强度接近的峰（second_peak_amp > 0.5），
        # 且峰间距接近带宽（峰在带宽边缘，而非载波旁的边带）
        if second_peak_amp > 0.5 and peak_spacing > 0.5 * max(bw, 1.0):
            return "FSK", 0.85

        # PSK：瞬时频率有离群点（相位跳变），但幅度恒定
        if outlier > 0.0003 and mag_cv < 0.1:
            return "PSK", 0.8

        # AM：幅度连续慢变（mag_cv 大），频率恒定（outlier≈0）
        if mag_cv > 0.2 and outlier < 0.01:
            return "AM", 0.75

        # ASK：幅度变化但不是连续慢变（数字两电平）
        if mag_cv > 0.12 and zero < 0.05:
            return "ASK", 0.7

        # FM：频率连续变化，幅度恒定，无离群
        if mag_cv < 0.1 and freq_std > 100:
            return "FM", 0.75

        # QAM：幅度中等变化 + 相位也变
        if 0.05 < mag_cv < 0.2 and outlier > 0.001:
            return "QAM", 0.6

        # 兜底
        if mag_cv > 0.1:
            return "AM", 0.55

        return "UNKNOWN", 0.3

    # ------------------------------------------------------------------

    @staticmethod
    def _suggest(modulation: str, feat: dict) -> list:
        suggestions = []
        if modulation in ("OOK", "ASK"):
            suggestions.append("用包络检波 + 阈值判决")
            suggestions.append("尝试 ±5% 采样率偏移补偿")
        elif modulation == "FSK":
            suggestions.append("用正交鉴频（微分相位）后过零判决")
            suggestions.append(f"估计频偏: peak_spacing={feat['peak_spacing']:.0f}Hz")
        elif modulation == "PSK":
            suggestions.append("需要 Costas 环载波恢复")
            suggestions.append("BPSK 用科斯塔斯环，QPSK 用 4 相位鉴别")
        elif modulation == "FM":
            suggestions.append("用鉴频器（微分相位）→ 低通 → 音频")
        elif modulation == "AM":
            suggestions.append("用包络检波，可加 DC 阻断")
        elif modulation == "QAM":
            suggestions.append("需要均衡器 + 载波恢复")
            suggestions.append("先用盲识别判 16/64-QAM")
        elif modulation == "NOISE":
            suggestions.append("无信号，调整中心频率或增益")
        return suggestions
