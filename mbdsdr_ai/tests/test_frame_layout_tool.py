# SPDX-License-Identifier: MIT
"""P1-2 帧布局配置注入框架锚点断言。

CRC 校验锚点：Rocksoft 注册表标准值 "123456789" →
  CRC-16/CCITT-FALSE = 0x29B1 / CRC-16/UMTS = 0xFEE8 /
  CRC-32/ISO-HDLC = 0xCBF43926。
字段提取锚点：CCSDS 主头部公开标准布局（团队按公开标准独立实现）。
"""
from __future__ import annotations

import json

import pytest

from mbdsdr_ai.frame_layout_tool import (
    crc_check, crc_compute, field_extract, frame_layout_spec_load,
    register_tool_registry,
)

CHECK = b"123456789"

# CCSDS 空间包主头部（公开标准布局，测试夹具）
CCSDS_SPEC = {
    "name": "ccsds_primary_header",
    "fields": [
        {"name": "version", "offset_bits": 0, "length_bits": 3,
         "type": "uint"},
        {"name": "packet_type", "offset_bits": 3, "length_bits": 1,
         "type": "uint"},
        {"name": "secondary_hdr", "offset_bits": 4, "length_bits": 1,
         "type": "uint"},
        {"name": "apid", "offset_bits": 5, "length_bits": 11,
         "type": "uint"},
        {"name": "seq_flags", "offset_bits": 16, "length_bits": 2,
         "type": "uint"},
        {"name": "seq_count", "offset_bits": 18, "length_bits": 14,
         "type": "uint"},
        {"name": "packet_len", "offset_bits": 32, "length_bits": 16,
         "type": "uint"},
    ],
}


# ── spec 加载 ────────────────────────────────────────────────
def test_spec_load_from_json_text():
    text = json.dumps(CCSDS_SPEC)
    spec = frame_layout_spec_load(text)
    assert spec["name"] == "ccsds_primary_header"
    assert len(spec["fields"]) == 7


def test_spec_load_from_dict_passthrough():
    spec = frame_layout_spec_load(dict(CCSDS_SPEC))
    assert spec["fields"][3]["name"] == "apid"


@pytest.mark.parametrize("bad", [
    "",                              # 空文本
    "not json{{",                    # 非 JSON 且无 PyYAML
    json.dumps({"fields": []}),      # 缺 name
    json.dumps({"name": "x"}),       # 缺 fields
    json.dumps({"name": "x", "fields": [
        {"name": "a", "offset_bits": -1, "length_bits": 4}]}),
    json.dumps({"name": "x", "fields": [
        {"name": "a", "offset_bits": 0, "length_bits": 0}]}),
    json.dumps({"name": "x", "fields": [
        {"name": "a", "offset_bits": 0, "length_bits": 4},
        {"name": "a", "offset_bits": 8, "length_bits": 4}]}),  # 重名
    json.dumps({"name": "x", "fields": [
        {"name": "a", "offset_bits": 0, "length_bits": 4,
         "type": "float128"}]}),     # 未知类型
])
def test_spec_load_rejects_bad(bad):
    with pytest.raises(ValueError):
        frame_layout_spec_load(bad)


# ── 字段提取 ─────────────────────────────────────────────────
def test_field_extract_ccsds_header():
    # version=0b010, type=1, sec=0, apid=0x123, seq=0b11, count=0x1234,
    # len=0x0100 → 手工装配
    hdr = bytes([
        0b0101_0001, 0x23,          # version/type/sec + apid 高 3/低 8
        0b1101_0010, 0x34,          # seq_flags=11 + count 高 6 / 低 8
        0x01, 0x00,
    ])
    out = field_extract(hdr, CCSDS_SPEC)
    assert out["version"] == 0b010
    assert out["packet_type"] == 1
    assert out["secondary_hdr"] == 0
    assert out["apid"] == 0x123
    assert out["seq_flags"] == 0b11
    assert out["seq_count"] == 0x1234
    assert out["packet_len"] == 0x0100


