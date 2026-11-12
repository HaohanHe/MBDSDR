# SPDX-License-Identifier: MIT
"""帧布局「帧型变体 + 派生」schema 钉扎测试。

参考机制：FoxTelem ``ac2cz/FoxTelem@537b7cc``
``src/telemetry/BitArrayLayout.java:54-65`` 的 RT/MAX/MIN/WOD/EXP/CAN_* 帧型
与 ``parentLayout``/``secondaryLayout`` 派生。本仓用顶层可选键
``frame_type`` + ``derived_from`` 表达同一语义：

  * 父布局给基础字段；子布局同名整体覆盖（子胜、位置不变），新名追加；
  * 同一 fields 列表内重名 → 报错，不静默覆盖；
  * 派生布局必须自带 source.url + source.retrieved_at（无出处不入库）；
  * 无 derived_from 的旧条目解析结果与 v1 逐字一致（只多 _layout_name）。
"""
import json
from pathlib import Path

import pytest

from mbdsdr_ai import sat_profile as SP
from mbdsdr_ai.verify_chain import extract_fields

DATA = Path(__file__).resolve().parents[1] / "data" / "frame_layouts.json"
TABLE = json.loads(DATA.read_text(encoding="utf-8"))

_GOOD_SOURCE = {
    "url": "https://example.org/spec.pdf",
    "retrieved_at": "2026-11-12T00:00:00+08:00",
}


def _field(name, offset, length, ftype="hex", **extra):
    return {"name": name, "offset": offset, "len": length, "type": ftype,
            **extra}


def _derived_table(fields, *, parent="base_layout", source=_GOOD_SOURCE, **extra):
    return {
        "_schema": "test",
        "base_layout": {
            "sync": {"type": "asm", "hex": "1ACFFC1D"},
            "crc": {"name": "crc16_ccitt_false", "cover": "payload"},
            "honesty": "parent honesty",
            "verification": "schema_only",
            "fields": [_field("a", 0, 1), _field("b", 1, 2)],
        },
        "child": {
            "frame_type": "RT",
            "derived_from": parent,
            "honesty": "child honesty",
            "verification": "schema_only",
            **({"source": source} if source is not None else {}),
            "fields": fields,
            **extra,
        },
    }


# ── 合并优先级（写死在 _merge_fields，这里钉死）────────────────
def test_child_wins_same_name_in_place_and_appends_new():
    """同名覆盖：子胜且保留父位置；新名追加到末尾。"""
    table = _derived_table([_field("b", 9, 3), _field("c", 12, 1, "uint8")])
    layout = SP._resolve_layout(table, "child")
    assert [(f["name"], f["offset"], f["len"]) for f in layout["fields"]] == [
        ("a", 0, 1), ("b", 9, 3), ("c", 12, 1)]
    # 覆盖是整体替换，不是字段级 merge 残留
    assert layout["fields"][1] == _field("b", 9, 3)
    assert layout["_derived_from"] == "base_layout"
    assert layout["_layout_name"] == "child"


def test_child_overrides_scalar_blocks_but_inherits_sync():
    """子条目的 honesty/crc 覆盖父条目；sync 未写则继承。"""
    table = _derived_table([], crc={"name": "crc16_ccsds"})
    layout = SP._resolve_layout(table, "child")
    assert layout["honesty"] == "child honesty"
    assert layout["crc"]["name"] == "crc16_ccsds"
    assert layout["sync"]["hex"] == "1ACFFC1D"


def test_duplicate_field_inside_one_list_rejected():
    """同一 fields 内重名 → 报错（不静默后者覆盖前者）。"""
    table = _derived_table([_field("b", 3, 1), _field("b", 4, 1)])
    with pytest.raises(ValueError, match="字段重名"):
        SP._resolve_layout(table, "child")


def test_unknown_parent_honest_error():
    table = _derived_table([], parent="no_such_parent")
    with pytest.raises(ValueError, match="未知帧布局"):
        SP._resolve_layout(table, "child")


def test_derivation_cycle_rejected():
    table = {
        "_schema": "test",
        "x": {"derived_from": "y", "source": _GOOD_SOURCE,
              "verification": "schema_only", "fields": []},
        "y": {"derived_from": "x", "source": _GOOD_SOURCE,
              "verification": "schema_only", "fields": []},
    }
    with pytest.raises(ValueError, match="成环"):
        SP._resolve_layout(table, "x")


