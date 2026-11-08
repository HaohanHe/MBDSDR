# SPDX-License-Identifier: MIT
"""会话事件日志测试（Sprint 3，参照 dsh session log "Model-visible ⟺ logged"）。

覆盖：
- SessionLog 单元：append-only / seq 单调 / 禁用 no-op / 写失败不抛 / 半截行跳过。
- 集成：驱动 _chat_impl 一轮工具调用，断言事件流含 turn_start→user_message→
  assistant_message→tool_call→tool_result→turn_end，即模型可见的都落了日志。
"""
import json
import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

from mbdsdr_ai.session_log import SessionLog  # noqa: E402


class TestSessionLogUnit:
    def test_emit_appends_and_seq_monotonic(self, tmp_path):
        log = SessionLog("conv_a", directory=str(tmp_path))
        log.emit("turn_start", round=1)
        log.emit("user_message", content="hi")
        log.emit("turn_end", latency_ms=12.3)
        events = log.read_events()
        assert [e["type"] for e in events] == ["turn_start", "user_message", "turn_end"]
        assert [e["seq"] for e in events] == [1, 2, 3]
        assert events[1]["content"] == "hi"
        assert all(e["conv"] == "conv_a" for e in events)
        assert all("ts" in e for e in events)

    def test_append_only_across_instances(self, tmp_path):
        """同 conv 的第二个 SessionLog 实例继续追加，不覆盖已有行。"""
        SessionLog("conv_b", directory=str(tmp_path)).emit("user_message", content="1")
        log2 = SessionLog("conv_b", directory=str(tmp_path))
        log2.emit("user_message", content="2")
        lines = (tmp_path / "conv_b.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2, "append-only：不得覆盖旧事件"

    def test_disabled_is_noop(self, tmp_path):
        log = SessionLog("conv_c", directory=str(tmp_path), enabled=False)
        assert log.emit("user_message", content="x") is None
        assert log.path is None
        assert log.read_events() == []

    def test_read_events_missing_file_empty(self, tmp_path):
        log = SessionLog("conv_never_written", directory=str(tmp_path))
        assert log.read_events() == []

    def test_broken_dir_does_not_raise(self, tmp_path):
        """目录不可创建时，emit 返回 None 且不抛异常（主循环安全）。"""
        bad = tmp_path / "afile"
        bad.write_text("not a dir", encoding="utf-8")
        log = SessionLog("conv_x", directory=str(bad / "sub"))
        assert log.emit("user_message", content="x") is None  # 不抛

    def test_partial_line_skipped(self, tmp_path):
        log = SessionLog("conv_p", directory=str(tmp_path))
        log.emit("user_message", content="ok")
        # 手动追加一条崩溃时的半截行
        with open(log.path, "a", encoding="utf-8") as f:
            f.write('{"type": "trunc"')  # 无换行的坏行
            f.write("\n")
        log.emit("turn_end")
        types = log.event_types()
        assert "trunc" not in types
        assert types == ["user_message", "turn_end"]

    def test_non_serializable_field_falls_back_to_str(self, tmp_path):
        log = SessionLog("conv_s", directory=str(tmp_path))

        class Weird:
            def __repr__(self):
                return "<weird>"

        ev = log.emit("tool_result", obj=Weird())
        assert ev is not None  # default=str 兜底，不因不可序列化而丢事件


class TestSessionLogIntegration:
    def _agent(self, tmp_path):
        from test_agent_retry import _make_minimal_agent, _mock_model_responses
        agent, call_count = _make_minimal_agent(None, "")
        agent.model_manager.chat_stream = _mock_model_responses("test_tool")
        # 注入指向临时目录的 session log（避免污染 ~/.mbdsdr）
        agent._session_log = SessionLog(agent.conversation_id, directory=str(tmp_path))
        agent._session_log_conv = agent.conversation_id
        return agent, call_count

    def test_chat_impl_emits_full_event_stream(self, tmp_path):
        from unittest.mock import patch
        agent, _ = self._agent(tmp_path)
        with patch.object(agent, "_cancel_pending", return_value=False):
            result = agent._chat_impl("打开频谱", max_tool_rounds=5)
        assert result["content"] == "最终回复"

        types = agent._session_log.event_types()
        # 不变量：模型可见的每一步都有对应事件
        assert types[0] == "turn_start"
        assert "user_message" in types
        assert "assistant_message" in types
        assert "tool_call" in types
        assert "tool_result" in types
        assert types[-1] == "turn_end"

        events = agent._session_log.read_events()
        by_type = {}
        for e in events:
            by_type.setdefault(e["type"], []).append(e)
        # tool_call 记了工具名与参数；tool_result 记了成功标志
        assert by_type["tool_call"][0]["tool"] == "test_tool"
        assert by_type["tool_result"][0]["tool"] == "test_tool"
        assert "success" in by_type["tool_result"][0]
        # user_message 落了原文
        assert by_type["user_message"][0]["content"] == "打开频谱"

    def test_session_log_writes_to_disk(self, tmp_path):
        from unittest.mock import patch
        agent, _ = self._agent(tmp_path)
        with patch.object(agent, "_cancel_pending", return_value=False):
            agent._chat_impl("测试", max_tool_rounds=5)
        path = tmp_path / f"{agent.conversation_id}.jsonl"
        assert path.exists(), "事件日志应落盘到 <conv>.jsonl"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 5
        for ln in lines:
            json.loads(ln)  # 每行都是合法 JSON
