# SPDX-License-Identifier: MIT
"""Self-consistency tests for the DVB-S2 BCH/LDPC concatenated chain."""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.dvbs2_bch import (
    bch_modes,
    bch_parameters,
    bch_syndrome,
    decode_bch,
    decode_dvbs2_fec,
    encode_bch,
    encode_dvbs2_fec,
)
from mbdsdr_ai.dvbs2_ldpc import encode_ldpc


def _clean_llr(word: np.ndarray) -> np.ndarray:
    # Module-wide LLR convention: positive favors zero, negative favors one.
    return (1.0 - 2.0 * word.astype(np.float64)) * 4.0


@pytest.mark.parametrize("frame,rate", bch_modes())
def test_clean_cascade_every_mode(frame: str, rate: str) -> None:
    parameters = bch_parameters(frame, rate)
    message = np.zeros(parameters["K_bch"], dtype=np.uint8)
    message[::193] = 1
    message[-1] ^= 1
    word = encode_dvbs2_fec(message, frame, rate)
    assert np.array_equal(word, encode_ldpc(encode_bch(message, frame, rate), frame, rate))

    result = decode_dvbs2_fec(_clean_llr(word), frame, rate)
    assert result["success"] is True
    assert result["uncorrectable"] is False
    assert result["corrected"] is False
    assert result["n_errors"] == 0
    assert result["reason"] == "no_errors"
    assert result["ldpc"]["converged"] is True
    assert result["ldpc"]["iterations"] == 0
    assert result["ldpc"]["unsatisfied"] == 0
    assert np.array_equal(result["bbframe"], message)
    assert np.array_equal(result["codeword"], encode_bch(message, frame, rate))


@pytest.mark.parametrize("frame,rate", (("normal", "9/10"), ("normal", "2/3"), ("short", "1/4")))
def test_converged_ldpc_then_bch_corrects_t(frame: str, rate: str) -> None:
    parameters = bch_parameters(frame, rate)
    rng = np.random.default_rng(91012 + parameters["bch_t"])
    message = rng.integers(0, 2, parameters["K_bch"], dtype=np.uint8)
    inner = encode_bch(message, frame, rate)
    damaged_inner = inner.copy()
    positions = np.linspace(0, parameters["N_bch"] - 1, parameters["bch_t"], dtype=np.int64)
    damaged_inner[positions] ^= 1

    # The LDPC word is already valid, so this isolates and verifies the
    # required stage order: zero-iteration LDPC, then BCH error correction.
    outer = encode_ldpc(damaged_inner, frame, rate)
    result = decode_dvbs2_fec(_clean_llr(outer), frame, rate)
    assert result["ldpc"]["converged"] is True
    assert result["ldpc"]["iterations"] == 0
    assert result["success"] is True
    assert result["corrected"] is True
    assert result["n_errors"] == parameters["bch_t"]
    assert result["reason"] == "corrected"
    assert np.array_equal(result["codeword"], inner)
    assert np.array_equal(result["bbframe"], message)


def test_fixed_soft_noise_cascade() -> None:
    frame, rate = "short", "1/4"
    rng = np.random.default_rng(51024)
    parameters = bch_parameters(frame, rate)
    message = rng.integers(0, 2, parameters["K_bch"], dtype=np.uint8)
    word = encode_dvbs2_fec(message, frame, rate)
    llr = (1.0 - 2.0 * word.astype(np.float64)) * 2.0
    llr += rng.normal(0.0, 0.8, word.size)
    assert np.count_nonzero((llr < 0.0).astype(np.uint8) != word) > 0

    result = decode_dvbs2_fec(llr, frame, rate)
    assert result["ldpc"]["converged"] is True
    assert result["ldpc"]["iterations"] > 0
    assert result["success"] is True
    assert np.array_equal(result["bbframe"], message)
    assert not np.any(bch_syndrome(result["codeword"], frame, rate))


