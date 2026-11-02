# SPDX-License-Identifier: MIT
"""DVB-S2 shortened BCH coding and its cascade with DVB-S2 LDPC.

The generator is the product of the first ``t`` Table 6 polynomials over
GF(2).  With the primitive element defined by ``g1``, polynomial ``g_i`` has
root ``alpha**(2*i-1)``.  Their conjugate roots therefore give the consecutive
roots ``alpha**1`` through ``alpha**(2*t)`` used by the decoder.

Bit arrays are transmitted most-significant polynomial coefficient first.
The full-length BCH message has ``2**m - 1 - N_bch`` virtual leading zero
bits before the BBFRAME.  Encoding removes the corresponding leading zero
codeword bits; syndrome calculation and Chien search restore their positions
conceptually, but never search or correct the virtual prefix.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import json
from typing import Any

import numpy as np

from .dvbs2_ldpc import decode_ldpc, encode_ldpc

_NORMAL_RATES = ("1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10")
_SHORT_RATES = _NORMAL_RATES[:-1]
_EXPECTED_MODES = frozenset(
    [("normal", rate) for rate in _NORMAL_RATES]
    + [("short", rate) for rate in _SHORT_RATES]
)
_SOURCE_PDF_SHA256 = "19077f80420eb17519fcf6f83e2847d7a877940b134c5306814b8c6f60d8475d"
_PARAMETER_EXTRACT_SHA256 = "982b26f3166ccf35237d6a4bbfbda00a932b6a99d155095ddbb8071c62011330"


@dataclass(frozen=True)
class _Code:
    frame: str
    rate: str
    standard_table: str
    polynomial_table: str
    k: int
    n: int
    k_ldpc: int
    n_ldpc: int
    t: int
    m: int
    generator_polynomial_ids: tuple[str, ...]

    @property
    def parity_bits(self) -> int:
        return self.n - self.k

    @property
    def n_full(self) -> int:
        return (1 << self.m) - 1

    @property
    def shortened_bits(self) -> int:
        return self.n_full - self.n


@dataclass(frozen=True)
class _Family:
    m: int
    polynomials: tuple[int, ...]
    codes: dict[str, _Code]


@dataclass(frozen=True)
class _Field:
    m: int
    order: int
    exp: np.ndarray
    log: np.ndarray


_FAMILIES: dict[str, _Family] | None = None
_FIELDS: dict[str, _Field] = {}
_GENERATORS: dict[tuple[str, str], int] = {}


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid DVB-S2 BCH table {label}")
    return value


def _load_families() -> dict[str, _Family]:
    global _FAMILIES
    if _FAMILIES is not None:
        return _FAMILIES
    resource = resources.files(__package__).joinpath("data/dvbs2_bch.json")
    with resource.open("r", encoding="utf-8") as stream:
        document = json.load(stream)
    if document.get("schema") != "dvb-s2.bch.runtime-tables.v1":
        raise ValueError("unsupported DVB-S2 BCH table schema")
    standard = document.get("standard", {})
    pdf = standard.get("source_pdf", {})
    extract = standard.get("parameter_extract", {})
    if (
        standard.get("designation") != "ETSI EN 302 307-1"
        or standard.get("version") != "V1.4.1"
        or pdf.get("sha256") != _SOURCE_PDF_SHA256
        or pdf.get("pages") != 80
        or extract.get("file") != "dvbs2_fec_parameters.json"
        or extract.get("sha256") != _PARAMETER_EXTRACT_SHA256
    ):
        raise ValueError("unexpected DVB-S2 BCH source identity")

    raw_families = document.get("families")
    if not isinstance(raw_families, dict) or set(raw_families) != {"normal", "short"}:
        raise ValueError("DVB-S2 BCH data must contain normal and short families")
    families: dict[str, _Family] = {}
    for frame in ("normal", "short"):
        raw_family = raw_families[frame]
        m = _integer(raw_family.get("bch_m"), "m")
        if m != (16 if frame == "normal" else 14):
            raise ValueError(f"invalid DVB-S2 BCH field degree for {frame}")
        raw_polynomials = raw_family.get("polynomials")
        if not isinstance(raw_polynomials, list) or len(raw_polynomials) != 12:
            raise ValueError(f"invalid Table 6 polynomial list for {frame}")
        polynomials: list[int] = []
        for index, raw_polynomial in enumerate(raw_polynomials, 1):
            mask = raw_polynomial.get("bitmask")
            if (
                raw_polynomial.get("id") != f"g{index}"
                or raw_polynomial.get("index") != index
                or raw_polynomial.get("degree") != m
                or isinstance(mask, bool)
                or not isinstance(mask, int)
                or mask <= 0
                or mask >> (m + 1)
                or not (mask & (1 << m))
                or not (mask & 1)
            ):
                raise ValueError(f"invalid Table 6 polynomial g{index} for {frame}")
            exponents = [degree for degree in range(m + 1) if mask & (1 << degree)]
            coefficients = [(mask >> degree) & 1 for degree in range(m + 1)]
            if (
                raw_polynomial.get("exponents") != exponents
                or raw_polynomial.get("coefficients_ascending") != coefficients
                or raw_polynomial.get("roots") is not None
            ):
                raise ValueError(f"inconsistent Table 6 polynomial g{index} for {frame}")
            polynomials.append(mask)

        expected_rates = _NORMAL_RATES if frame == "normal" else _SHORT_RATES
        raw_codes = raw_family.get("codes")
        if not isinstance(raw_codes, list) or len(raw_codes) != len(expected_rates):
            raise ValueError(f"invalid DVB-S2 BCH code list for {frame}")
        codes: dict[str, _Code] = {}
        for raw_code in raw_codes:
            rate = raw_code.get("rate")
            if not isinstance(rate, str) or rate in codes or rate not in expected_rates:
                raise ValueError(f"invalid or duplicate DVB-S2 BCH rate for {frame}: {rate!r}")
            k = _integer(raw_code.get("K_bch"), "K_bch")
            n = _integer(raw_code.get("N_bch"), "N_bch")
            k_ldpc = _integer(raw_code.get("k_ldpc"), "k_ldpc")
            n_ldpc = _integer(raw_code.get("n_ldpc"), "n_ldpc")
            t = _integer(raw_code.get("bch_t"), "bch_t")
            code_m = _integer(raw_code.get("bch_m"), "bch_m")
            parity_bits = _integer(raw_code.get("bch_parity_bits"), "bch_parity_bits")
            expected_ids = tuple(f"g{index}" for index in range(1, t + 1))
            if (
                code_m != m
                or t > 12
                or n - k != t * m
                or parity_bits != t * m
                or k_ldpc != n
                or n_ldpc != (64800 if frame == "normal" else 16200)
                or n >= (1 << m) - 1
                or raw_code.get("generator_polynomial_ids") != list(expected_ids)
            ):
                raise ValueError(f"inconsistent DVB-S2 BCH parameters for {frame} {rate}")
            codes[rate] = _Code(
                frame=frame,
                rate=rate,
                standard_table=raw_family["parameter_table"],
                polynomial_table=raw_family["polynomial_table"],
                k=k,
                n=n,
                k_ldpc=k_ldpc,
                n_ldpc=n_ldpc,
                t=t,
                m=m,
                generator_polynomial_ids=expected_ids,
            )
        if tuple(codes) != expected_rates:
            raise ValueError(f"unexpected DVB-S2 BCH mode order or coverage for {frame}")
        unavailable = raw_family.get("unavailable")
        expected_unavailable = [] if frame == "normal" else ["9/10"]
        if not isinstance(unavailable, list) or [entry.get("rate") for entry in unavailable] != expected_unavailable:
            raise ValueError(f"unexpected unavailable DVB-S2 BCH modes for {frame}")
        families[frame] = _Family(m, tuple(polynomials), codes)
    _FAMILIES = families
    return families


def _field_for(frame: str) -> _Field:
    cached = _FIELDS.get(frame)
    if cached is not None:
        return cached
    family = _load_families()[frame]
    size = 1 << family.m
    order = size - 1
    primitive = family.polynomials[0]
    powers = [1]
    for _ in range(1, order):
        value = powers[-1] << 1
        if value & size:
            value ^= primitive
        powers.append(value & order)
    if len(set(powers)) != order or 0 in powers:
        raise ValueError(f"Table 6 g1 is not primitive for {frame} frames")

    # Table 6 lists polynomials, not roots.  Verify the derived odd-root
    # mapping instead of storing an unverified root table in the runtime data.
    for index, mask in enumerate(family.polynomials, 1):
        value = 0
        for degree in range(family.m + 1):
            if mask & (1 << degree):
                value ^= powers[((2 * index - 1) * degree) % order]
        if value:
            raise ValueError(f"unexpected Table 6 root mapping for {frame} g{index}")
    log = np.full(order + 1, -1, dtype=np.int32)
    log[np.asarray(powers, dtype=np.int32)] = np.arange(order, dtype=np.int32)
    field = _Field(
        family.m,
        order,
        np.asarray(powers + powers, dtype=np.int32),
        log,
    )
    _FIELDS[frame] = field
    return field


def _gf_mul(field: _Field, left: int, right: int) -> int:
    if not left or not right:
        return 0
    return int(field.exp[int(field.log[left]) + int(field.log[right])])


def _gf_div(field: _Field, numerator: int, denominator: int) -> int:
    if not denominator:
        raise ZeroDivisionError("GF division by zero")
    if not numerator:
        return 0
    return int(field.exp[(int(field.log[numerator]) - int(field.log[denominator])) % field.order])


def _generator_for(code: _Code) -> int:
    key = (code.frame, code.rate)
    cached = _GENERATORS.get(key)
    if cached is not None:
        return cached
    generator = 1
    for factor in _load_families()[code.frame].polynomials[:code.t]:
        product = 0
        remaining = generator
        while remaining:
            lowest = remaining & -remaining
            product ^= factor << (lowest.bit_length() - 1)
            remaining ^= lowest
        generator = product
    if generator.bit_length() != code.parity_bits + 1 or not generator & 1:
        raise ValueError(f"invalid generated BCH polynomial for {code.frame} {code.rate}")
    _GENERATORS[key] = generator
    return generator


def bch_modes() -> tuple[tuple[str, str], ...]:
    """Return all supported ``(frame, rate)`` pairs in standard-table order."""
    _load_families()
    return tuple(sorted(_EXPECTED_MODES, key=lambda mode: (
        0 if mode[0] == "normal" else 1,
        (_NORMAL_RATES if mode[0] == "normal" else _SHORT_RATES).index(mode[1]),
    )))


def _code_for(frame: str, rate: str) -> _Code:
    try:
        return _load_families()[frame].codes[rate]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unsupported DVB-S2 BCH mode: {frame!r} {rate!r}") from exc


def bch_parameters(frame: str, rate: str) -> dict[str, Any]:
    """Return dimensions, shortening, and source identity for one mode."""
    code = _code_for(frame, rate)
    return {
        "frame": code.frame,
        "rate": code.rate,
        "standard_table": code.standard_table,
        "polynomial_table": code.polynomial_table,
        "K_bch": code.k,
        "N_bch": code.n,
        "k_ldpc": code.k_ldpc,
        "n_ldpc": code.n_ldpc,
        "bch_t": code.t,
        "bch_m": code.m,
        "bch_parity_bits": code.parity_bits,
        "k": code.k,
        "n": code.n,
        "t": code.t,
        "m": code.m,
        "n_full": code.n_full,
        "shortened_bits": code.shortened_bits,
        "generator_polynomial_ids": code.generator_polynomial_ids,
        "source_pdf_sha256": _SOURCE_PDF_SHA256,
        "parameter_extract_sha256": _PARAMETER_EXTRACT_SHA256,
    }


def _binary_array(value: Any, length: int, name: str) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim != 1 or array.size != length:
        raise ValueError(f"{name} must be a one-dimensional array of {length} bits")
    if not np.issubdtype(array.dtype, np.number):
        raise TypeError(f"{name} must contain numeric binary values")
    if not np.all((array == 0) | (array == 1)):
        raise ValueError(f"{name} must contain only 0 and 1")
    return np.ascontiguousarray(array, dtype=np.uint8)


def _parity(message: np.ndarray, code: _Code) -> np.ndarray:
    generator = _generator_for(code)
    degree = code.parity_bits
    generator_low = np.asarray(
        [(generator >> exponent) & 1 for exponent in range(degree)],
        dtype=np.uint8,
    )
    remainder = np.zeros(degree, dtype=np.uint8)
    for bit in message:
        feedback = int(bit ^ remainder[-1])
        remainder[1:] = remainder[:-1]
        remainder[0] = 0
        if feedback:
            remainder ^= generator_low
    # Polynomial coefficients are stored low-to-high internally, but the
    # transmitted parity suffix is high-to-low, just like the message.
    return remainder[::-1].copy()


def encode_bch(bbframe: Any, frame: str, rate: str) -> np.ndarray:
    """Encode a BBFRAME as a systematic DVB-S2 shortened BCH codeword.

    Leading virtual zero bits of the full-length message leave the feedback
    register at zero, so processing the BBFRAME directly is exactly equivalent
    to full-length encoding followed by removal of the zero prefix.
    """
    code = _code_for(frame, rate)
    message = _binary_array(bbframe, code.k, "bbframe")
    return np.concatenate((message, _parity(message, code))).astype(np.uint8, copy=False)


def _syndrome(word: np.ndarray, code: _Code) -> np.ndarray:
    field = _field_for(code.frame)
    nonzero = np.flatnonzero(word)
    exponents = code.n - 1 - nonzero
    values: list[int] = []
    for root_index in range(1, 2 * code.t + 1):
        indices = (root_index * exponents) % field.order
        values.append(int(np.bitwise_xor.reduce(field.exp[indices], initial=0)))
    return np.asarray(values, dtype=np.int32)


def bch_syndrome(received: Any, frame: str, rate: str) -> np.ndarray:
    """Return ``S1..S2t`` field-element syndromes of a shortened codeword."""
    code = _code_for(frame, rate)
    word = _binary_array(received, code.n, "received")
    return _syndrome(word, code)


def _error_locator(syndrome: np.ndarray, code: _Code) -> tuple[list[int], int]:
    field = _field_for(code.frame)
    size = 2 * code.t + 1
    locator = [1] + [0] * (size - 1)
    previous = locator.copy()
    degree = 0
    shift = 1
    discrepancy_previous = 1
    for step in range(2 * code.t):
        discrepancy = int(syndrome[step])
        for index in range(1, degree + 1):
            discrepancy ^= _gf_mul(field, locator[index], int(syndrome[step - index]))
        if discrepancy == 0:
            shift += 1
            continue
        scale = _gf_div(field, discrepancy, discrepancy_previous)
        saved = locator.copy()
        for index in range(size - shift):
            if previous[index]:
                locator[index + shift] ^= _gf_mul(field, scale, previous[index])
        if 2 * degree <= step:
            degree = step + 1 - degree
            previous = saved
            discrepancy_previous = discrepancy
            shift = 1
        else:
            shift += 1
    return locator, degree


def _failure(reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "uncorrectable": True,
        "codeword": None,
        "bbframe": None,
        "n_errors": None,
        "corrected": False,
        "reason": reason,
    }


def _success(word: np.ndarray, code: _Code, errors: int) -> dict[str, Any]:
    return {
        "success": True,
        "uncorrectable": False,
        "codeword": word.copy(),
        "bbframe": word[:code.k].copy(),
        "n_errors": errors,
        "corrected": errors > 0,
        "reason": "corrected" if errors else "no_errors",
    }


def decode_bch(received: Any, frame: str, rate: str) -> dict[str, Any]:
    """Bounded-distance decode one shortened BCH word.

    At most ``t`` errors are corrected.  Beyond that radius the decoder can
    report failure or can algebraically miscorrect to another valid codeword;
    successful syndrome closure alone is not a guarantee that more than ``t``
    channel errors are detectable.  Failure never returns a candidate data
    word.  ``n_errors`` is the decoded locator degree, not a channel oracle.
    """
    code = _code_for(frame, rate)
    word = _binary_array(received, code.n, "received")
    syndrome = _syndrome(word, code)
    if not np.any(syndrome):
        return _success(word, code, 0)

    locator, degree = _error_locator(syndrome, code)
    if degree < 1 or degree > code.t:
        return _failure("error_capability_exceeded")

    # Chien search over actual shortened positions only.  A bit at array
    # index i has polynomial position p=N_bch-1-i, so its locator root is
    # alpha**(-p).  Virtual leading positions are deliberately absent.
    positions = np.arange(code.n - 1, -1, -1, dtype=np.int64)
    evaluations = np.ones(code.n, dtype=np.int32)
    field = _field_for(code.frame)
    for exponent in range(1, degree + 1):
        coefficient = locator[exponent]
        if coefficient:
            powers = (int(field.log[coefficient]) - exponent * positions) % field.order
            evaluations ^= field.exp[powers]
    roots = np.flatnonzero(evaluations == 0)
    if roots.size != degree:
        return _failure("error_location_failed")

    corrected = word.copy()
    corrected[roots] ^= 1
    if np.any(_syndrome(corrected, code)):
        return _failure("post_correction_syndrome_nonzero")
    return _success(corrected, code, degree)


def encode_dvbs2_fec(bbframe: Any, frame: str, rate: str) -> np.ndarray:
    """Cascade ``BBFRAME -> shortened BCH -> systematic LDPC``."""
    return encode_ldpc(encode_bch(bbframe, frame, rate), frame, rate)


def decode_dvbs2_fec(
    llr: Any,
    frame: str,
    rate: str,
    max_iterations: int = 50,
    alpha: float = 0.8,
) -> dict[str, Any]:
    """Decode LDPC first, then BCH only after LDPC convergence.

    Positive LLR favors zero and negative LLR favors one.  Top-level data
    fields describe the final BCH result; ``ldpc`` retains the complete LDPC
    diagnostic result.  A non-converged LDPC stage returns no BCH candidate.
    """
    _code_for(frame, rate)
    ldpc_result = decode_ldpc(llr, frame, rate, max_iterations=max_iterations, alpha=alpha)
    if not ldpc_result["converged"]:
        result = _failure(f"ldpc_{ldpc_result['reason']}")
        result["ldpc"] = ldpc_result
        return result
    result = decode_bch(ldpc_result["info"], frame, rate)
    result["ldpc"] = ldpc_result
    return result


__all__ = [
    "bch_modes",
    "bch_parameters",
    "encode_bch",
    "bch_syndrome",
    "decode_bch",
    "encode_dvbs2_fec",
    "decode_dvbs2_fec",
]
