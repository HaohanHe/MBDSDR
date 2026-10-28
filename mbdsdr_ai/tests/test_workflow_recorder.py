#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
WorkflowRecorder 录制/回放测试（纯逻辑，不出网不触硬件）
========================================================

agent 自动化回放错乱会把错误动作重复放大。钉住：
生命周期、暂停语义、参数化、回放替换、失败可观测、落盘读回。

运行: python3 -m pytest mbdsdr_ai/tests/test_workflow_recorder.py -v
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.workflow_recorder import (  # noqa: E402
    RecordingStatus, WorkflowRecorder,
)


@pytest.fixture()
def rec(tmp_path):
    return WorkflowRecorder(storage_dir=str(tmp_path))


def _record_two(rec):
    rid = rec.start_recording("tune", tags=["demo"])
    rec.record_tool_call("set_frequency", {"hz": 145500000}, "ok", True, 1.0)
    rec.record_tool_call("set_gain", {"db": 20}, "ok", True, 2.0)
    return rid


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------
def test_start_record_stop_lifecycle(rec):
    rid = _record_two(rec)
    stopped = rec.stop_recording()
    assert stopped is not None
    assert stopped.recording_id == rid
    assert stopped.status == RecordingStatus.STOPPED
    assert len(stopped.steps) == 2
    assert stopped.steps[0].step_index == 1
    assert stopped.steps[1].step_index == 2
    assert rec.current_recording is None


def test_double_start_raises(rec):
    rec.start_recording("a")
    with pytest.raises(RuntimeError):
        rec.start_recording("b")


def test_stop_without_start_returns_none(rec):
    assert rec.stop_recording() is None


def test_record_without_session_is_noop_not_crash(rec):
    """未开录制时 record_tool_call 必须静默跳过，不是崩、更不是凭空造记录。"""
    rec.record_tool_call("x", {}, "r", True)
    assert rec.current_recording is None


# ---------------------------------------------------------------------------
# 暂停语义（暂停期间绝不记录——否则回放多出幽灵步骤）
# ---------------------------------------------------------------------------
def test_paused_recording_captures_nothing(rec):
    rec.start_recording("p")
    rec.record_tool_call("a", {}, "ok", True)
    rec.pause_recording()
    rec.record_tool_call("ghost", {}, "ok", True)   # 必须被丢弃
    rec.resume_recording()
    rec.record_tool_call("b", {}, "ok", True)
    stopped = rec.stop_recording()
    names = [s.tool_name for s in stopped.steps]
    assert names == ["a", "b"], f"暂停期间不得记录，实际 {names}"


def test_resume_only_from_paused(rec):
    rec.start_recording("r")
    rec.resume_recording()   # RECORDING 态 resume 应无副作用
    rec.record_tool_call("x", {}, "ok", True)
    assert len(rec.current_recording.steps) == 1


# ---------------------------------------------------------------------------
# 批注
# ---------------------------------------------------------------------------
def test_annotate_step(rec):
    _record_two(rec)
    rec.annotate_step(2, "这里是增益")
    assert rec.current_recording.steps[1].user_annotation == "这里是增益"
    # 越界批注不崩
    rec.annotate_step(99, "nope")


# ---------------------------------------------------------------------------
# 模板与参数化
# ---------------------------------------------------------------------------
def test_create_template_and_parameterize(rec):
    rid = _record_two(rec)
    rec.stop_recording()
    tpl = rec.create_template(
        rid, "tune-wf",
        parameter_map={"freq": "145500000", "gain": "20"})
    assert tpl.template_id in rec.templates
    # 参数化把匹配值换成 {{name}}
    p0 = tpl.steps[0]["parameters"]
    p1 = tpl.steps[1]["parameters"]
    assert p0.get("hz") == "{{freq}}", f"实际 {p0}"
    assert p1.get("db") == "{{gain}}", f"实际 {p1}"


def test_create_template_unknown_recording_raises(rec):
    with pytest.raises(ValueError):
        rec.create_template("nope", "x")


def test_replay_substitutes_parameters(rec):
    rid = _record_two(rec)
    rec.stop_recording()
    tpl = rec.create_template(rid, "wf", parameter_map={"freq": "145500000"})
    seen = []

    def executor(tool, params):
        seen.append((tool, dict(params)))
        return "ok", True

    results = rec.replay_template(tpl.template_id,
                                  {"freq": 435000000}, executor)
    assert len(results) == 2
    assert seen[0][1]["hz"] == 435000000, f"参数必须被替换，实际 {seen[0]}"
    assert seen[0][0] == "set_frequency"
    assert all(r["success"] for r in results)


def test_replay_preserves_parameter_type(rec):
    """整值占位符必须保留原类型——数值参数不得被抹成字符串。

    否则回放给工具的频率/增益是 str，下游类型校验直接拒。
    """
    rid = _record_two(rec)
    rec.stop_recording()
    tpl = rec.create_template(rid, "wf", parameter_map={"freq": "145500000"})
    seen = []
    rec.replay_template(tpl.template_id, {"freq": 435000000},
                        lambda t, p: (seen.append((t, dict(p))) or ("ok", True)))
    assert isinstance(seen[0][1]["hz"], int), \
        f"hz 必须仍是 int，实际 {type(seen[0][1]['hz'])}"


def test_replay_unknown_template_raises(rec):
    with pytest.raises(ValueError):
        rec.replay_template("nope", {}, lambda t, p: ("ok", True))


def test_replay_failure_is_recorded_not_hidden(rec):
    """失败步骤必须带 error 标注，绝不静默装成功。"""
    rid = _record_two(rec)
    rec.stop_recording()
    tpl = rec.create_template(rid, "wf")
    calls = {"n": 0}

    def executor(tool, params):
        calls["n"] += 1
        return ("fail", False) if calls["n"] == 1 else ("ok", True)

    results = rec.replay_template(tpl.template_id, {}, executor)
    assert results[0]["success"] is False
    assert "error" in results[0], "失败必须显式标注"
    assert results[1]["success"] is True, "后续步骤照常（不中断）"


# ---------------------------------------------------------------------------
# 落盘读回
# ---------------------------------------------------------------------------
def test_recording_persisted_and_listed(rec, tmp_path):
    rid = _record_two(rec)
    rec.stop_recording()
    listed = rec.list_recordings()
    assert any(r["recording_id"] == rid for r in listed)
    loaded = rec.get_recording(rid)
    assert loaded is not None
    assert len(loaded.steps) == 2
    # 文件真的落盘了
    files = list(tmp_path.glob("*.json"))
    assert files, "stop 时必须落盘"


def test_template_persisted(rec):
    rid = _record_two(rec)
    rec.stop_recording()
    tpl = rec.create_template(rid, "wf")
    assert any(t["template_id"] == tpl.template_id
               for t in rec.list_templates())
    assert rec.get_template(tpl.template_id) is not None


def test_delete_recording_and_template(rec):
    rid = _record_two(rec)
    rec.stop_recording()
    tpl = rec.create_template(rid, "wf")
    assert rec.delete_template(tpl.template_id) is True
    assert rec.get_template(tpl.template_id) is None
    assert rec.delete_recording(rid) is True
    assert rec.get_recording(rid) is None
    assert rec.delete_recording("nope") is False
