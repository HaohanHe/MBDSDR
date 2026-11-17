# SPDX-License-Identifier: MIT
"""工具面单一事实源守卫（Task 33/34/61）。

四方口径：

  * registry（``ToolRegistry``，模型可见工具的唯一注册源）；
  * 能力目录（``desktop/capability_catalog.py`` 的
    ``tool_registry_capabilities``）；
  * 文档台账（``docs/tool-availability.md`` 的逐项总表）；
  * MCP 面（``MCPToolSet`` / ``_build_tool_table``）。

断言 registry ↔ 能力目录 ↔ 文档双向差为 0；MCP 与 registry 的差异必须
恰好等于 ``mbdsdr_ai/core/tool_surface_map.py`` 里已登记的白名单。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest  # noqa: E402

import tool_surface_audit as audit  # noqa: E402
from capability_catalog import tool_registry_capabilities  # noqa: E402
from mbdsdr_ai.core import tool_surface_map as surface_map  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402


@pytest.fixture(scope="module")
def registry():
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.config import AgentConfig
    from mbdsdr_ai.agent import MBDSDRAgent

    original = _mm.ModelManager.fetch_models
    _mm.ModelManager.fetch_models = lambda self, *a, **k: None
    try:
        agent = MBDSDRAgent(AgentConfig(api_key="sk-test",
                                        model="test-model"))
    finally:
        _mm.ModelManager.fetch_models = original
    return agent.tool_registry


@pytest.fixture(scope="module")
def diff(registry):
    return surface_map.diff_surfaces(audit.mcp_tool_names(),
                                     set(registry.tools))


# ---------------------------------------------------------------------------
# Task 33：能力目录覆盖全部 registry 工具
# ---------------------------------------------------------------------------
def test_catalog_covers_every_registry_tool(registry):
    from capability_catalog import build_catalog, build_full_catalog

    caps = tool_registry_capabilities(registry)
    got = {c.source.split(":", 1)[1] for c in caps
           if c.source.startswith("tool_registry:")}
    assert got == set(registry.tools), (
        f"能力目录与 registry 双向差：缺 {sorted(set(registry.tools) - got)}，"
        f"多 {sorted(got - set(registry.tools))}")
    assert len(caps) == len(registry.tools)
    # 全量入口 = 既有目录（MCP 域汇总等）+ 逐工具行，既有入口不变
    assert len(build_full_catalog(registry)) == len(build_catalog()) + len(caps)


def test_catalog_marks_domain_read_write_availability(registry):
    caps = {c.source.split(":", 1)[1]: c
            for c in tool_registry_capabilities(registry)}
    writes = {name for name, tool in registry.tools.items()
              if tool.get("write")}
    assert {name for name, c in caps.items() if c.access == "write"} == writes
    for name, tool in registry.tools.items():
        cap = caps[name]
        assert cap.domain, f"{name} 缺域标注"
        assert cap.status == ("available" if tool.get("available", True)
                              else "unavailable")
        if cap.access == "write":
            assert "confirm_token" in cap.note


def test_unclassified_tool_falls_into_ungrouped_bucket():
    reg = ToolRegistry()
    reg.register(name="zz_no_domain", description="无分类工具",
                 parameters={"type": "object", "properties": {}},
                 handler=lambda a: "ok", category="")
    cap = tool_registry_capabilities(reg)[0]
    assert cap.source == "tool_registry:zz_no_domain"
    assert cap.domain == "未分组"


# ---------------------------------------------------------------------------
# Task 34/61：MCP 子集映射与差异白名单
# ---------------------------------------------------------------------------
def test_mcp_surface_exists_as_declared_subset(registry, diff):
    """MCP 面不是 registry 的全集：差异必须恰好等于已登记白名单。"""
    assert set(diff.mcp_only) == surface_map.MCP_ONLY_WHITELIST
    assert set(diff.registry_only) == surface_map.REGISTRY_ONLY_WHITELIST
    assert diff.clean

    for mcp_name, reg_name in diff.mapped.items():
        assert reg_name in registry.tools
        # 映射是别名解析规则的结果，不是手抄快照
        assert surface_map.resolve_mcp_tool(mcp_name, registry) == reg_name


def test_mapping_entries_have_no_stale_or_missing_targets(diff):
    assert not diff.mapping_missing_in_registry
    assert not diff.mapping_stale
    assert not diff.mcp_undeclared


# ---------------------------------------------------------------------------
# 巡检脚本（Task 61）
# ---------------------------------------------------------------------------
def test_audit_script_reports_clean(registry, capsys):
    assert audit.main([]) == 0
    out = capsys.readouterr().out
    assert f"registry={len(registry.tools)}" in out
    assert "结论：一致" in out


def test_audit_script_json_shape(capsys):
    assert audit.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["counts"]["registry"] == report["counts"]["catalog"]
    assert report["counts"]["registry"] == report["counts"]["docs"]
    assert report["mcp"]["clean"] is True


def test_audit_script_fails_and_lists_doc_drift(monkeypatch, capsys):
    """反证：文档少一个工具 → 退出 1 并打印名字。"""
    original = audit.doc_tool_names
    dropped = []

    def drifted(path=audit.DOC):
        names = original(path)
        dropped.append(names[-1])
        return names[:-1]

    monkeypatch.setattr(audit, "doc_tool_names", drifted)
    assert audit.main([]) == 1
    out = capsys.readouterr().out
    assert "registry 有、文档缺" in out
    assert dropped and dropped[0] in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
