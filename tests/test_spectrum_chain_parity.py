#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
频谱两条计算链路同口径对拍（W43 / P1-12 / gap-spectrum G7）反证
==============================================================

同一段真实复 IQ，分别走：

  * 显示链路 `desktop/spectrum_widget.py::SpectrumDataGenerator.push_iq`
    （窗 × fft → fftshift → dBFS → 多帧幅度平均，屏幕上的谱线就是它）；
  * AI/工具链路 `mbdsdr_ai/spectrum_processor.py::SpectrumProcessor`
    （`spectrum_analyze_iq` 一类工具用的那份）。

改前两者是**两套口径**（仅静态+局部实测，2026-11-11）：

  * 窗函数默认值不同（显示 Nuttall / 处理器 Hann）；
  * dB 基准不同：显示是 dBFS（峰 = N×窗相干增益/2），处理器是裸功率
    `10log10(|FFT|²)`，同一段 IQ 差一个 ≈ 20log10(N·cg/2) 的常数；
  * 噪声底估计不同：显示 = 全帧中位数，处理器 = 最低 10% 的均值；
  * 于是「峰 − 底噪」差值和绝对门限（如 −60 dB）跨链路含义不一致。

判据（同一 IQ、同一 Nuttall 窗、同 FFT 点数、送绘根数 = FFT 点数）：

1. 峰值 dB 与显示链路相差 ≤ 0.5 dB；
2. 噪声底 dB 与显示链路的 `noise_floor_db()` 相差 ≤ 0.5 dB；
3. 纯噪声输入（无单音）同样满足判据 2——底噪不是「凑出来的」。

改前复跑（`git show HEAD:mbdsdr_ai/spectrum_processor.py` 单独放到临时目录
跑同一场景）：显示 peak −0.352 / noise −62.678，处理器 peak +53.562 /
noise −21.973 → **峰值差 +53.914 dB、底噪差 +40.705 dB**，判据 1/2 均 FAIL。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_chain_parity.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "mbdsdr_ai"))

_FFT = 2048
_SR = 2.4e6
_CF = 98.5e6
_TONE_HZ = 200e3
_TOL_DB = 0.5


def _iq_with_tone(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(_FFT) / _SR
    tone = 0.5 * np.exp(2j * np.pi * _TONE_HZ * t)
    noise = 0.01 * (rng.normal(size=_FFT) + 1j * rng.normal(size=_FFT))
    return (tone + noise).astype(np.complex64)


def _display_chain(iq: np.ndarray):
    from spectrum_widget import SpectrumDataGenerator
    gen = SpectrumDataGenerator(num_bins=_FFT)   # 1:1 送绘，排除重采样影响
    gen.set_fft_size(_FFT)
    gen.set_avg_frames(1)                        # 单帧对单帧
    gen.set_window("Nuttall")
    gen.push_iq(iq, _CF, _SR)
    return gen


def test_peak_and_noise_floor_match_display_chain():
    """有单音时：峰值与噪声底都要和显示链路同口径（≤0.5 dB）。"""
    from spectrum_processor import SpectrumProcessor

    iq = _iq_with_tone()
    gen = _display_chain(iq)
    display_peak = float(np.nanmax(np.asarray(gen.spectrum, dtype=float)))
    display_noise = float(gen.noise_floor_db())
    assert np.isfinite(display_peak) and np.isfinite(display_noise)

    proc = SpectrumProcessor(fft_size=_FFT, window="nuttall")
    data = proc.compute_spectrum(iq, _CF, _SR)

    peak_diff = float(data.peak_power_db) - display_peak
    noise_diff = float(data.noise_floor_db) - display_noise
    assert abs(peak_diff) <= _TOL_DB, (
        f"峰值跨链路口径不一致：处理器 {data.peak_power_db:.3f} dB vs "
        f"显示 {display_peak:.3f} dB（差 {peak_diff:+.3f} dB，改前 ≈ +60 dB）")
    assert abs(noise_diff) <= _TOL_DB, (
        f"噪声底跨链路口径不一致：处理器 {data.noise_floor_db:.3f} dB vs "
        f"显示 {display_noise:.3f} dB（差 {noise_diff:+.3f} dB，改前 ≈ +63 dB）")

    # 峰值频点也要落在同一根 bin 上（200 kHz 偏移）。
    assert abs(float(data.peak_freq) - (_CF + _TONE_HZ)) <= _SR / _FFT, (
        f"峰值频点不一致：{data.peak_freq}")


def test_noise_floor_matches_on_pure_noise():
    """纯噪声（无单音）：噪声底仍要同口径——底噪不是靠信号峰凑出来的。"""
    from spectrum_processor import SpectrumProcessor

    rng = np.random.default_rng(11)
    iq = (0.01 * (rng.normal(size=_FFT) + 1j * rng.normal(size=_FFT))
          ).astype(np.complex64)
    gen = _display_chain(iq)
    display_noise = float(gen.noise_floor_db())

    proc = SpectrumProcessor(fft_size=_FFT, window="nuttall")
    data = proc.compute_spectrum(iq, _CF, _SR)

    assert abs(float(data.noise_floor_db) - display_noise) <= _TOL_DB, (
        f"纯噪声底噪不一致：处理器 {data.noise_floor_db:.3f} dB vs "
        f"显示 {display_noise:.3f} dB")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
