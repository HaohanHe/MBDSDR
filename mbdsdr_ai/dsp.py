"""
MBDSDR AI 内核 - DSP 数字信号处理模块
=====================================
DSP Module：IQ 前端校正 + 真实解调算法 + 信号处理工具。

对照白皮书第五章和专题 05 实现：
1. IQ 前端校正（DC blocker + I/Q 平衡协方差白化 + 整数抽取）
2. 真实解调算法（FM/AM/SSB/LSB/USB/CW）
3. 信号处理工具（AGC、滤波、重采样）

这是 SDR 软件最核心的基本功，之前完全是空壳。
"""

import numpy as np

from typing import Tuple, Optional, Dict, Any


# ═══════════════════════════════════════════════════════
# 1. IQ 前端校正（对照专题 05）
# ═══════════════════════════════════════════════════════

class DCBlocker:
    """
    直流阻断器（一阶 IIR）。

    y[n] = x[n] - x[n-1] + R * y[n-1]
    R ∈ [0,1)，典型 0.995~0.999。

    相比"减整段均值"，能跟踪随温度/时间缓慢漂移的直流，
    且流式、O(1) 状态、无需整段已知。
    """

    def __init__(self, r: float = 0.998):
        self.r = r
        self._x_prev = 0.0
        self._y_prev = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段 IQ 样本（分别对 I 和 Q 做 DC 阻断）。"""
        if x.dtype == np.complex64 or x.dtype == np.complex128:
            i_part = self._process_real(x.real)
            q_part = self._process_real(x.imag)
            return i_part + 1j * q_part
        else:
            return self._process_real(x)

    def _process_real(self, x: np.ndarray) -> np.ndarray:
        y = np.zeros_like(x, dtype=np.float64)
        x_prev = self._x_prev
        y_prev = self._y_prev
        r = self.r
        for n in range(len(x)):
            y[n] = x[n] - x_prev + r * y_prev
            x_prev = x[n]
            y_prev = y[n]
        self._x_prev = x_prev
        self._y_prev = y_prev
        return y.astype(x.dtype)

    def reset(self):
        self._x_prev = 0.0
        self._y_prev = 0.0


class IQCalibrator:
    """
    I/Q 不平衡校正：协方差白化。

    常见做法是分别反解增益差 g 与相位误差 φ 再补偿，
    但 g 与 φ 在二阶矩里相互耦合，逐参数反解不彻底。

    本实现改用协方差白化：
    1. 去均值后估计 2×2 协方差 C = [[varI, covIQ],[covIQ, varQ]]
    2. 特征分解 C = VΛVᵀ，构造白化矩阵 W = V·diag(1/√Λ)·Vᵀ
    3. 对 [I,Q]ᵀ 左乘 W，使两路零均值、等功率、互不相关

    白化对任意"增益差 + 正交相位误差"组合都成立，不依赖参数化假设。
    """

    def __init__(self):
        self._whitening_matrix: Optional[np.ndarray] = None
        self._mean: Optional[np.ndarray] = None
        self._fitted = False

    def fit(self, x: np.ndarray, n_samples: int = 10000) -> Dict[str, Any]:
        """
        从一段 IQ 样本估计白化矩阵。

        返回诊断信息：增益误差、相位误差、协方差矩阵。
        """
        # 取前 n_samples 个样本估计
        n = min(n_samples, len(x))
        iq = np.column_stack([x[:n].real, x[:n].imag])

        # 去均值
        mean = np.mean(iq, axis=0)
        iq_centered = iq - mean

        # 协方差矩阵
        cov = np.cov(iq_centered.T)

        # 特征分解
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        # 避免除零
        eigenvalues = np.maximum(eigenvalues, 1e-12)

        # 白化矩阵 W = V · diag(1/√Λ) · Vᵀ
        whitening = eigenvectors @ np.diag(1.0 / np.sqrt(eigenvalues)) @ eigenvectors.T

        self._whitening_matrix = whitening
        self._mean = mean
        self._fitted = True

        # 诊断：估计增益误差和相位误差
        var_i = cov[0, 0]
        var_q = cov[1, 1]
        cov_iq = cov[0, 1]

        gain_err = np.sqrt(var_q / var_i) if var_i > 0 else 1.0
        # 相位误差估计（从协方差的非对角元）
        if var_i > 0 and var_q > 0:
            phase_err_rad = np.arcsin(np.clip(cov_iq / np.sqrt(var_i * var_q), -1, 1))
            phase_err_deg = np.degrees(phase_err_rad)
        else:
            phase_err_deg = 0.0

        return {
            "fitted": True,
            "samples_used": n,
            "covariance_matrix": cov.tolist(),
            "iq_gain_error": float(gain_err),
            "iq_phase_error_deg": float(phase_err_deg),
            "dc_offset_i": float(mean[0]),
            "dc_offset_q": float(mean[1]),
        }

    def apply(self, x: np.ndarray) -> np.ndarray:
        """应用白化矩阵到 IQ 样本。"""
        if not self._fitted or self._whitening_matrix is None:
            return x

        iq = np.column_stack([x.real, x.imag])
        # 去均值
        iq_centered = iq - self._mean
        # 白化
        iq_white = iq_centered @ self._whitening_matrix.T
        return iq_white[:, 0] + 1j * iq_white[:, 1]

    def is_fitted(self) -> bool:
        return self._fitted


def decimate(x: np.ndarray, factor: int) -> np.ndarray:
    """
    整数抽取：先 Hanning 窗 sinc 低通抗混叠，再每 factor 取 1。

    这是 DDC（数字下变频）的末级。
    """
    if factor <= 1:
        return x

    # 设计低通滤波器（截止频率 0.5/factor）
    num_taps = min(64, len(x) // 4)
    if num_taps < 4:
        # 样本太少，直接抽取
        return x[::factor]

    t = np.arange(num_taps) - (num_taps - 1) / 2
    # sinc 低通
    h = np.sinc(2 * t / factor) / factor
    # Hanning 窗
    h *= np.hanning(num_taps)
    # 归一化
    h /= np.sum(h)

    # 滤波
    if x.dtype in (np.complex64, np.complex128):
        i_filtered = np.convolve(x.real, h, mode='same')
        q_filtered = np.convolve(x.imag, h, mode='same')
        filtered = i_filtered + 1j * q_filtered
    else:
        filtered = np.convolve(x, h, mode='same')

    # 抽取
    return filtered[::factor]


def front_end(
    x: np.ndarray,
    dc_r: float = 0.998,
    correct_iq: bool = True,
    decimation: int = 1,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    一站式 IQ 前端校正：DC 阻断 → I/Q 平衡 → 抽取。

    返回 (校正后 IQ, 诊断信息)。
    """
    diagnostics = {}

    # 1. DC 阻断
    dc_blocker = DCBlocker(r=dc_r)
    x = dc_blocker.process(x)
    diagnostics["dc_blocked"] = True

    # 2. I/Q 平衡
    if correct_iq:
        calibrator = IQCalibrator()
        cal_diag = calibrator.fit(x)
        x = calibrator.apply(x)
        diagnostics["iq_correction"] = cal_diag
    else:
        diagnostics["iq_correction"] = {"skipped": True}

    # 3. 抽取
    if decimation > 1:
        x = decimate(x, decimation)
        diagnostics["decimation"] = decimation
        diagnostics["effective_sample_rate_ratio"] = 1.0 / decimation

    return x, diagnostics


