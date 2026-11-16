# -*- coding: utf-8 -*-
"""gnss_monitor.monitor_gnss_band 平均功率口径回归（#112）。

缺陷：power_dbm（文档/用户报告均称「平均功率」）原为 mean(psd_db)，即 dB 域
线性平均＝几何均值的 dB。平均功率的物理定义是线性 PSD 算术均值再转 dB：
10*log10(mean(psd))。AM≥GM，谱越尖（干扰越强）几何均值越偏低——探针实测
CW 干扰下原读数恒≈-65.5dB 对干扰功率全盲，强干扰低报达 42.4dB。

真值源：
  - GNSSBandResult.power_dbm 注释「平均功率（相对读数 dB，非 dBm）」
  - sdr_tools.py:6508 用户可见「平均功率: {power_dbm:.1f} dB」
  - 物理定义 + 本文件 bin 居中纯音的解析真值 mean(psd)=A²/fs。

确定性：固定 rng 种子 / 解析信号，不绑墙钟/网络/硬件。
mutation 反证：把 avg_power_db 改回 mean(psd_db) → 解析真值与响应性测试即红。
"""
import numpy as np
import pytest

from mbdsdr_ai.gnss_monitor import monitor_gnss_band, GNSS_BANDS

CENTER = GNSS_BANDS["L1"]["center_freq_hz"]


def _psd_of(iq, fs):
    """独立复刻 monitor_gnss_band 的 PSD（行 205-207），用于交叉核对。"""
    n = len(iq)
    nfft = min(4096, n)
    if nfft < 256:
        nfft = 256
    fft_data = np.fft.fftshift(np.fft.fft(iq[:nfft], nfft))
    return np.abs(fft_data) ** 2 / (nfft * fs)


def test_avg_power_matches_analytic_tone_power():
    """bin 居中纯音：mean(psd)=A²/fs 解析精确，power_dbm 必须等于其 dB。

    原几何均值口径会把几乎全零的 bin 平均成≈-120dB，与真值差数十 dB。
    """
    fs = 2.048e6
    nfft = 4096
    A = 0.7
    k = 100                      # 整数 bin → 无频谱泄漏
    f0 = k * fs / nfft
    t = np.arange(nfft) / fs
    iq = A * np.exp(1j * 2 * np.pi * f0 * t)
    res = monitor_gnss_band(iq, CENTER, fs, "L1")
    # 解析真值含实现同款 +1e-12 epsilon 底（psd+1e-12），避免把地板扰动误判为缺陷。
    expected = 10.0 * np.log10(A ** 2 / fs + 1e-12)   # = 10log10(mean(psd)+eps)
    assert res.power_dbm == pytest.approx(expected, abs=1e-6), (
        f"平均功率应=10log10(A²/fs)={expected:.4f}dB，实得 {res.power_dbm:.4f}dB"
        "（几何均值口径回归？）")
    # 反证锚：几何均值口径在此信号上≈-120dB，必须显著不同于真值。
    psd = _psd_of(iq, fs)
    geo = float(np.mean(10 * np.log10(psd + 1e-12)))
    assert abs(geo - expected) > 30.0, "解析信号应能区分两种口径"
    assert abs(res.power_dbm - geo) > 30.0


def test_avg_power_responds_to_jammer_strength():
    """关键缺陷回归：平均功率必须随 CW 干扰增强而上升（原读数几乎不动）。"""
    fs = 2.048e6
    n = 8192
    t = np.arange(n) / fs
    rng = np.random.default_rng(2026)
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    readings = []
    for jam_db in (0, 20, 40):
        amp = 10 ** (jam_db / 20.0)
        iq = noise + amp * np.exp(1j * 2 * np.pi * 100e3 * t)
        readings.append(monitor_gnss_band(iq, CENTER, fs, "L1").power_dbm)
    # 每 +20dB 干扰，平均功率应显著上升（原几何均值口径三步几乎恒等）。
    assert readings[1] - readings[0] > 5.0, f"干扰+20dB 平均功率应明显上升: {readings}"
    assert readings[2] - readings[1] > 5.0, f"干扰+40dB 平均功率应明显上升: {readings}"
    assert readings[2] - readings[0] > 20.0, f"强干扰下平均功率应大幅上升: {readings}"


def test_avg_power_is_arithmetic_not_geometric_mean():
    """噪声+尖峰谱：power_dbm == 10log10(mean(psd))（算术），且 >= mean(psd_db)（几何）。"""
    fs = 2.048e6
    n = 8192
    t = np.arange(n) / fs
    rng = np.random.default_rng(7)
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    iq = noise + 30.0 * np.exp(1j * 2 * np.pi * 100e3 * t)   # 强 CW
    res = monitor_gnss_band(iq, CENTER, fs, "L1")
    psd = _psd_of(iq, fs)
    arith_db = float(10 * np.log10(np.mean(psd) + 1e-12))
    geo_db = float(np.mean(10 * np.log10(psd + 1e-12)))
    assert res.power_dbm == pytest.approx(arith_db, abs=1e-6)
    assert res.power_dbm >= geo_db - 1e-9, "AM≥GM：算术均值 dB 不应低于几何均值 dB"
    assert res.power_dbm - geo_db > 10.0, "强干扰下两口径应差 >10dB（缺陷可观测）"


def test_noise_floor_and_peak_unchanged_monotonic_stats():
    """噪声底(median)/峰值(max) 是单调不变统计，修复不应改动它们。"""
    fs = 2.048e6
    n = 8192
    t = np.arange(n) / fs
    rng = np.random.default_rng(99)
    iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    iq += 10.0 * np.exp(1j * 2 * np.pi * 100e3 * t)
    res = monitor_gnss_band(iq, CENTER, fs, "L1")
    psd = _psd_of(iq, fs)
    psd_db = 10 * np.log10(psd + 1e-12)
    assert res.noise_floor_dbm == pytest.approx(float(np.median(psd_db)), abs=1e-6)
    assert res.peak_power_dbm == pytest.approx(float(np.max(psd_db)), abs=1e-6)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))