# ── 出处红线 ───────────────────────────────────────────────────
@pytest.mark.parametrize("source", [
    None,
    {"retrieved_at": _GOOD_SOURCE["retrieved_at"]},
    {"url": _GOOD_SOURCE["url"]},
    {"url": "  ", "retrieved_at": "2026-11-12T00:00:00+08:00"},
])
def test_derived_layout_without_source_rejected(source):
    table = _derived_table([], source=source)
    with pytest.raises(ValueError, match="source"):
        SP._resolve_layout(table, "child")


def test_repo_variants_all_carry_public_source():
    variants = [name for name, entry in TABLE.items()
                if not name.startswith("_") and entry.get("derived_from")]
    assert variants, "库里应至少有一个派生变体（否则本测试失去对象）"
    for name in variants:
        src = TABLE[name]["source"]
        assert src["url"].startswith("https://"), f"{name} source.url 非公开 URL"
        assert src["retrieved_at"].strip(), f"{name} 缺抓取时间"


def test_only_grandfathered_layouts_may_lack_source():
    """除既有 2 条外，任何顶层布局都必须带可核对的 source。

    语义：新增带 source（含 url + retrieved_at）的布局不会触发本断言；
    新增无 source、或 source 缺 URL/抓取时间的顶层布局都会红——即本测试
    只钉「无出处不许入库」，不限制有出处的新布局数量。
    """
    without = {name for name, entry in TABLE.items()
               if not name.startswith("_") and not entry.get("source")}
    assert without == {"astu_1_tm_example", "ccsds_tc_classic"}, (
        f"出现无出处的布局条目: {sorted(without)}——新布局必须带 source，"
        "无出处不入库")
    for name, entry in TABLE.items():
        if name.startswith("_") or not entry.get("source"):
            continue
        src = entry["source"]
        assert str(src.get("url", "")).startswith("http"), (
            f"{name} 的 source.url 不是公开 URL")
        assert str(src.get("retrieved_at", "")).strip(), (
            f"{name} 的 source 缺抓取时间")


# ── 验证等级（机器字段 verification）────────────────────────────
def test_repo_layouts_all_declare_schema_only():
    """截至 2026-11-12 无任何布局做过规范示例逐字段对拍或 OTA 验证。

    因此库内只允许 schema_only；谁把某条改成 spec_example / ota，必须
    同时给出 verification_evidence，否则本测试与加载器都会红。
    """
    names = [n for n in TABLE if not n.startswith("_")]
    assert names, "库里应有布局条目"
    for name in names:
        assert TABLE[name].get("verification") == "schema_only", (
            f"{name} 的 verification 必须是 schema_only：当前没有规范示例或 "
            "OTA 证据；声称更高等级必须同时给 verification_evidence")


def test_missing_verification_rejected():
    table = _derived_table([])
    del table["child"]["verification"]
    with pytest.raises(ValueError, match="缺 verification"):
        SP._resolve_layout(table, "child")
    table = _derived_table([])
    del table["base_layout"]["verification"]
    with pytest.raises(ValueError, match="缺 verification"):
        SP._resolve_layout(table, "child")


def test_invalid_verification_rejected():
    table = _derived_table([])
    table["child"]["verification"] = "looks_good"
    with pytest.raises(ValueError, match="非法"):
        SP._resolve_layout(table, "child")


@pytest.mark.parametrize("level", ["spec_example", "ota"])
def test_claiming_one_level_up_requires_evidence_path(level):
    table = _derived_table([])
    table["child"]["verification"] = level
    with pytest.raises(ValueError, match="verification_evidence"):
        SP._resolve_layout(table, "child")
    table["child"]["verification_evidence"] = "  "
    with pytest.raises(ValueError, match="verification_evidence"):
        SP._resolve_layout(table, "child")
    table["child"]["verification_evidence"] = (
        "mbdsdr_ai/tests/evidence/spec_example.json")
    layout = SP._resolve_layout(table, "child")
    assert layout["verification"] == level


