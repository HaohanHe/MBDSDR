# SPDX-License-Identifier: MIT
"""单通道 GPS C/A DLL/Costas PLL 的合成信号行为测试。

参考码由 gps_ca 的公开 G1/G2 多项式现场构造并显式注入；这里不引入、
也不钉扎任何 PRN 相位分配表。全部信号都是合成基带，不代表 OTA 验证。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from mbdsdr_ai import gps_ca
from mbdsdr_ai.gps_bit_sync import nav_bit_sync
from mbdsdr_ai.gps_tracking import CA_CHIP_RATE, TrackingConfig, track_gps_ca

FS = 2_046_000.0                         # 2 samples/chip
SEED = 20261102


def _external_code() -> np.ndarray:
    # 一个外部 Gold 码实例，不宣称它对应某个 PRN。
    bits = gps_ca.g1_sequence() ^ np.roll(gps_ca.g2_sequence(), 37)
    return gps_ca.to_pm1(bits)


CODE = _external_code()


def _nav_bits(count: int) -> np.ndarray:
    # 三样本同号游程使位同步过渡率明确低于随机水平。
    groups = np.arange((count + 2) // 3) % 2
    return np.repeat(groups, 3)[:count].astype(np.int64)


def _signal(
    *,
    duration_ms: int,
    delay_chips: float,
    doppler_hz: float,
    carrier_phase_rad: float = 0.0,
    doppler_rate_hz_s: float = 0.0,
    bits: np.ndarray | None = None,
    sample_rate_hz: float = FS,
    snr_per_sample_db: float | None = None,
    seed: int = SEED,
) -> np.ndarray:
    n = int(round(sample_rate_hz * duration_ms / 1000.0))
    t = np.arange(n, dtype=np.float64) / sample_rate_hz
    code_index = np.floor(CA_CHIP_RATE * t - delay_chips).astype(np.int64) % 1023
    if bits is None:
        data = np.ones(n, dtype=np.float64)
    else:
        bit_index = np.floor(t * 50.0).astype(np.int64)
        assert bit_index[-1] < bits.size
        # 与现有 nav_bit_sync 一致：正软判决对应比特 1。
        data = 2.0 * bits[bit_index] - 1.0
    phase = (2.0 * np.pi * doppler_hz * t
             + np.pi * doppler_rate_hz_s * t * t + carrier_phase_rad)
    signal = data * CODE[code_index] * np.exp(1j * phase)
    if snr_per_sample_db is not None:
        noise_var = 10.0 ** (-snr_per_sample_db / 10.0)
        rng = np.random.default_rng(seed)
        noise = np.sqrt(noise_var / 2.0) * (
            rng.standard_normal(n) + 1j * rng.standard_normal(n))
        signal = signal + noise
    return signal


def _track(x: np.ndarray, delay: float, doppler: float, **kwargs):
    return track_gps_ca(
        x, FS, CODE, initial_code_phase_chips=delay,
        initial_doppler_hz=doppler, **kwargs)


def _circular_code_error(estimate, truth: float) -> np.ndarray:
    return (np.asarray(estimate) - truth + 511.5) % 1023.0 - 511.5


def _bpsk_phase_error(estimate, truth) -> np.ndarray:
    # Costas BPSK 的相位观测按 pi 取模。
    return 0.5 * np.angle(np.exp(2j * (np.asarray(estimate) - truth)))


@pytest.mark.parametrize(
    "delay_chips,doppler_hz",
    [(0.0, 250.0), (19.0, -275.0),
     (123.375, 180.0), (1022.625, -160.0)],
    ids=["integer-zero-delay-positive-f", "integer-positive-delay-negative-f",
         "fractional-delay-positive-f", "fractional-wrap-negative-f"],
)
def test_tracks_integer_fractional_delay_and_signed_doppler(
        delay_chips, doppler_hz):
    duration_ms = 300
    x = _signal(duration_ms=duration_ms, delay_chips=delay_chips,
                doppler_hz=doppler_hz, carrier_phase_rad=0.3,
                bits=_nav_bits(duration_ms // 20))
    res = _track(x, delay_chips, doppler_hz)
    assert res["locked"] and res["ever_locked"]
    tail = slice(-100, None)
    code_error = _circular_code_error(
        res["code_phase_chips"][tail], delay_chips)
    freq_error = np.asarray(res["carrier_frequency_hz"][tail]) - doppler_hz
    t = np.asarray(res["time_s"][tail])
    true_phase = 2.0 * np.pi * doppler_hz * t + 0.3
    phase_error = _bpsk_phase_error(res["carrier_phase_rad"][tail], true_phase)
    assert math.sqrt(float(np.mean(code_error ** 2))) < 0.01
    assert math.sqrt(float(np.mean(freq_error ** 2))) < 0.25
    assert math.sqrt(float(np.mean(phase_error ** 2))) < 0.03


def test_tracks_optional_frequency_ramp():
    duration_ms = 500
    delay, f0, rate = 47.4, -90.0, 12.0
    x = _signal(duration_ms=duration_ms, delay_chips=delay,
                doppler_hz=f0, doppler_rate_hz_s=rate,
                carrier_phase_rad=-0.2,
                bits=_nav_bits(duration_ms // 20))
    res = _track(x, delay, f0, doppler_rate_hz_s=rate)
    assert res["locked"]
    tail = slice(-200, None)
    t = np.asarray(res["time_s"][tail])
    code_error = _circular_code_error(res["code_phase_chips"][tail], delay)
    freq_error = np.asarray(res["carrier_frequency_hz"][tail]) - (f0 + rate * t)
    true_phase = 2.0 * np.pi * f0 * t + np.pi * rate * t * t - 0.2
    phase_error = _bpsk_phase_error(res["carrier_phase_rad"][tail], true_phase)
    assert math.sqrt(float(np.mean(code_error ** 2))) < 0.01
    assert math.sqrt(float(np.mean(freq_error ** 2))) < 0.25
    assert math.sqrt(float(np.mean(phase_error ** 2))) < 0.03


def test_pll_pulls_initial_frequency_error():
    duration_ms = 500
    delay, true_doppler = 40.3, 85.0
    x = _signal(duration_ms=duration_ms, delay_chips=delay,
                doppler_hz=true_doppler, carrier_phase_rad=0.4,
                bits=_nav_bits(duration_ms // 20))
    # 捕获频差有 +7 Hz 误差，PLL 必须牵引而非只在完美初值下保持。
    res = _track(x, delay, true_doppler - 7.0)
    assert res["locked"] and res["ever_locked"]
    tail = slice(-200, None)
    t = np.asarray(res["time_s"][tail])
    code_error = _circular_code_error(res["code_phase_chips"][tail], delay)
    freq_error = np.asarray(res["carrier_frequency_hz"][tail]) - true_doppler
    true_phase = 2.0 * np.pi * true_doppler * t + 0.4
    phase_error = _bpsk_phase_error(res["carrier_phase_rad"][tail], true_phase)
    assert math.sqrt(float(np.mean(code_error ** 2))) < 0.01
    assert math.sqrt(float(np.mean(freq_error ** 2))) < 0.25
    assert math.sqrt(float(np.mean(phase_error ** 2))) < 0.03


def test_fixed_seed_awgn_tracking_error_and_lock():
    duration_ms = 400
    delay, doppler = 47.4, -140.0
    x = _signal(duration_ms=duration_ms, delay_chips=delay,
                doppler_hz=doppler, bits=_nav_bits(duration_ms // 20),
                snr_per_sample_db=-15.0, seed=SEED)
    res = _track(x, delay, doppler)
    assert res["locked"]
    tail = slice(-200, None)
    code_error = _circular_code_error(res["code_phase_chips"][tail], delay)
    freq_error = np.asarray(res["carrier_frequency_hz"][tail]) - doppler
    t = np.asarray(res["time_s"][tail])
    phase_error = _bpsk_phase_error(
        res["carrier_phase_rad"][tail], 2.0 * np.pi * doppler * t)
    assert math.sqrt(float(np.mean(code_error ** 2))) < 0.08
    assert math.sqrt(float(np.mean(freq_error ** 2))) < 2.0
    assert math.sqrt(float(np.mean(phase_error ** 2))) < 0.15


def test_pure_noise_is_rejected_with_fixed_seed():
    x = _signal(duration_ms=300, delay_chips=12.0, doppler_hz=100.0,
                snr_per_sample_db=-15.0, seed=SEED)
    rng = np.random.default_rng(SEED)
    # 保留与 AWGN 用例相同的复噪声尺度，但移除全部码信号。
    noise_var = 10.0 ** (15.0 / 10.0)
    x = np.sqrt(noise_var / 2.0) * (
        rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))
    res = _track(x, 12.0, 100.0)
    assert not res["locked"] and not res["ever_locked"]
    assert res["lock_events"] == 0
    assert not any(res["locked_per_ms"])


def test_wrong_initial_code_phase_does_not_claim_lock():
    x = _signal(duration_ms=200, delay_chips=30.0, doppler_hz=80.0)
    res = _track(x, 35.0, 80.0)       # 偏 5 chip，不在 DLL 牵引范围内
    assert not res["ever_locked"] and not res["locked"]
    assert res["lock_events"] == 0


def test_signal_loss_after_valid_lock_is_reported():
    duration_ms = 400
    delay, doppler = 22.25, 65.0
    x = _signal(duration_ms=duration_ms, delay_chips=delay,
                doppler_hz=doppler, bits=_nav_bits(duration_ms // 20))
    split = int(FS * 0.2)
    rng = np.random.default_rng(SEED + 1)
    x[split:] = rng.standard_normal(x.size - split) + 1j * rng.standard_normal(
        x.size - split)
    res = _track(x, delay, doppler)
    assert res["ever_locked"] and any(res["locked_per_ms"][:150])
    assert not res["locked"] and res["lost_lock"]
    assert res["loss_events"] == 1
    assert not any(res["locked_per_ms"][-50:])


def test_prompt_feeds_existing_nav_bit_sync_without_adapter():
    bits = _nav_bits(24)               # 480 ms / 20 = 24 bit
    x = _signal(duration_ms=480, delay_chips=12.25, doppler_hz=75.0,
                bits=bits)
    tracked = _track(x, 12.25, 75.0)
    synced = nav_bit_sync(tracked["prompt_i"], samples_per_bit=20)
    assert synced["in_sync"], synced["reason"]
    assert synced["bit_offset"] == 0
    assert synced["bits"] == bits.tolist()


def test_noninteger_samples_per_ms_mapping_and_trailing_samples():
    fs = 2_047_500.0                    # 2047.5 samples/ms
    # 三个边界依次为 2048、4095、6143，验证四舍五入而非逐块截断。
    x = np.zeros(6144, dtype=np.complex128)
    res = track_gps_ca(x, fs, CODE, 0.0, 0.0)
    assert res["sample_start"] == [0, 2048, 4095]
    assert res["sample_stop"] == [2048, 4095, 6143]
    assert res["n_samples_used"] == 6143
    assert res["n_samples_trailing"] == 1
    expected_dt = np.diff([0, 2048, 4095, 6143]) / fs
    np.testing.assert_allclose(res["dt_s"], expected_dt, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(res["time_s"], np.array(res["sample_stop"]) / fs,
                               rtol=0.0, atol=0.0)


@pytest.mark.parametrize("phase_error", [-0.2, 0.2])
@pytest.mark.parametrize("data_sign", [-1.0, 1.0])
def test_loop_dt_costas_bpsk_and_frequency_sign(phase_error, data_sign):
    fs = 2_047_500.0
    stop = 2048
    # 全 +1 外部序列令 DLL 无码片边沿；BPSK 的 0/pi 数据符号不得改变
    # Costas 相位误差符号。该用例同时独立钉住连续域系数乘真实块 dt。
    code = np.ones(1023, dtype=np.float64)
    x = data_sign * np.exp(1j * phase_error) * np.ones(stop, dtype=complex)
    cfg = TrackingConfig(pll_bandwidth_hz=10.0)
    res = track_gps_ca(x, fs, code, 0.0, 0.0, config=cfg)
    assert res["n_ms"] == 1
    dt = stop / fs
    damping = cfg.damping
    wn = 8.0 * damping * 10.0 / (4.0 * damping * damping + 1.0)
    kp, ki = 2.0 * damping * wn, wn * wn
    assert res["pll_discriminator_rad"][0] == pytest.approx(phase_error, abs=1e-12)
    assert res["carrier_phase_rad"][0] == pytest.approx(
        kp * phase_error * dt, abs=1e-12)
    assert res["carrier_frequency_hz"][0] == pytest.approx(
        ki * phase_error * dt / (2.0 * np.pi), abs=1e-12)
    assert np.sign(res["carrier_frequency_hz"][0]) == np.sign(phase_error)


@pytest.mark.parametrize("initial_delay,expected_dll_sign,comparison",
                         [(19.8, -1.0, 1.0), (20.2, 1.0, -1.0)])
def test_dll_discriminator_and_delay_correction_sign(
        initial_delay, expected_dll_sign, comparison):
    x = _signal(duration_ms=1, delay_chips=20.0, doppler_hz=0.0)
    res = _track(x, initial_delay, 0.0)
    assert np.sign(res["dll_discriminator"][0]) == expected_dll_sign
    # comparison=+1 要求时延增大，-1 要求时延减小。
    assert comparison * (res["code_phase_chips"][0] - initial_delay) > 0.0


def test_invalid_signal_and_initial_parameters_are_rejected():
    valid_iq = np.ones(2046, dtype=np.complex128)
    cases = [
        {"iq": np.array([], dtype=complex)},
        {"iq": np.ones((2, 2046), dtype=complex)},
        {"iq": np.array([np.nan + 0j] * 2046)},
        {"sample_rate_hz": 0.0},
        {"sample_rate_hz": np.inf},
        {"ca_code": np.ones(1022)},
        {"ca_code": np.zeros(1023)},
        {"ca_code": np.full(1023, 1.0 + 1.0j)},
        {"initial_code_phase_chips": -0.01},
        {"initial_code_phase_chips": 1023.0},
        {"initial_doppler_hz": np.nan},
        {"initial_carrier_phase_rad": np.inf},
        {"doppler_rate_hz_s": np.nan},
        {"config": object()},
    ]
    for overrides in cases:
        kwargs = dict(iq=valid_iq, sample_rate_hz=FS, ca_code=CODE,
                      initial_code_phase_chips=0.0, initial_doppler_hz=0.0)
        kwargs.update(overrides)
        with pytest.raises(ValueError):
            track_gps_ca(**kwargs)
    with pytest.raises(ValueError, match="不足"):
        track_gps_ca(np.ones(100, dtype=complex), FS, CODE, 0.0, 0.0)


@pytest.mark.parametrize("kwargs", [
    {"dll_bandwidth_hz": 0.0}, {"pll_bandwidth_hz": np.nan},
    {"damping": 0.0}, {"early_late_spacing_chips": 0.0},
    {"early_late_spacing_chips": 1.1}, {"lock_code_ratio_db": -1.0},
    {"lock_carrier_min": 1.1}, {"lock_dll_max": 0.0},
    {"lock_window_ms": 0}, {"lock_confirm_ms": 1.5},
    {"loss_confirm_ms": True},
])
def test_invalid_loop_and_lock_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        TrackingConfig(**kwargs)
