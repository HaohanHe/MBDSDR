# SPDX-License-Identifier: MIT
"""P0-6 协议级解码验证链测试（验收脊柱）。

golden 回归：``tests/golden/astu1_tm_synthetic_golden.json``（合成来源，
provenance 如实标注 synthetic_encoder_generated——不冒充真实 OTA 样本）。
链路：golden 位流 → ASM 切帧 → CRC16-CCITT-FALSE → 逐字段抽取 → 断言
逐条 PASS。任一步失败（同步/CRC/字段越界/断言 FAIL）显式断言。
"""
import json
from pathlib import Path

import pytest

from mbdsdr_ai.verify_chain import (
    extract_fields, sync_and_frame, verify_sample,
)

GOLDEN = (Path(__file__).resolve().parents[2] / "tests" / "golden" /
          "astu1_tm_synthetic_golden.json")


@pytest.fixture(scope="module")
def golden():
    assert GOLDEN.is_file(), f"golden 缺失: {GOLDEN}"
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


_ASSERTS = [
    {"field": "battery_mv", "op": "eq", "value": 7500},
    {"field": "temp_dc", "op": "in_range", "value": [20.0, 22.0]},
    {"field": "mode", "op": "eq", "value": 3},
    {"field": "seq", "op": "eq", "value": 0x12345678},
    {"field": "callsign", "op": "eq", "value": "BI1AAA"},
]


def test_golden_full_chain_pass(golden):
    """验收脊柱主用例：golden 位流全链 PASS、字段逐值相等。"""
    assert golden["sample_provenance"] == "synthetic_encoder_generated"
    r = verify_sample(golden["bits"], layout_name=golden["layout"],
                      profile_name=golden["profile"],
                      assertions=_ASSERTS)
    assert r["overall_pass"], f"断言失败: {r['assertions']}"
    assert r["n_pass"] == len(_ASSERTS) and r["n_fail"] == 0
    assert r["crc"]["ok"]
    vals = golden["field_values"]
    assert r["fields"]["battery_mv"]["value"] == vals["battery_mv"]
    assert r["fields"]["temp_dc"]["value"] == pytest.approx(vals["temp_dc"])
    assert r["fields"]["callsign"]["value"] == vals["callsign"]
    # honesty 标注透传（示例布局不冒充公开标准）
    assert "示例" in r["honesty"]


def test_sync_slice_roundtrip(golden):
    """ASM 切帧：偏移 0、载荷字节与 golden 一致。"""
    r = sync_and_frame(golden["bits"], layout_name=golden["layout"])
    assert r["asm_offset_bits"] == 0
    assert r["payload_hex"] == golden["frame_payload_hex"]


def test_missing_asm_honest_error(golden):
    """同步字不存在 → 报错不产出帧。"""
    bits = [0] * len(golden["bits"])
    with pytest.raises(ValueError, match="找不到 ASM"):
        sync_and_frame(bits, layout_name=golden["layout"])


def test_crc_corruption_rejected(golden):
    """载荷区翻 1 bit → CRC 失败，验证链报错（不产出字段结论）。"""
    bits = list(golden["bits"])
    bits[40] ^= 1  # ASM 后第一个数据位
    with pytest.raises(ValueError, match="CRC"):
        verify_sample(bits, layout_name=golden["layout"])


def test_assertion_fail_reported(golden):
    """断言不满足 → overall_pass=False 且逐条 FAIL 可见。"""
    r = verify_sample(golden["bits"], layout_name=golden["layout"],
                      assertions=[{"field": "battery_mv", "op": "eq",
                                   "value": 9999}])
    assert not r["overall_pass"]
    assert r["n_fail"] == 1
    assert r["assertions"][0]["got"] == 7500


def test_unknown_field_and_op_reported(golden):
    """未知字段/未知操作 → 逐条标记失败而非崩溃。"""
    r = verify_sample(golden["bits"], layout_name=golden["layout"],
                      assertions=[{"field": "no_such", "op": "eq", "value": 0},
                                  {"field": "mode", "op": "bogus", "value": 0}])
    assert r["n_fail"] == 2


def test_extract_fields_bounds(golden):
    """字段越界（截短载荷）→ 报错。"""
    payload = bytes.fromhex(golden["frame_payload_hex"])[:-4]
    with pytest.raises(ValueError, match="越界"):
        extract_fields(payload, layout_name=golden["layout"])


def test_profile_swap_layout_unchanged(golden):
    """换星只换配置：同一布局不同剖面，字段抽取一致（链与皮分离）。"""
    r1 = verify_sample(golden["bits"], layout_name=golden["layout"],
                       profile_name="astu_1")
    r2 = verify_sample(golden["bits"], layout_name=golden["layout"],
                       profile_name="gk2a_lrit")
    assert r1["fields"] == r2["fields"]


# ── 工具注册面 ────────────────────────────────────────────────
def test_tool_registered():
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    names = {t["name"] for t in r.list_tools()}
    assert "verify_decode_chain" in names


def test_tool_handler_end_to_end(golden):
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    h = r.tools["verify_decode_chain"]["handler"]
    res = h({"bits": golden["bits"], "layout_name": golden["layout"],
             "profile_name": golden["profile"], "assertions": _ASSERTS})
    assert res.success
    assert res.data["n_fail"] == 0
    bad = h({"bits": [0] * len(golden["bits"]),
             "layout_name": golden["layout"]})
    assert not bad.success
