# SPDX-License-Identifier: MIT
"""questions + MCP 人机问答工具测试。

来源：2026-10-14 kilocode 审计 Top-3（question 服务：多问题+选项+阻塞+
dismiss）。stdio 请求-响应式传输不能阻塞提问方，落成 ask/answer/status
三件套；会话内存态如实文档化。
"""
from mbdsdr_ai.core.mcp_server import MCPToolSet
from mbdsdr_ai.questions import QuestionError, QuestionStore

# ---------------------------------------------------------------------------
# QuestionStore 单元
# ---------------------------------------------------------------------------


def _store():
    return QuestionStore()


def test_ask_returns_pending_not_blocking():
    q = _store().ask("接 VNA 了吗？", ["接了", "没接"])
    assert q.status == "pending"
    assert q.question_id == "q1"
    assert q.answer is None


def test_ask_validation_honest():
    s = _store()
    for bad in ((("", ["a", "b"]),),
                (("q?", ["only-one"]),),
                (("q?", ["a", "a", "b"]),)):
        try:
            s.ask(*bad[0])
            assert False, "应当诚实报错"
        except QuestionError:
            pass


def test_ask_duplicate_id_rejected():
    s = _store()
    s.ask("q1?", ["y", "n"], question_id="abc")
    try:
        s.ask("q2?", ["y", "n"], question_id="abc")
        assert False, "重复 id 必须拒绝"
    except QuestionError:
        pass


def test_answer_must_hit_option():
    s = _store()
    s.ask("which?", ["left", "right"])
    try:
        s.answer("q1", "middle")
        assert False, "非选项答案必须拒绝（防幻觉自由发挥）"
    except QuestionError:
        pass
    q = s.answer("q1", "left")
    assert q.status == "answered" and q.answer == "left"


def test_answer_free_text_and_override():
    s = _store()
    s.ask("which?", ["left", "right"])
    q = s.answer("q1", "other thing", free_text=True)
    assert q.free_text is True and q.answer == "other thing"
    try:
        s.answer("q1", "right")
        assert False, "重复回答必须报错"
    except QuestionError:
        pass
    q2 = s.answer("q1", "right", override=True)
    assert q2.answer == "right"


def test_answer_unknown_id():
    try:
        _store().answer("nope", "x")
        assert False
    except QuestionError:
        pass


def test_status_views():
    s = _store()
    s.ask("a?", ["y", "n"])
    s.ask("b?", ["y", "n"], question_id="q2")
    s.answer("q2", "y")
    assert len(s.list(status="pending")) == 1
    assert len(s.list(status="answered")) == 1
    pend = s.pending_envelopes()
    assert pend[0]["question_id"] == "q1"
    assert "answer" not in pend[0]          # pending 信封不带答案


# ---------------------------------------------------------------------------
# MCP 工具集成
# ---------------------------------------------------------------------------


def _set():
    return MCPToolSet()


def test_mcp_ask_status_answer_flow():
    ts = _set()
    r = ts.call_tool("ask_question", {"question": "接好了吗？",
                                      "options": ["接好了", "还没有"],
                                      "context": "NanoVNA CH0"})
    assert r["ok"] is True and r["outcome"] == "question_pending"
    qid = r["question"]["question_id"]

    st = ts.call_tool("question_status", {})
    assert st["pending_count"] == 1
    assert "重启" in st["note"]            # 内存态如实标注

    bad = ts.call_tool("answer_question", {"question_id": qid, "answer": "也许"})
    assert bad["ok"] is False              # 非选项被拒

    ok = ts.call_tool("answer_question", {"question_id": qid, "answer": "接好了"})
    assert ok["ok"] is True and ok["outcome"] == "answered"
    one = ts.call_tool("question_status", {"question_id": qid})
    assert one["question"]["answer"] == "接好了"


def test_mcp_qa_tools_are_extension_domain():
    ts = _set()
    cards = ts.tools_list_result({"detail": "name"})[0]["tools"]
    names = {n for c in cards for n in (c.get("tools") or [])}
    assert {"ask_question", "answer_question", "question_status"} <= names
    qa_card = [c for c in cards if c["domain"] == "人机问答"][0]
    assert len(qa_card["tools"]) == 3


def test_mcp_qa_not_gated():
    """问答不走 arm_manual：对话层动作，不是硬件写。"""
    ts = _set()
    r = ts.call_tool("ask_question", {"question": "x?",
                                      "options": ["a", "b"]})
    assert "gated" not in r
    assert r["ok"] is True


def test_mcp_qa_store_per_instance():
    """每个 MCPToolSet 实例有独立问答 store（不串会话）。"""
    a, b = _set(), _set()
    a.call_tool("ask_question", {"question": "a?", "options": ["1", "2"]})
    st = b.call_tool("question_status", {})
    assert st["pending_count"] == 0
