# SPDX-License-Identifier: MIT
"""FT4 位/符号/软能量层确定性测试。

官方常量直接对拍 ARRL 附件验证脚本；协议布局依据 2020 QEX PDF 第 2、3、
10 页。这里不伪造音频同步结果，软能量均显式注入真实编解码链。
"""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from mbdsdr_ai.ft4_codec import (  # noqa: E402
    CODEWORD_BITS,
    DATA_POSITIONS,
    DATA_SYMBOLS,
    FRAME_SYMBOLS,
    GRAY_MAP,
    SYNC_SEQUENCES,
    WHITENING_BITS,
    build_frame,
    codeword_parity_ok,
    codeword_to_data_tones,
    decode_ft4_payload,
    encode_ft4_tones,
    extract_data_tones,
    message_to_codeword,
    soft_tones_to_llr,
    unwhiten_message,
    whiten_message,
)
from mbdsdr_ai.ft8_encode import (  # noqa: E402
    add_crc14,
    ldpc_encode_174_91,
    pack77_standard,
)


def _clean_energies(tones, peak=8.0):
    energies = np.zeros((DATA_SYMBOLS, 4), dtype=np.float64)
    for symbol, tone in enumerate(tones):
        energies[symbol, tone] = peak
    return energies


def test_official_constants_crosscheck_script_passes():
    root = WORKSPACE_ROOT / "standards" / "wsjt"
    script = root / "verify_sources.py"
    if not script.is_file():
        pytest.skip("workspace standards/wsjt crosscheck harness is not provisioned")
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=WORKSPACE_ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    record = json.loads((root / "verification.json").read_text(encoding="utf-8"))
    assert record["status"] == "verified"
    checks = record["ft4"]
    for key in (
        "official_generator_satisfies_parity_checks",
        "repository_tables_match_official_parity_exactly",
        "repository_mn_checks_match_official_parity_exactly",
        "repository_generator_hex_rows_match_official_generator_exactly",
        "ft8_codec_encode_matches_official_generator_bitwise",
        "ft8_encode_ldpc_matches_official_generator_bitwise",
    ):
        assert checks[key] is True, key
    assert checks["encoder_comparison_messages"] >= 2


def test_whitening_sequence_matches_qex_and_is_involutive():
    qex_rows = (
        "0100101001011110100010"
        "0110110100101100001000"
        "1010011110010101010110"
        "11111000101"
    )
    assert "".join(str(bit) for bit in WHITENING_BITS) == qex_rows
    assert whiten_message([0] * 77) == list(WHITENING_BITS)
    message = pack77_standard("CQ", "BI1AAA", "OM74")
    whitened = whiten_message(message)
    assert whitened != message
    assert unwhiten_message(whitened) == message


def test_crc_is_computed_after_whitening():
    message = pack77_standard("CQ", "BI1AAA", "OM74")
    codeword = message_to_codeword(message)
    assert len(codeword) == CODEWORD_BITS
    assert codeword[:77] == whiten_message(message)
    assert codeword[:91] == add_crc14(whiten_message(message))
    assert codeword_parity_ok(codeword)
    damaged = codeword.copy()
    damaged[123] ^= 1
    assert not codeword_parity_ok(damaged)


@pytest.mark.parametrize("word,tone", [(0, 0), (1, 1), (2, 3), (3, 2)])
def test_all_gray_words_and_soft_llr_signs(word, tone):
    assert GRAY_MAP[word] == tone
    pair = [(word >> 1) & 1, word & 1]
    codeword = pair * DATA_SYMBOLS
    assert codeword_to_data_tones(codeword) == [tone] * DATA_SYMBOLS
    llr = np.asarray(soft_tones_to_llr(_clean_energies([tone] * DATA_SYMBOLS)))
    expected = np.asarray(
        [8.0 if bit == 0 else -8.0 for bit in pair] * DATA_SYMBOLS
    )
    np.testing.assert_array_equal(llr, expected)


def test_frame_sync_ramp_and_data_positions():
    data = [(3 * index + 1) % 4 for index in range(DATA_SYMBOLS)]
    frame = build_frame(data)
    assert len(frame) == FRAME_SYMBOLS
    assert frame[0] == frame[104] == 0
    assert frame[1:5] == list(SYNC_SEQUENCES[0])
    assert frame[34:38] == list(SYNC_SEQUENCES[1])
    assert frame[67:71] == list(SYNC_SEQUENCES[2])
    assert frame[100:104] == list(SYNC_SEQUENCES[3])
    assert DATA_POSITIONS == (
        tuple(range(5, 34)) + tuple(range(38, 67)) + tuple(range(71, 100))
    )
    assert extract_data_tones(frame) == data