# ═══════════════════════════════════════════════════════
# 2. 真实解调算法
# ═══════════════════════════════════════════════════════

def fm_demod(x: np.ndarray, deviation: float = 75000.0,
             sample_rate: float = 2400000.0) -> np.ndarray:
    """
    FM 调频解调（正交鉴频）。

    y[n] = angle(x[n] * conj(x[n-1])) / (2π * deviation / sample_rate)

    deviation: 最大频偏，广播 FM 75kHz，窄带 FM 5kHz
    """
    if len(x) < 2:
        return np.zeros(len(x), dtype=np.float32)

    # 正交鉴频
    phase_diff = np.angle(x[1:] * np.conj(x[:-1]))
    # 归一化到音频
    gain = sample_rate / (2 * np.pi * deviation)
    audio = phase_diff * gain

    # 音频去直流
    audio = audio - np.mean(audio)

    return audio.astype(np.float32)


def am_demod(x: np.ndarray) -> np.ndarray:
    """
    AM 调幅解调（包络检波）。

    y[n] = |x[n]| - mean(|x|)
    """
    envelope = np.abs(x)
    # 去直流
    audio = envelope - np.mean(envelope)
    return audio.astype(np.float32)


def ssb_demod(x: np.ndarray, mode: str = "USB",
              carrier_offset: float = 1500.0,
              sample_rate: float = 2400000.0) -> np.ndarray:
    """
    SSB 单边带解调（Weaver 法简化版）。

    USB: 取上边带
    LSB: 取下边带

    carrier_offset: 载波偏移频率（BFO），典型 1500Hz
    """
    n = len(x)
    t = np.arange(n) / sample_rate

    # 本振
    lo = np.exp(1j * 2 * np.pi * carrier_offset * t)

    if mode.upper() == "USB":
        # 上边带：下变频后取实部
        mixed = x * np.conj(lo)
        audio = mixed.real
    else:
        # 下边带：下变频后取虚部（符号反转）
        mixed = x * lo
        audio = -mixed.imag

    # 低通滤波（简单移动平均）
    kernel_size = max(1, int(sample_rate / 4000))  # ~4kHz 音频带宽
    if kernel_size > 1 and len(audio) > kernel_size:
        kernel = np.ones(kernel_size) / kernel_size
        audio = np.convolve(audio, kernel, mode='same')

    # 去直流
    audio = audio - np.mean(audio)

    return audio.astype(np.float32)


