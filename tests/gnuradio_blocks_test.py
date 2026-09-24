"""
GNU Radio 真实 DSP 块移植测试
================================
对照 repos/gnuradio 源码逐行移植的块：
  FIRFilter / FFTFilter / IIRFilter / PFBArbResampler /
  AGC2 / RationalResampler / ClockRecoveryMM

每个测试都断言真实数值行为，不造假：
  - FIR  == numpy.convolve
  - FFT  == FIR（误差 < 1e-6）
  - AGC2 阶跃后稳定在参考电平
  - Rational 2x 上采样频谱正确（无镜像泄漏）
  - PFB 任意比率重采样无混叠
  - ClockRecoveryMM 已知符号率信号位同步锁定
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.gnuradio_blocks import (  # noqa: E402
    FIRFilter,
    FFTFilter,
    IIRFilter,
    PFBArbResampler,
    AGC2,
    RationalResampler,
    ClockRecoveryMM,
)


# ── 1. FIR：已知抽头 → 已知输入 → 与 numpy.convolve 一致 ──────────
def test_fir_matches_numpy_convolve():
    np.random.seed(42)
    taps = np.array([0.05, 0.1, 0.2, 0.4, 0.2, 0.1, 0.05])
    x = np.random.randn(128)
    y = FIRFilter(taps).filter(x)
    ref = np.convolve(x, taps)
    assert len(y) == len(ref) == len(x) + len(taps) - 1
    assert np.max(np.abs(y - ref)) < 1e-12, "FIR 输出必须 == np.convolve"

    # 复数输入
    xc = np.random.randn(64) + 1j * np.random.randn(64)
    yc = FIRFilter(taps).filter(xc)
    refc = np.convolve(xc, taps)
    assert np.max(np.abs(yc - refc)) < 1e-12


# ── 2. FFTFilter：长信号 → 与 FIR 一致（误差 < 1e-6）──────────────
def test_fft_filter_matches_fir():
    np.random.seed(7)
    # 长抽头低通原型（sinc * 汉窗）
    n = 201
    t = np.arange(n) - (n - 1) / 2
    taps = np.sinc(0.25 * t) * np.hanning(n)
    x = np.random.randn(5000)

    fir_out = FIRFilter(taps).filter(x)
    fft_out = FFTFilter(taps).filter(x)

    assert len(fft_out) == len(fir_out), "FFT/FIR 输出长度应一致"
    err = np.max(np.abs(fft_out - fir_out))
    assert err < 1e-6, f"FFT 快速卷积与直接 FIR 不一致: max err={err:.2e}"


# ── 3. AGC2：幅度阶跃 → 输出稳定在参考电平 ────────────────────────
def test_agc2_converges_to_reference():
    # 先小信号 0.2（增益按 decay 慢爬），再阶跃到大信号 3.0
    sig = np.concatenate([np.full(3000, 0.2 + 0j), np.full(2000, 3.0 + 0j)])
    agc = AGC2(attack_rate=0.5, decay_rate=0.01, reference=1.0, gain=1.0)
    out = agc.process(sig)

    # 小信号段尾：输出幅度应收敛到 reference=1.0
    quiet_level = np.mean(np.abs(out[2500:2900]))
    assert abs(quiet_level - 1.0) < 0.1, f"小信号段未收敛: {quiet_level:.3f}"

    # 大信号阶跃后尾段：增益应被 attack 快速压回，输出仍 ≈ reference
    tail_level = np.mean(np.abs(out[-200:]))
    assert abs(tail_level - 1.0) < 0.15, f"AGC 阶跃后未稳定到参考电平: {tail_level:.3f}"



# ── 4. RationalResampler：上采样 2 倍 → 频谱正确 ─────────────────
def test_rational_resampler_2x_spectrum():
    # 在 fs_in 下的单音，归一化频率 0.1（绝对带宽内，远离 Nyquist）
    fs_in = 1.0
    f0 = 0.1
    n = 4096
    t = np.arange(n)
    x = np.cos(2 * np.pi * f0 / fs_in * t)

    rr = RationalResampler(2, 1)  # 上采样 2 倍
    y = rr.process(x)

    # 输出应约为 2*n 点
    assert len(y) > 1.5 * n, f"上采样倍数不足: {len(y)/n:.2f}"

    # 上采样后绝对频率不变：f0_hz = f0*fs_in = 0.1 Hz；fs_out=2 Hz。
    # 抗镜像低通应把 fs_out-f0_hz = 1.9 Hz 处的上采样镜像压下去。
    Y = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    freqs = np.fft.rfftfreq(len(y), d=1.0 / 2.0)  # fs_out=2 → 实际 Hz
    peak_bin = np.argmax(Y[1:]) + 1
    peak_freq = freqs[peak_bin]
    assert abs(peak_freq - f0 * fs_in) < 0.02, \
        f"上采样后频谱峰位错误: {peak_freq:.3f} (期望 {f0*fs_in:.3f})"

    # 镜像抑制：fs_out - f0_hz 处能量应远低于主峰
    img_freq = 2.0 - f0 * fs_in
    img_bin = np.argmin(np.abs(freqs - img_freq))
    img_level = Y[img_bin] / Y[peak_bin]
    assert img_level < 0.1, f"上采样镜像未抑制: 镜像/主峰={img_level:.3f}"


# ── 5. PFB 任意重采样：无混叠 ────────────────────────────────────
def test_pfb_resampler_no_aliasing():
    # rate=0.75：抽取（fs_out=0.75*fs_in）。输入单音绝对频率不变，
    # 归一化到新 fs_out 后频率 = f0/rate = 0.3/0.75 = 0.4（< 新 Nyquist 0.5，无混叠）。
    np.random.seed(1)
    fs_in = 1.0
    f0 = 0.30
    n = 4000
    t = np.arange(n)
    x = np.exp(2j * np.pi * f0 * t)

    # 抗混叠原型低通
    L = 257
    tt = np.arange(L) - (L - 1) / 2
    taps = np.sinc(0.5 * tt) * np.hanning(L)

    pfb = PFBArbResampler(rate=0.75, taps=taps, filter_size=32)
    y = pfb.process(x)

    # 输出/输入比率 ≈ 0.75
    ratio = len(y) / len(x)
    assert abs(ratio - 0.75) < 0.02, f"PFB 重采样比率错误: {ratio:.3f}"

    # 输出频谱主峰位置：新 fs 归一化频率 = f0/rate
    Y = np.abs(np.fft.fftshift(np.fft.fft(y)))
    f = np.fft.fftshift(np.fft.fftfreq(len(y), d=1.0))
    peak = f[np.argmax(Y)]
    expected = f0 / 0.75
    assert abs(peak - expected) < 0.03, \
        f"PFB 输出主峰位置错误: {peak:.3f} (期望 {expected:.3f})"
    # 无混叠：主峰必须在新基带内（|peak| < 0.5）
    assert abs(peak) < 0.5, f"PFB 发生混叠: peak={peak:.3f}"


# ── 6. ClockRecoveryMM：已知符号率 → 位同步锁定 ──────────────────
def test_clock_recovery_mm_locks():
    np.random.seed(3)
    omega = 4.0  # 每个符号 4 个采样
    nsym = 300
    bits = np.random.choice([-1.0, 1.0], nsym)
    # NRZ 矩形脉冲成形
    x = np.repeat(bits, int(omega)) + 0.02 * np.random.randn(int(nsym * omega))

    mm = ClockRecoveryMM(omega=omega, gain_omega=0.005, mu=0.0,
                         gain_mu=0.05, omega_relative_limit=0.01)
    out = mm.process(x)

    # 恢复出的符号数应 ≈ nsym
    assert abs(len(out) - nsym) <= 3, \
        f"恢复符号数偏离过大: {len(out)} vs {nsym}"

    # 锁定后采样点应落在 ±1 眼图中心
    sampled = np.abs(out[50:])
    assert np.mean(sampled) > 0.8, f"位同步未锁定，采样电平偏低: {np.mean(sampled):.3f}"

    # omega 应收敛回中值附近
    assert abs(mm.omega - mm.omega_mid) < mm.omega_lim + 0.05


# ── 7. IIRFilter：与 scipy.lfilter 对比（一阶/二阶）───────────────
def test_iir_filter_matches_lfilter():
    from scipy.signal import lfilter
    np.random.seed(5)
    x = np.random.randn(200)
    # y[n] = 0.5x[n] + 0.5x[n-1] + 0.3y[n-1]
    b = [0.5, 0.5]
    a = [1.0, -0.3]  # GR fbtaps[i] = -a[i]
    iir = IIRFilter(fftaps=b, fbtaps=[0.0, 0.3])
    y = iir.process(x).real
    ref = lfilter(b, a, x)
    assert np.max(np.abs(y - ref)) < 1e-8, \
        f"IIR 与 scipy.lfilter 不一致: {np.max(np.abs(y-ref)):.2e}"


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("All GNU Radio block tests passed.")
