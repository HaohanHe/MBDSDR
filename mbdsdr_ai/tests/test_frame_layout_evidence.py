# SPDX-License-Identifier: MIT
"""verification_evidence「真实可复跑证据」钉扎 + 反证。

背景（§2 文档-实现冲突）：``sat_profile._require_verification`` 承诺
spec_example / ota 必须有「可复跑证据」，但旧实现只校验路径字符串非空——
指向一个根本不存在的文件也能加载。本测试把承诺钉死：

  * 证据路径必须指向真实存在、含非空白内容的文件，否则拒载；
  * 首份证据 artifact 用 sha256 已核验的公开 CCSDS 230.1-G-3 Annex F
    Example 10 官方示例帧，经本仓解释器逐字段对拍——本测试**重新计算**
    每个 decoded/extracted 值并与 artifact 钉扎值对拍（可复跑、非空头）。

反证（mutation）：把加载器退回「只查字符串非空」，则
``test_spec_example_rejects_missing_evidence_file`` 与
``test_claiming_one_level_up_requires_evidence_path`` 的不存在路径分支必红。
"""
import json
from pathlib import Path

import pytest

from mbdsdr_ai import sat_profile as SP
from mbdsdr_ai.verify_chain import extract_fields, _decode_bits

_HERE = Path(__file__).resolve().parent
_ARTIFACT_REL = "mbdsdr_ai/tests/evidence/ccsds_tc_classic_annex_f_example10.json"
_ARTIFACT = _HERE / "evidence" / "ccsds_tc_classic_annex_f_example10.json"
EVIDENCE = json.loads(_ARTIFACT.read_text(encoding="utf-8"))


def _table(level, evidence):
    """最小非派生布局（非派生无需 source）；只触发 verification 证据闸。"""
    entry = {
        "verification": level,
        "sync": {"type": "asm", "hex": "1ACFFC1D"},
        "fields": [{"name": "a", "offset": 0, "len": 1, "type": "hex"}],
    }
    if evidence is not None:
        entry["verification_evidence"] = evidence
    return {"_schema": "test", "L": entry}


# ── 证据 artifact 可复跑（非空头、非 vacuous）──────────────────────
def test_artifact_has_verifiable_public_provenance():
    spec = EVIDENCE["spec"]
    assert spec["url"].startswith("https://public.ccsds.org/")
    assert len(spec["sha256"]) == 64
    int(spec["sha256"], 16)  # 必须是合法 hex
    assert "Annex F" in spec["section"] and "Example 10" in spec["section"]
    assert EVIDENCE["layout_name"] == "ccsds_tc_classic"
    assert EVIDENCE["supports_verification"] == "spec_example"
    assert EVIDENCE["result"] == "pass"


def test_artifact_header_fields_reproduce_from_official_frame():
    """重新逐位解码官方示例帧，与 artifact 钉扎值对拍（解释器变了即红）。"""
    frame = bytes.fromhex(EVIDENCE["example_frame_hex"])
    assert len(frame) == EVIDENCE["documented_field_values"]["n_bytes"]
    for f in EVIDENCE["header_bit_fields"]:
        got = _decode_bits(frame, f["bit_offset"], f["bit_len"])
        assert got == f["decoded"], f"{f['name']} 解码漂移: {got} != {f['decoded']}"
        if "documented" in f:
            assert got == f["documented"], f"{f['name']} 与规范文档值不符"
    # 帧长字段 = 总字节数 - 1（CCSDS 232.0-B-4 §4.1.1）
    by_name = {f["name"]: f for f in EVIDENCE["header_bit_fields"]}
    assert by_name["frame_length"]["decoded"] == len(frame) - 1


def test_artifact_layout_fields_reproduce_via_interpreter():
    """用本仓 extract_fields 重新抽取，与 artifact 逐字段对拍。"""
    frame = bytes.fromhex(EVIDENCE["example_frame_hex"])
    fields = extract_fields(frame, layout_name=EVIDENCE["layout_name"])
    comp = {c["field"]: c for c in EVIDENCE["layout_field_comparison"]}
    hdr = comp["frame_header_hex"]
    assert fields["frame_header_hex"]["value"] == hdr["extracted"] == hdr["expected_hex"]
    data = comp["data"]
    got = fields["data"]["value"]
    assert got == data["extracted"]
    # len=-1 含帧尾 FECF；纯用户数据 = 去末 2 字节（4 hex 字符）
    assert got[:-4] == data["pure_data_hex"] == data["documented_data_hex"]
    assert got[-4:] == data["fecf_hex"] == data["documented_fecf_hex"]
    assert all(c["pass"] for c in EVIDENCE["layout_field_comparison"])


# ── 加载器：证据必须是真实存在、非空的文件 ─────────────────────────
@pytest.mark.parametrize("level", ["spec_example", "ota"])
def test_real_evidence_file_loads(level):
    layout = SP._resolve_layout(_table(level, _ARTIFACT_REL), "L")
    assert layout["verification"] == level


@pytest.mark.parametrize("level", ["spec_example", "ota"])
def test_spec_example_rejects_missing_evidence_file(level):
    """反证：不存在的路径必须被拒（旧实现只查字符串非空 → 会放行）。"""
    with pytest.raises(ValueError, match="证据文件"):
        SP._resolve_layout(
            _table(level, "mbdsdr_ai/tests/evidence/__no_such_file__.json"), "L")


@pytest.mark.parametrize("level", ["spec_example", "ota"])
def test_spec_example_rejects_empty_evidence_file(level, tmp_path):
    """反证：真实存在但空/纯空白的文件不算可复跑证据。"""
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="为空"):
        SP._resolve_layout(_table(level, str(empty)), "L")
    blank = tmp_path / "blank.json"
    blank.write_text("   \n\t  ", encoding="utf-8")
    with pytest.raises(ValueError, match="为空"):
        SP._resolve_layout(_table(level, str(blank)), "L")


def test_missing_evidence_path_still_rejected():
    """缺路径（None/空串）照旧拒载——强化没有放松原有闸。"""
    for ev in (None, "", "   "):
        with pytest.raises(ValueError, match="verification_evidence"):
            SP._resolve_layout(_table("spec_example", ev), "L")


def test_schema_only_needs_no_evidence():
    """schema_only 不触发证据闸（库内全部布局当前都是 schema_only）。"""
    layout = SP._resolve_layout(_table("schema_only", None), "L")
    assert layout["verification"] == "schema_only"