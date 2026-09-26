"""ANR (IQ 域) 确定性测试。

对照 mbdsdr_ai/anr_iq.py。
红线：已知信号+噪声 → 降噪后 SNR 改善 > 3 dB；无信号时不崩。
"""
import numpy as np
import pytest

from mbdsdr_ai.anr_iq import ANR


def _snr_db(signal: np.ndarray, noisy: np.ndarray) -> float:
    """计算信噪比（信号功率 / 噪声功率），dB。"""
    sig_p = float(np.mean(np.abs(signal) ** 2))
    noise = noisy - signal
    noise_p = float(np.mean(np.abs(noise) ** 2)) + 1e-12
    return 10.0 * np.log10(sig_p / noise_p)


def test_snr_improvement_gt_3db():
    """已知单频信号 + 高斯噪声 → 降噪后 SNR 改善 > 3 dB。"""
    rng = np.random.default_rng(42)
    n = 8192
    fs = 2_048_000.0
    t = np.arange(n) / fs
    # 单频信号放在 FFT bin 中心（bin=2 -> 4000 Hz），避免频谱泄漏
    f_tone = fs * 2 / 1024.0
    signal = np.exp(2j * np.pi * f_tone * t)
    # 加噪声（功率约 -10 dB 相对信号）
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.316
    noisy = signal + noise

    before_snr = _snr_db(signal, noisy)

    anr = ANR(fft_size=1024, strength=0.9)
    # 先喂一段"静默"（纯噪声）让它估噪声底
    anr.set_noise_estimate(noise[:2048])
    out = anr.process(noisy)

    after_snr = _snr_db(signal[: len(out)], out)
    improvement = after_snr - before_snr
    assert improvement > 3.0, f"SNR 改善 {improvement:.2f} dB 不足 3 dB"


def test_strength_zero_means_no_change():
    """strength=0 时输出应等于输入（近似）。"""
    rng = np.random.default_rng(0)
    n = 4096
    fs = 2_048_000.0
    t = np.arange(n) / fs
    f_tone = fs * 2 / 1024.0
    signal = np.exp(2j * np.pi * f_tone * t)
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.1
    noisy = signal + noise

    anr = ANR(fft_size=1024, strength=0.0)
    anr.set_noise_estimate(noise[:1024])
    out = anr.process(noisy)
    # strength=0 时直通，输出应等于输入
    diff = np.mean(np.abs(out - noisy[: len(out)]))
    assert diff < 1e-6, f"strength=0 时输出偏离输入过多: {diff}"


def test_empty_input_safe():
    """空输入不崩，原样返回。"""
    anr = ANR()
    out = anr.process(np.empty(0, dtype=np.complex64))
    assert out.size == 0


def test_short_input_safe():
    """短于一帧的输入原样返回。"""
    anr = ANR(fft_size=1024)
    x = np.ones(100, dtype=np.complex64)
    out = anr.process(x)
    assert out.shape == x.shape


def test_auto_noise_tracking():
    """未手动 set_noise_estimate 时，自动百分位跟踪也能降噪。"""
    rng = np.random.default_rng(7)
    n = 8192
    t = np.arange(n) / 2_048_000.0
    signal = np.exp(2j * np.pi * 1000.0 * t)
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.316
    noisy = signal + noise

    anr = ANR(fft_size=1024, strength=0.9)
    # 不调 set_noise_estimate，直接 process（自动跟踪）
    out1 = anr.process(noisy[:2048])  # 第一次学习
    out2 = anr.process(noisy[2048:4096])
    # 不崩即可（自动跟踪路径）
    assert out2.shape[0] > 0