def test_repo_layout_claiming_ota_without_evidence_is_rejected():
    """反证：把真实条目改成 ota 但不配证据 → 加载即红。"""
    table = json.loads(json.dumps(TABLE))
    table["funcube1_ao73_frame"]["verification"] = "ota"
    with pytest.raises(ValueError, match="verification_evidence"):
        SP._resolve_layout(table, "funcube1_ao73_frame")


# ── 兼容性：旧条目解析逐字不变 ─────────────────────────────────
def test_legacy_layout_resolution_unchanged():
    for name in ("astu_1_tm_example", "ccsds_tc_classic"):
        layout = SP.load_frame_layout(name)
        raw = dict(TABLE[name])
        raw["_layout_name"] = name
        assert layout == raw, f"{name} 的解析结果相对 v1 有漂移"
        assert "_derived_from" not in layout


def test_interpreter_contract_unchanged_for_base_and_variant():
    """extract_fields 仍只收 layout_name，派生名不外泄新参数。"""
    payload = bytes.fromhex("001B000E060102030405060708")
    base = extract_fields(payload, layout_name="ccsds_tc_classic")
    assert set(base) == {"frame_header_hex", "data"}
    assert base["data"]["value"] == "0102030405060708"


# ── 真实 profile 展开前后对比 ──────────────────────────────────
def test_real_segmented_layout_before_and_after():
    """ccsds_tc_classic（真实公开标准布局）派生前后字段清单对比。

    展开前: frame_header_hex(0,5) + data(5,-1)
    展开后: frame_header_hex(0,5) + data(6,-1) + segment_header_hex(5,1)
    依据 CCSDS 232.0-B-4 §4.1.3.2.2 / Figure 4-3，出处见条目 source。
    """
    base = SP.load_frame_layout("ccsds_tc_classic")
    derived = SP.load_frame_layout("ccsds_tc_segmented")
    before = [(f["name"], f["offset"], f["len"]) for f in base["fields"]]
    after = [(f["name"], f["offset"], f["len"]) for f in derived["fields"]]
    assert before == [("frame_header_hex", 0, 5), ("data", 5, -1)]
    assert after == [("frame_header_hex", 0, 5), ("data", 6, -1),
                     ("segment_header_hex", 5, 1)]
    assert derived["frame_type"] == "AD_SEGMENTED"
    assert derived["_derived_from"] == "ccsds_tc_classic"
    assert derived["source"]["retrieved_at"] == "2026-11-12T19:02:44+08:00"


def test_real_segmented_layout_extract_on_official_frame_bytes():
    """同一份官方 Annex F 帧字节，派生布局按新偏移解释（机制验证）。

    注意：官方 Example 10 是 Type-AD（无 Segment Header），这里只验证
    「同名覆盖 + 新字段追加」的机制，不声称该样例真的带 Segment Header。
    """
    payload = bytes.fromhex("001B000E060102030405060708")
    fields = extract_fields(payload, layout_name="ccsds_tc_segmented")
    assert fields["frame_header_hex"]["value"] == "001B000E06"
    assert fields["segment_header_hex"]["value"] == "01"
    assert fields["data"]["value"] == "02030405060708"


