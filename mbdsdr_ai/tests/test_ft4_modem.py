# SPDX-License-Identifier: MIT
"""FT4 独立复基带前端测试。

只验证合成 GFSK、盲时频同步、软能量与既有 FT4 解码链的合成闭环；
不把固定种子 AWGN 结果外推为弱信号门限或真实 OTA 性能。
"""
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.integrate import trapezoid

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from mbdsdr_ai.ft4_codec import (  # noqa: E402
    SYNC_SEQUENCES,
    build_frame,
    encode_ft4_tones,
)
from mbdsdr_ai.ft4_modem import (  # noqa: E402
    BT,
    FRAME_SAMPLES,
    FS_HZ,
    MODULATION_INDEX,
    N_TONES,
    NSPS,
    SYMBOL_SEC,
    TONE_SPACING_HZ,
    Ft4Modulator,
    Ft4Receiver,
    aligned_tone_energies,
    gaussian_frequency_pulse,
)
from mbdsdr_ai.ft8_encode import pack77_standard  # noqa: E402

MESSAGE = pack77_standard("CQ", "BI1AAA", "OM74")
FRAME = np.asarray(build_frame(encode_ft4_tones(MESSAGE)), dtype=np.int8)
MODULATOR = Ft4Modulator()
RECEIVER = Ft4Receiver()


@pytest.fixture(scope="module")
def clean_wave():
    return MODULATOR.modulate(FRAME)


@pytest.fixture(scope="module")
def clean_decode(clean_wave):
    return RECEIVER.decode(clean_wave)


def test_qex_gfsk_parameters_and_sampling_choice():
    assert SYMBOL_SEC == pytest.approx(0.048)
    assert MODULATION_INDEX == pytest.approx(1.0)
    assert BT == pytest.approx(1.0)
    assert N_TONES == 4
    assert TONE_SPACING_HZ == pytest.approx(1.0 / 0.048)
    assert FS_HZ == 12_000
    assert NSPS == 576
    assert NSPS / FS_HZ == pytest.approx(SYMBOL_SEC)
    assert FRAME_SAMPLES == 105 * 576


def test_gaussian_pulse_matches_normalization_and_symmetry():
    t = np.linspace(-8.0 * SYMBOL_SEC, 8.0 * SYMBOL_SEC, 200_001)
    pulse = gaussian_frequency_pulse(t)
    area = trapezoid(pulse, t)
    assert area == pytest.approx(1.0, abs=1e-6)
    assert pulse.max() == pytest.approx(1.0 / SYMBOL_SEC, rel=1e-3)
    assert np.all(pulse >= 0.0)
    np.testing.assert_allclose(pulse, pulse[::-1], atol=1e-12, rtol=0.0)


def test_gfsk_continuous_unit_envelope_except_ramps(clean_wave):
    assert clean_wave.shape == (FRAME_SAMPLES,)
    assert clean_wave.dtype == np.complex128
    assert np.isfinite(clean_wave).all()
    np.testing.assert_allclose(
        np.abs(clean_wave[NSPS:-NSPS]), np.ones(FRAME_SAMPLES - 2 * NSPS),
        atol=1e-12, rtol=0.0,
    )
    assert abs(clean_wave[0]) == pytest.approx(0.0, abs=1e-15)
    assert abs(clean_wave[-1]) == pytest.approx(0.0, abs=1e-15)


def test_awgn_is_fixed_seed_reproducible():
    kwargs = {
        "freq_offset_hz": 5.0,
        "time_offset_samples": 17,
        "awgn_snr_db": 3.0,
        "noise_seed": 20261102,
    }
    first = MODULATOR.modulate(FRAME, **kwargs)
    second = MODULATOR.modulate(FRAME, **kwargs)
    np.testing.assert_array_equal(first, second)
    kwargs["noise_seed"] += 1
    third = MODULATOR.modulate(FRAME, **kwargs)
    assert not np.array_equal(first, third)


def test_aligned_soft_energy_recovers_all_105_frame_tones(clean_wave):
    energies = aligned_tone_energies(clean_wave, 0)
    assert energies.shape == (105, 4)
    assert np.isfinite(energies).all()
    assert np.all(energies >= 0.0)
    np.testing.assert_array_equal(np.argmax(energies, axis=1), FRAME)
    expected = energies[np.arange(105), FRAME]
    assert float(expected.mean()) > 0.5


def test_clean_audio_blind_sync_and_soft_decode(clean_decode):
    assert clean_decode["ok"] is True, clean_decode
    assert clean_decode["text"] == "CQ BI1AAA OM74"
    assert clean_decode["data_bits"] == MESSAGE
    assert clean_decode["parity_ok"] is True
    assert clean_decode["crc_ok"] is True
    sync = clean_decode["sync"]
    assert sync.synced is True
    assert abs(sync.time_offset_samples) <= 8
    assert abs(sync.base_freq_hz) <= 0.25


def test_all_four_costas_blocks_and_ramps_after_sync(clean_decode):
    symbols = clean_decode["sync"].symbols
    assert symbols.shape == (105,)
    assert symbols[0] == symbols[104] == 0
    for start, sequence in zip((1, 34, 67, 100), SYNC_SEQUENCES):
        np.testing.assert_array_equal(symbols[start:start + 4], sequence)