def test_field_extract_types_scale_enum():
    spec = {
        "name": "mixed",
        "fields": [
            {"name": "temp", "offset_bits": 0, "length_bits": 8,
             "type": "int", "scale": 0.5, "units": "C"},
            {"name": "mode", "offset_bits": 8, "length_bits": 2,
             "type": "uint", "enum": {"0": "idle", "1": "tx"}},
            {"name": "call", "offset_bits": 16, "length_bits": 48,
             "type": "ascii"},
        ],
    }
    frame = bytes([0xF6, 0b01_000000]) + b"BI1AAA"
    out = field_extract(frame, spec)
    assert out["temp"] == -5.0          # 0xF6 有符号 = -10，×0.5
    assert out["mode"] == "tx"
    assert out["call"] == "BI1AAA"


def test_field_extract_enum_unmapped_honest():
    spec = {"name": "e", "fields": [
        {"name": "m", "offset_bits": 0, "length_bits": 2,
         "enum": {"0": "a"}}]}
    out = field_extract(bytes([0b11_000000]), spec)
    assert out["m"] == 3 and out["m_unmapped"] is True


def test_field_extract_out_of_bounds_raises():
    spec = {"name": "x", "fields": [
        {"name": "big", "offset_bits": 8, "length_bits": 64}]}
    with pytest.raises(ValueError):
        field_extract(bytes(4), spec)


# ── CRC ──────────────────────────────────────────────────────
def test_crc16_ccitt_false_anchor():
    assert crc_compute(CHECK, 16, 0x1021, init=0xFFFF) == 0x29B1


def test_crc16_umts_anchor():
    assert crc_compute(CHECK, 16, 0x8005, init=0x0000) == 0xFEE8


def test_crc32_iso_hdlc_anchor():
    assert crc_compute(CHECK, 32, 0x04C11DB7, init=0xFFFFFFFF,
                       refin=True, xorout=0xFFFFFFFF) == 0xCBF43926


def test_crc_check_frame_ok_and_corrupt():
    payload = b"\x01\x02\x03\x04"
    crc = crc_compute(payload, 16, 0x1021, init=0xFFFF)
    frame = payload + crc.to_bytes(2, "big")
    res = crc_check(frame, 16, 0x1021, init=0xFFFF)
    assert res["ok"] and res["computed"] == crc
    bad = bytearray(frame)
    bad[0] ^= 0xFF
    res = crc_check(bytes(bad), 16, 0x1021, init=0xFFFF)
    assert not res["ok"]
    assert res["computed"] != res["expected"]


def test_crc_check_explicit_offset():
    # CRC 覆盖 frame[:4]，其后还有 1 字节填充（不得被误吞进 expected）
    payload = b"\x10\x20\xAA\xBB"
    crc = crc_compute(payload, 16, 0x8005)
    frame = payload + crc.to_bytes(2, "big") + b"\x00"
    res = crc_check(frame, 16, 0x8005, crc_offset_bytes=4)
    assert res["ok"]
    assert res["crc_bytes"] == 2


def test_crc_width_validation():
    with pytest.raises(ValueError):
        crc_compute(CHECK, 40, 0x104C11DB7)


# ── 工具注册 ─────────────────────────────────────────────────
def test_tool_registration():
    from mbdsdr_ai.tool_registry import ToolRegistry

    r = ToolRegistry()
    n_before = len(r.tools)
    register_tool_registry(r)
    assert len(r.tools) == n_before + 3
    for name in ("frame_layout_spec_load", "field_extract", "crc_check"):
        assert name in r.tools

    hdr_hex = "5123d2340100"
    res = r.tools["field_extract"]["handler"](
        {"frame": hdr_hex, "spec": CCSDS_SPEC})
    assert res.success and res.data["apid"] == 0x123

    payload = b"\x01\x02\x03"
    crc = crc_compute(payload, 16, 0x1021, init=0xFFFF)
    frame_hex = (payload + crc.to_bytes(2, "big")).hex()
    res = r.tools["crc_check"]["handler"](
        {"frame": frame_hex, "width": 16, "poly": 0x1021, "init": 0xFFFF})
    assert res.success and res.data["ok"]
