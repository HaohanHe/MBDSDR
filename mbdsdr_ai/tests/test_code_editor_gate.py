# SPDX-License-Identifier: MIT
"""code_editor 写工具 arm_manual 门回归（phase64）。

背景：docs/audit_r2/38_llm_judge_code_editor.md:21 早已登记
"code_modify_file required 仅 file_path、new_content 缺省空串 → 清空文件"，
36_agent_part2.md:81 登记"agent.py 层无确认回调——纯靠模型自觉"。
phase64 实测坐实：tests/ 根 dry-call 以 file_path="README.md" 调
code_modify_file（new_content 缺省空串）把 tracked README 清空，随后
code_git_commit（files 缺省 → git add -A）把破坏连同脏工作区自动提交。
本测试钉死门的三条行为：schema 声明、未武装拒执行、武装后可穿越。
"""
from __future__ import annotations
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from mbdsdr_ai import MBDSDRAgent, AgentConfig  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_README = os.path.join(_REPO_ROOT, "README.md")

# 四个写工具：改文件 / 改段 / 提交 / 回滚
_GATED_TOOLS = ("code_modify_file", "code_modify_section", "code_git_commit", "code_rollback")

# 覆盖四工具所需必填参数的并集（缺 arm_manual 时都应被拒）
_ARGS_BASE = {
    "file_path": "README.md",
    "message": "gate-probe",
    "old_section": "a",
    "new_section": "b",
    "edit_id": "no_such_edit",
}


@pytest.fixture(scope="module")
def agent():
    return MBDSDRAgent(AgentConfig())


@pytest.fixture(scope="module")
def readme_bytes():
    with open(_README, "rb") as f:
        return f.read()


def _assert_readme_untouched(readme_bytes):
    with open(_README, "rb") as f:
        assert f.read() == readme_bytes, "tracked README.md 被测试改动"


class TestWriteToolArmGate:
    def test_gated_tools_declare_arm_manual(self, agent):
        """四个写工具 schema 必须声明 arm_manual 且进 required（缺参即拒）。"""
        tr = agent.tool_registry
        for name in _GATED_TOOLS:
            defn = tr.tools[name]["definition"]["function"]
            props = defn["parameters"]["properties"]
            assert "arm_manual" in props, f"{name} 缺少 arm_manual 参数声明"
            assert "arm_manual" in defn["parameters"]["required"], \
                f"{name} 的 arm_manual 未进 required"

    @pytest.mark.parametrize("tool", _GATED_TOOLS)
    def test_missing_arm_manual_refused(self, agent, tool, readme_bytes):
        """缺 arm_manual：registry 预检即拒，不触达 handler；README 不动。"""
        res = agent.tool_registry.call(tool, dict(_ARGS_BASE))
        assert res.success is False
        assert "arm_manual" in res.content
        _assert_readme_untouched(readme_bytes)

    @pytest.mark.parametrize("tool", _GATED_TOOLS)
    def test_explicit_false_refused_with_gated_envelope(self, agent, tool, readme_bytes):
        """显式 arm_manual=false：gated 信封（data.gated），不假装成功。"""
        args = dict(_ARGS_BASE, arm_manual=False)
        res = agent.tool_registry.call(tool, args)
        assert res.success is False
        assert (res.data or {}).get("gated") is True
        assert "未执行" in res.content
        _assert_readme_untouched(readme_bytes)

    def test_armed_call_passes_gate_editor_contains_path(self, agent, readme_bytes):
        """armed=true 穿过门到达 editor；editor 自身 containment 兜底——
        越界路径被拒。证明门拒的是"未武装"，不是把执行路径弄坏。"""
        res = agent.tool_registry.call("code_modify_file", {
            "file_path": "../../../../tmp/mbdsdr_gate_probe.txt",
            "new_content": "should never land",
            "arm_manual": True,
        })
        assert res.success is False
        assert "越界" in res.content or "安全限制" in res.content
        assert not os.path.exists("/tmp/mbdsdr_gate_probe.txt")
        _assert_readme_untouched(readme_bytes)

    def test_editor_mechanics_intact_when_used_directly(self, tmp_path):
        """底层 CodeEditor 直调机械正常（tmp 项目根内真写 + 备份 + 可回滚）：
        证明门拦的是"未武装调用"，不是把编辑器本身弄坏。"""
        from mbdsdr_ai.code_editor import CodeEditor
        target = tmp_path / "probe.py"
        target.write_text("old\n", encoding="utf-8")
        ce = CodeEditor(project_root=str(tmp_path),
                        backup_dir=str(tmp_path / "bak"), git_enabled=False)
        rec = ce.modify_file("probe.py", "new\n", "gate mechanics probe")
        assert target.read_text(encoding="utf-8") == "new\n"
        assert rec.edit_id
        assert list((tmp_path / "bak").glob("probe.py.*.bak")), "修改前备份应存在"
        ok, _msg = ce.rollback(rec.edit_id)
        assert ok
        assert target.read_text(encoding="utf-8") == "old\n"
