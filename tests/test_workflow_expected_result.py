# SPDX-License-Identifier: MIT
"""
tests/test_workflow_expected_result.py
======================================
#83 回归锁：WorkflowStep.expected_result 死参数真落地（结果校验）。

此前 expected_result 声明/从 JSON 加载/序列化回 JSON，但全仓库零消费——
与刚修的 step.timeout 同类的 §2 死参数 bug。现落地最小语义：非空时要求
步骤结果正文含该字面子串（大小写不敏感），不符按失败参与既有重试/失败接缝；
为空=不校验（零回归，所有既有工作流不受影响）。

钉住的事实（每条配反向用例）：
  - 空 expected_result→不校验，照常成功（向后兼容）。
  - 含期望片段→成功；大小写不敏感匹配。
  - 不含→步骤失败、failed_step 落账、后续步骤不跑、错误文案指明期望片段。
  - 不符参与重试：先不符后符→重试后成功；恒不符→重试耗尽失败；
    retry_on_failure=False→只调一次。
  - 与取消接缝协同：不符后重试等待前取消→不再重试，判 cancelled。
  - _result_matches 各形态（ToolResult/裸串/None/空期望）+ JSON 往返保真。

运行:
    PYTHONUTF8=1 QT_QPA_PLATFORM=offscreen MBDSDR_OFFLINE=1 \
        python -m pytest tests/test_workflow_expected_result.py -v
"""
import json
import os
import sys
import threading
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.workflow_engine import (  # noqa: E402
    Workflow, WorkflowStep, WorkflowEngine,
)


class _R:
    """最小 ToolResult 鸭子类型。"""
    def __init__(self, content="ok"):
        self.success = True
        self.content = content
        self.data = {}


def _engine(tmp_path, steps):
    we = WorkflowEngine(workflows_dir=str(tmp_path))
    we.workflows["flow"] = Workflow(name="flow", steps=steps)
    return we


def _step(step_id=1, expected="", max_retries=0, retry_on_failure=False,
          tool_name="t1"):
    return WorkflowStep(step_id=step_id, tool_name=tool_name,
                        expected_result=expected, max_retries=max_retries,
                        retry_on_failure=retry_on_failure)


# ── 校验语义 ────────────────────────────────────────────────────

class TestExpectedResultSemantics:
    def test_empty_expected_no_check(self, tmp_path):
        """反向：expected_result 为空→不校验，任意正文都成功（零回归）。"""
        we = _engine(tmp_path, [_step(expected="")])
        we.set_tool_executor(lambda name, params: _R("anything at all"))
        res = we.execute("flow")
        assert res.success is True
        assert res.completed_steps == 1

    def test_matching_substring_passes(self, tmp_path):
        we = _engine(tmp_path, [_step(expected="freq")])
        we.set_tool_executor(lambda name, params: _R("center freq 145.800 MHz"))
        res = we.execute("flow")
        assert res.success is True
        assert res.completed_steps == 1

    def test_case_insensitive_match(self, tmp_path):
        we = _engine(tmp_path, [_step(expected="SUCCESS")])
        we.set_tool_executor(lambda name, params: _R("operation success"))
        res = we.execute("flow")
        assert res.success is True

    def test_mismatch_fails_step_and_stops(self, tmp_path):
        """不含期望片段→步骤失败，后续步骤不跑，错误文案指明期望。"""
        we = _engine(tmp_path, [
            _step(step_id=1, expected="missing", tool_name="t1"),
            _step(step_id=2, tool_name="t2"),
        ])
        calls = []

        def _exec(name, params):
            calls.append(name)
            return _R("ok")

        we.set_tool_executor(_exec)
        res = we.execute("flow")

        assert res.success is False
        assert res.completed_steps == 0
        assert res.failed_step == 1
        assert calls == ["t1"], "第 1 步校验失败后第 2 步不应跑"
        assert "missing" in res.step_results[0]["error"]
        assert "期望片段" in res.step_results[0]["error"]

    def test_match_lets_subsequent_steps_run(self, tmp_path):
        """反向：校验通过→后续步骤照常执行。"""
        we = _engine(tmp_path, [
            _step(step_id=1, expected="ok", tool_name="t1"),
            _step(step_id=2, tool_name="t2"),
        ])
        calls = []

        def _exec(name, params):
            calls.append(name)
            return _R("ok")

        we.set_tool_executor(_exec)
        res = we.execute("flow")
        assert res.success is True
        assert res.completed_steps == 2
        assert calls == ["t1", "t2"]


