# SPDX-License-Identifier: MIT
"""重复调用守卫（tests/test_agent_repeat_guard.py）
=================================================

学 `deepseek-harness` 的 guard/repeat-tool-reminder：模型连续用**相同工具 +
相同参数**调用时（3/5/8 次）往模型可见的工具结果里插一句提醒，避免它原地打转
把 token 烧在同一个调用上。

本测试锁三条语义：

1. 第 1/2 次不插话，第 3 次才插；提醒里带真实次数；
2. 参数一变就重新计数（不是"同一工具名"就算重复）；
3. 提醒**只进模型上下文**，不改 UI/审计看到的结果——`all_tool_results`
   在第 4152 行就已登记，守卫在其后，所以那里必须还是原始 content。

反证：把 `_repeat_count in (3, 5, 8)` 改成 `(1, ...)` → 用例 1 红；
把 key 只用 `tool_name` 拼 → 用例 2 红；把提醒改成 append 到
`all_tool_results` 的副本上 → 用例 3 红。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.tool_results import ToolResult  # noqa: E402


class _FakeContext:
    def __init__(self):
        self.messages = []

    def add_tool_message(self, tool_call_id, content, tool_name=""):
        self.messages.append({"id": tool_call_id, "content": content,
                              "tool": tool_name})


def _bare_agent():
    """object.__new__ 构造：只装 _finalize_one_tool 真正用到的那几样。"""
    ag = object.__new__(MBDSDRAgent)
    ag._repeat_key = ""
    ag._repeat_count = 0
    ag.context_manager = _FakeContext()
    ag.logs = []
    ag.hooks = []
    ag.captured = []
    ag._slog = lambda event, **kw: ag.logs.append((event, kw))
    ag._hook = lambda event, **kw: ag.hooks.append((event, kw))
    ag._capture_tool_state = lambda name, res: ag.captured.append(name)
    return ag


def _call(ag, name: str, args: str, results=None, content: str = "ok"):
    """模拟一次工具收尾；返回这次真正被模型看到的 content。"""
    res = ToolResult(success=True, content=content, tool_name=name)
    ag._finalize_one_tool(name, f"call_{len(ag.context_manager.messages)}",
                          {"arguments": args}, res, 0,
                          results if results is not None else [])
    return ag.context_manager.messages[-1]["content"]


def test_nudge_starts_on_third_identical_call():
    ag = _bare_agent()
    args = json.dumps({"center_freq": 100e6})
    assert "[guard]" not in _call(ag, "get_spectrum", args)
    assert "[guard]" not in _call(ag, "get_spectrum", args)
    third = _call(ag, "get_spectrum", args)
    assert "[guard]" in third
    assert "第 3 次" in third
    assert "get_spectrum" in third
    assert "ok" in third, "提醒不能把工具真实结果挤掉"


def test_nudge_repeats_at_five_and_eight_but_not_four():
    ag = _bare_agent()
    args = json.dumps({"x": 1})
    seen = [_call(ag, "list_devices", args) for _ in range(8)]
    assert "[guard]" not in seen[3], "第 4 次不该再插（避免每轮都喊）"
    assert "第 5 次" in seen[4]
    assert "[guard]" not in seen[6]
    assert "第 8 次" in seen[7]


def test_changed_arguments_reset_the_counter():
    ag = _bare_agent()
    for _ in range(3):
        _call(ag, "tune", json.dumps({"freq": 100e6}))
    # 参数变了：这是新调用，不该继承上一串的计数
    out = _call(ag, "tune", json.dumps({"freq": 101e6}))
    assert "[guard]" not in out
    assert ag._repeat_count == 1


def test_nudge_does_not_leak_into_ui_results():
    ag = _bare_agent()
    args = json.dumps({"same": True})
    results = []
    for _ in range(3):
        _call(ag, "read_status", args, results=results)
    # 审计/界面看到的是执行结果本身：干净、逐字一致
    assert [r["content"] for r in results] == ["ok", "ok", "ok"]
    # 模型看到的最后一条才带提醒
    assert "[guard]" in ag.context_manager.messages[-1]["content"]
