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
            "fields": [_field("a", 0, 1), _field("b", 1, 2)],
        },
        "child": {
            "frame_type": "RT",
            "derived_from": parent,
            "honesty": "child honesty",
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
        "x": {"derived_from": "y", "source": _GOOD_SOURCE, "fields": []},
        "y": {"derived_from": "x", "source": _GOOD_SOURCE, "fields": []},
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
    """除既有 2 条外，任何顶层布局都必须带 source（防无出处新 profile）。"""
    without = {name for name, entry in TABLE.items()
               if not name.startswith("_") and not entry.get("source")}
    assert without == {"astu_1_tm_example", "ccsds_tc_classic"}, (
        f"出现无出处的布局条目: {sorted(without)}——新布局必须带 source，"
        "无出处不入库")


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