def test_clean_end_to_end_roundtrip_and_unpack():
    message = pack77_standard("CQ", "BI1AAA", "OM74")
    tones = encode_ft4_tones(message)
    result = decode_ft4_payload(_clean_energies(tones))
    assert result["ok"] is True, result
    assert result["rejected"] is False
    assert result["reject_reason"] == ""
    assert result["parity_ok"] is True
    assert result["crc_ok"] is True
    assert result["data_bits"] == message
    assert result["text"] == "CQ BI1AAA OM74"


def test_fixed_seed_soft_perturbation_roundtrip():
    message = pack77_standard("CQ", "BI1AAA", "R-07")
    tones = encode_ft4_tones(message)
    rng = np.random.default_rng(20261102)
    energies = _clean_energies(tones, peak=6.0) + rng.uniform(
        0.0, 0.75, size=(DATA_SYMBOLS, 4)
    )
    result = decode_ft4_payload(energies)
    assert result["ok"] is True, result
    assert result["data_bits"] == message
    assert result["text"] == "CQ BI1AAA R-07"


def test_valid_ldpc_with_bad_crc_is_rejected():
    message = pack77_standard("CQ", "BI1AAA", "OM74")
    whitened = whiten_message(message)
    bits91 = add_crc14(whitened)
    bits91[80] ^= 1
    codeword = ldpc_encode_174_91(bits91)
    assert codeword_parity_ok(codeword)
    result = decode_ft4_payload(_clean_energies(codeword_to_data_tones(codeword)))
    assert result["ok"] is False
    assert result["rejected"] is True
    assert result["parity_ok"] is True
    assert result["crc_ok"] is False
    assert result["reject_reason"] == "crc14"
    assert result["data_bits"] is None
    assert result["unpacked"] is None
    assert result["text"] is None


def test_pure_noise_honest_failure():
    for seed in (11, 29, 47, 83):
        rng = np.random.default_rng(seed)
        result = decode_ft4_payload(rng.random((DATA_SYMBOLS, 4)))
        assert result["ok"] is False, (seed, result)
        assert result["rejected"] is True
        assert result["reject_reason"] in {"ldpc_parity", "crc14"}
        assert result["data_bits"] is None
        assert result["text"] is None


def test_zero_energy_is_not_a_zero_message_decode():
    result = decode_ft4_payload(np.zeros((DATA_SYMBOLS, 4)))
    assert result["ok"] is False
    assert result["reject_reason"] == "no_energy"
    assert result["iters"] == 0
    assert result["codeword"] is None


@pytest.mark.parametrize(
    "bad",
    [
        [0] * 76,
        np.zeros((77, 1)),
        [0] * 76 + [2],
        [0] * 76 + [np.nan],
    ],
)
def test_invalid_message_bits_raise(bad):
    with pytest.raises(ValueError):
        whiten_message(bad)


@pytest.mark.parametrize(
    "bad",
    [
        [0] * 86,
        [0] * 86 + [4],
        [0] * 86 + [0.5],
        [0] * 86 + [np.inf],
    ],
)
def test_invalid_data_tones_raise(bad):
    with pytest.raises(ValueError):
        build_frame(bad)


def test_invalid_codeword_shape_and_values_raise():
    with pytest.raises(ValueError):
        codeword_to_data_tones([0] * 173)
    with pytest.raises(ValueError):
        codeword_to_data_tones([0] * 173 + [-1])


@pytest.mark.parametrize(
    "bad",
    [
        np.zeros((86, 4)),
        np.zeros((87, 5)),
        np.zeros((87, 4, 1)),
        [[0.0, 0.0, 0.0] for _ in range(87)],
    ],
)
def test_invalid_energy_shapes_raise(bad):
    with pytest.raises(ValueError):
        decode_ft4_payload(bad)


@pytest.mark.parametrize("value", [-1.0, np.nan, np.inf, -np.inf])
def test_invalid_energy_values_raise(value):
    energies = np.ones((DATA_SYMBOLS, 4))
    energies[17, 2] = value
    with pytest.raises(ValueError):
        decode_ft4_payload(energies)


@pytest.mark.parametrize("max_iter", [0, -1, 1.5, True, "30"])
def test_invalid_iteration_limits_raise(max_iter):
    with pytest.raises(ValueError):
        decode_ft4_payload(np.ones((DATA_SYMBOLS, 4)), max_iter=max_iter)
