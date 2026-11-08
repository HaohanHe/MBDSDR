# SPDX-License-Identifier: MIT
"""工具执行超时测试（Sprint 2，参照 dsh guard/timeout-policy）。

靶：硬件/MCP handler 无响应时不能把 agent 主循环挂死。设计要点：
- default_timeout_s=None（默认）时 handler 在调用线程内联执行——绝不把
  Qt/主线程 handler 甩到后台线程，历史行为零回归。
- 设了超时（registry 级 default_timeout_s 或工具级 timeout_s）才用 daemon
  线程 + join(timeout)；超时返回结构化 tool_timeout（retryable），不静默。
- 工具级 timeout_s 覆盖 registry 级；显式 <=0 关闭超时。
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402
from mbdsdr_ai.error_catalog import retryable  # noqa: E402

_PARAMS = {"type": "object", "properties": {}}


def _reg(default_timeout_s=None):
    return ToolRegistry(default_timeout_s=default_timeout_s)


class TestTimeoutCatalog:
    def test_tool_timeout_is_retryable(self):
        assert retryable("tool_timeout") is True


class TestInlineDefault:
    def test_no_timeout_runs_on_calling_thread(self):
        """默认不设超时：handler 必须在调用线程内联执行（零回归关键断言）。"""
        r = _reg()
        seen = {}

        def handler(args):
            seen["thread"] = threading.current_thread()
            return {"success": True, "content": "ok"}

        r.register("t_inline", "d", _PARAMS, handler)
        caller = threading.current_thread()
        out = r.call("t_inline", {})
        assert out.success is True
        assert seen["thread"] is caller, "无超时却把 handler 甩到了别的线程"

    def test_fast_handler_within_timeout(self):
        r = _reg(default_timeout_s=5.0)

        def handler(args):
            return {"success": True, "content": "quick"}

        r.register("t_fast", "d", _PARAMS, handler)
        out = r.call("t_fast", {})
        assert out.success is True and out.content == "quick"


class TestTimeoutFires:
    def test_slow_handler_times_out(self):
        r = _reg(default_timeout_s=0.15)

        def handler(args):
            time.sleep(1.0)
            return {"success": True, "content": "too late"}

        r.register("t_slow", "d", _PARAMS, handler)
        out = r.call("t_slow", {})
        assert out.success is False
        assert out.error == "tool_timeout"
        assert out.latency_ms >= 140, "应在超时点返回，而非等 handler 跑完"
        assert out.latency_ms < 900, "不该阻塞到 handler 的 1s sleep 结束"

    def test_timeout_message_names_tool_and_seconds(self):
        r = _reg(default_timeout_s=0.15)

        def handler(args):
            time.sleep(1.0)
            return "x"

        r.register("hang_tool", "d", _PARAMS, handler)
        out = r.call("hang_tool", {})
        assert "hang_tool" in out.content
        assert out.next_step  # 有可操作建议

    def test_per_tool_timeout_overrides_registry(self):
        """registry 不设超时，但工具级 timeout_s 生效。"""
        r = _reg(default_timeout_s=None)

        def handler(args):
            time.sleep(1.0)
            return "x"

        r.register("t_over", "d", _PARAMS, handler, timeout_s=0.15)
        out = r.call("t_over", {})
        assert out.success is False and out.error == "tool_timeout"

    def test_per_tool_disable_timeout(self):
        """registry 设了超时，但工具级 timeout_s=0 关闭——handler 内联跑完。"""
        r = _reg(default_timeout_s=0.15)
        seen = {}

        def handler(args):
            seen["thread"] = threading.current_thread()
            time.sleep(0.3)  # 超过 registry 默认，但本工具已关超时
            return {"success": True, "content": "done"}

        r.register("t_disable", "d", _PARAMS, handler, timeout_s=0)
        caller = threading.current_thread()
        out = r.call("t_disable", {})
        assert out.success is True and out.content == "done"
        assert seen["thread"] is caller, "timeout_s=0 应内联执行"


class TestExceptionPath:
    def test_handler_exception_structured_under_timeout(self):
        """开超时后 handler 抛异常，仍走既有结构化异常路径（不被吞成超时）。"""
        r = _reg(default_timeout_s=5.0)

        def handler(args):
            raise ValueError("boom")

        r.register("t_exc", "d", _PARAMS, handler)
        out = r.call("t_exc", {})
        assert out.success is False
        assert out.error and out.error != "tool_timeout"
        assert "boom" in out.content or "ValueError" in out.content