def test_ldpc_exhaustion_returns_no_bch_candidate() -> None:
    frame, rate = "short", "1/4"
    parameters = bch_parameters(frame, rate)
    indices = np.arange(parameters["n_ldpc"])
    alternating_llr = np.where(indices % 2, -4.0, 4.0)
    result = decode_dvbs2_fec(
        alternating_llr,
        frame,
        rate,
        max_iterations=1,
    )
    assert result["ldpc"]["converged"] is False
    assert result["ldpc"]["reason"] == "max_iterations"
    assert result["success"] is False
    assert result["uncorrectable"] is True
    assert result["codeword"] is None
    assert result["bbframe"] is None
    assert result["n_errors"] is None
    assert result["corrected"] is False
    assert result["reason"] == "ldpc_max_iterations"


def test_valid_ldpc_does_not_hide_bch_failure() -> None:
    frame, rate = "short", "1/4"
    parameters = bch_parameters(frame, rate)
    rng = np.random.default_rng(44112)
    # A valid LDPC layer can carry a word that is not a correctable BCH word.
    invalid_inner = rng.integers(0, 2, parameters["N_bch"], dtype=np.uint8)
    direct = decode_bch(invalid_inner, frame, rate)
    assert direct["success"] is False
    outer = encode_ldpc(invalid_inner, frame, rate)

    result = decode_dvbs2_fec(_clean_llr(outer), frame, rate)
    assert result["ldpc"]["converged"] is True
    assert result["ldpc"]["iterations"] == 0
    assert result["success"] is False
    assert result["uncorrectable"] is True
    assert result["codeword"] is None
    assert result["bbframe"] is None
    assert result["reason"] == direct["reason"]


def test_cascade_rejects_unavailable_short_mode() -> None:
    with pytest.raises(ValueError, match="unsupported DVB-S2 BCH mode"):
        encode_dvbs2_fec([], "short", "9/10")
    with pytest.raises(ValueError, match="unsupported DVB-S2 BCH mode"):
        decode_dvbs2_fec([], "short", "9/10")


# ── Task 2.4：FY-4 剖面驱动的级联路（MODCOD 只从配置注入） ──────
@pytest.mark.parametrize("profile_name", ("fy4_a_lrit", "fy4_a_hrit23"))
def test_fy4_profile_drives_cascade(profile_name: str) -> None:
    """卫星参数库给 MODCOD/符号率，代码侧不出现 FY-4 数字。

    链路：BBFrAME（前 32 bit 为两次遥测字）→ BCH → LDPC → 注入 BCH
    可纠错数 → LDPC 收敛 → BCH 纠错 → 位级与字段级都回到原值。
    """
    from mbdsdr_ai.dvbs2_pl import MODCOD_TABLE
    from mbdsdr_ai.sat_profile import load_sat_profile

    profile = load_sat_profile(profile_name)
    downlink = profile["downlink"]
    assert downlink["modulation"] == "dvbs2_qpsk"
    mode = MODCOD_TABLE[int(downlink["modcod"])]
    assert mode.modulation == "QPSK"
    frame, rate = "normal", mode.code_rate

    parameters = bch_parameters(frame, rate)
    rng = np.random.default_rng(20261117 + int(downlink["modcod"]))
    message = rng.integers(0, 2, parameters["K_bch"], dtype=np.uint8)
    counter, voltage_mv = 0x1234, 7500
    message[:16] = [(counter >> (15 - i)) & 1 for i in range(16)]
    message[16:32] = [(voltage_mv >> (15 - i)) & 1 for i in range(16)]

    inner = encode_bch(message, frame, rate)
    damaged = inner.copy()
    positions = np.linspace(0, parameters["N_bch"] - 1,
                            parameters["bch_t"], dtype=np.int64)
    damaged[positions] ^= 1
    outer = encode_ldpc(damaged, frame, rate)

    result = decode_dvbs2_fec(_clean_llr(outer), frame, rate)
    assert result["ldpc"]["converged"] is True
    assert result["success"] is True
    assert result["corrected"] is True
    assert result["n_errors"] == parameters["bch_t"]
    assert np.array_equal(result["bbframe"], message)

    bits = [int(b) for b in result["bbframe"][:32]]
    got_counter = int("".join(str(b) for b in bits[:16]), 2)
    got_voltage = int("".join(str(b) for b in bits[16:32]), 2)
    assert (got_counter, got_voltage) == (counter, voltage_mv)