def cw_demod(x: np.ndarray, tone_freq: float = 700.0,
             sample_rate: float = 2400000.0) -> np.ndarray:
    """
    CW 等幅报解调（拍频振荡 BFO）。

    将 CW 信号搬移到音频频段，输出音频。
    """
    n = len(x)
    t = np.arange(n) / sample_rate
    bfo = np.exp(1j * 2 * np.pi * tone_freq * t)
    mixed = x * bfo
    audio = mixed.real
    # 去直流
    audio = audio - np.mean(audio)
    return audio.astype(np.float32)


def demodulate(x: np.ndarray, mode: str = "FM",
               sample_rate: float = 2400000.0,
               deviation: float = 75000.0) -> np.ndarray:
    """
    统一解调入口。

    mode: FM / NFM / WFM / AM / LSB / USB / CW
    """
    mode = mode.upper()

    if mode in ("FM", "WFM"):
        return fm_demod(x, deviation=deviation, sample_rate=sample_rate)
    elif mode == "NFM":
        return fm_demod(x, deviation=5000.0, sample_rate=sample_rate)
    elif mode == "AM":
        return am_demod(x)
    elif mode == "USB":
        return ssb_demod(x, mode="USB", sample_rate=sample_rate)
    elif mode == "LSB":
        return ssb_demod(x, mode="LSB", sample_rate=sample_rate)
    elif mode == "CW":
        return cw_demod(x, sample_rate=sample_rate)
    else:
        # 默认 FM
        return fm_demod(x, deviation=deviation, sample_rate=sample_rate)


# ═══════════════════════════════════════════════════════
# 3. AGC 自动增益控制
# ═══════════════════════════════════════════════════════

