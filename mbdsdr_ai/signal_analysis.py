"""
MBDSDR AI - 高级信号分析工具
===============================

扩展SDR信号分析能力：
- 信号检测与识别
- 调制方式自动识别（AMR）
- 解调质量评估
- 频谱特征提取
- 干扰源定位
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from scipy import signal as scipy_signal


# ========================================================================
# 信号检测
# ========================================================================

def detect_signals(spectrum: np.ndarray, freqs: np.ndarray,
                   threshold_db: float = 10.0, min_bw: float = 1e3) -> List[Dict]:
    """
    频谱信号检测。

    参数:
        spectrum: 频谱幅度（dB）
        freqs: 频率数组（Hz）
        threshold_db: 检测门限（相对于噪声底）
        min_bw: 最小带宽（Hz）

    返回:
        检测到的信号列表
    """
    # 估计噪声底（中值）
    noise_floor = np.median(spectrum)

    # 超过门限的点
    above_threshold = spectrum > (noise_floor + threshold_db)

    # 找连续段
    signals = []
    in_signal = False
    start_idx = 0

    for i, above in enumerate(above_threshold):
        if above and not in_signal:
            # 信号开始
            start_idx = i
            in_signal = True
        elif not above and in_signal:
            # 信号结束
            bw = freqs[i - 1] - freqs[start_idx]
            if bw >= min_bw:
                peak_idx = start_idx + np.argmax(spectrum[start_idx:i])
                peak_freq = freqs[peak_idx]
                peak_power = spectrum[peak_idx]
                signals.append({
                    "center_freq_hz": float(peak_freq),
                    "bandwidth_hz": float(bw),
                    "peak_power_db": float(peak_power),
                    "start_hz": float(freqs[start_idx]),
                    "stop_hz": float(freqs[i - 1]),
                })
            in_signal = False

    # 处理最后一段
    if in_signal:
        bw = freqs[-1] - freqs[start_idx]
        if bw >= min_bw:
            peak_idx = start_idx + np.argmax(spectrum[start_idx:])
            peak_freq = freqs[peak_idx]
            peak_power = spectrum[peak_idx]
            signals.append({
                "center_freq_hz": float(peak_freq),
                "bandwidth_hz": float(bw),
                "peak_power_db": float(peak_power),
                "start_hz": float(freqs[start_idx]),
                "stop_hz": float(freqs[-1]),
            })

    return signals


# ========================================================================
# 调制方式识别（简化版）
# ========================================================================

def identify_modulation(iq: np.ndarray, sample_rate: float) -> Dict:
    """
    简化调制方式识别。

    基于信号统计特征判断调制类型。
    """
    # 基本特征
    amplitude = np.abs(iq)
    phase = np.angle(iq)
    freq = np.diff(phase)

    # 特征计算
    mean_amp = np.mean(amplitude)
    std_amp = np.std(amplitude)
    amp_cv = std_amp / (mean_amp + 1e-10)  # 幅度变异系数

    mean_freq = np.mean(freq)
    std_freq = np.std(freq)

    # 判断规则
    modulation = "unknown"
    confidence = 0.0

    # AM：幅度变化大，频率变化小
    if amp_cv > 0.3 and std_freq < 0.1:
        modulation = "AM"
        confidence = min(0.9, amp_cv)

    # FM：幅度恒定，频率变化大
    elif amp_cv < 0.1 and std_freq > 0.2:
        modulation = "FM"
        confidence = min(0.9, std_freq)

    # SSB：单边带，幅度变化中等
    elif 0.1 < amp_cv < 0.3 and std_freq > 0.1:
        modulation = "SSB"
        confidence = 0.7

    # QPSK：恒定幅度，相位离散
    elif amp_cv < 0.05 and std_freq < 0.05:
        # 检查相位是否有4个聚类
        phase_norm = (phase + np.pi) / (2 * np.pi)
        hist, _ = np.histogram(phase_norm, bins=8, range=(0, 1))
        peaks = np.sum(hist > np.mean(hist) * 1.5)
        if peaks >= 3:
            modulation = "QPSK"
            confidence = 0.8

    # BPSK：恒定幅度，2个相位聚类
    elif amp_cv < 0.05 and peaks < 3 and peaks >= 1:
        modulation = "BPSK"
        confidence = 0.7

    return {
        "modulation": modulation,
        "confidence": float(confidence),
        "features": {
            "amplitude_cv": float(amp_cv),
            "freq_std": float(std_freq),
            "mean_amplitude": float(mean_amp),
        },
    }


# ========================================================================
# 解调质量评估
# ========================================================================

def estimate_ber(constellation: np.ndarray, ref_points: np.ndarray) -> float:
    """
    估计误比特率（基于星座图聚类）。

    参数:
        constellation: 接收星座点
        ref_points: 参考星座点（如QPSK的4个点）

    返回:
        估计BER（0-1）
    """
    # 计算每个接收点到最近参考点的距离
    errors = 0
    for point in constellation:
        dists = np.abs(ref_points - point)
        min_idx = np.argmin(dists)

        # 如果距离超过阈值，认为是错误
        threshold = np.mean(dists) * 0.5
        if dists[min_idx] > threshold:
            errors += 1

    return errors / len(constellation)


# ========================================================================
# 频谱特征提取
# ========================================================================

def extract_spectrum_features(spectrum: np.ndarray,
                             freqs: np.ndarray) -> Dict:
    """
    提取频谱特征。

    返回：中心频率、带宽、峰均比、噪声底等。
    """
    # 噪声底（中值）
    noise_floor = float(np.median(spectrum))

    # 峰值
    peak_power = float(np.max(spectrum))
    peak_idx = np.argmax(spectrum)
    peak_freq = float(freqs[peak_idx])

    # 带宽（-3dB）
    peak_db = peak_power
    threshold = peak_db - 3
    above = spectrum > threshold

    if np.any(above):
        indices = np.where(above)[0]
        bw = float(freqs[indices[-1]] - freqs[indices[0]])
    else:
        bw = 0.0

    # 峰均比
    papr = peak_power - noise_floor

    return {
        "center_freq_hz": peak_freq,
        "bandwidth_hz": bw,
        "peak_power_db": peak_db,
        "noise_floor_db": noise_floor,
        "papr_db": float(papr),
        "dynamic_range_db": float(peak_db - noise_floor),
    }


# ========================================================================
# 干扰源检测
# ========================================================================

def detect_interference(spectrum: np.ndarray,
                        freqs: np.ndarray) -> List[Dict]:
    """
    检测干扰源。

    识别非自然信号的异常频谱特征。
    """
    # 先检测信号
    signals = detect_signals(spectrum, freqs, threshold_db=15.0, min_bw=1e3)

    interferences = []
    for sig in signals:
        # 检查是否是窄带强信号（典型干扰特征）
        bw = sig["bandwidth_hz"]
        power = sig["peak_power_db"]

        # 窄带强信号可能是干扰
        if bw < 10e3 and power > 20:
            interferences.append({
                "type": "narrowband",
                "freq_hz": sig["center_freq_hz"],
                "bandwidth_hz": bw,
                "power_db": power,
                "confidence": 0.8,
            })

        # 宽带信号（可能是干扰）
        elif bw > 1e6 and power > 15:
            interferences.append({
                "type": "broadband",
                "freq_hz": sig["center_freq_hz"],
                "bandwidth_hz": bw,
                "power_db": power,
                "confidence": 0.6,
            })

    return interferences
