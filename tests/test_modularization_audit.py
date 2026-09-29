#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
test_modularization_audit.py — 四张注册表开放度冒烟测试
======================================================

守护"新增一个 X 是否只改注册一处、不动核心 switch"：
  - decoder_registry：register 一个假解码器后 list_decoders 包含它；
  - tool_registry：register 一个假工具后 list_tools 包含它；
  - panel_registry：register 一个假面板后 list 包含它；
  - skill_registry：SkillRegistry 能扫描到 skills/ 目录。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_modularization_audit.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


# ─────────────────────────────────────────────────────────────────────
# decoder_registry
# ─────────────────────────────────────────────────────────────────────
def test_decoder_registry_open():
    from mbdsdr_ai import decoder_registry as dr
    fake_mode = "ZZFAKEDEC"
    dr._REGISTRY.pop(fake_mode, None)
    try:
        before = {d.mode for d in dr.list_decoders()}
        dr.register(dr.DecoderDescriptor(
            mode=fake_mode,
            description="审计用假解码器",
            module="mbdsdr_ai.pocsag_decoder",   # 复用一个存在的模块（不真正 decode）
            decode_func="pocsag_decode",
            freq_bands_mhz=[(100.0, 200.0)],
        ))
        after = {d.mode for d in dr.list_decoders()}
        assert fake_mode in after - before or fake_mode in after
        # get / select_by_frequency 也应能查到
        assert dr.get_decoder(fake_mode) is not None
        assert any(d.mode == fake_mode for d in dr.select_by_frequency(150.0))
    finally:
        dr._REGISTRY.pop(fake_mode, None)


def test_decoder_registry_builtin_present():
    from mbdsdr_ai import decoder_registry as dr
    modes = {d.mode for d in dr.list_decoders()}
    assert {"POCSAG", "ACARS", "VOR"} <= modes


# ─────────────────────────────────────────────────────────────────────
# tool_registry
# ─────────────────────────────────────────────────────────────────────
def test_tool_registry_open():
    from mbdsdr_ai.tool_registry import ToolRegistry
    tr = ToolRegistry()
    name = "zz_fake_audit_tool"
    tr.register(
        name=name,
        description="审计用假工具",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: "ok",
        category="audit",
    )
    names = [t.get("name") for t in tr.list_tools()]
    assert name in names
    # 调度也应走 registry.call 查表
    res = tr.call(name, {})
    assert res.success, f"假工具调用失败: {res.content}"


# ─────────────────────────────────────────────────────────────────────
# panel_registry
# ─────────────────────────────────────────────────────────────────────
def test_panel_registry_open():
    from panel_registry import registry, PanelSpec, AREA_BOTTOM
    reg = registry()
    pid = "zz_fake_audit_panel"
    reg._specs.pop(pid, None)
    try:
        reg.register(PanelSpec(pid, "审计假面板", "?", AREA_BOTTOM))
        assert reg.get(pid) is not None
        assert pid in reg.ids()
        assert any(s.id == pid for s in reg.list())
    finally:
        reg._specs.pop(pid, None)


# ─────────────────────────────────────────────────────────────────────
# skill_registry
# ─────────────────────────────────────────────────────────────────────
def test_skill_registry_scan():
    from mbdsdr_ai.skill_registry import SkillRegistry
    skills_dir = os.path.join(_ROOT, "skills")
    sr = SkillRegistry(skills_dir)
    listed = sr.list()
    names = {s["name"] for s in listed}
    # 仓库 skills/ 目录下当前应有三个技能
    assert {"find-interference", "sat-track", "sstv-decode"} <= names, \
        f"扫描到的技能: {names}"
    # 至少一个技能能 load 出正文
    body = sr.load("find-interference")
    assert body and len(body) > 0
