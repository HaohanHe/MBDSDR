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
