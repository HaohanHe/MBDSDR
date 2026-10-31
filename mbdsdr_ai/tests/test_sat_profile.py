# SPDX-License-Identifier: MIT
"""卫星参数库/帧布局配置面测试（P0-1 钉扎层）。

红线（「算法进工具、皮进配置」）：
  * ``sat_profiles.json`` 里的卫星身份数值（频点/符号率/同步字）
    **不得**出现在任何 Python 源码里——grep 级防回归，命中即红；
  * 同一套接收链代码换两套卫星配置各自正确工作（换星只换配置）；
  * 未知星名/布局名诚实报错并列名单，不猜不静默回退；
  * crc16_ccsds 预置在 CRC 内核注册面（TC 帧 golden 的依赖）。
"""
import json
from pathlib import Path

import pytest

from mbdsdr_ai import sat_profile as SP

PKG_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PKG_DIR / "data"

#: 通用 SDR 采样率，不作为卫星身份数值参与防回归扫描
_GENERIC_RATES = {"48000", "250000", "1000000", "4800", "24000", "96000"}


def _profile_literals() -> set:
    """sat_profiles.json 里的卫星身份数值（字符串形），用于源码扫描。

    口径只取**身份性**数值：频点（≥1e7——不与任何通用 SDR/LLM 常量撞，
    此前实测 128000/120000/1e6/250000 均为通用值误报）与同步字 hex。
    baud/alpha/增益/symrate/resample 比值是通用 DSP 值，不参与扫描。
    CCSDS ASM 是公开标准常数，允许作为链路默认值存在（ccsds_rx 等
    先于参数库落地，标准默认 ≠ 卫星皮）。
    """
    table = json.loads((DATA_DIR / "sat_profiles.json").read_text("utf-8"))
    lits: set = set()
    for name, prof in table.items():
        if name.startswith("_"):
            continue

        def walk(node):
            if isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
            elif isinstance(node, (int, float)):
                if abs(node) >= 1e7:
                    lits.add(repr(node))
        walk(prof)
    return lits


def test_no_satellite_literals_in_python_source():
    """卫星身份数值只存在于配置 JSON，源码 grep 级零命中。"""
    lits = _profile_literals()
    assert lits, "参数库里应至少有一些身份数值（配置表非空）"
    hits = []
    for py in sorted(PKG_DIR.glob("*.py")):
        src = py.read_text(encoding="utf-8")
        for lit in lits:
            if lit in src:
                hits.append(f"{py.name}: {lit}")
    assert not hits, (
        "卫星身份数值泄漏进源码（应只存在于 data/sat_profiles.json）：\n  "
        + "\n  ".join(hits))


def test_profiles_have_sources():
    """参数库每条数值带 source 可追溯（诚实纪律钉扎）。"""
    table = json.loads((DATA_DIR / "sat_profiles.json").read_text("utf-8"))
    missing = []
    for name, prof in table.items():
        if name.startswith("_"):
            continue
        if "source" not in prof:
            missing.append(name)
    assert not missing, f"剖面缺 source: {missing}"


def test_unknown_profile_honest_error():
    with pytest.raises(ValueError, match="未知卫星参数剖面"):
        SP.load_sat_profile("no_such_sat_xyz")
    with pytest.raises(ValueError, match="未知帧布局"):
        SP.load_frame_layout("no_such_layout_xyz")


def test_profile_swap_same_code_path():
    """换星只换配置：astu_1 与 gk2a 两套剖面喂同一条 CCSDS 编解码链，
    各自自环正确（编码→解码，帧内容一致）。"""
    from mbdsdr_ai.ccsds_tools import ccsds_decode_bits, ccsds_encode_frame

    results = {}
    for name in ("astu_1", "gk2a_lrit"):
        prof = SP.load_sat_profile(name)
        args = SP.profile_to_rx_chain_args(prof)
        assert args.get("asm_word"), f"{name} 剖面应给出 ASM 同步字"
        payload = bytes(range(1, 32))
        # 信息位长显式给（链路工具不猜帧长——诚实纪律）
        chain = {**args, "info_bits": len(payload) * 8}
        enc = ccsds_encode_frame({**chain, "data": payload.hex()})
        dec = ccsds_decode_bits({**chain, "bits": enc["bits"]})
        assert dec["count"] == 1, f"{name}: 解码 0 帧（{dec.get('diagnostic')}）"
        assert bytes.fromhex(dec["frames"][0]["hex"]) == payload
        results[name] = args
    # 两套配置必须真的不同（换配置 ≠ 同配置换名）
    assert results["astu_1"] != results["gk2a_lrit"]


def test_frame_layout_load_fields():
    """astu_1_tm_example 布局：字段/同步字/CRC 齐全且带 honesty 标注。"""
    layout = SP.load_frame_layout("astu_1_tm_example")
    assert layout["sync"]["hex"].upper() == "1ACFFC1D"
    assert layout["crc"]["name"] == "crc16_ccitt_false"
    assert "example" in layout["honesty"] or "示例" in layout["honesty"]
    names = [f["name"] for f in layout["fields"]]
    assert "battery_mv" in names and "callsign" in names


def test_crc16_ccsds_registered():
    """crc16_ccsds 预置已挂进 CRC 内核注册面（TC 帧构建的依赖）。"""
    from mbdsdr_ai.crc import CRC16_CCSDS, CrcParams, _preset
    assert isinstance(CRC16_CCSDS, CrcParams)
    assert CRC16_CCSDS.poly == 0x1021 and CRC16_CCSDS.init == 0xFFFF
    assert not CRC16_CCSDS.refin and CRC16_CCSDS.xorout == 0x0000
    assert _preset("crc16_ccsds") is CRC16_CCSDS
    # 公开已知检查值钉扎（CRC16/CCITT-FALSE 同参数，check("123456789")=0x29B1）
    from mbdsdr_ai.crc import crc_generic
    assert crc_generic(b"123456789", CRC16_CCSDS) == 0x29B1


def test_tools_registered():
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    names = {t["name"] for t in r.list_tools()}
    for want in ("sat_profile_list", "sat_profile_load",
                 "frame_layout_list", "frame_layout_load"):
        assert want in names, f"工具 {want} 未注册"


def test_tool_handlers_roundtrip():
    """工具 handler 级：list → load → 剖面翻译参数可用。"""
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    lst = r.tools["sat_profile_list"]["handler"]({})
    assert lst.success and "astu_1" in lst.data["profiles"]
    prof = r.tools["sat_profile_load"]["handler"]({"name": "astu_1"})
    assert prof.success
    args = SP.profile_to_rx_chain_args(prof.data)
    assert args["scheme"] == "bpsk"
    assert args["asm_word"] == 0x1ACFFC1D
    bad = r.tools["sat_profile_load"]["handler"]({"name": "nope"})
    assert not bad.success
