# SPDX-License-Identifier: MIT
"""code_editor 写工具外部确认 token 门回归（phase64；S13 迁移）。

背景：docs/audit_r2/38_llm_judge_code_editor.md:21 早已登记
"code_modify_file required 仅 file_path、new_content 缺省空串 → 清空文件"，
36_agent_part2.md:81 登记"agent.py 层无确认回调——纯靠模型自觉"。
phase64 实测坐实：tests/ 根 dry-call 以 file_path="README.md" 调
code_modify_file（new_content 缺省空串）把 tracked README 清空，随后
code_git_commit（files 缺省 → git add -A）把破坏连同脏工作区自动提交。
S13 后模型自报 ``arm_manual=true`` 不再构成确认：写工具只认外部确认通道
签发的一次性 ``confirm_token``。本测试钉死门的行为：schema 声明、
缺 token 拒执行、自报 arm_manual 拒执行、持有效 token 后可穿越。
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

# 覆盖四工具所需必填参数的并集（缺 confirm_token 时都应被拒）
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


@pytest.fixture(autouse=True)
def _clean_gate():
    from mbdsdr_ai.tx_guard import reset_tx_gate
    reset_tx_gate()
    yield
    reset_tx_gate()


@pytest.fixture(scope="module")
def readme_bytes():
    with open(_README, "rb") as f:
        return f.read()


def _assert_readme_untouched(readme_bytes):
    with open(_README, "rb") as f:
        assert f.read() == readme_bytes, "tracked README.md 被测试改动"


class TestWriteToolTokenGate:
    def test_gated_tools_declare_confirm_token(self, agent):
        """四个写工具 schema 必须声明 confirm_token 且进 required；
        ``arm_manual`` 不得再出现在 schema（假接口）。"""
        tr = agent.tool_registry
        for name in _GATED_TOOLS:
            defn = tr.tools[name]["definition"]["function"]
            props = defn["parameters"]["properties"]
            assert "confirm_token" in props, f"{name} 缺少 confirm_token 参数声明"
            assert "confirm_token" in defn["parameters"]["required"], \
                f"{name} 的 confirm_token 未进 required"
            assert "arm_manual" not in props, f"{name} schema 仍暴露 arm_manual"
            assert "arm_manual" not in defn["parameters"]["required"]

    @pytest.mark.parametrize("tool", _GATED_TOOLS)
    def test_missing_confirm_token_refused(self, agent, tool, readme_bytes):
        """缺 confirm_token：registry 预检即拒，不触达 handler；README 不动。"""
        res = agent.tool_registry.call(tool, dict(_ARGS_BASE))
        assert res.success is False
        assert "confirm_token" in res.content
        _assert_readme_untouched(readme_bytes)

    @pytest.mark.parametrize("tool", _GATED_TOOLS)
    def test_model_self_reported_arm_manual_refused(self, agent, tool, readme_bytes):
        """模型自报 arm_manual=true、无有效 token：handler 门拒，gated 信封。"""
        args = dict(_ARGS_BASE, arm_manual=True, confirm_token="")
        res = agent.tool_registry.call(tool, args)
        assert res.success is False
        assert (res.data or {}).get("gated") is True
        # 显式拒绝码：arm_manual 已不支持，不再被当作任何形式的确认。
        assert (res.data or {}).get("reason") == "gate_arm_manual_untrusted"
        assert "未执行" in res.content
        _assert_readme_untouched(readme_bytes)

    def test_token_armed_call_passes_gate_editor_contains_path(self, agent, readme_bytes):
        """持外部确认 token 穿过门到达 editor；editor 自身 containment 兜底——
        越界路径被拒。证明门拒的是"无 token"，不是把执行路径弄坏。"""
        from mbdsdr_ai.tx_guard import issue_confirmation
        args = {
            "file_path": "../../../../tmp/mbdsdr_gate_probe.txt",
            "new_content": "should never land",
        }
        token = issue_confirmation("code_modify_file", args, ttl_s=10)
        res = agent.tool_registry.call("code_modify_file",
                                       dict(args, confirm_token=token))
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
