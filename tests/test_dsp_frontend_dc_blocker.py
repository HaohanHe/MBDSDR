"""DC blocker 确定性测试。

对照 mbdsdr_ai/dc_blocker.py（上游 sdrpp correction/dc_blocker.h:54-60）。
"""
import numpy as np
from mbdsdr_ai.dc_blocker import DCBlocker


def _bin_db(spectrum_db):
    return spectrum_db


def test_dc_blocker_removes_dc_keeps_tone():
    """输入 1.0(DC) + 0.1*sin(2π*1000t)：DC 残留 < -40dB，1kHz 保留 > -1dB。"""
    fs = 1_000_000.0
    n = 200_000
    t = np.arange(n) / fs
    # 直流 1.0（实信号，I/Q 同）
    x = 1.0 + 0.1 * np.sin(2 * np.pi * 1000.0 * t)

    blk = DCBlocker(r=0.999)
    y = blk.process(x.astype(np.float64))

    # 跳过暂态
    y_s = y[5000:]
    residual_dc = abs(np.mean(y_s))
    # DC 抑制相对输入 DC=1.0
    dc_db = 20 * np.log10(max(residual_dc, 1e-12))
    assert dc_db < -40.0, f"DC 残留 {dc_db:.1f} dB，应 < -40 dB"

    # 1kHz 信号保留度：矩形窗 FFT（相干增益=N/2，无窗损失）测 1kHz bin
    N = len(y_s)
    Y = np.fft.rfft(y_s)
    freqs = np.fft.rfftfreq(N, 1.0 / fs)
    idx = np.argmin(np.abs(freqs - 1000.0))
    tone_out = 2.0 * abs(Y[idx]) / N
    retention_db = 20 * np.log10(max(tone_out / 0.1, 1e-12))
    assert retention_db > -1.0, f"1kHz 保留 {retention_db:.2f} dB，应 > -1 dB"


def test_dc_blocker_iq_independent():
    """复数 IQ：I/Q 两路独立处理，无串扰。"""
    fs = 100_000.0
    n = 50_000
    t = np.arange(n) / fs
    # I 路 DC=0.5，Q 路 DC=-0.3，各加不同频率小信号
    x = (0.5 + 0.05 * np.sin(2 * np.pi * 500.0 * t)) \
        + 1j * (-0.3 + 0.05 * np.sin(2 * np.pi * 800.0 * t))
    blk = DCBlocker(r=0.999)
    y = blk.process(x.astype(np.complex128))
    y_s = y[2000:]
    # I、Q 残留 DC 都应很小
    assert abs(np.mean(y_s.real)) < 0.01
    assert abs(np.mean(y_s.imag)) < 0.01


def test_dc_blocker_stateful_continuity():
    """分块处理与一次性处理结果一致（跨块状态连续）。"""
    fs = 100_000.0
    n = 20_000
    t = np.arange(n) / fs
    x = 1.0 + 0.1 * np.sin(2 * np.pi * 1000.0 * t)

    y1 = DCBlocker(r=0.999).process(x.astype(np.float64))

    b2 = DCBlocker(r=0.999)
    half = n // 2
    ya = b2.process(x[:half].astype(np.float64))
    yb = b2.process(x[half:].astype(np.float64))
    y2 = np.concatenate([ya, yb])

    assert np.allclose(y1, y2, atol=1e-10)
