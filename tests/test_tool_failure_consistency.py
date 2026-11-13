# SPDX-License-Identifier: MIT
"""S3：同一次工具失败，步骤卡 / 状态条 / Hooks 必须说同一件事。

事故（2026-11-13）：步骤卡按 ``ToolResult.success`` 显示失败，但对话栏
用 ``ok = bool(res) and not res.get("error")`` 现算；失败结果的 ``error``
默认是空串，于是同一次失败在 Hooks 里记 ``success: true``、在编排任务里
记 ``COMPLETED``——两处说的是「做成了」。

钉住三件事：
  1. ``tool_success`` 只认 ``success`` 字段；没有该字段时返回 None（未知），
     绝不默认成功。
  2. 一条失败结果经真实 ``AIPanel._on_ai_finished`` 链路后，步骤卡 / Hooks /
     编排任务三处一致为「失败」；成功结果三处一致为「成功」。
  3. 工具环没回结果（未执行）时，不登记任务、不写 tool_result 事件——
     不伪造任何一种结论。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)  # noqa: F401

import ai_steps  # noqa: E402
from desktop.ai_panel import AIPanel  # noqa: E402
from mbdsdr_ai.tool_results import ToolResult, tool_success  # noqa: E402


def _result_dict(success: bool, content: str = "", error: str = "") -> dict:
    """``ToolResult.to_dict()`` 形状（与 mbdsdr_ai/tool_results 一致）。"""
    return {"success": success, "content": content, "tool_name": "probe_tool",
            "args": {}, "latency_ms": 1.0, "error": error,
            "truncated": False, "data": {}, "next_step": ""}


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(AIPanel, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(AIPanel, "CONFIG_FILE", str(tmp_path / "config.json"))
    for k in ("MBDSDR_API_KEY", "MBDSDR_BASE_URL", "MBDSDR_MODEL",
              "MBDSDR_API_BASE"):
        monkeypatch.delenv(k, raising=False)
    from mbdsdr_ai import AgentConfig, MBDSDRAgent
    p = AIPanel()
    # 真实内核：真 tool_registry / orchestrator / hook_manager，不用替身
    p.agent = MBDSDRAgent(AgentConfig(api_key=""))
    yield p
    p.close()
    p.deleteLater()


def _run(panel, results, calls=None, content=""):
    panel._on_ai_finished({
        "content": content, "latency_ms": 1.0, "compacted": False,
        "tool_calls": calls or [{"name": "probe_tool", "arguments": "{}"}],
        "tool_results": results,
    })


def test_tool_success_reads_only_success_field():
    """成败唯一判据：error 空串的失败仍是失败；无 success 字段是「未知」。"""
    assert tool_success(ToolResult(False, "设备未响应")) is False
    assert tool_success(ToolResult(True, "ok")) is True
    assert tool_success({"success": False, "error": ""}) is False
    assert tool_success({"success": True, "error": "warning"}) is True
    assert tool_success(None) is None
    assert tool_success({"content": "没有 success 字段"}) is None


def test_failed_tool_is_failed_in_step_card_hooks_and_task(panel):
    """同一个失败结果（error 故意留空）三处必须一致为失败。"""
    _run(panel, [_result_dict(False, "设备未响应")])

    # 1) 步骤卡
    assert panel._tool_log[0].status == ai_steps.STATUS_FAILED

    # 2) Hooks：tool_result 事件必须记 success=False
    events = panel.agent.hook_manager.get_history("agent.tool_result", limit=5)
    assert events and events[-1].data.get("success") is False, \
        "Hooks 不能把这次失败记成 success:true"

    # 3) 编排任务（状态条 / 能力矩阵读的就是它）
    rows = panel._bridge_obj().live_state()["task_rows"]
    row = [r for r in rows if r["name"] == "probe_tool"][-1]
    assert row["status"] == "failed", "同一失败不得落 COMPLETED"

    # 状态条真的把 failed 渲染出来（不是只存在内存里）
    panel._refresh_live_bar()
    assert "failed" in panel.live_details.text()


def test_successful_tool_stays_success_everywhere(panel):
    """反方向不许误杀：成功结果三处仍为成功。"""
    _run(panel, [_result_dict(True, "已连接")])

    assert panel._tool_log[0].status == ai_steps.STATUS_SUCCESS
    events = panel.agent.hook_manager.get_history("agent.tool_result", limit=5)
    assert events and events[-1].data.get("success") is True
    rows = panel._bridge_obj().live_state()["task_rows"]
    row = [r for r in rows if r["name"] == "probe_tool"][-1]
    assert row["status"] == "completed"


def test_missing_result_is_not_recorded_as_success(panel):
    """工具环没回结果的调用：不登记任务、不写 tool_result（未执行 ≠ 成功）。"""
    _run(panel, [_result_dict(True, "ok")],
         calls=[{"name": "probe_tool", "arguments": "{}"},
                {"name": "probe_unexecuted", "arguments": "{}"}])

    rows = panel._bridge_obj().live_state()["task_rows"]
    assert [r["name"] for r in rows] == ["probe_tool"], \
        "没结果的调用不得被登记成任何结论"
    events = panel.agent.hook_manager.get_history("agent.tool_result", limit=10)
    assert all(e.data.get("tool") != "probe_unexecuted" for e in events)
    assert panel._tool_log[1].status == ai_steps.STATUS_RUNNING
