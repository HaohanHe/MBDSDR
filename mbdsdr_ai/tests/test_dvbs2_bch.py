# SPDX-License-Identifier: MIT
"""Mathematical and behavioral tests for DVB-S2 shortened BCH.

No expected codeword in this file comes from the production BCH encoder.
Systematic encoding expectations use independent GF(2) polynomial division.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from mbdsdr_ai.dvbs2_bch import (
    bch_modes,
    bch_parameters,
    bch_syndrome,
    decode_bch,
    encode_bch,
)
from mbdsdr_ai.dvbs2_ldpc import ldpc_parameters

_RUNTIME_PATH = Path(__file__).resolve().parents[1] / "data" / "dvbs2_bch.json"
_EXTRACT_PATH = (
    Path(__file__).resolve().parents[3]
    / "standards"
    / "etsi-en-302-307-1"
    / "dvbs2_fec_parameters.json"
)
_KAT_PATH = _EXTRACT_PATH.with_name("dvbs2_bch_vectors.json")
_KAT_SHA256 = "d7764e85634bdacfc77c2ef9a4c00b3c8ad03dba888d92ee3153a3a4b313e03e"


def _load_independent_kat() -> dict | None:
    if not _KAT_PATH.is_file():
        return None
    raw = _KAT_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _KAT_SHA256:
        raise ValueError("independent DVB-S2 BCH KAT identity changed")
    document = json.loads(raw)
    if document.get("schema") != "dvbs2-bch-independent-kat-v1":
        raise ValueError("unsupported independent DVB-S2 BCH KAT schema")
    sources = document.get("sources", {})
    if (
        sources.get("encoder", {}).get("project") != "drmpeg/gr-dvbs2"
        or sources.get("encoder", {}).get("commit") != "99a2ac5592fa41e9c0f8f757995f3451ab8184a6"
        or sources.get("decoder", {}).get("project") != "drmpeg/gr-dvbs2rx"
        or sources.get("decoder", {}).get("commit") != "74df1dadbd20b02ce0cdfd3244f9c97022981235"
    ):
        raise ValueError("independent DVB-S2 BCH KAT provenance changed")
    if len(document.get("vectors", [])) != 21:
        raise ValueError("independent DVB-S2 BCH KAT must contain all 21 modes")
    return document


_KAT_DOCUMENT = _load_independent_kat()
_KAT_SKIP = pytest.mark.skipif(
    _KAT_DOCUMENT is None,
    reason="external independent DVB-S2 BCH KAT is not available",
)
_KAT_CASES = (
    _KAT_DOCUMENT["vectors"]
    if _KAT_DOCUMENT is not None
    else [
        pytest.param(
            None,
            id="missing-external-kat",
            marks=pytest.mark.skip(reason="external independent DVB-S2 BCH KAT is not available"),
        )
    ]
)
_RUNTIME = json.loads(_RUNTIME_PATH.read_text(encoding="utf-8"))
_MODES = bch_modes()
_REPRESENTATIVE_MODES = (("normal", "9/10"), ("normal", "2/3"), ("short", "1/4"))
_CODE_KEYS = (
    "rate",
    "K_bch",
    "N_bch",
    "k_ldpc",
    "n_ldpc",
    "bch_t",
    "bch_m",
    "bch_parity_bits",
    "generator_polynomial_ids",
    "source",
)


def _gf2_multiply(left: int, right: int) -> int:
    product = 0
    while left:
        lowest = left & -left
        product ^= right << (lowest.bit_length() - 1)
        left ^= lowest
    return product


def _generator(frame: str, rate: str) -> int:
    family = _RUNTIME["families"][frame]
    code = next(item for item in family["codes"] if item["rate"] == rate)
    generator = 1
    for polynomial in family["polynomials"][:code["bch_t"]]:
        generator = _gf2_multiply(generator, polynomial["bitmask"])
    return generator


def _reference_systematic_encode(message: np.ndarray, generator: int) -> np.ndarray:
    """Independent, direct polynomial long division, high degree first."""
    degree = generator.bit_length() - 1
    divisor = np.asarray(
        [(generator >> shift) & 1 for shift in range(degree, -1, -1)],
        dtype=np.uint8,
    )
    work = np.zeros(message.size + degree, dtype=np.uint8)
    work[:message.size] = message
    for index in range(message.size):
        if work[index]:
            work[index:index + degree + 1] ^= divisor
    assert not np.any(work[:message.size])
    return np.concatenate((message, work[message.size:]))


def _reference_powers(frame: str) -> tuple[int, list[int]]:
    family = _RUNTIME["families"][frame]
    m = family["bch_m"]
    size = 1 << m
    order = size - 1
    primitive = family["polynomials"][0]["bitmask"]
    powers = [1]
    for _ in range(1, order):
        value = powers[-1] << 1
        if value & size:
            value ^= primitive
        powers.append(value & order)
    return order, powers


def _evaluate_gf2_polynomial(mask: int, root_exponent: int, order: int, powers: list[int]) -> int:
    value = 0
    for degree in range(mask.bit_length()):
        if mask & (1 << degree):
            value ^= powers[(root_exponent * degree) % order]
    return value


def _message(pattern: str, size: int) -> np.ndarray:
    message = np.zeros(size, dtype=np.uint8)
    if pattern == "first":
        message[0] = 1
    elif pattern == "last":
        message[-1] = 1
    elif pattern == "boundaries":
        message[[0, 1, size - 2, size - 1]] = 1
    elif pattern == "alternating":
        message[::2] = 1
    else:
        raise AssertionError(pattern)
    return message


@pytest.fixture(scope="module")
def examples() -> dict[tuple[str, str], tuple[np.ndarray, np.ndarray]]:
    rng = np.random.default_rng(20261102)
    examples = {}
    for frame, rate in _REPRESENTATIVE_MODES:
        parameters = bch_parameters(frame, rate)
        message = rng.integers(0, 2, parameters["K_bch"], dtype=np.uint8)
        examples[(frame, rate)] = (message, encode_bch(message, frame, rate))
    return examples


def test_runtime_data_matches_verified_extract() -> None:
    if not _EXTRACT_PATH.is_file():
        pytest.skip("verified standard-parameter extract is not available")
    raw = _EXTRACT_PATH.read_bytes()
    extract = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == "982b26f3166ccf35237d6a4bbfbda00a932b6a99d155095ddbb8071c62011330"
    standard = _RUNTIME["standard"]
    assert standard["source_pdf"] == extract["source"]["pdf"]
    assert standard["parameter_extract"] == {
        "file": _EXTRACT_PATH.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert _RUNTIME["polynomial_encoding"] == extract["polynomial_encoding"]
    for frame, source_family in extract["frame_families"].items():
        runtime_family = _RUNTIME["families"][frame]
        assert runtime_family["parameter_table"] == source_family["parameter_table"]
        assert runtime_family["polynomial_table"] == source_family["polynomial_table"]
        assert runtime_family["bch_m"] == source_family["bch_m"]
        assert runtime_family["polynomials"] == source_family["bch_polynomials"]
        assert runtime_family["unavailable"] == source_family["unavailable"]
        projected = [{key: code[key] for key in _CODE_KEYS} for code in source_family["codes"]]
        assert runtime_family["codes"] == projected


@pytest.mark.parametrize("frame", ("normal", "short"))
def test_primitive_g1_and_odd_table6_roots(frame: str) -> None:
    order, powers = _reference_powers(frame)
    assert len(set(powers)) == order
    assert 0 not in powers
    m = _RUNTIME["families"][frame]["bch_m"]
    for polynomial in _RUNTIME["families"][frame]["polynomials"]:
        index = polynomial["index"]
        root = 2 * index - 1
        assert _evaluate_gf2_polynomial(polynomial["bitmask"], root, order, powers) == 0
        # Independently verify the whole binary conjugacy class.  Together
        # g1..gt cover alpha**1 through alpha**(2t), not merely odd roots.
        conjugate = root
        for _ in range(m):
            conjugate = (2 * conjugate) % order
            assert _evaluate_gf2_polynomial(polynomial["bitmask"], conjugate, order, powers) == 0


@pytest.mark.parametrize("frame,rate", _MODES)
def test_generator_degree_and_consecutive_roots(frame: str, rate: str) -> None:
    parameters = bch_parameters(frame, rate)
    generator = _generator(frame, rate)
    assert generator.bit_length() - 1 == parameters["bch_t"] * parameters["bch_m"]
    assert generator & 1
    order, powers = _reference_powers(frame)
    for root in range(1, 2 * parameters["bch_t"] + 1):
        assert _evaluate_gf2_polynomial(generator, root, order, powers) == 0


def test_modes_dimensions_and_source_identity() -> None:
    expected = [("normal", rate) for rate in ("1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10")]
    expected += [("short", rate) for rate in ("1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9")]
    assert list(bch_modes()) == expected
    for frame, rate in expected:
        parameters = bch_parameters(frame, rate)
        assert parameters["N_bch"] == parameters["K_bch"] + parameters["bch_parity_bits"]
        assert parameters["N_bch"] == parameters["K_bch"] + parameters["bch_t"] * parameters["bch_m"]
        assert parameters["N_bch"] == parameters["k_ldpc"] == ldpc_parameters(frame, rate)["k"]
        assert parameters["n_ldpc"] == ldpc_parameters(frame, rate)["n"]
        assert parameters["n_full"] == (1 << parameters["bch_m"]) - 1
        assert parameters["shortened_bits"] == parameters["n_full"] - parameters["N_bch"]
        assert parameters["shortened_bits"] > 0
        assert parameters["source_pdf_sha256"] == _RUNTIME["standard"]["source_pdf"]["sha256"]
        assert parameters["parameter_extract_sha256"] == _RUNTIME["standard"]["parameter_extract"]["sha256"]


@pytest.mark.parametrize("frame,rate", _REPRESENTATIVE_MODES)
@pytest.mark.parametrize("pattern", ("first", "last", "boundaries", "alternating"))
def test_encoding_matches_independent_division_and_full_zero_prefix(
    frame: str,
    rate: str,
    pattern: str,
) -> None:
    parameters = bch_parameters(frame, rate)
    message = _message(pattern, parameters["K_bch"])
    generator = _generator(frame, rate)
    expected = _reference_systematic_encode(message, generator)
    actual = encode_bch(message, frame, rate)
    assert np.array_equal(actual, expected)

    # This is the shortening definition itself, evaluated independently:
    # prepend s virtual zero message bits, encode the full-length code, then
    # remove the known zero prefix from the systematic full codeword.
    full_message = np.concatenate((np.zeros(parameters["shortened_bits"], dtype=np.uint8), message))
    full_word = _reference_systematic_encode(full_message, generator)
    assert full_word.size == parameters["n_full"]
    assert not np.any(full_word[:parameters["shortened_bits"]])
    suffix = full_word[parameters["shortened_bits"]:]
    assert np.array_equal(expected, suffix), (
        np.count_nonzero(expected != suffix),
        np.flatnonzero(expected != suffix)[:10],
    )
    assert np.array_equal(actual, suffix), (
        np.count_nonzero(actual != suffix),
        np.flatnonzero(actual != suffix)[:10],
        np.count_nonzero(actual != expected),
    )


@pytest.mark.parametrize("frame,rate", _MODES)
def test_every_mode_is_systematic_with_zero_syndrome(frame: str, rate: str) -> None:
    parameters = bch_parameters(frame, rate)
    message = np.zeros(parameters["K_bch"], dtype=np.uint8)
    message[::257] = 1
    message[-1] ^= 1
    word = encode_bch(message, frame, rate)
    assert word.dtype == np.uint8
    assert word.shape == (parameters["N_bch"],)
    assert np.array_equal(word[:parameters["K_bch"]], message)
    assert not np.any(bch_syndrome(word, frame, rate))


@pytest.mark.parametrize("frame,rate", _REPRESENTATIVE_MODES)
def test_zero_error_decode(frame: str, rate: str, examples) -> None:
    message, word = examples[(frame, rate)]
    result = decode_bch(word, frame, rate)
    assert result["success"] is True
    assert result["uncorrectable"] is False
    assert result["corrected"] is False
    assert result["n_errors"] == 0
    assert result["reason"] == "no_errors"
    assert np.array_equal(result["codeword"], word)
    assert np.array_equal(result["bbframe"], message)


@pytest.mark.parametrize("frame,rate", _REPRESENTATIVE_MODES)
@pytest.mark.parametrize("position", ("first_info", "last_info", "first_parity", "last_parity"))
def test_single_error_at_systematic_and_parity_boundaries(
    frame: str,
    rate: str,
    position: str,
    examples,
) -> None:
    parameters = bch_parameters(frame, rate)
    message, word = examples[(frame, rate)]
    indices = {
        "first_info": 0,
        "last_info": parameters["K_bch"] - 1,
        "first_parity": parameters["K_bch"],
        "last_parity": parameters["N_bch"] - 1,
    }
    damaged = word.copy()
    damaged[indices[position]] ^= 1
    result = decode_bch(damaged, frame, rate)
    assert result["success"] is True
    assert result["corrected"] is True
    assert result["n_errors"] == 1
    assert result["reason"] == "corrected"
    assert np.array_equal(result["codeword"], word)
    assert np.array_equal(result["bbframe"], message)


@pytest.mark.parametrize("frame,rate", _REPRESENTATIVE_MODES)
def test_exactly_t_errors_including_boundaries(frame: str, rate: str, examples) -> None:
    parameters = bch_parameters(frame, rate)
    message, word = examples[(frame, rate)]
    boundaries = np.asarray(
        [0, parameters["K_bch"] - 1, parameters["K_bch"], parameters["N_bch"] - 1],
        dtype=np.int64,
    )
    rng = np.random.default_rng(8128 + parameters["bch_t"])
    remaining = np.setdiff1d(np.arange(parameters["N_bch"]), boundaries)
    positions = np.concatenate((boundaries, rng.choice(remaining, parameters["bch_t"] - 4, replace=False)))
    damaged = word.copy()
    damaged[positions] ^= 1
    result = decode_bch(damaged, frame, rate)
    assert result["success"] is True
    assert result["n_errors"] == parameters["bch_t"]
    assert np.array_equal(result["codeword"], word)
    assert np.array_equal(result["bbframe"], message)
    assert not np.any(bch_syndrome(result["codeword"], frame, rate))


@pytest.mark.parametrize("frame,rate", _REPRESENTATIVE_MODES)
def test_beyond_t_has_honest_miscorrection_or_failure_contract(
    frame: str,
    rate: str,
    examples,
) -> None:
    parameters = bch_parameters(frame, rate)
    _, word = examples[(frame, rate)]
    damaged = word.copy()
    positions = np.arange(parameters["bch_t"] + 1, dtype=np.int64)
    damaged[positions] ^= 1
    result = decode_bch(damaged, frame, rate)
    assert set(result) == {"success", "uncorrectable", "codeword", "bbframe", "n_errors", "corrected", "reason"}
    if result["success"]:
        # Bounded-distance decoders may miscorrect beyond t.  Do not demand
        # detection, recovery of the original word, or an impossible error
        # estimate greater than the guaranteed decoding radius.
        assert result["uncorrectable"] is False
        assert 0 <= result["n_errors"] <= parameters["bch_t"]
        assert result["codeword"] is not None and result["bbframe"] is not None
        assert not np.any(bch_syndrome(result["codeword"], frame, rate))
    else:
        assert result["uncorrectable"] is True
        assert result["codeword"] is None and result["bbframe"] is None
        assert result["n_errors"] is None
        assert result["corrected"] is False
        assert result["reason"]


@pytest.mark.parametrize("frame,rate", (("short", "9/10"), ("wide", "1/2"), ("normal", "2/7")))
def test_unsupported_modes_are_rejected(frame: str, rate: str) -> None:
    with pytest.raises(ValueError, match="unsupported DVB-S2 BCH mode"):
        bch_parameters(frame, rate)
    with pytest.raises(ValueError, match="unsupported DVB-S2 BCH mode"):
        encode_bch([], frame, rate)


@pytest.mark.parametrize("invalid", ("short", "long", "two_dimensional", "nonbinary", "negative", "nan", "text"))
def test_input_validation(invalid: str) -> None:
    parameters = bch_parameters("short", "1/4")
    k = parameters["K_bch"]
    n = parameters["N_bch"]
    if invalid == "short":
        message = np.zeros(k - 1, dtype=np.uint8)
        word = np.zeros(n - 1, dtype=np.uint8)
    elif invalid == "long":
        message = np.zeros(k + 1, dtype=np.uint8)
        word = np.zeros(n + 1, dtype=np.uint8)
    elif invalid == "two_dimensional":
        message = np.zeros((1, k), dtype=np.uint8)
        word = np.zeros((1, n), dtype=np.uint8)
    elif invalid == "nonbinary":
        message = np.full(k, 2, dtype=np.uint8)
        word = np.full(n, 2, dtype=np.uint8)
    elif invalid == "negative":
        message = np.full(k, -1, dtype=np.int8)
        word = np.full(n, -1, dtype=np.int8)
    elif invalid == "nan":
        message = np.full(k, np.nan)
        word = np.full(n, np.nan)
    else:
        message = np.asarray(["0"] * k)
        word = np.asarray(["0"] * n)
    with pytest.raises((TypeError, ValueError)):
        encode_bch(message, "short", "1/4")
    with pytest.raises((TypeError, ValueError)):
        bch_syndrome(word, "short", "1/4")
    with pytest.raises((TypeError, ValueError)):
        decode_bch(word, "short", "1/4")


def _hex_bits(payload: str) -> np.ndarray:
    """Decode the KAT's packed bytes MSB-first, without production code."""
    return np.unpackbits(np.frombuffer(bytes.fromhex(payload), dtype=np.uint8))


