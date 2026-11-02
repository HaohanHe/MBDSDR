# SPDX-License-Identifier: MIT
"""DVB-S2 LDPC address tables, accumulator encoding, and parity checks.

The address rows come from ETSI EN 302 307-1 Annexes B and C.  This module
covers only the LDPC layer: it does not apply BCH, scrambling, or PL framing.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import json
from typing import Any

import numpy as np

_NORMAL_RATES = ("1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10")
_SHORT_RATES = _NORMAL_RATES[:-1]
_EXPECTED_MODES = frozenset(
    [("normal", rate) for rate in _NORMAL_RATES]
    + [("short", rate) for rate in _SHORT_RATES]
)
_SOURCE_PDF_SHA256 = "19077f80420eb17519fcf6f83e2847d7a877940b134c5306814b8c6f60d8475d"


@dataclass(frozen=True)
class _Table:
    frame: str
    rate: str
    standard_table: str
    q: int
    k: int
    n: int
    rows: tuple[tuple[int, ...], ...]

    @property
    def parity_bits(self) -> int:
        return self.n - self.k


_TABLES: dict[tuple[str, str], _Table] | None = None


@dataclass(frozen=True)
class _Graph:
    """Tanner edges stored by adjacent check; no dense check matrix."""

    edge_check: np.ndarray
    edge_var: np.ndarray
    check_start: np.ndarray


_GRAPHS: dict[tuple[str, str], _Graph] = {}


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid LDPC table {label}")
    return value


def _load_tables() -> dict[tuple[str, str], _Table]:
    global _TABLES
    if _TABLES is not None:
        return _TABLES
    resource = resources.files(__package__).joinpath("data/dvbs2_ldpc_tables.json")
    with resource.open("r", encoding="utf-8") as stream:
        document = json.load(stream)
    if document.get("schema") != "dvb-s2.ldpc.runtime-tables.v1":
        raise ValueError("unsupported DVB-S2 LDPC table schema")
    pdf = document.get("standard", {}).get("source_pdf", {})
    if pdf.get("sha256") != _SOURCE_PDF_SHA256 or pdf.get("pages") != 80:
        raise ValueError("unexpected DVB-S2 LDPC source identity")
    raw_tables = document.get("tables")
    if not isinstance(raw_tables, list):
        raise ValueError("LDPC tables must be a list")
    tables: dict[tuple[str, str], _Table] = {}
    for raw in raw_tables:
        if not isinstance(raw, dict):
            raise ValueError("invalid LDPC table entry")
        frame, rate = raw.get("frame"), raw.get("rate")
        if (frame, rate) not in _EXPECTED_MODES or (frame, rate) in tables:
            raise ValueError(f"invalid or duplicate LDPC mode: {frame} {rate}")
        q = _positive_int(raw.get("q"), "q")
        k = _positive_int(raw.get("k"), "k")
        n = _positive_int(raw.get("n"), "n")
        if n != (64800 if frame == "normal" else 16200) or k >= n:
            raise ValueError(f"invalid LDPC dimensions for {frame} {rate}")
        if k % 360 or n - k != 360 * q:
            raise ValueError(f"inconsistent LDPC q/k/n for {frame} {rate}")
        raw_rows = raw.get("rows")
        if not isinstance(raw_rows, list) or len(raw_rows) != k // 360:
            raise ValueError(f"invalid LDPC row count for {frame} {rate}")
        rows: list[tuple[int, ...]] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, list) or not raw_row:
                raise ValueError(f"empty LDPC address row for {frame} {rate}")
            if any(isinstance(x, bool) or not isinstance(x, int) for x in raw_row):
                raise ValueError(f"non-integer LDPC address for {frame} {rate}")
            row = tuple(raw_row)
            if len(set(row)) != len(row) or any(x < 0 or x >= n - k for x in row):
                raise ValueError(f"invalid LDPC address row for {frame} {rate}")
            rows.append(row)
        standard_table = raw.get("standard_table")
        if not isinstance(standard_table, str) or not standard_table:
            raise ValueError("missing LDPC standard table identifier")
        tables[(frame, rate)] = _Table(frame, rate, standard_table, q, k, n, tuple(rows))
    if frozenset(tables) != _EXPECTED_MODES:
        raise ValueError("LDPC table does not contain exactly the DVB-S2 modes")
    _TABLES = tables
    return tables


def ldpc_modes() -> tuple[tuple[str, str], ...]:
    """Return all supported ``(frame, rate)`` pairs in standard-table order."""
    tables = _load_tables()
    ordered = [("normal", rate) for rate in _NORMAL_RATES]
    ordered.extend(("short", rate) for rate in _SHORT_RATES)
    return tuple(mode for mode in ordered if mode in tables)


def _table_for(frame: str, rate: str) -> _Table:
    try:
        return _load_tables()[(frame, rate)]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unsupported DVB-S2 LDPC mode: {frame!r} {rate!r}") from exc


def ldpc_parameters(frame: str, rate: str) -> dict[str, Any]:
    """Return dimensions and source identity for one supported mode."""
    table = _table_for(frame, rate)
    return {
        "frame": table.frame,
        "rate": table.rate,
        "standard_table": table.standard_table,
        "q": table.q,
        "k": table.k,
        "n": table.n,
        "parity_bits": table.parity_bits,
        "row_count": len(table.rows),
    }


def _binary_array(value, length: int, name: str) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim != 1 or array.size != length:
        raise ValueError(f"{name} must be a one-dimensional array of {length} bits")
    if not np.issubdtype(array.dtype, np.number):
        raise TypeError(f"{name} must contain numeric binary values")
    if not np.all((array == 0) | (array == 1)):
        raise ValueError(f"{name} must contain only 0 and 1")
    return np.ascontiguousarray(array, dtype=np.uint8)


def _information_syndrome(bits: np.ndarray, table: _Table) -> np.ndarray:
    parity = np.zeros(table.parity_bits, dtype=np.uint8)
    offsets = np.arange(360, dtype=np.int64) * table.q
    for group, addresses in enumerate(table.rows):
        info = bits[group * 360:(group + 1) * 360]
        for address in addresses:
            indices = (address + offsets) % table.parity_bits
            np.bitwise_xor.at(parity, indices, info)
    return parity


def encode_ldpc(info_bits, frame: str, rate: str) -> np.ndarray:
    """Encode ``k`` information bits into a systematic ``n``-bit FECFRAME.

    Parity accumulation follows the modulo-(n-k) addresses and the final
    sequential parity-bit recursion in EN 302 307-1 clause 5.3.2.
    """
    table = _table_for(frame, rate)
    info = _binary_array(info_bits, table.k, "info_bits")
    parity = _information_syndrome(info, table)
    np.bitwise_xor.accumulate(parity, out=parity)
    return np.concatenate((info, parity)).astype(np.uint8, copy=False)


def ldpc_syndrome(codeword, frame: str, rate: str) -> np.ndarray:
    """Return the ``n-k`` binary parity-check results for a codeword."""
    table = _table_for(frame, rate)
    word = _binary_array(codeword, table.n, "codeword")
    syndrome = _information_syndrome(word[:table.k], table)
    parity = word[table.k:]
    syndrome[0] ^= parity[0]
    syndrome[1:] ^= parity[1:] ^ parity[:-1]
    return syndrome


def _graph_for(table: _Table) -> _Graph:
    key = (table.frame, table.rate)
    cached = _GRAPHS.get(key)
    if cached is not None:
        return cached
    offsets = np.arange(360, dtype=np.int64) * table.q
    within_group = np.arange(360, dtype=np.int64)
    edge_checks = []
    edge_vars = []
    for group, addresses in enumerate(table.rows):
        variables = group * 360 + within_group
        for address in addresses:
            edge_checks.append((address + offsets) % table.parity_bits)
            edge_vars.append(variables)
    # Check 0 contains p[0].  Check j>0 contains p[j] and p[j-1].
    following = np.arange(1, table.parity_bits, dtype=np.int64)
    edge_checks.extend((np.zeros(1, dtype=np.int64), following, following))
    edge_vars.extend((
        np.asarray([table.k], dtype=np.int64),
        table.k + following,
        table.k + following - 1,
    ))
    checks = np.concatenate(edge_checks)
    variables = np.concatenate(edge_vars)
    order = np.argsort(checks, kind="stable")
    checks = checks[order]
    variables = variables[order].astype(np.int32)
    starts = np.searchsorted(checks, np.arange(table.parity_bits + 1))
    if np.any((checks[1:] == checks[:-1]) & (variables[1:] == variables[:-1])):
        raise ValueError(f"duplicate variable within an LDPC check for {table.frame} {table.rate}")
    graph = _Graph(checks.astype(np.int32), variables, starts.astype(np.int64))
    if graph.check_start[-1] != variables.size or graph.check_start[0] != 0:
        raise ValueError("invalid sparse LDPC check offsets")
    _GRAPHS[key] = graph
    return graph


def _graph_syndrome(word: np.ndarray, graph: _Graph, parity_bits: int) -> np.ndarray:
    syndrome = np.zeros(parity_bits, dtype=np.uint8)
    np.bitwise_xor.at(syndrome, graph.edge_check, word[graph.edge_var])
    return syndrome


def decode_ldpc(llr, frame: str, rate: str, max_iterations: int = 50, alpha: float = 0.8) -> dict[str, Any]:
    """Decode with flooding normalized min-sum over sparse Tanner edges.

    Positive LLR favors bit 0 and negative LLR favors bit 1.  ``iterations``
    counts message-passing updates; an initially valid word needs zero.  On
    iteration exhaustion the hard decisions are returned with an honest
    ``converged=False`` and ``reason='max_iterations'``.
    """
    table = _table_for(frame, rate)
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
        raise ValueError("max_iterations must be a positive integer")
    if isinstance(alpha, bool):
        raise ValueError("alpha must be in (0, 1]")
    try:
        scale = float(alpha)
    except (TypeError, ValueError) as exc:
        raise ValueError("alpha must be in (0, 1]") from exc
    if not np.isfinite(scale) or not 0.0 < scale <= 1.0:
        raise ValueError("alpha must be in (0, 1]")
    try:
        channel = np.asarray(llr, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("llr must be a real numeric array") from exc
    if channel.ndim != 1 or channel.size != table.n:
        raise ValueError(f"llr must be a one-dimensional array of {table.n} values")
    if not np.all(np.isfinite(channel)):
        raise ValueError("llr must contain only finite values")
    channel = np.ascontiguousarray(channel)

    graph = _graph_for(table)
    starts = graph.check_start[:-1]
    edge_indices = np.arange(graph.edge_var.size)
    messages = np.zeros(graph.edge_var.size, dtype=np.float64)
    posterior = channel.copy()
    word = (posterior < 0.0).astype(np.uint8)
    syndrome = _graph_syndrome(word, graph, table.parity_bits)
    unsatisfied = int(np.count_nonzero(syndrome))
    if unsatisfied == 0:
        return {
            "converged": True,
            "codeword": word,
            "info": word[:table.k].copy(),
            "iterations": 0,
            "unsatisfied": 0,
            "reason": "converged",
        }

    for iteration in range(1, max_iterations + 1):
        variable_to_check = posterior[graph.edge_var] - messages
        negative = (variable_to_check < 0.0).astype(np.uint8)
        negative_parity = np.add.reduceat(negative, starts) & 1
        outgoing_negative = (negative_parity[graph.edge_check] - negative) & 1

        magnitudes = np.abs(variable_to_check)
        minima = np.minimum.reduceat(magnitudes, starts)
        at_minimum = magnitudes == minima[graph.edge_check]
        first_minimum = np.minimum.reduceat(
            np.where(at_minimum, edge_indices, edge_indices.size), starts
        )
        second_minima = np.minimum.reduceat(
            np.where(at_minimum, np.inf, magnitudes), starts
        )
        outgoing_magnitude = minima[graph.edge_check].copy()
        excluded = edge_indices == first_minimum[graph.edge_check]
        outgoing_magnitude[excluded] = second_minima[graph.edge_check[excluded]]
        messages = scale * outgoing_magnitude
        messages[outgoing_negative != 0] *= -1.0

        posterior = channel + np.bincount(
            graph.edge_var, weights=messages, minlength=table.n
        )
        word = (posterior < 0.0).astype(np.uint8)
        syndrome = _graph_syndrome(word, graph, table.parity_bits)
        unsatisfied = int(np.count_nonzero(syndrome))
        if unsatisfied == 0:
            return {
                "converged": True,
                "codeword": word,
                "info": word[:table.k].copy(),
                "iterations": iteration,
                "unsatisfied": 0,
                "reason": "converged",
            }
    return {
        "converged": False,
        "codeword": word,
        "info": word[:table.k].copy(),
        "iterations": max_iterations,
        "unsatisfied": unsatisfied,
        "reason": "max_iterations",
    }