# ── 真实星上遥测：FUNcube-1（AO-73）──────────────────────────
def _set_bits(buf, bit_offset, bit_len, value):
    for i in range(bit_len):
        bit = (value >> (bit_len - 1 - i)) & 1
        pos = bit_offset + i
        mask = 1 << (7 - (pos % 8))
        if bit:
            buf[pos // 8] |= mask
        else:
            buf[pos // 8] &= ~mask


def test_funcube1_frame_structure_is_documented_bit_table():
    layout = SP.load_frame_layout("funcube1_ao73_frame")
    assert len(layout["fields"]) == 61  # 2 头 + 16 EPS + 10 BOB + 6 RF + 4 PA + 6 ANTS + 16 SW + 1 载荷
    by_name = {f["name"]: f for f in layout["fields"]}
    # 位偏移逐段累加：头 8bit、RT 440bit（§1），载荷 200 字节从第 56 字节起
    assert (by_name["sat_id"]["bit_offset"], by_name["sat_id"]["bit_len"]) == (0, 2)
    assert (by_name["frame_type_id"]["bit_offset"],
            by_name["frame_type_id"]["bit_len"]) == (2, 6)
    assert by_name["battery_voltage"]["bit_offset"] == 72
    assert by_name["sun_sensor_xp"]["bit_offset"] == 200
    assert by_name["bus_5v_voltage"]["bit_offset"] == 290
    assert by_name["sequence_number"]["bit_offset"] == 400
    assert (by_name["deployment_wait_next_boot"]["bit_offset"],
            by_name["deployment_wait_next_boot"]["bit_len"]) == (447, 1)
    assert (by_name["payload"]["offset"], by_name["payload"]["len"]) == (56, 200)
    assert layout["source"]["sha256"] == (
        "86e12bc7b882726d47e69b4834379e042c309a95e58c0b1920ce9281a87d90cd")
    assert "FUNcube-1" in layout["source"]["document"]


def test_funcube1_frame_bit_extraction_end_to_end():
    """构造 256 字节帧（非 OTA 样本），按文档位偏移逐段取值。"""
    frame = bytearray(256)
    _set_bits(frame, 0, 2, 2)          # Sat Id = [1][0] 飞行件
    _set_bits(frame, 2, 6, 1)          # Frame Type = 01
    _set_bits(frame, 72, 16, 0x1234)   # Battery voltage
    _set_bits(frame, 200, 10, 1023)    # Sun Sensor X+
    _set_bits(frame, 290, 10, 512)     # 5.0 bus voltage（跨字节）
    _set_bits(frame, 399, 1, 1)        # Antenna deployment 3
    _set_bits(frame, 400, 24, 0xABCDEF)  # Sequence number
    _set_bits(frame, 424, 6, 42)       # DTMF command count
    _set_bits(frame, 430, 5, 17)       # DTMF last command
    _set_bits(frame, 437, 1, 1)        # Data valid EPS
    _set_bits(frame, 443, 1, 1)        # In eclipse mode
    f = extract_fields(bytes(frame), layout_name="funcube1_ao73_frame")
    assert f["sat_id"]["value"] == 2
    assert f["frame_type_id"]["value"] == 1
    assert f["battery_voltage"]["value"] == 0x1234
    assert f["sun_sensor_xp"]["value"] == 1023
    assert f["bus_5v_voltage"]["value"] == 512
    assert f["antenna_deployment_3"]["value"] == 1
    assert f["sequence_number"]["value"] == 0xABCDEF
    assert f["dtmf_command_count"]["value"] == 42
    assert f["dtmf_last_command"]["value"] == 17
    assert f["data_valid_eps"]["value"] == 1
    assert f["in_eclipse_mode"]["value"] == 1
    assert f["payload"]["value"] == "00" * 200
    assert f["battery_voltage"]["unit"] == ""


def test_funcube1_fitter_variant_overrides_payload_only():
    frame = bytearray(256)
    _set_bits(frame, 0, 2, 2)
    _set_bits(frame, 2, 6, 14)         # FM1
    _set_bits(frame, 72, 16, 0x0BAD)
    frame[56:56 + 13] = b"HELLO FUNCUBE"
    base = extract_fields(bytes(frame), layout_name="funcube1_ao73_frame")
    fm = extract_fields(bytes(frame), layout_name="funcube1_ao73_fitter")
    assert base["payload"]["value"].startswith("48454C4C4F")
    assert fm["payload"]["value"].startswith("HELLO FUNCUBE")
    assert fm["battery_voltage"]["value"] == 0x0BAD  # 其余字段继承
    assert fm["frame_type_id"]["value"] == 14
    layout = SP.load_frame_layout("funcube1_ao73_fitter")
    assert layout["_derived_from"] == "funcube1_ao73_frame"
    assert layout["frame_type"] == "FM"


def test_bit_field_out_of_range_rejected():
    from mbdsdr_ai.verify_chain import _decode_bits
    assert _decode_bits(b"\x0f\xf0", 4, 8) == 0xFF
    assert _decode_bits(b"\x80", 0, 1) == 1
    with pytest.raises(ValueError, match="位字段越界"):
        _decode_bits(b"\x00", 1, 8)
    with pytest.raises(ValueError, match="位字段越界"):
        _decode_bits(b"\x00", 0, 0)

