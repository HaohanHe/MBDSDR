# SPDX-License-Identifier: MIT
"""经典 WSPR Type 1 音频前端的合成、软解调与受限搜索测试。"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mbdsdr_ai.wspr_codec import (  # noqa: E402
    SYNC_VECTOR,
    WsprMessage,
    encode_message,
    pack_message,
)
from mbdsdr_ai.wspr_modem import (  # noqa: E402
    REFERENCE_SAMPLES_PER_SYMBOL,
    REFERENCE_SAMPLE_RATE_HZ,
    WSPR_BAUD,
    WSPR_TONE_SPACING_HZ,
    WSPR_TRANSMISSION_SECONDS,
    decode_aligned,
    encode_audio,
    llrs_from_energies,
    search_and_decode,
    symbol_energies,
    sync_metric,
    synthesize_symbols,
)


CALLSIGN = "BI1AAA"
GRID = "FN20"
POWER = 37
MESSAGE = WsprMessage(CALLSIGN, GRID, POWER)
RATE = REFERENCE_SAMPLE_RATE_HZ
FRAME_SAMPLES = 162 * REFERENCE_SAMPLES_PER_SYMBOL
FREQUENCY_GRID = tuple(np.arange(-1.5, 1.51, 0.25))
TIME_GRID = tuple(np.arange(0.0, 1.01, 0.25))


def test_protocol_constants_and_unpadded_frame_length():
    assert WSPR_BAUD == pytest.approx(12000.0 / 8192.0)
    assert WSPR_TONE_SPACING_HZ == WSPR_BAUD
    assert WSPR_TRANSMISSION_SECONDS == pytest.approx(110.592)
    assert REFERENCE_SAMPLE_RATE_HZ == 375.0
    assert REFERENCE_SAMPLES_PER_SYMBOL == 256

    audio = encode_audio(CALLSIGN, GRID, POWER)
    assert audio.shape == (41472,)
    assert audio.shape == (FRAME_SAMPLES,)
    assert audio.size / RATE == pytest.approx(WSPR_TRANSMISSION_SECONDS)
    assert np.iscomplexobj(audio)


def test_silence_padding_changes_only_buffer_length():
    audio = encode_audio(
        CALLSIGN, GRID, POWER, leading_silence_seconds=0.5,
        trailing_silence_seconds=1.0,
    )
    leading = int(round(0.5 * RATE))
    trailing = int(round(1.0 * RATE))
    assert audio.size == leading + FRAME_SAMPLES + trailing
    assert np.count_nonzero(audio[:leading]) == 0
    assert np.count_nonzero(audio[-trailing:]) == 0
    assert np.count_nonzero(audio[leading:leading + FRAME_SAMPLES]) == FRAME_SAMPLES


def test_cpfsk_phase_increment_and_cross_symbol_continuity():
    symbols = encode_message(CALLSIGN, GRID, POWER)
    audio = synthesize_symbols(symbols)
    increments = np.angle(audio[1:] * np.conj(audio[:-1]))
    frequencies = (np.asarray(symbols, dtype=np.float64) - 1.5) * WSPR_TONE_SPACING_HZ
    expected = np.repeat(2.0 * np.pi * frequencies / RATE,
                         REFERENCE_SAMPLES_PER_SYMBOL)[:-1]
    # 期望数组也保留符号边界：相位不得在每个符号开头重置。
    assert increments == pytest.approx(expected, abs=2e-12)
    assert np.abs(audio) == pytest.approx(np.ones(audio.size))


def test_real_audio_is_exactly_complex_baseband_real_part():
    symbols = encode_message(CALLSIGN, GRID, POWER)
    common = dict(
        sample_rate_hz=RATE,
        center_frequency_hz=80.0,
        amplitude=0.7,
        leading_silence_seconds=0.25,
        trailing_silence_seconds=0.5,
    )
    analytic = synthesize_symbols(symbols, complex_baseband=True, **common)
    real = synthesize_symbols(symbols, complex_baseband=False, **common)
    assert np.isrealobj(real)
    assert real == pytest.approx(analytic.real, abs=1e-14)


@pytest.mark.parametrize("complex_baseband,center", [(True, 0.0), (False, 80.0)])
def test_aligned_audio_roundtrip(complex_baseband, center):
    audio = encode_audio(
        CALLSIGN, GRID, POWER, center_frequency_hz=center,
        complex_baseband=complex_baseband,
    )
    result = decode_aligned(audio, RATE, center)
    assert result is not None
    assert result.message == MESSAGE
    assert result.bits == tuple(pack_message(CALLSIGN, GRID, POWER))
    assert len(result.bits) == 50
    assert result.frequency_hz == center
    assert result.start_sample == 0
    assert result.time_offset_seconds == 0.0
    assert result.sync_metric > 0.99


def test_tone_energies_sync_and_llr_signs():
    symbols = encode_message(CALLSIGN, GRID, POWER)
    audio = synthesize_symbols(symbols)
    energies = symbol_energies(audio, RATE, 0.0)
    assert energies.shape == (162, 4)
    assert np.isfinite(energies).all()
    assert (energies >= 0.0).all()
    assert np.argmax(energies, axis=1).tolist() == symbols
    assert sync_metric(energies) == pytest.approx(1.0, abs=1e-12)

    llr = llrs_from_energies(energies)
    assert llr.shape == (162,)
    assert np.isfinite(llr).all()
    hard_data = (llr < 0.0).astype(np.int64)
    assert hard_data.tolist() == [symbol >> 1 for symbol in symbols]


def test_sync_selects_the_protocol_tone_pairs():
    energies = np.zeros((162, 4), dtype=np.float64)
    sync = np.asarray(SYNC_VECTOR)
    rows = np.arange(162)
    # 在 sync 指定的数据 0 音上放单位能量；其余三个音为零。
    energies[rows, np.where(sync == 0, 0, 1)] = 1.0
    llr = llrs_from_energies(energies)
    assert (llr > 0.0).all()
    assert sync_metric(energies) == pytest.approx(1.0)


def test_zero_energies_have_zero_sync_and_erasure_llrs():
    energies = np.zeros((162, 4), dtype=np.float64)
    assert sync_metric(energies) == 0.0
    assert llrs_from_energies(energies) == pytest.approx(np.zeros(162))


def test_grid_search_recovers_offset_with_seeded_moderate_awgn():
    true_frequency = 0.75
    leading_seconds = 0.5
    clean = encode_audio(
        CALLSIGN, GRID, POWER,
        center_frequency_hz=true_frequency,
        leading_silence_seconds=leading_seconds,
        trailing_silence_seconds=1.0,
    )
    rng = np.random.default_rng(20261102)
    noise = 0.03 * (rng.standard_normal(clean.size)
                    + 1j * rng.standard_normal(clean.size))

    # 搜索器只获得公开网格，没有获得 true_frequency 或真实起点。
    result = search_and_decode(
        clean + noise,
        RATE,
        nominal_center_frequency_hz=0.0,
        frequency_offsets_hz=FREQUENCY_GRID,
        time_offsets_seconds=TIME_GRID,
    )
    assert result is not None
    assert result.message == MESSAGE
    assert result.frequency_hz == pytest.approx(0.75)
    expected_start = int(round(leading_seconds * RATE))
    assert result.start_sample == expected_start
    assert result.time_offset_seconds == pytest.approx(expected_start / RATE)
    assert result.sync_metric > 0.35


def test_seeded_noise_only_buffer_returns_none_on_same_grid():
    size = FRAME_SAMPLES + int(round(1.5 * RATE))
    rng = np.random.default_rng(20261102)
    noise = 0.03 * (rng.standard_normal(size) + 1j * rng.standard_normal(size))
    assert search_and_decode(
        noise, RATE,
        frequency_offsets_hz=FREQUENCY_GRID,
        time_offsets_seconds=TIME_GRID,
    ) is None


def test_zero_audio_is_an_honest_empty_result():
    audio = np.zeros(FRAME_SAMPLES + int(round(1.5 * RATE)), dtype=np.complex128)
    assert search_and_decode(
        audio, RATE,
        frequency_offsets_hz=FREQUENCY_GRID,
        time_offsets_seconds=TIME_GRID,
    ) is None
    assert decode_aligned(audio, RATE, 0.0) is None


@pytest.mark.parametrize("bad_audio", [
    np.zeros((2, 3)),
    np.array([]),
    np.array(["not-audio"] * FRAME_SAMPLES),
    np.full(FRAME_SAMPLES, np.nan),
    np.full(FRAME_SAMPLES, np.inf),
    np.full(FRAME_SAMPLES, -np.inf),
    np.full(FRAME_SAMPLES, np.nan + 0j),
])
def test_invalid_audio_rejected(bad_audio):
    with pytest.raises(ValueError):
        symbol_energies(bad_audio, RATE, 0.0)


def test_incomplete_frame_and_invalid_start_rejected():
    with pytest.raises(ValueError):
        symbol_energies(np.zeros(FRAME_SAMPLES - 1), RATE, 0.0)
    audio = np.zeros(FRAME_SAMPLES + 1)
    with pytest.raises(ValueError):
        decode_aligned(audio, RATE, 0.0, start_sample=-1)
    with pytest.raises(ValueError):
        decode_aligned(audio, RATE, 0.0, start_sample=0.5)


@pytest.mark.parametrize("bad_symbols", [
    [0] * 161,
    [0] * 163,
    [[0] * 162],
    [0.0] * 162,
    [True] * 162,
    ["0"] * 162,
    [0] * 161 + [-1],
    [0] * 161 + [4],
    "0" * 162,
])
def test_invalid_symbols_rejected(bad_symbols):
    with pytest.raises(ValueError):
        synthesize_symbols(bad_symbols)


@pytest.mark.parametrize("rate", [0.0, -375.0, 8000.0, math.nan, math.inf, "375"])
def test_invalid_or_nonintegral_sample_rate_rejected(rate):
    with pytest.raises(ValueError):
        synthesize_symbols(encode_message(CALLSIGN, GRID, POWER), sample_rate_hz=rate)


@pytest.mark.parametrize("center,complex_baseband", [
    (-0.1, False),
    (RATE / 2, False),
    (RATE / 2, True),
    (-RATE / 2, True),
    (math.nan, True),
    (math.inf, False),
])
def test_invalid_center_frequency_rejected(center, complex_baseband):
    with pytest.raises(ValueError):
        synthesize_symbols(
            encode_message(CALLSIGN, GRID, POWER),
            center_frequency_hz=center,
            complex_baseband=complex_baseband,
        )


@pytest.mark.parametrize("amplitude", [0.0, -1.0, math.nan, math.inf])
def test_invalid_amplitude_rejected(amplitude):
    with pytest.raises(ValueError):
        synthesize_symbols(encode_message(CALLSIGN, GRID, POWER), amplitude=amplitude)


@pytest.mark.parametrize("seconds", [-0.1, math.nan, math.inf])
def test_invalid_silence_duration_rejected(seconds):
    with pytest.raises(ValueError):
        synthesize_symbols(
            encode_message(CALLSIGN, GRID, POWER),
            leading_silence_seconds=seconds,
        )


@pytest.mark.parametrize("bad_energies", [
    np.zeros((161, 4)),
    np.zeros((162, 3)),
    np.zeros(162 * 4),
    np.full((162, 4), np.nan),
    np.full((162, 4), np.inf),
    np.pad(np.zeros((161, 4)), ((0, 1), (0, 0)), constant_values=-1.0),
    np.array([["bad"] * 4] * 162),
])
def test_invalid_energy_arrays_rejected(bad_energies):
    with pytest.raises(ValueError):
        sync_metric(bad_energies)
    with pytest.raises(ValueError):
        llrs_from_energies(bad_energies)


@pytest.mark.parametrize("threshold", [-1.01, 1.01, math.nan, math.inf])
def test_invalid_sync_threshold_rejected(threshold):
    audio = np.zeros(FRAME_SAMPLES)
    with pytest.raises(ValueError):
        decode_aligned(audio, RATE, 0.0, min_sync_metric=threshold)
    with pytest.raises(ValueError):
        search_and_decode(audio, RATE, min_sync_metric=threshold)


@pytest.mark.parametrize("budget", [0, -1, 1.5, True])
def test_invalid_decode_budgets_rejected(budget):
    audio = np.zeros(FRAME_SAMPLES)
    with pytest.raises(ValueError):
        decode_aligned(audio, RATE, 0.0, max_fec_expansions=budget)
    with pytest.raises(ValueError):
        search_and_decode(audio, RATE, max_decode_candidates=budget)
    with pytest.raises(ValueError):
        search_and_decode(audio, RATE, max_fec_expansions=budget)


@pytest.mark.parametrize("kwargs", [
    {"frequency_offsets_hz": []},
    {"frequency_offsets_hz": [0.0, math.nan]},
    {"frequency_offsets_hz": "0"},
    {"time_offsets_seconds": []},
    {"time_offsets_seconds": [-0.25]},
    {"time_offsets_seconds": [0.0, math.inf]},
    {"time_offsets_seconds": 0.0},
])
def test_invalid_search_grids_rejected(kwargs):
    with pytest.raises(ValueError):
        search_and_decode(np.zeros(FRAME_SAMPLES), RATE, **kwargs)
