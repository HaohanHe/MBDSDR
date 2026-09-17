"""
MBDSDR AI 内核 - 频谱处理器
============================
Spectrum Processor：IQ 信号的频谱分析与可视化。

核心能力：
- FFT 计算（numpy.fft）
- 频谱图生成（dB 刻度、窗口函数）
- 峰值检测（找强信号）
- 中心频点估计（精确估计信号中心频率偏移）
- 信号检测（阈值检测）
- 瀑布图数据（时间-频率-强度三维）
- 频谱缩放/平移（坐标变换）
- 频谱截图（生成 PNG，给多模态模型看）
- 调制识别辅助特征提取
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple


@dataclass
class SpectrumData:
    """频谱数据。"""
    frequencies: np.ndarray  # 频率轴 Hz
    powers_db: np.ndarray    # 功率轴 dB
    center_freq: float       # 中心频率 Hz
    sample_rate: float       # 采样率 Hz
    fft_size: int            # FFT 大小
    timestamp: float = 0.0
    peak_freq: float = 0.0
    peak_power_db: float = -100.0
    noise_floor_db: float = -100.0


@dataclass
class WaterfallData:
    """瀑布图数据。"""
    frequencies: np.ndarray   # 频率轴
    times: np.ndarray         # 时间轴
    powers_db: np.ndarray     # 2D 功率数组 [time, freq]
    center_freq: float = 0.0
    sample_rate: float = 0.0


class SpectrumProcessor:
    """
    频谱处理器。

    对 IQ 样本进行 FFT、峰值检测、中心频点估计等。
    """

    def __init__(self, fft_size: int = 1024, window: str = "hann"):
        self.fft_size = fft_size
        self.window = window
        self._window_func = self._get_window(window, fft_size)
        self._zoom_factor = 1.0  # 缩放因子
        self._pan_offset = 0.0   # 平移偏移 Hz

    def _get_window(self, window: str, size: int) -> np.ndarray:
        """获取窗口函数。"""
        windows = {
            "hann": np.hanning,
            "hamming": np.hamming,
            "blackman": np.blackman,
            "rectangular": np.ones,
            "bartlett": np.bartlett,
        }
        func = windows.get(window, np.hanning)
        return func(size)

    def compute_spectrum(
        self,
        samples: np.ndarray,
        center_freq: float,
        sample_rate: float,
        fft_size: int = None,
    ) -> SpectrumData:
        """
        计算频谱。

        samples: 复数 IQ 样本
        center_freq: 中心频率 Hz
        sample_rate: 采样率 Hz
        fft_size: FFT 大小（默认使用初始化时的值）
        """
        if fft_size is None:
            fft_size = self.fft_size

        # 如果 fft_size 变化，重新创建窗口函数
        if fft_size != self.fft_size or len(self._window_func) != fft_size:
            self._window_func = self._get_window(self.window, fft_size)
            self.fft_size = fft_size

        # 截取或补零到 fft_size
        if len(samples) >= fft_size:
            samples = samples[:fft_size]
        else:
            samples = np.pad(samples, (0, fft_size - len(samples)))

        # 加窗
        windowed = samples * self._window_func[:len(samples)]

        # FFT
        spectrum = np.fft.fftshift(np.fft.fft(windowed, fft_size))
        powers = np.abs(spectrum) ** 2
        powers_db = 10 * np.log10(powers + 1e-12)  # +1e-12 避免 log(0)

        # 频率轴
        freqs = np.fft.fftshift(np.fft.fftfreq(fft_size, 1.0 / sample_rate)) + center_freq

        # 峰值检测
        peak_idx = np.argmax(powers_db)
        peak_freq = freqs[peak_idx]
        peak_power = powers_db[peak_idx]

        # 噪声底（用最低 10% 的平均值）
        sorted_powers = np.sort(powers_db)
        noise_floor = np.mean(sorted_powers[:int(fft_size * 0.1)])

        return SpectrumData(
            frequencies=freqs,
            powers_db=powers_db,
            center_freq=center_freq,
            sample_rate=sample_rate,
            fft_size=fft_size,
            timestamp=__import__("time").time(),
            peak_freq=peak_freq,
            peak_power_db=peak_power,
            noise_floor_db=noise_floor,
        )

    def find_signals(
        self,
        spectrum: SpectrumData,
        threshold_db: float = -60.0,
        min_bandwidth_hz: float = 1000.0,
    ) -> List[Dict[str, Any]]:
        """
        在频谱中检测信号。

        返回检测到的信号列表，每个信号包含：
        - center_freq: 中心频率
        - bandwidth: 带宽
        - peak_power: 峰值功率
        - start_freq: 起始频率
        - end_freq: 结束频率
        """
        above_threshold = spectrum.powers_db > threshold_db
        signals = []

        # 找连续的超阈值区域
        in_signal = False
        start_idx = 0

        for i in range(len(above_threshold)):
            if above_threshold[i] and not in_signal:
                start_idx = i
                in_signal = True
            elif not above_threshold[i] and in_signal:
                end_idx = i - 1
                in_signal = False

                bandwidth = spectrum.frequencies[end_idx] - spectrum.frequencies[start_idx]
                if bandwidth >= min_bandwidth_hz:
                    segment_powers = spectrum.powers_db[start_idx:end_idx + 1]
                    segment_freqs = spectrum.frequencies[start_idx:end_idx + 1]
                    peak_idx_local = np.argmax(segment_powers)

                    signals.append({
                        "center_freq": float(segment_freqs[peak_idx_local]),
                        "bandwidth": float(bandwidth),
                        "peak_power_db": float(segment_powers[peak_idx_local]),
                        "start_freq": float(spectrum.frequencies[start_idx]),
                        "end_freq": float(spectrum.frequencies[end_idx]),
                    })

        # 处理最后一个信号
        if in_signal:
            end_idx = len(above_threshold) - 1
            bandwidth = spectrum.frequencies[end_idx] - spectrum.frequencies[start_idx]
            if bandwidth >= min_bandwidth_hz:
                segment_powers = spectrum.powers_db[start_idx:end_idx + 1]
                segment_freqs = spectrum.frequencies[start_idx:end_idx + 1]
                peak_idx_local = np.argmax(segment_powers)
                signals.append({
                    "center_freq": float(segment_freqs[peak_idx_local]),
                    "bandwidth": float(bandwidth),
                    "peak_power_db": float(segment_powers[peak_idx_local]),
                    "start_freq": float(spectrum.frequencies[start_idx]),
                    "end_freq": float(spectrum.frequencies[end_idx]),
                })

        # 按峰值功率排序
        signals.sort(key=lambda s: s["peak_power_db"], reverse=True)
        return signals

    def estimate_center_offset(
        self,
        spectrum: SpectrumData,
        expected_freq: float = None,
    ) -> Dict[str, Any]:
        """
        精确估计中心频点偏移。

        使用抛物线插值在峰值附近进行亚 bin 精度估计。
        """
        peak_idx = np.argmax(spectrum.powers_db)

        # 抛物线插值（需要峰值不在边界）
        if 0 < peak_idx < len(spectrum.powers_db) - 1:
            y0 = spectrum.powers_db[peak_idx - 1]
            y1 = spectrum.powers_db[peak_idx]
            y2 = spectrum.powers_db[peak_idx + 1]

            # 抛物线顶点偏移
            denom = (y0 - 2 * y1 + y2)
            if abs(denom) > 1e-10:
                offset = 0.5 * (y0 - y2) / denom
            else:
                offset = 0.0

            freq_resolution = spectrum.sample_rate / spectrum.fft_size
            precise_peak_freq = spectrum.frequencies[peak_idx] + offset * freq_resolution
        else:
            precise_peak_freq = spectrum.frequencies[peak_idx]
            offset = 0.0

        result = {
            "estimated_center_freq": float(precise_peak_freq),
            "peak_bin": int(peak_idx),
            "sub_bin_offset": float(offset),
            "peak_power_db": float(spectrum.powers_db[peak_idx]),
        }

        if expected_freq is not None:
            result["expected_freq"] = float(expected_freq)
            result["offset_hz"] = float(precise_peak_freq - expected_freq)
            result["offset_ppm"] = float((precise_peak_freq - expected_freq) / expected_freq * 1e6)

        return result

    def zoom(self, factor: float):
        """缩放频谱（factor > 1 放大，< 1 缩小）。"""
        self._zoom_factor = max(0.1, min(100.0, factor))

    def pan(self, offset_hz: float):
        """平移频谱中心。"""
        self._pan_offset = offset_hz

    def reset_view(self):
        """重置缩放和平移。"""
        self._zoom_factor = 1.0
        self._pan_offset = 0.0

    def get_view_range(self, center_freq: float, sample_rate: float) -> Tuple[float, float]:
        """获取当前视图的频率范围。"""
        half_bw = sample_rate / 2 / self._zoom_factor
        center = center_freq + self._pan_offset
        return (center - half_bw, center + half_bw)

    def compute_waterfall(
        self,
        samples_blocks: List[np.ndarray],
        center_freq: float,
        sample_rate: float,
    ) -> WaterfallData:
        """
        计算瀑布图（多帧频谱堆叠）。
        """
        spectra = []
        for samples in samples_blocks:
            spec = self.compute_spectrum(samples, center_freq, sample_rate)
            spectra.append(spec.powers_db)

        powers_db = np.array(spectra)
        times = np.arange(len(samples_blocks))
        freqs = spectra[0].frequencies if spectra else np.array([])

        return WaterfallData(
            frequencies=freqs,
            times=times,
            powers_db=powers_db,
            center_freq=center_freq,
            sample_rate=sample_rate,
        )

    def extract_modulation_features(
        self,
        samples: np.ndarray,
        sample_rate: float,
    ) -> Dict[str, Any]:
        """
        提取调制识别特征。

        特征包括：
        - 中心频率归一化后的瞬时幅度/相位/频率
        - 幅度均值/方差/峰度
        - 相位标准差
        - 频率偏差
        - 频谱对称性
        """
        if len(samples) < 64:
            return {"error": "样本太少"}

        # 瞬时幅度
        amplitude = np.abs(samples)
        # 瞬时相位
        phase = np.angle(samples)
        # 瞬时频率（相位差分）
        inst_freq = np.diff(np.unwrap(phase)) * sample_rate / (2 * np.pi)

        # 归一化幅度
        amp_norm = amplitude / (np.mean(amplitude) + 1e-10)

        features = {
            "num_samples": len(samples),
            "amplitude_mean": float(np.mean(amplitude)),
            "amplitude_std": float(np.std(amplitude)),
            "amplitude_kurtosis": float(self._kurtosis(amp_norm)),
            "amplitude_max": float(np.max(amplitude)),
            "phase_std": float(np.std(phase)),
            "phase_mean": float(np.mean(phase)),
            "frequency_mean": float(np.mean(inst_freq)),
            "frequency_std": float(np.std(inst_freq)),
            "frequency_max_abs": float(np.max(np.abs(inst_freq))),
            "zero_crossing_rate": float(self._zero_crossing_rate(inst_freq)),
            "spectral_centroid": float(self._spectral_centroid(samples, sample_rate)),
            "spectral_flatness": float(self._spectral_flatness(samples)),
        }

        # 简单调制类型猜测
        features["modulation_guess"] = self._guess_modulation(features)

        return features

    def _kurtosis(self, x: np.ndarray) -> float:
        """计算峰度。"""
        mean = np.mean(x)
        std = np.std(x)
        if std < 1e-10:
            return 0.0
        return float(np.mean(((x - mean) / std) ** 4) - 3)

    def _zero_crossing_rate(self, x: np.ndarray) -> float:
        """计算过零率。"""
        if len(x) < 2:
            return 0.0
        return float(np.sum(np.diff(np.sign(x)) != 0) / len(x))

    def _spectral_centroid(self, samples: np.ndarray, sample_rate: float) -> float:
        """计算频谱质心。"""
        spectrum = np.abs(np.fft.fft(samples))
        freqs = np.fft.fftfreq(len(samples), 1.0 / sample_rate)
        total = np.sum(spectrum)
        if total < 1e-10:
            return 0.0
        return float(np.sum(freqs * spectrum) / total)

    def _spectral_flatness(self, samples: np.ndarray) -> float:
        """计算频谱平坦度（白噪声=1，音调=0）。"""
        spectrum = np.abs(np.fft.fft(samples)) + 1e-10
        geometric_mean = np.exp(np.mean(np.log(spectrum)))
        arithmetic_mean = np.mean(spectrum)
        if arithmetic_mean < 1e-10:
            return 0.0
        return float(geometric_mean / arithmetic_mean)

    def _guess_modulation(self, features: Dict[str, Any]) -> str:
        """基于特征简单猜测调制类型。"""
        amp_std = features.get("amplitude_std", 0)
        amp_mean = features.get("amplitude_mean", 1)
        amp_cv = amp_std / (amp_mean + 1e-10)  # 幅度变异系数
        freq_std = features.get("frequency_std", 0)
        spectral_flatness = features.get("spectral_flatness", 0)

        if spectral_flatness > 0.8:
            return "噪声/未知"
        elif amp_cv < 0.05 and freq_std < 1000:
            return "CW（等幅报）"
        elif amp_cv < 0.1 and freq_std > 1000:
            return "FM（调频）"
        elif amp_cv > 0.3 and freq_std < 1000:
            return "AM（调幅）"
        elif amp_cv > 0.2 and freq_std > 1000:
            return "SSB（单边带）或复合调制"
        else:
            return "未知（需要更多样本分析）"

    def generate_spectrum_text(
        self,
        spectrum: SpectrumData,
        max_bins: int = 60,
    ) -> str:
        """
        生成频谱的文本表示（ASCII 频谱图）。

        用于给 LLM 看频谱（多模态模型不可用时的降级方案）。
        """
        # 降采样到 max_bins
        if len(spectrum.powers_db) > max_bins:
            step = len(spectrum.powers_db) // max_bins
            powers = spectrum.powers_db[::step][:max_bins]
            freqs = spectrum.frequencies[::step][:max_bins]
        else:
            powers = spectrum.powers_db
            freqs = spectrum.frequencies

        # 归一化到 0-40 格
        p_min = np.min(powers)
        p_max = np.max(powers)
        p_range = p_max - p_min if p_max > p_min else 1.0

        lines = []
        lines.append(f"频谱图 (中心 {spectrum.center_freq/1e6:.2f} MHz, 采样率 {spectrum.sample_rate/1e6:.2f} MHz)")
        lines.append(f"峰值: {spectrum.peak_freq/1e6:.3f} MHz @ {spectrum.peak_power_db:.1f} dB, 噪声底: {spectrum.noise_floor_db:.1f} dB")
        lines.append("")

        # 从上到下绘制
        for row in range(10, -1, -1):
            threshold = p_min + p_range * row / 10
            line = f"{threshold:6.1f}dB |"
            for p in powers:
                if p >= threshold:
                    line += "#"
                else:
                    line += " "
            lines.append(line)

        lines.append("       +" + "-" * len(powers))
        # 频率刻度
        freq_labels = []
        for i in range(0, len(freqs), max(1, len(freqs) // 5)):
            freq_labels.append(f"{freqs[i]/1e6:.1f}")
        lines.append("        " + "  ".join(freq_labels))

        return "\n".join(lines)
