# SPDX-License-Identifier: MIT
"""P1-1 位同步/子帧装配/HOW 校验锚点断言（golden 单源）。

合成链路：golden nav_bits（tests/golden/gps_lnav_golden.json，P0-3
编码器输出钉扎）→ 比特重复 20× → 随机前导偏移 + 软判决噪声 →
nav_bit_sync 必须恢复偏移与比特 → subframe_assemble/how_validate 与
golden expected_decoded 逐字段一致。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mbdsdr_ai import gps_nav as gnav
from mbdsdr_ai.gps_bit_sync import (
    how_validate, nav_bit_sync, register_tool_registry, subframe_assemble,
)

GOLDEN = (Path(__file__).resolve().parents[2] / "tests" / "golden" /
          "gps_lnav_golden.json")

_RNG = np.random.default_rng(20261101)
SPS = 20


@pytest.fixture(scope="module")
def golden():
    assert GOLDEN.is_file(), f"golden 缺失: {GOLDEN}"
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return g


def _to_soft_stream(bits, offset, snr_db):
    """比特 → 20×/bit 软判决流，前加 offset 样本随机相位 + 高斯噪声。"""
    rep = np.repeat(np.array(bits, dtype=float) * 2 - 1, SPS)
    prefix = _RNG.choice([-1.0, 1.0], offset) * 0.3
    sig = np.concatenate([prefix, rep])
    nvar = 1.0 / 10 ** (snr_db / 10)
    noise = np.sqrt(nvar / 2) * (_RNG.standard_normal(sig.size) +
                                 1j * _RNG.standard_normal(sig.size)).real
    return sig + noise


@pytest.mark.parametrize("offset", [0, 3, 11, 19])
@pytest.mark.parametrize("snr_db", [12.0, 7.0])
def test_bit_sync_recovers_offset_and_bits(golden, offset, snr_db):
    bits = golden["nav_bits"]
    stream = _to_soft_stream(bits, offset, snr_db)
    res = nav_bit_sync(stream.tolist(), samples_per_bit=SPS)
    assert res["in_sync"], res["reason"]
    assert res["bit_offset"] == offset
    # 前导偏移不属于电文：对齐后逐比特一致
    assert res["bits"] == bits


def test_bit_sync_pure_noise_honest(golden):
    noise = _RNG.standard_normal(SPS * 120)
    res = nav_bit_sync(noise.tolist(), samples_per_bit=SPS)
    assert not res["in_sync"]
    assert res["reason"]


def test_bit_sync_hard_bits_input(golden):
    bits = golden["nav_bits"]
    rep = np.repeat(np.array(bits), SPS).tolist()
    res = nav_bit_sync(rep, samples_per_bit=SPS)
    assert res["in_sync"]
    assert res["bit_offset"] == 0
    assert res["bits"] == bits


def test_bit_sync_input_validation(golden):
    with pytest.raises(ValueError):
        nav_bit_sync([], samples_per_bit=SPS)
    with pytest.raises(ValueError):
        nav_bit_sync([0.1] * 30, samples_per_bit=SPS)  # <4 比特周期
    with pytest.raises(ValueError):
        nav_bit_sync([0.1] * 100, samples_per_bit=2)   # sps 太小


def test_subframe_assemble_roundtrip(golden):
    bits = golden["nav_bits"]
    res = subframe_assemble(bits + [0, 1, 0])  # 尾部残 3 bit
    assert res["n_subframes"] == 3
    assert res["truncated_bits"] == 3
    eph = gnav.subframes_to_ephemeris(res["subframes"])
    assert eph["iode"] == golden["expected_decoded"]["iode"]
    assert abs(eph["sqrt_a"] - golden["expected_decoded"]["sqrt_a"]) < 1e-6


def test_subframe_assemble_rejects_bad(golden):
    bits = golden["nav_bits"]
    with pytest.raises(ValueError):
        subframe_assemble(bits[:299])           # 不足一帧
    corrupt = list(bits)
    corrupt[10] ^= 1                            # TLM 内比特翻转 → 奇偶链断
    with pytest.raises(ValueError):
        subframe_assemble(corrupt)


@pytest.mark.parametrize("idx, sfid, tow_count", [(0, 1, 100), (1, 2, 101),
                                                  (2, 3, 102)])
def test_how_validate_fields(golden, idx, sfid, tow_count):
    bits = golden["nav_bits"]
    sf = bits[idx * 300: (idx + 1) * 300]
    # D29*/D30* 链：上一子帧末字的接收位 29/30（首子帧从 0 起）
    d29 = bits[idx * 300 - 2] if idx else 0
    d30 = bits[idx * 300 - 1] if idx else 0
    res = how_validate(sf, d29_star=d29, d30_star=d30)
    assert res["preamble_ok"] and res["parity_ok"]
    assert res["sfid"] == sfid
    assert res["tow_count"] == tow_count
    assert res["tow_seconds"] == tow_count * 6.0


def test_how_validate_rejects_bad_tlm(golden):
    bits = golden["nav_bits"]
    sf = list(bits[:300])
    sf[0] ^= 1                                  # 前导字首比特翻转
    with pytest.raises(ValueError):
        how_validate(sf)


def test_tool_registration():
    from mbdsdr_ai.tool_registry import ToolRegistry

    r = ToolRegistry()
    n_before = len(r.tools)
    register_tool_registry(r)
    assert len(r.tools) == n_before + 3
    for name in ("nav_bit_sync", "subframe_assemble", "how_validate"):
        assert name in r.tools

    bits = json.loads(GOLDEN.read_text(encoding="utf-8"))["nav_bits"]
    rep = np.repeat(np.array(bits), SPS).tolist()
    res = r.tools["nav_bit_sync"]["handler"](
        {"stream": rep, "samples_per_bit": SPS})
    assert res.success and res.data["bit_offset"] == 0