class AGC:
    """
    自动增益控制（简化版）。

    维持输出信号幅度在目标水平。
    """

    def __init__(self, target_level: float = 0.5, attack: float = 0.01,
                 release: float = 0.001, max_gain: float = 60.0):
        self.target_level = target_level
        self.attack = attack
        self.release = release
        self.max_gain = max_gain
        self._current_gain = 1.0

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段信号。"""
        # 计算当前信号电平
        level = np.sqrt(np.mean(np.abs(x) ** 2))
        if level < 1e-10:
            return x

        # 计算需要的增益
        desired_gain = self.target_level / level
        desired_gain = min(desired_gain, self.max_gain)

        # 平滑增益变化
        if desired_gain > self._current_gain:
            # 增益上升（attack 快）
            alpha = self.attack
        else:
            # 增益下降（release 慢）
            alpha = self.release

        self._current_gain = (1 - alpha) * self._current_gain + alpha * desired_gain

        return x * self._current_gain

    def reset(self):
        self._current_gain = 1.0


# ═══════════════════════════════════════════════════════
# 4. 基带录制（真正写文件）
# ═══════════════════════════════════════════════════════

def write_cf32(x: np.ndarray, path: str):
    """
    写 cf32 格式（交错 float32 小端 [I0,Q0,I1,Q1,...]）。

    GNU Radio / SDR++ 默认格式。
    """
    # 转换为交错 float32
    interleaved = np.column_stack([x.real, x.imag]).flatten().astype(np.float32)
    interleaved.tofile(path)


def write_cs16(x: np.ndarray, path: str, gain: float = 1.0):
    """
    写 cs16 格式（交错 int16 小端）。

    省一半空间，16bit 定点。
    """
    # 归一化到 [-1,1] 然后量化到 int16
    max_val = np.max(np.abs(x))
    if max_val > 0:
        normalized = x / max_val * gain
    else:
        normalized = x
    # clip 到 [-1,1]
    normalized = np.clip(normalized, -1.0, 1.0)
    # 转换为交错 int16
    i16 = (normalized.real * 32767).astype(np.int16)
    q16 = (normalized.imag * 32767).astype(np.int16)
    interleaved = np.column_stack([i16, q16]).flatten()
    interleaved.tofile(path)


def write_wav(x: np.ndarray, path: str, sample_rate: int = 48000, gain: float = 1.0):
    """
    写 WAV 格式（双声道 16bit PCM，I=左、Q=右）。

    SDR# 风格，任意音频工具可直接打开。
    """
    import wave
    # 归一化
    max_val = np.max(np.abs(x))
    if max_val > 0:
        normalized = x / max_val * gain
    else:
        normalized = x
    normalized = np.clip(normalized, -1.0, 1.0)
    # 转换为 int16 交错
    i16 = (normalized.real * 32767).astype(np.int16)
    q16 = (normalized.imag * 32767).astype(np.int16)
    interleaved = np.column_stack([i16, q16]).flatten()

    with wave.open(path, 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(interleaved.tobytes())


def write_csv(x: np.ndarray, path: str, decimation: int = 1):
    """
    写 CSV 格式（文本行 I,Q，首行表头）。

    教学、表格审计用。建议配合大抽取。
    """
    if decimation > 1:
        x = x[::decimation]
    with open(path, 'w') as f:
        f.write("I,Q\n")
        for sample in x:
            f.write(f"{sample.real:.8f},{sample.imag:.8f}\n")


def write_sidecar_json(path: str, metadata: Dict[str, Any]):
    """
    写 sidecar 元数据 JSON（可回放的关键）。

    录制停止时写 <文件>.json，记录中心频率/采样率/格式/样本数/时间。
    """
    sidecar_path = path + ".json"
    with open(sidecar_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    return sidecar_path


# 延迟导入 json（在文件顶部导入会导致循环）
import json


# ═══════════════════════════════════════════════════════
# 5. 信号分析工具
# ═══════════════════════════════════════════════════════

def compute_snr(x: np.ndarray, signal_band: Tuple[float, float] = None,
                sample_rate: float = 2400000.0) -> Dict[str, Any]:
    """
    计算 SNR（信噪比）。

    如果指定 signal_band（Hz），在该频带内计算信号功率，
    其余频带计算噪声功率。
    """
    spectrum = np.fft.fftshift(np.fft.fft(x))
    powers = np.abs(spectrum) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(x), 1.0 / sample_rate))

    if signal_band:
        # 指定信号频带
        signal_mask = (freqs >= signal_band[0]) & (freqs <= signal_band[1])
        noise_mask = ~signal_mask
        signal_power = np.mean(powers[signal_mask])
        noise_power = np.mean(powers[noise_mask])
    else:
        # 用峰值附近作为信号，其余作为噪声
        peak_idx = np.argmax(powers)
        bandwidth = max(1, len(x) // 20)  # 5% 带宽作为信号
        start = max(0, peak_idx - bandwidth // 2)
        end = min(len(x), peak_idx + bandwidth // 2)
        signal_power = np.mean(powers[start:end])
        noise_power = np.mean(np.concatenate([powers[:start], powers[end:]]))

    snr_db = 10 * np.log10(signal_power / max(noise_power, 1e-12))

    return {
        "snr_db": float(snr_db),
        "signal_power": float(signal_power),
        "noise_power": float(noise_power),
        "signal_band_hz": list(signal_band) if signal_band else None,
    }


def estimate_bandwidth(x: np.ndarray, sample_rate: float = 2400000.0,
                       threshold_db: float = -20.0) -> Dict[str, Any]:
    """
    估计信号占用带宽。

    找到功率超过峰值 -threshold_db 的频率范围。
    """
    spectrum = np.fft.fftshift(np.fft.fft(x))
    powers_db = 10 * np.log10(np.abs(spectrum) ** 2 + 1e-12)
    freqs = np.fft.fftshift(np.fft.fftfreq(len(x), 1.0 / sample_rate))

    peak_power = np.max(powers_db)
    threshold = peak_power + threshold_db  # threshold_db 是负数
    above_threshold = powers_db >= threshold

    if np.any(above_threshold):
        indices = np.where(above_threshold)[0]
        start_freq = freqs[indices[0]]
        end_freq = freqs[indices[-1]]
        bandwidth = end_freq - start_freq
        center_freq = (start_freq + end_freq) / 2
    else:
        start_freq = end_freq = center_freq = 0.0
        bandwidth = 0.0

    return {
        "bandwidth_hz": float(bandwidth),
        "center_freq_hz": float(center_freq),
        "start_freq_hz": float(start_freq),
        "end_freq_hz": float(end_freq),
        "peak_power_db": float(peak_power),
        "threshold_db": float(threshold),
    }
