"""
新时空面板 - 自动调制识别（AMR）实时流分析器
================================================

在已有 :mod:`mbdsdr_ai.amr` 分类器之上，面向实时频谱/IQ 流做一层
"信号检测 → 频谱特征提取 → 调制分类 → 标签归一化" 的流水线。

设计原则（不瞎猜）：
- 先用功率谱噪声基底 + SNR 阈值做信号检测；低于阈值或带宽过窄一律判为
  "未检测到信号"，绝不输出猜测的调制方式。
- 复用已有 :class:`mbdsdr_ai.amr.AMRClassifier`（懒加载），不重写分类器。
- SSB 的上边带/下边带归属由频谱不对称性判定（模型本身没有 SSB 模板）：
  基带上边带功率显著大于下边带记为 USB，反之为 LSB。
- 仅吃功率谱的轻量路径 :meth:`analyze_spectrum` 不做 IQ 级分类，供频谱组件
  实时刷新使用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np

from .amr import AMRClassifier, ModulationType


# 无信号时统一用一个很低的功率值代替 -inf，便于 UI 直接显示
_NO_SIGNAL_POWER_DB = -120.0

# 下边带/上边带功率比超过该倍数即判定为单边带归属
_SIDEBAND_RATIO = 1.5

# 信号质心偏离基带中心超过该频率(Hz)才承认边带归属，避免中心处 CW 误判
_OFF_CENTER_GATE_HZ = 200.0


@dataclass
class AMRStreamResult:
    """单次流分析结果。"""

    signal_detected: bool
    modulation: str               # FM/AM/USB/LSB/CW/数字/未检测到信号/待IQ分析
    confidence: float             # 0-1
    bandwidth_hz: float           # 信号 -3dB 带宽（Hz），无信号为 0
    peak_count: int               # 频谱峰数量，无信号为 0
    center_offset_hz: float       # 信号中心相对基带中心的偏移（Hz）
    noise_floor_db: float         # 噪声基底（dBFS）
    signal_power_db: float        # 信号功率（dBFS），无信号为 -120.0
    snr_db: float                 # 信噪比（dB），无信号为 0
    spectral_features: Dict[str, Any] = field(default_factory=dict)
    raw_label: str = ""           # AMR 模型原始标签，如 SSB/FSK 等


class AMRStreamAnalyzer:
    """复基带 IQ / 功率谱的实时 AMR 流分析器。"""

    def __init__(self, snr_threshold_db: float = 6.0,
                 min_bandwidth_hz: float = 100.0):
        self.snr_threshold_db = float(snr_threshold_db)
        self.min_bandwidth_hz = float(min_bandwidth_hz)
        # 窄强单音(CW)接收余量：段宽不足 min_bandwidth 但峰值高出该阈值仍算信号
        self.narrow_peak_margin_db = 15.0
        self._classifier: Optional[AMRClassifier] = None

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #
    def _ensure_classifier(self) -> AMRClassifier:
        """懒加载内置分类器（首次 analyze_iq 时创建）。"""
        if self._classifier is None:
            self._classifier = AMRClassifier()
        return self._classifier

    def reset(self) -> None:
        """重置内部状态（本分析器无跨帧状态，保留接口以对齐面板生命周期）。"""
        self._classifier = None

    def analyze_iq(self, iq: np.ndarray, sample_rate: float,
                   center_freq: float = 0.0) -> AMRStreamResult:
        """从复基带 IQ 做完整分析：信号检测 → 特征提取 → 分类 → 标签映射。"""
        iq = np.asarray(iq, dtype=np.complex128)
        if iq.size == 0:
            return self._no_signal(noise_floor_db=_NO_SIGNAL_POWER_DB)

        spec_db, psd, freqs = self._power_spectrum_db(iq, sample_rate)
        det = self._detect_and_features(spec_db, psd, freqs, sample_rate)

        if not det["signal_detected"]:
            return AMRStreamResult(
                signal_detected=False,
                modulation="未检测到信号",
                confidence=0.0,
                bandwidth_hz=0.0,
                peak_count=0,
                center_offset_hz=0.0,
                noise_floor_db=det["noise_floor_db"],
                signal_power_db=_NO_SIGNAL_POWER_DB,
                snr_db=0.0,
                spectral_features=det["spectral_features"],
                raw_label="",
            )

        # 有信号：调用已有 AMR 分类器
        clf = self._ensure_classifier()
        amr_res = clf.classify_iq(iq.tolist(), sample_rate)
        raw_type = amr_res.predicted_modulation
        raw_label = raw_type.value
        confidence = float(amr_res.confidence)

        modulation, confidence = self._decide_modulation(
            raw_type, det["upper_power"], det["lower_power"],
            det["center_offset_hz"], confidence)

        return AMRStreamResult(
            signal_detected=True,
            modulation=modulation,
            confidence=confidence,
            bandwidth_hz=det["bandwidth_hz"],
            peak_count=det["peak_count"],
            center_offset_hz=det["center_offset_hz"],
            noise_floor_db=det["noise_floor_db"],
            signal_power_db=det["signal_power_db"],
            snr_db=det["snr_db"],
            spectral_features=det["spectral_features"],
            raw_label=raw_label,
        )

    def analyze_spectrum(self, spectrum_db: np.ndarray,
                         freqs_hz: np.ndarray) -> AMRStreamResult:
        """仅从功率谱（dB）做信号检测和频谱特征，不做 IQ 级分类。"""
        spectrum_db = np.asarray(spectrum_db, dtype=np.float64)
        freqs_hz = np.asarray(freqs_hz, dtype=np.float64)
        if spectrum_db.size == 0 or freqs_hz.size == 0:
            return self._no_signal(noise_floor_db=_NO_SIGNAL_POWER_DB)

        # 由 dB 反推线性功率用于质心/边带能量计算
        psd = 10.0 ** (spectrum_db / 10.0)
        sample_rate = float(abs(freqs_hz[-1] - freqs_hz[0]))

        det = self._detect_and_features(spectrum_db, psd, freqs_hz, sample_rate)

        if not det["signal_detected"]:
            return AMRStreamResult(
                signal_detected=False,
                modulation="未检测到信号",
                confidence=0.0,
                bandwidth_hz=0.0,
                peak_count=0,
                center_offset_hz=0.0,
                noise_floor_db=det["noise_floor_db"],
                signal_power_db=_NO_SIGNAL_POWER_DB,
                snr_db=0.0,
                spectral_features=det["spectral_features"],
                raw_label="",
            )

        return AMRStreamResult(
            signal_detected=True,
            modulation="待IQ分析",
            confidence=0.0,
            bandwidth_hz=det["bandwidth_hz"],
            peak_count=det["peak_count"],
            center_offset_hz=det["center_offset_hz"],
            noise_floor_db=det["noise_floor_db"],
            signal_power_db=det["signal_power_db"],
            snr_db=det["snr_db"],
            spectral_features=det["spectral_features"],
            raw_label="",
        )

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    @staticmethod
    def _power_spectrum_db(iq: np.ndarray, sample_rate: float):
        """计算 fftshift 后的功率谱(dBFS)、线性功率与频率轴(Hz)。"""
        n = iq.size
        spec = np.fft.fft(iq)
        power = np.abs(spec) ** 2
        psd = np.fft.fftshift(power) / float(n)          # 每 bin 平均功率
        spec_db = 10.0 * np.log10(psd + 1e-12)
        freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / sample_rate))
        return spec_db, psd, freqs

    def _detect_and_features(self, spec_db: np.ndarray, psd: np.ndarray,
                             freqs: np.ndarray,
                             sample_rate: float) -> Dict[str, Any]:
        """统一的信号检测 + 频谱特征提取，供 analyze_iq / analyze_spectrum 复用。

        信号判据：在噪声基底之上，存在一段连续、宽度达到 min_bandwidth_hz 的
        抬升区。纯白噪声的频谱近似平坦，超过阈值的只是离散窄毛刺（单段宽度
        不足），因此不会被误判为信号 —— 宁可漏检也不瞎猜。
        """
        n = spec_db.size
        bin_hz = sample_rate / n if n > 0 else 1.0

        # 噪声基底：频谱最低 20% 分位数
        noise_floor_db = float(np.percentile(spec_db, 20.0))
        threshold = noise_floor_db + self.snr_threshold_db

        above = spec_db > threshold

        features_empty = {
            "centroid_hz": 0.0, "spread_hz": 0.0, "flatness": 0.0,
            "skewness": 0.0, "kurtosis": 0.0,
        }

        def _no_signal():
            return {
                "signal_detected": False,
                "noise_floor_db": noise_floor_db,
                "bandwidth_hz": 0.0, "peak_count": 0,
                "center_offset_hz": 0.0,
                "signal_power_db": _NO_SIGNAL_POWER_DB,
                "snr_db": 0.0,
                "upper_power": 0.0, "lower_power": 0.0,
                "spectral_features": features_empty,
            }

        if not np.any(above):
            return _no_signal()

        # 连续抬升段：真实信号是局部宽峰；噪声是离散窄毛刺。
        # 宽信号(AM/FM/FSK/PSK/SSB)靠段宽过滤；窄强单音(CW)靠峰值余量接收——
        # 噪声毛刺即使偶发过阈值，峰值余量也远达不到载波水平。
        run_edges = self._find_runs(above)
        min_bins = max(1, int(math.ceil(self.min_bandwidth_hz / bin_hz)))
        narrow_peak_threshold = threshold + self.narrow_peak_margin_db
        kept = []
        for s, e in run_edges:
            width_bins = e - s + 1
            run_peak = float(np.max(spec_db[s:e + 1]))
            if width_bins >= min_bins or run_peak >= narrow_peak_threshold:
                kept.append((s, e))
        peak_count = len(kept)
        if peak_count == 0:
            return _no_signal()

        mask = np.zeros(n, dtype=bool)
        for s, e in kept:
            mask[s:e + 1] = True

        peak_bin = int(np.argmax(spec_db))
        peak_db = float(spec_db[peak_bin])
        snr_db = peak_db - noise_floor_db

        # -3dB 带宽：先对频谱做包络平滑，避免离散谱线导致峰宽塌缩为单 bin。
        # 平滑窗口取约占总带宽 1%（不少于 5 bin），反映信号占据带宽的包络。
        smooth_win = max(5, int(n * 0.01))
        if smooth_win % 2 == 0:
            smooth_win += 1
        kernel = np.ones(smooth_win) / smooth_win
        spec_smooth = np.convolve(spec_db, kernel, mode="same")
        bw_peak = int(np.argmax(spec_smooth))
        bw_left, bw_right = self._minus3db_edges(spec_smooth, bw_peak, mask)
        bandwidth_hz = float((bw_right - bw_left) * bin_hz)

        # 质心 / 上下边带功率（相对基带中心 0Hz）
        peak_psd = psd[mask]
        peak_freqs = freqs[mask]
        total_p = float(peak_psd.sum())
        if total_p > 1e-12:
            center_offset_hz = float(np.sum(peak_freqs * peak_psd) / total_p)
            spread_hz = float(math.sqrt(
                max(0.0, np.sum(((peak_freqs - center_offset_hz) ** 2) * peak_psd) / total_p)))
        else:
            center_offset_hz = float(freqs[peak_bin])
            spread_hz = 0.0

        pos = freqs > 0
        neg = freqs < 0
        upper_power = float(np.sum(psd[pos & mask]))
        lower_power = float(np.sum(psd[neg & mask]))

        # 高阶频谱特征（在峰值区域内的线性功率谱上计算）
        features = self._spectral_stats(peak_freqs, peak_psd, bin_hz)

        return {
            "signal_detected": True,
            "noise_floor_db": noise_floor_db,
            "bandwidth_hz": bandwidth_hz,
            "peak_count": int(peak_count),
            "center_offset_hz": center_offset_hz,
            "signal_power_db": peak_db,
            "snr_db": float(snr_db),
            "upper_power": upper_power,
            "lower_power": lower_power,
            "spectral_features": features,
        }

    @staticmethod
    def _find_runs(above: np.ndarray):
        """返回 above 中所有 True 连续段的 (start, end) 索引列表（含端点）。"""
        idx = np.where(above)[0]
        if idx.size == 0:
            return []
        breaks = np.where(np.diff(idx) > 1)[0]
        starts = np.concatenate(([idx[0]], idx[breaks + 1]))
        ends = np.concatenate((idx[breaks], [idx[-1]]))
        return list(zip(starts.tolist(), ends.tolist()))

    @staticmethod
    def _minus3db_edges(spec_db: np.ndarray, peak_bin: int,
                         mask: np.ndarray):
        """从峰位向左右找到功率下降 3dB 的 bin 索引（沿主峰外扩，不被阈值掩膜门限卡死）。"""
        target = spec_db[peak_bin] - 3.0
        n = spec_db.size
        left = peak_bin
        while left > 0 and spec_db[left - 1] >= target:
            left -= 1
        right = peak_bin
        while right < n - 1 and spec_db[right + 1] >= target:
            right += 1
        return left, right

    @staticmethod
    def _spectral_stats(freqs: np.ndarray, power: np.ndarray,
                        bin_hz: float) -> Dict[str, float]:
        """在峰值区域内计算 centroid/spread/flatness/skewness/kurtosis。"""
        total = float(power.sum())
        if total <= 1e-12 or freqs.size == 0:
            return {"centroid_hz": 0.0, "spread_hz": 0.0, "flatness": 0.0,
                    "skewness": 0.0, "kurtosis": 0.0}
        centroid = float(np.sum(freqs * power) / total)
        spread = float(math.sqrt(max(0.0,
            np.sum(((freqs - centroid) ** 2) * power) / total)))
        if spread > 1e-9:
            skew = float(np.sum(
                (((freqs - centroid) / spread) ** 3) * power) / total)
            kurt = float(np.sum(
                (((freqs - centroid) / spread) ** 4) * power) / total - 3.0)
        else:
            skew, kurt = 0.0, 0.0
        # 频谱平坦度：几何均值 / 算术均值
        p_pos = power[power > 1e-12]
        if p_pos.size > 1:
            flatness = float(math.exp(np.mean(np.log(p_pos))) /
                             (np.mean(power) + 1e-12))
        else:
            flatness = 0.0
        flatness = max(0.0, min(1.0, flatness))
        return {
            "centroid_hz": centroid,
            "spread_hz": spread,
            "flatness": flatness,
            "skewness": skew,
            "kurtosis": kurt,
        }

    def _decide_modulation(self, raw_type: ModulationType,
                           upper_power: float, lower_power: float,
                           center_offset_hz: float,
                           confidence: float):
        """把模型原始标签映射为面板展示标签，返回 (modulation, confidence)。

        决策优先级：
        1. 强单边带不对称（信号显著偏离基带中心，且某一边带能量占绝对主导）：
           这是 SSB 的确定性谱证据，即使模型（无 SSB 模板）误判为噪声，也据此
           判定 USB/LSB —— 这不是猜测，而是谱测量。
        2. 模型判为 NOISE/UNKNOWN：不瞎猜，返回"未检测到信号"。
        3. 其余按模型标签直接映射。
        """
        # 边带功率比（相对基带中心 0Hz）
        if lower_power > 1e-12 and upper_power > 1e-12:
            upper_dom = upper_power > lower_power * _SIDEBAND_RATIO
            lower_dom = lower_power > upper_power * _SIDEBAND_RATIO
        elif upper_power > 1e-12:
            upper_dom, lower_dom = True, False
        elif lower_power > 1e-12:
            upper_dom, lower_dom = False, True
        else:
            upper_dom = lower_dom = False

        # 只有信号明显偏离基带中心时才承认边带归属；
        # 中心处的 CW/对称 AM-FM 即使两侧噪声有 1.5 倍起伏也不触发。
        off_center = abs(center_offset_hz) >= _OFF_CENTER_GATE_HZ
        if off_center and (upper_dom or lower_dom):
            return ("LSB" if lower_dom else "USB"), confidence

        if raw_type == ModulationType.SSB:
            return ("LSB" if lower_dom else "USB"), confidence

        # 模型即便过了阈值仍判为噪声/未知 → 不瞎猜
        if raw_type in (ModulationType.NOISE, ModulationType.UNKNOWN):
            return "未检测到信号", 0.0

        direct = {
            ModulationType.FM: "FM",
            ModulationType.AM: "AM",
            ModulationType.CW: "CW",
            ModulationType.FSK: "数字",
            ModulationType.PSK: "数字",
            ModulationType.QAM: "数字",
            ModulationType.OFDM: "数字",
        }
        return direct.get(raw_type, "未检测到信号"), confidence

    @staticmethod
    def _no_signal(noise_floor_db: float) -> AMRStreamResult:
        return AMRStreamResult(
            signal_detected=False,
            modulation="未检测到信号",
            confidence=0.0,
            bandwidth_hz=0.0,
            peak_count=0,
            center_offset_hz=0.0,
            noise_floor_db=noise_floor_db,
            signal_power_db=_NO_SIGNAL_POWER_DB,
            snr_db=0.0,
            spectral_features={},
            raw_label="",
        )