def test_time_offset_is_found_by_search_not_supplied():
    delay = 731
    wave = MODULATOR.modulate(FRAME, time_offset_samples=delay)
    capture = np.concatenate((wave, np.zeros(503, dtype=np.complex128)))
    result = RECEIVER.decode(capture)
    assert result["ok"] is True, result
    assert result["text"] == "CQ BI1AAA OM74"
    sync = result["sync"]
    assert sync.n_time_bins > 2
    assert abs(sync.time_offset_samples - delay) <= 8
    assert abs(sync.base_freq_hz) <= 0.25


@pytest.mark.parametrize("freq_offset", [17.3, -11.4])
def test_frequency_offset_is_found_by_two_dimensional_search(freq_offset):
    delay = 293
    wave = MODULATOR.modulate(
        FRAME, freq_offset_hz=freq_offset, time_offset_samples=delay
    )
    capture = np.concatenate((wave, np.zeros(509, dtype=np.complex128)))
    result = RECEIVER.decode(capture)
    assert result["ok"] is True, result
    assert result["text"] == "CQ BI1AAA OM74"
    sync = result["sync"]
    assert sync.n_freq_bins > 2
    assert abs(sync.base_freq_hz - freq_offset) <= 0.25
    assert abs(sync.time_offset_samples - delay) <= 8


def test_fixed_seed_awgn_audio_roundtrip():
    wave = MODULATOR.modulate(
        FRAME,
        freq_offset_hz=7.7,
        time_offset_samples=419,
        awgn_snr_db=3.0,
        noise_seed=20261102,
    )
    capture = np.concatenate((wave, np.zeros(503, dtype=np.complex128)))
    result = RECEIVER.decode(capture)
    assert result["ok"] is True, result
    assert result["text"] == "CQ BI1AAA OM74"
    assert result["data_bits"] == MESSAGE
    assert abs(result["sync"].base_freq_hz - 7.7) <= 0.25


def test_pure_noise_is_rejected_without_text():
    for seed in (11, 29, 47):
        rng = np.random.default_rng(seed)
        noise = (
            rng.standard_normal(FRAME_SAMPLES + 1200)
            + 1j * rng.standard_normal(FRAME_SAMPLES + 1200)
        ) / np.sqrt(2.0)
        result = RECEIVER.decode(noise)
        assert result["ok"] is False, (seed, result)
        assert result["rejected"] is True
        assert result["sync"].synced is False
        assert result["reject_reason"] in {"low_sync_ratio", "low_sync_contrast"}
        assert result["data_bits"] is None
        assert result["text"] is None


def test_short_and_zero_capture_are_honest_empty():
    short = RECEIVER.sync(np.zeros(100, dtype=np.complex128))
    assert short.synced is False
    assert short.reason == "short_capture"
    assert short.energies.shape == (0, 4)
    zero = RECEIVER.sync(np.zeros(FRAME_SAMPLES, dtype=np.complex128))
    assert zero.synced is False
    assert zero.reason == "no_signal"
    assert zero.symbols.size == 0


@pytest.mark.parametrize(
    "fs,nsps",
    [(0, 576), (12000, 0), (12000, 575), (12000.5, 576), (True, 576)],
)
def test_invalid_modulator_rates_raise(fs, nsps):
    with pytest.raises(ValueError):
        Ft4Modulator(fs=fs, nsps=nsps)


@pytest.mark.parametrize(
    "frame",
    [
        np.zeros(104, dtype=int),
        np.zeros((105, 1), dtype=int),
        np.concatenate((np.zeros(104, dtype=int), [4])),
        np.concatenate((np.zeros(104, dtype=int), [0.5])),
        np.concatenate((np.zeros(104, dtype=float), [np.nan])),
    ],
)
def test_invalid_frames_raise(frame):
    with pytest.raises(ValueError):
        MODULATOR.modulate(frame)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"time_offset_samples": -1},
        {"time_offset_samples": 1.5},
        {"freq_offset_hz": np.nan},
        {"freq_offset_hz": 6000.0},
        {"awgn_snr_db": np.inf},
    ],
)
def test_invalid_modulation_parameters_raise(kwargs):
    with pytest.raises(ValueError):
        MODULATOR.modulate(FRAME, **kwargs)


def test_aligned_energy_rejects_invalid_alignment_and_samples(clean_wave):
    with pytest.raises(ValueError):
        aligned_tone_energies(clean_wave, -1)
    with pytest.raises(ValueError):
        aligned_tone_energies(clean_wave, True)
    with pytest.raises(ValueError):
        aligned_tone_energies(clean_wave, 1)
    with pytest.raises(ValueError):
        aligned_tone_energies(clean_wave, 0, base_freq_hz=np.nan)
    with pytest.raises(ValueError):
        aligned_tone_energies(clean_wave.reshape(1, -1), 0)
    damaged = clean_wave.copy()
    damaged[10] = np.nan
    with pytest.raises(ValueError):
        aligned_tone_energies(damaged, 0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fs": 12000, "nsps": 500},
        {"freq_search_hz": (80.0, -80.0)},
        {"freq_search_hz": (0.0,)},
        {"coarse_freq_step_hz": 0.0},
        {"coarse_time_step_samples": 0},
        {"coarse_time_step_samples": 577},
        {"sync_ratio_threshold": 0.9},
        {"sync_contrast_threshold": 0.5},
    ],
)
def test_invalid_receiver_strategies_raise(kwargs):
    with pytest.raises(ValueError):
        Ft4Receiver(**kwargs)


@pytest.mark.parametrize("max_iter", [0, -1, 1.5, True])
def test_invalid_decode_iteration_limit_raises_before_search(clean_wave, max_iter):
    with pytest.raises(ValueError):
        RECEIVER.decode(clean_wave, max_iter=max_iter)