# ── 与重试接缝协同 ──────────────────────────────────────────────

class TestExpectedResultRetry:
    def test_mismatch_then_match_retries_to_success(self, tmp_path):
        """先不符后符→重试后成功（不符走与工具异常同一条重试接缝）。"""
        we = _engine(tmp_path, [_step(expected="yes", max_retries=2,
                                      retry_on_failure=True)])
        contents = ["nope", "nope", "ok yes"]
        idx = {"i": 0}

        def _exec(name, params):
            c = contents[idx["i"]]
            idx["i"] += 1
            return _R(c)

        we.set_tool_executor(_exec)
        with patch("time.sleep"):  # 退避不真睡，保持快/确定
            res = we.execute("flow")

        assert res.success is True
        assert res.completed_steps == 1
        assert idx["i"] == 3, "应重试到第 3 次才匹配"

    def test_persistent_mismatch_exhausts_retries(self, tmp_path):
        we = _engine(tmp_path, [_step(expected="yes", max_retries=1,
                                      retry_on_failure=True)])
        calls = []

        def _exec(name, params):
            calls.append(name)
            return _R("nope")

        we.set_tool_executor(_exec)
        with patch("time.sleep"):
            res = we.execute("flow")

        assert res.success is False
        assert len(calls) == 2, "1 初始 + 1 重试"
        assert res.failed_step == 1

    def test_no_retry_when_retry_disabled(self, tmp_path):
        """反向：retry_on_failure=False→校验不符只调一次。"""
        we = _engine(tmp_path, [_step(expected="yes", max_retries=2,
                                      retry_on_failure=False)])
        calls = []

        def _exec(name, params):
            calls.append(name)
            return _R("nope")

        we.set_tool_executor(_exec)
        res = we.execute("flow")
        assert res.success is False
        assert len(calls) == 1

    def test_mismatch_then_cancel_stops_retry(self, tmp_path):
        """与 #82 取消接缝协同：不符后、重试等待前取消→不再重试，判 cancelled。"""
        we = _engine(tmp_path, [_step(expected="yes", max_retries=2,
                                      retry_on_failure=True)])
        ev = threading.Event()
        calls = []

        def _exec(name, params):
            calls.append(name)
            ev.set()  # 取消在首次校验不符后、重试等待前生效
            return _R("nope")

        we.set_tool_executor(_exec)
        res = we.execute("flow", cancel_event=ev)

        assert len(calls) == 1, "取消后不应重试"
        assert res.success is False
        assert res.error_code == "cancelled"
        assert res.step_results[0].get("cancelled") is True


# ──  helper 单元 + schema 往返 ──────────────────────────────────

class TestHelperAndSchema:
    def test_result_matches_forms(self, tmp_path):
        we = WorkflowEngine(workflows_dir=str(tmp_path))
        # ToolResult 鸭子：读 .content
        assert we._result_matches(_R("Hello World"), "world") is True
        assert we._result_matches(_R("Hello"), "missing") is False
        # 裸串结果：str() 兜底
        assert we._result_matches("raw text", "TEXT") is True
        # None 结果：空正文，非空期望不匹配
        assert we._result_matches(None, "x") is False
        # 空期望：恒 True（调用方已跳过，这里再兜一层）
        assert we._result_matches(None, "") is True
        assert we._result_matches(_R("anything"), "") is True
        # 期望两端空白被 strip
        assert we._result_matches(_R("ok"), "  ok  ") is True

    def test_expected_result_json_roundtrip(self, tmp_path):
        """from_dict/to_dict 保真 expected_result（持久化 schema 完整）。"""
        data = {
            "name": "rt",
            "steps": [{"step_id": 1, "tool_name": "t1",
                       "expected_result": "锁定"}],
        }
        p = tmp_path / "rt.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        we = WorkflowEngine(workflows_dir=str(tmp_path))
        wf = we.workflows["rt"]
        assert wf.steps[0].expected_result == "锁定"
        # 序列化回去仍带该字段
        assert wf.to_dict()["steps"][0]["expected_result"] == "锁定"

    def test_default_expected_result_empty(self):
        """默认空串：未声明 expected_result 的旧步骤不受影响。"""
        s = WorkflowStep(step_id=1, tool_name="t1")
        assert s.expected_result == ""