@_KAT_SKIP
def test_independent_kat_identity_and_coverage() -> None:
    assert _KAT_DOCUMENT is not None
    assert _KAT_DOCUMENT["scope"] == "BCH outer code only; LDPC is not encoded, decoded, or validated by these vectors."
    assert _KAT_DOCUMENT["bit_and_hex_format"]["bit_mapping"] == (
        "bit_i = (byte[i//8] >> (7 - (i % 8))) & 1"
    )
    assert _KAT_DOCUMENT["self_check"]["status"] == "passed"
    assert _KAT_DOCUMENT["self_check"]["frame_rate_combinations"] == 21
    assert _KAT_DOCUMENT["self_check"]["decode_cases"] == 42
    expected_ids = [f"{frame}_{rate.replace('/', '_')}" for frame, rate in bch_modes()]
    assert [case["id"] for case in _KAT_DOCUMENT["vectors"]] == expected_ids
    assert sum(case["frame"] == "normal" for case in _KAT_DOCUMENT["vectors"]) == 11
    assert sum(case["frame"] == "short" for case in _KAT_DOCUMENT["vectors"]) == 10


@_KAT_SKIP
@pytest.mark.parametrize(
    "case",
    _KAT_CASES,
    ids=lambda case: case["id"] if case is not None else "missing-external-kat",
)
def test_independent_bch_known_answers(case: dict) -> None:
    """Compare encoding and both independent decodes bit-for-bit per mode."""
    frame, rate = case["frame"], case["rate"]
    parameters = bch_parameters(frame, rate)
    assert case["k_bch"] == parameters["K_bch"]
    assert case["n_bch"] == parameters["N_bch"]
    assert case["parity_bits"] == parameters["bch_parity_bits"]
    assert case["error_correction_capability"] == parameters["bch_t"]
    shortening = case["shortening"]
    assert shortening["full_n"] == parameters["n_full"]
    assert shortening["full_k"] == parameters["n_full"] - parameters["bch_parity_bits"]
    assert shortening["shortened_leading_zero_bits"] == parameters["shortened_bits"]
    assert shortening["transmitted_leading_zero_bits"] == 16

    message = _hex_bits(case["message_hex"])
    expected_word = _hex_bits(case["encoded_codeword_hex"])
    expected_parity = _hex_bits(case["parity_hex"])
    assert message.size == parameters["K_bch"]
    assert expected_word.size == parameters["N_bch"]
    assert expected_parity.size == parameters["bch_parity_bits"]
    assert not np.any(message[:16])
    np.testing.assert_array_equal(expected_word[:parameters["K_bch"]], message)
    np.testing.assert_array_equal(expected_word[parameters["K_bch"]:], expected_parity)

    actual_word = encode_bch(message, frame, rate)
    np.testing.assert_array_equal(actual_word, expected_word)
    np.testing.assert_array_equal(actual_word[parameters["K_bch"]:], expected_parity)
    assert not np.any(bch_syndrome(actual_word, frame, rate))

    decode_cases = {decode_case["name"]: decode_case for decode_case in case["decode_cases"]}
    assert set(decode_cases) == {"error_free", "fixed_5_errors"}
    for name in ("error_free", "fixed_5_errors"):
        decode_case = decode_cases[name]
        assert decode_case["decode_status"] == "success"
        received = _hex_bits(decode_case["received_hex"])
        expected_decoded = _hex_bits(decode_case["decoded_codeword_hex"])
        np.testing.assert_array_equal(
            np.flatnonzero(received != expected_word),
            np.asarray(decode_case["error_positions"], dtype=np.int64),
        )
        np.testing.assert_array_equal(expected_decoded, expected_word)

        result = decode_bch(received, frame, rate)
        assert result["success"] is True
        assert result["uncorrectable"] is False
        assert result["n_errors"] == len(decode_case["error_positions"])
        assert result["corrected"] is (name == "fixed_5_errors")
        np.testing.assert_array_equal(result["codeword"], expected_decoded)
        np.testing.assert_array_equal(result["bbframe"], message)
        assert not np.any(bch_syndrome(result["codeword"], frame, rate))

    assert decode_cases["error_free"]["reported_corrections"] is None
    assert decode_cases["fixed_5_errors"]["reported_corrections"] == 5
    assert decode_cases["fixed_5_errors"]["error_positions"] == [
        0,
        359,
        parameters["K_bch"] - 1,
        parameters["K_bch"],
        parameters["N_bch"] - 1,
    ]
