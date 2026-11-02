# SPDX-License-Identifier: MIT
"""DVB-S2 LDPC tests against independently generated FECFRAME vectors."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from mbdsdr_ai.dvbs2_ldpc import (  # noqa: E402
    decode_ldpc,
    encode_ldpc,
    ldpc_modes,
    ldpc_parameters,
    ldpc_syndrome,
)

VECTOR_PATH = WORKSPACE_ROOT / "standards" / "etsi-en-302-307-1" / "dvbs2_ldpc_vectors.json"
VECTOR_SHA256 = "d614c3572136938c556cac458f78873658c753073686c0e95b0b94cdb30c637f"


def _vectors():
    if hashlib.sha256(VECTOR_PATH.read_bytes()).hexdigest() != VECTOR_SHA256:
        raise ValueError("independent DVB-S2 vector identity changed")
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))["vectors"]


VECTORS = _vectors()


def _bits(payload: str) -> np.ndarray:
    return np.unpackbits(np.frombuffer(base64.b64decode(payload), dtype=np.uint8))


@pytest.mark.parametrize("case", VECTORS, ids=lambda case: case["id"])
def test_independent_fecframe_vectors_match_bit_for_bit(case):
    frame, rate = case["frame_type"], case["rate"]
    parameters = ldpc_parameters(frame, rate)
    info = _bits(case["input_base64_msb"])
    expected = _bits(case["fecframe_base64_msb"])
    assert parameters["k"] == case["kldpc_nbch"] == info.size
    assert parameters["n"] == case["nldpc"] == expected.size
    assert parameters["standard_table"] == case["standard_table"]
    actual = encode_ldpc(info, frame, rate)
    assert actual.dtype == np.uint8
    np.testing.assert_array_equal(actual, expected)
    assert not np.any(ldpc_syndrome(actual, frame, rate))


def test_exactly_the_standard_normal_and_short_modes_are_loaded():
    normal_rates = ("1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10")
    expected = {("normal", rate) for rate in normal_rates}
    expected |= {("short", rate) for rate in normal_rates[:-1]}
    assert set(ldpc_modes()) == expected
    assert len(ldpc_modes()) == len(expected)
    for frame, rate in expected:
        parameters = ldpc_parameters(frame, rate)
        assert parameters["n"] == (64800 if frame == "normal" else 16200)
        assert parameters["parity_bits"] == 360 * parameters["q"]
        assert parameters["row_count"] == parameters["k"] // 360


@pytest.mark.parametrize("frame,rate", [("medium", "1/2"), ("normal", "1/7"), ("short", "9/10")])
def test_unknown_mode_is_rejected(frame, rate):
    with pytest.raises(ValueError, match="unsupported"):
        encode_ldpc([], frame, rate)


@pytest.mark.parametrize("frame,rate", [("normal", "1/2"), ("short", "1/4")])
def test_binary_input_validation(frame, rate):
    k = ldpc_parameters(frame, rate)["k"]
    with pytest.raises(ValueError, match=str(k)):
        encode_ldpc(np.zeros(k - 1, dtype=np.uint8), frame, rate)
    with pytest.raises(ValueError, match="one-dimensional"):
        encode_ldpc(np.zeros((k, 1), dtype=np.uint8), frame, rate)
    invalid = np.zeros(k, dtype=np.uint8)
    invalid[k // 2] = 2
    with pytest.raises(ValueError, match="only 0 and 1"):
        encode_ldpc(invalid, frame, rate)
    with pytest.raises(TypeError, match="numeric"):
        encode_ldpc(["0"] * k, frame, rate)


@pytest.mark.parametrize("frame,rate", [("normal", "1/2"), ("short", "1/4")])
def test_clean_llr_decodes_without_message_passing(frame, rate):
    rng = np.random.default_rng(20261102)
    k = ldpc_parameters(frame, rate)["k"]
    info = rng.integers(0, 2, size=k, dtype=np.uint8)
    codeword = encode_ldpc(info, frame, rate)
    llr = np.where(codeword == 0, 12.0, -12.0)
    result = decode_ldpc(llr, frame, rate)
    assert result["converged"] is True
    assert result["reason"] == "converged"
    assert result["iterations"] == 0
    assert result["unsatisfied"] == 0
    np.testing.assert_array_equal(result["codeword"], codeword)
    np.testing.assert_array_equal(result["info"], info)


@pytest.mark.parametrize("frame,rate", [("normal", "1/2"), ("short", "1/4")])
def test_fixed_seed_gaussian_soft_decode(frame, rate):
    rng = np.random.default_rng(20261103)
    parameters = ldpc_parameters(frame, rate)
    info = rng.integers(0, 2, size=parameters["k"], dtype=np.uint8)
    codeword = encode_ldpc(info, frame, rate)
    llr = np.where(codeword == 0, 3.0, -3.0) + rng.normal(
        0.0, 1.0, size=parameters["n"]
    )
    assert np.count_nonzero((llr < 0).astype(np.uint8) != codeword) > 0
    started = time.perf_counter()
    result = decode_ldpc(llr, frame, rate, max_iterations=50, alpha=0.8)
    elapsed = time.perf_counter() - started
    print(
        f"LDPC {frame} {rate} Gaussian decode: {elapsed:.3f}s, "
        f"iterations={result['iterations']}, converged={result['converged']}"
    )
    assert result["converged"] is True, result["reason"]
    assert result["reason"] == "converged"
    assert result["unsatisfied"] == 0
    assert 1 <= result["iterations"] <= 50
    np.testing.assert_array_equal(result["codeword"], codeword)
    np.testing.assert_array_equal(result["info"], info)
    assert not np.any(ldpc_syndrome(result["codeword"], frame, rate))


def test_iteration_limit_reports_nonconvergence_and_sparse_checks_match():
    frame, rate = "normal", "1/2"
    rng = np.random.default_rng(20261104)
    llr = rng.normal(0.0, 1.0, size=ldpc_parameters(frame, rate)["n"])
    result = decode_ldpc(llr, frame, rate, max_iterations=3)
    assert set(result) == {
        "converged", "codeword", "info", "iterations", "unsatisfied", "reason"
    }
    assert result["converged"] is False
    assert result["reason"] == "max_iterations"
    assert result["iterations"] == 3
    assert result["unsatisfied"] > 0
    syndrome = ldpc_syndrome(result["codeword"], frame, rate)
    assert np.count_nonzero(syndrome) == result["unsatisfied"]


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_iteration_limit_is_rejected(value):
    with pytest.raises(ValueError, match="max_iterations"):
        decode_ldpc(np.zeros(16200), "short", "1/4", max_iterations=value)


@pytest.mark.parametrize("value", [0.0, -0.1, 1.1, np.nan, np.inf, True, "bad"])
def test_invalid_normalization_is_rejected(value):
    with pytest.raises(ValueError, match="alpha"):
        decode_ldpc(np.zeros(16200), "short", "1/4", alpha=value)


def test_invalid_llr_shape_length_and_nonfinite_values_are_rejected():
    n = ldpc_parameters("short", "1/4")["n"]
    with pytest.raises(ValueError, match=str(n)):
        decode_ldpc(np.zeros(n - 1), "short", "1/4")
    with pytest.raises(ValueError, match="one-dimensional"):
        decode_ldpc(np.zeros((n, 1)), "short", "1/4")
    for value in (np.nan, np.inf, -np.inf):
        llr = np.zeros(n)
        llr[n // 2] = value
        with pytest.raises(ValueError, match="finite"):
            decode_ldpc(llr, "short", "1/4")
    with pytest.raises(ValueError, match="numeric"):
        decode_ldpc(["bad"] * n, "short", "1/4")
