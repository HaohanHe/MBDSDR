# SPDX-License-Identifier: MIT
"""desktop/ai_steps.py 纯逻辑行为测试（Qt 无关）。

覆盖「桌面 AI 过程可视性」的投影语义（字段对齐 cpp StepResult / mobile TaskStep）：
1. 状态只由真实结果推导：无结果 = running，绝不臆造 success；
2. 成功/失败分类（显式 success 标志 + "Error..."/{"ok": false} 文本判据）；
3. 关键参数/摘要截断长度取自 tokens.AI（不再散落魔法数字）；
4. workflow_execute / subagent_execute 多步 JSON 结果投影为子步骤卡，
   子代理无单步结果时如实标注「单步结果未回传」。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import json

import ai_steps
from tokens import tokens


def _chat_result(content="", success=True, latency_ms=0.0, error="", args=None):
    """构造 ToolResult.to_dict() 形状的结果（与 mbdsdr_ai/tool_registry 一致）。"""
    return {
        "success": success,
        "content": content,
        "tool_name": "",
        "args": args or {},
        "latency_ms": latency_ms,
        "error": error,
        "truncated": False,
        "data": {},
        "next_step": "",
    }


class TestStepsFromChat:
    def test_success_step_carries_tool_args_summary_latency(self):
        calls = [{"name": "tune_fm", "arguments": '{"freq_mhz": 98.5}'}]
        results = [_chat_result('{"ok": true}', latency_ms=12.5)]
        steps = ai_steps.steps_from_chat(calls, results)
        assert len(steps) == 1
        s = steps[0]
        assert s.tool == "tune_fm"
        assert s.status == ai_steps.STATUS_SUCCESS
        assert "98.5" in s.args_preview
        assert s.elapsed_ms == 12.5

    def test_failure_from_success_flag(self):
        calls = [{"name": "sdr_record_start", "arguments": "{}"}]
        results = [_chat_result("设备未连接", success=False, error="not_connected")]
        steps = ai_steps.steps_from_chat(calls, results)
        assert steps[0].status == ai_steps.STATUS_FAILED
        assert steps[0].error == "not_connected"

    def test_failure_from_ok_false_text(self):
        """success 标志为真但结果体显式 {"ok": false} 时仍判失败（对齐 mobile）。"""
        calls = [{"name": "radio_ptt", "arguments": "{}"}]
        results = [_chat_result('{"ok": false, "error": "只收电台"}')]
        steps = ai_steps.steps_from_chat(calls, results)
        assert steps[0].status == ai_steps.STATUS_FAILED

    def test_missing_result_is_running_not_invented(self):
        """结果缺失（流被打断）→ running，绝不臆造成功/失败。"""
        calls = [{"name": "get_status", "arguments": "{}"},
                 {"name": "get_gps", "arguments": "{}"}]
        results = [_chat_result('{"ok": true}')]  # 只有一条结果
        steps = ai_steps.steps_from_chat(calls, results)
        assert steps[0].status == ai_steps.STATUS_SUCCESS
        assert steps[1].status == ai_steps.STATUS_RUNNING
        assert steps[1].summary == ""

    def test_args_preview_truncated_by_tokens(self):
        limit = int(tokens().AI["args_preview_chars"])
        calls = [{"name": "t", "arguments": json.dumps({"x": "a" * (limit + 50)})}]
        results = [_chat_result('{"ok": true}')]
        steps = ai_steps.steps_from_chat(calls, results)
        assert len(steps[0].args_preview) == limit + 1  # + 省略号
        assert steps[0].args_preview.endswith("…")

    def test_summary_truncated_by_tokens(self):
        limit = int(tokens().AI["summary_chars"])
        calls = [{"name": "t", "arguments": "{}"}]
        results = [_chat_result("b" * (limit + 30))]
        steps = ai_steps.steps_from_chat(calls, results)
        assert len(steps[0].summary) == limit + 1


class TestSummarySemantics:
    def test_summary_prefers_error_field(self):
        text = '{"ok": false, "error": "设备被占用"}'
        assert ai_steps.summarize_result(text) == "设备被占用"

    def test_summary_message_field(self):
        text = '{"message": "已调谐"}'
        assert ai_steps.summarize_result(text) == "已调谐"

    def test_summary_empty_text_is_empty(self):
        assert ai_steps.summarize_result("") == ""

    def test_is_failure_error_prefix_and_ok_false(self):
        assert ai_steps.is_failure_result("Error: 参数非法")
        assert ai_steps.is_failure_result('{"ok":false}')
        assert ai_steps.is_failure_result('{"ok" : false}')
        assert not ai_steps.is_failure_result('{"ok": true}')
        assert not ai_steps.is_failure_result("")


class TestWorkflowProjection:
    def test_step_results_become_child_cards(self):
        payload = {
            "workflow_name": "ai_sweep_find_stations",
            "success": False,
            "step_results": [
                {"step_id": 1, "tool": "sdr_ai_sweep", "success": True,
                 "result": '{"peaks": 3}'},
                {"step_id": 2, "tool": "sdr_spectrum_find_signals",
                 "success": False, "error": "阈值太高", "attempts": 3},
                {"step_id": 3, "tool": "sdr_set_demod", "skipped": True,
                 "reason": "条件不满足: x"},
            ],
        }
        children = ai_steps.steps_from_workflow(payload)
        assert [c.tool for c in children] == [
            "sdr_ai_sweep", "sdr_spectrum_find_signals", "sdr_set_demod"]
        assert [c.status for c in children] == [
            ai_steps.STATUS_SUCCESS, ai_steps.STATUS_FAILED, ai_steps.STATUS_SKIPPED]
        assert all(c.parent == "workflow_execute" and c.depth == 1 for c in children)
        assert children[1].error == "阈值太高"

    def test_workflow_tool_expands_children_in_chat(self):
        wf = {"workflow_name": "wf", "success": True,
              "step_results": [{"step_id": 1, "tool": "sdr_set_frequency",
                                "success": True, "result": "ok"}]}
        calls = [{"name": "workflow_execute",
                  "arguments": '{"workflow_name": "wf"}'}]
        results = [_chat_result(json.dumps(wf))]
        steps = ai_steps.steps_from_chat(calls, results)
        assert len(steps) == 2
        assert steps[0].tool == "workflow_execute"
        assert steps[1].tool == "sdr_set_frequency"
        assert steps[1].parent == "workflow_execute"
        assert steps[1].depth == 1


class TestSubagentProjection:
    def test_tools_used_become_child_cards_with_honest_note(self):
        payload = {
            "success": True, "status": "ok", "truncated": False,
            "subagent_id": "sa-1", "tools_used": ["sdr_status", "vfo_list"],
        }
        children = ai_steps.steps_from_subagent(payload)
        assert [c.tool for c in children] == ["sdr_status", "vfo_list"]
        assert all(c.status == ai_steps.STATUS_SUCCESS for c in children)
        # 无单步结果：摘要必须如实说明，不编造单步结果
        assert all("单步结果未回传" in c.summary for c in children)
        assert all(c.parent == "subagent_execute" and c.depth == 1 for c in children)

    def test_truncated_partial_is_failed_with_note(self):
        payload = {"success": False, "status": "truncated", "truncated": True,
                   "tools_used": ["sdr_spectrum_analyze"]}
        children = ai_steps.steps_from_subagent(payload)
        assert children[0].status == ai_steps.STATUS_FAILED
        assert "截断" in children[0].summary

    def test_subagent_tool_expands_children_in_chat(self):
        payload = {"success": True, "status": "ok", "tools_used": ["sdr_status"]}
        calls = [{"name": "subagent_execute", "arguments": "{}"}]
        results = [_chat_result(json.dumps(payload))]
        steps = ai_steps.steps_from_chat(calls, results)
        assert len(steps) == 2
        assert steps[1].parent == "subagent_execute"


class TestStepsFromToolResult:
    def test_error_result_is_failed(self):
        s = ai_steps.steps_from_tool_result("tune_fm", {"error": "未连接"})
        assert s.status == ai_steps.STATUS_FAILED
        assert s.error == "未连接"

    def test_skipped_is_not_hardware_execution(self):
        s = ai_steps.steps_from_tool_result(
            "workflow_list", {"skipped": True, "reason": "软件工具不二次转发"})
        assert s.status == ai_steps.STATUS_SKIPPED
        assert "不二次转发" in s.summary

    def test_plain_result_is_success(self):
        s = ai_steps.steps_from_tool_result("get_version", {"firmware": "0.6.0"})
        assert s.status == ai_steps.STATUS_SUCCESS
        assert s.tool == "get_version"
