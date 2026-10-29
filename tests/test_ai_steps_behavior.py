# SPDX-License-Identifier: MIT
"""desktop/ai_steps.py 诚实语义补测（Qt 无关）。

与既有 tests/test_ai_steps.py 的分工：那份覆盖 happy path（成功/失败/缺结果/
多步展开）；本文件专盯**投影层的失败/成功判据是否说谎**：

1. steps_from_tool_result 的失败判据（"error" 键取真值 vs 键存在即可）与
   steps_from_chat 必须同口径——否则同一份结果两条路径画出两种卡；
2. 错误信封、None、标量、缺 tool 名的 handling 不抛穿、不制造"假成功步骤"；
3. workflow_execute 子步骤：success:true 但子结果自己说失败时不得画 OK 卡
   （workflow_engine 只看 tool_executor 有没有抛异常，ToolResult.success=False
   的失败会带着 success:true 落到 step_results）；
4. subagent_execute：无单步结果时不补写单步成败理由。

跑法：python -m pytest tests/test_ai_steps_behavior.py
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import ai_steps  # noqa: E402
from tokens import tokens  # noqa: E402


def _chat_result(content="", success=True, latency_ms=0.0, error=""):
    return {"success": success, "content": content, "tool_name": "",
            "args": {}, "latency_ms": latency_ms, "error": error,
            "truncated": False, "data": {}, "next_step": ""}


class TestFailureCriteriaConsistency:
    """两条投影路径对同一份结果必须给出同一个判定。"""

    def test_null_error_key_is_not_a_failure(self):
        """{"error": None} 是空错误键，不是失败证据。"""
        payload = {"error": None, "firmware": "0.6.0"}
        step = ai_steps.steps_from_tool_result("get_version", dict(payload))
        assert step.status == ai_steps.STATUS_SUCCESS
        # 与 steps_from_chat 同口径（后者只看 error 取真值）
        from_chat = ai_steps.steps_from_chat(
            [{"name": "get_version", "arguments": "{}"}],
            [_chat_result(json.dumps(payload))])
        assert from_chat[0].status == step.status

    def test_empty_string_error_key_is_not_a_failure(self):
        step = ai_steps.steps_from_tool_result("get_status",
                                               {"error": "", "ok": True})
        assert step.status == ai_steps.STATUS_SUCCESS

    def test_non_empty_error_is_failed_and_carried(self):
        step = ai_steps.steps_from_tool_result("sdr_tune",
                                               {"error": "未连接"})
        assert step.status == ai_steps.STATUS_FAILED
        assert step.error == "未连接"

    def test_ok_false_payload_is_failed_even_without_error_key(self):
        step = ai_steps.steps_from_tool_result("radio_ptt",
                                               {"ok": False, "detail": "只收"})
        assert step.status == ai_steps.STATUS_FAILED

    def test_hardware_worker_error_envelope_shape(self):
        """mcp_worker 未连接时的真实形状 {"error": "未连接", "success": False}。"""
        step = ai_steps.steps_from_tool_result(
            "sdr_set_frequency", {"error": "未连接", "success": False})
        assert step.status == ai_steps.STATUS_FAILED
        assert "未连接" in step.summary

    def test_error_prefixed_value_is_classified_by_envelope_keys_only(self):
        """已知边界：Error: 前缀判据只作用于裸文本顶层内容，不扫字段值。

        steps_from_tool_result 的输入先 json.dumps 成串后以 "{" 开头，
        is_failure_result 的 startswith("Error") 分支恒不命中——这里如实钉住
        当前口径（显式 error 键 / ok:false 才算失败），不引入"扫字段值"的
        启发式误判。
        """
        step = ai_steps.steps_from_tool_result("sdr_record_start",
                                               {"message": "Error: 硬件忙"})
        assert step.status == ai_steps.STATUS_SUCCESS
        assert "Error: 硬件忙" in step.summary

    def test_skipped_wins_over_error_no_double_report(self):
        step = ai_steps.steps_from_tool_result(
            "workflow_list",
            {"skipped": True, "reason": "软件工具不二次转发", "error": "x"})
        assert step.status == ai_steps.STATUS_SKIPPED
        assert step.error == ""
        assert step.summary == "软件工具不二次转发"


class TestMalformedResultRobustness:
    def test_none_result_is_shown_not_swallowed(self):
        step = ai_steps.steps_from_tool_result("sdr_status", None)
        assert step.tool == "sdr_status"
        assert "None" in step.summary  # 如实呈现，不编空成功

    def test_scalar_result_is_wrapped(self):
        step = ai_steps.steps_from_tool_result("sdr_status", "ready")
        assert step.status == ai_steps.STATUS_SUCCESS
        assert "ready" in step.summary

    def test_tool_name_is_coerced_to_str(self):
        step = ai_steps.steps_from_tool_result(None, {"ok": True})
        assert step.tool == "None"

    def test_latency_is_taken_from_result(self):
        step = ai_steps.steps_from_tool_result("t", {"ok": True,
                                                     "latency_ms": 12.5})
        assert step.elapsed_ms == 12.5

    def test_long_cjk_summary_is_truncated(self):
        limit = int(tokens().AI["summary_chars"])
        step = ai_steps.steps_from_tool_result("t", {"error": "错" * (limit + 40)})
        assert len(step.summary) == limit + 1
        assert step.summary.endswith("…")


class TestWorkflowCrossCheck:
    def test_step_flagged_success_but_text_says_failure(self):
        """success:true 但子结果自己写 {"ok": false} -> 不得画 OK 卡。"""
        payload = {"workflow_name": "wf", "success": False, "step_results": [
            {"step_id": 1, "tool": "sdr_tune", "success": True,
             "result": '{"ok": false, "error": "设备忙"}'},
        ]}
        children = ai_steps.steps_from_workflow(payload)
        assert children[0].status == ai_steps.STATUS_FAILED
        assert "设备忙" in children[0].summary

    def test_step_flagged_success_with_error_prefixed_text(self):
        payload = {"step_results": [
            {"step_id": 1, "tool": "sdr_record_start", "success": True,
             "result": "Error: 参数非法"},
        ]}
        children = ai_steps.steps_from_workflow(payload)
        assert children[0].status == ai_steps.STATUS_FAILED

    def test_real_success_step_stays_success(self):
        payload = {"step_results": [
            {"step_id": 1, "tool": "sdr_set_frequency", "success": True,
             "result": '{"ok": true, "freq_hz": 435000000}'},
        ]}
        children = ai_steps.steps_from_workflow(payload)
        assert children[0].status == ai_steps.STATUS_SUCCESS
        assert children[0].summary != ""

    def test_empty_result_string_is_still_success_with_empty_summary(self):
        """workflow_engine 对 result 为假值时写 "" —— 成功但无摘要，不臆造。"""
        payload = {"step_results": [
            {"step_id": 1, "tool": "sdr_status", "success": True, "result": ""},
        ]}
        children = ai_steps.steps_from_workflow(payload)
        assert children[0].status == ai_steps.STATUS_SUCCESS
        assert children[0].summary == ""

    def test_non_dict_entries_are_skipped(self):
        payload = {"step_results": ["junk", None, 7]}
        assert ai_steps.steps_from_workflow(payload) == []

    def test_missing_step_results_is_empty(self):
        assert ai_steps.steps_from_workflow({}) == []
        assert ai_steps.steps_from_workflow({"step_results": None}) == []


class TestSubagentHonesty:
    def test_step_results_are_never_invented(self):
        payload = {"success": True, "status": "ok", "subagent_id": "sa-9",
                   "tools_used": ["sdr_status"]}
        children = ai_steps.steps_from_subagent(payload)
        assert len(children) == 1
        assert children[0].status == ai_steps.STATUS_SUCCESS
        assert children[0].summary == "子代理内部调用（单步结果未回传，随整体结果 ok）"
        assert children[0].error == ""
        assert children[0].extra["subagent_id"] == "sa-9"

    def test_legacy_tools_called_key_fallback(self):
        payload = {"success": True, "status": "ok", "tools_called": ["sdr_status"]}
        assert [c.tool for c in ai_steps.steps_from_subagent(payload)] == ["sdr_status"]

    def test_no_tools_used_yields_no_cards(self):
        for payload in ({}, {"success": True, "status": "ok"},
                        {"tools_used": None}):
            assert ai_steps.steps_from_subagent(payload) == []

    def test_overall_error_is_attached_to_child_cards(self):
        payload = {"success": False, "status": "error", "error": "LLM 调用失败",
                   "tools_used": ["sdr_status"]}
        child = ai_steps.steps_from_subagent(payload)[0]
        assert child.status == ai_steps.STATUS_FAILED
        assert child.error == "LLM 调用失败"


class TestChatProjectionEdges:
    def test_tool_call_without_name_is_unknown(self):
        steps = ai_steps.steps_from_chat([{"arguments": "{}"}],
                                         [_chat_result("ok")])
        assert steps[0].tool == "unknown"

    def test_non_dict_tool_calls_are_skipped(self):
        steps = ai_steps.steps_from_chat(["junk", None, {"name": "sdr_status"}],
                                         [_chat_result("ok")])
        assert len(steps) == 1 and steps[0].tool == "sdr_status"

    def test_unparseable_arguments_fall_back_to_empty_object(self):
        """JSON 解析失败的 arguments -> 空对象预览（"{}"，不是伪装成没有参数）。"""
        steps = ai_steps.steps_from_chat([{"name": "t", "arguments": "{bad"}],
                                         [_chat_result("ok")])
        assert steps[0].args_preview == "{}"

    def test_dict_content_is_json_dumped_for_summary(self):
        steps = ai_steps.steps_from_chat(
            [{"name": "t", "arguments": "{}"}],
            [_chat_result({"message": "已调谐"})])
        assert "已调谐" in steps[0].summary

    def test_null_tool_result_entry_treated_as_missing(self):
        """结果是 None（不是缺项）时按"未收到结果"落 running，不臆造成功。"""
        steps = ai_steps.steps_from_chat([{"name": "get_gps"}], [None])
        assert steps[0].status == ai_steps.STATUS_RUNNING
        assert steps[0].summary == ""

    def test_args_preview_of_none_and_list(self):
        assert ai_steps.args_preview(None) == ""
        assert json.loads(ai_steps.args_preview([1, 2])) == [1, 2]


class TestSubagentToolsShapeHonesty:
    """子代理只回传工具名列表——任何非数组形态都不得被展开成步骤卡。"""

    def test_bare_string_tools_used_does_not_fabricate_per_char_cards(self):
        """tools_used 是裸字符串时，按字符迭代会造出一堆不存在的成功卡。"""
        cards = ai_steps.steps_from_subagent(
            {"success": True, "status": "ok", "tools_used": "sdr_status"})
        assert cards == [], "非数组的 tools_used 不得展开: %r" % [
            c.tool for c in cards]

    def test_non_list_tools_used_shapes_yield_no_cards(self):
        for bad in (None, 7, {"a": 1}, True):
            cards = ai_steps.steps_from_subagent(
                {"success": True, "status": "ok", "tools_used": bad})
            assert cards == []

    def test_real_list_still_projects_one_card_per_tool(self):
        cards = ai_steps.steps_from_subagent(
            {"success": True, "status": "ok",
             "tools_used": ["sdr_status", "vfo_list"]})
        assert [c.tool for c in cards] == ["sdr_status", "vfo_list"]

    def test_success_true_with_non_ok_status_is_failed(self):
        """整体 status 与 success 打架时按 status（更严的一方）判失败。"""
        cards = ai_steps.steps_from_subagent(
            {"success": True, "status": "error", "tools_used": ["sdr_status"]})
        assert cards[0].status == ai_steps.STATUS_FAILED
        assert "整体结果失败" in cards[0].summary


class TestWorkflowNamingAndReasons:
    def test_tool_name_falls_back_to_tool_name_key_then_unknown(self):
        payload = {"step_results": [
            {"step_id": 1, "tool_name": "sdr_tune", "success": False,
             "error": "忙"},
            {"step_id": 2, "success": False, "error": "缺工具名"},
        ]}
        kids = ai_steps.steps_from_workflow(payload)
        assert [k.tool for k in kids] == ["sdr_tune", "unknown"]

    def test_skipped_without_reason_states_default_reason(self):
        payload = {"step_results": [{"step_id": 1, "tool": "sdr_ptt",
                                     "skipped": True}]}
        kid = ai_steps.steps_from_workflow(payload)[0]
        assert kid.status == ai_steps.STATUS_SKIPPED
        assert kid.summary == "条件不满足，未执行"

    def test_failed_step_without_error_text_says_step_failed(self):
        payload = {"step_results": [{"step_id": 1, "tool": "sdr_gain"}]}
        kid = ai_steps.steps_from_workflow(payload)[0]
        assert kid.status == ai_steps.STATUS_FAILED
        assert kid.summary == "步骤失败"


class TestTextToolEdges:
    def test_truncate_text_respects_disabled_limits(self):
        s = "abc" * 10
        assert ai_steps.truncate_text(s, 0) == s
        assert ai_steps.truncate_text(s, -1) == s
        assert ai_steps.truncate_text(None, 5) == ""

    def test_summary_takes_first_error_or_message_key_in_text_order(self):
        """摘要取文本里首个命中的 error/message 键（re.search，不看键优先级）。"""
        assert ai_steps.summarize_result(
            '{"message": "先出现", "error": "后出现"}') == "先出现"
        assert ai_steps.summarize_result(
            '{"error": "先出现", "message": "后出现"}') == "先出现"

    def test_args_preview_of_unserializable_object_falls_back_to_repr(self):
        class Weird:
            def __repr__(self):
                return "Weird()"

        preview = ai_steps.args_preview({"x": Weird()})
        assert preview, "不可序列化的参数不得退化成空预览"
        assert "x" in preview and "Weird" in preview

    def test_is_failure_result_ignores_whitespace_only_and_ok_true(self):
        assert not ai_steps.is_failure_result("   ")
        assert not ai_steps.is_failure_result('{"ok" : true}')
        assert ai_steps.is_failure_result("Error: timeout")
        assert ai_steps.is_failure_result('{"ok"  :  false}')
