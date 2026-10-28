#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
会话批准 TTL 测试
=================

红线（缺口 P1「approve_session 无 TTL」）：一次性授权不等于永久授权。
approve_session 免问的 (工具, 参数) 只在 TTL 窗口内有效，过期必须重新
批准——否则进程活得越久，忘掉的批准越危险。

运行: python3 -m pytest mbdsdr_ai/tests/test_session_approval_ttl.py -v
"""
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.core import mcp_server as ms  # noqa: E402
from mbdsdr_ai.core.mcp_server import MCPToolSet  # noqa: E402
from mbdsdr_ai.tool_policy import normalize_key  # noqa: E402


def _set() -> MCPToolSet:
    return MCPToolSet()   # 无后端，诚实空态，不触硬件


def _key(hz=145_500_000):
    return normalize_key("set_frequency", {"hz": hz})


def test_approval_is_cached_within_ttl(monkeypatch):
    """TTL 内第二次调用必须免问（缓存生效），标注 approval=session。"""
    srv = _set()
    srv._remember_session_approval(_key())
    out = srv.call_tool("set_frequency", {"hz": 145_500_000})
    # 无后端时结果是诚实空态，但放行路径（非 manual_gate_required）是关键
    assert out.get("code") != "manual_gate_required", \
        f"TTL 内应免问，实际: {out}"
    assert out.get("approval") == "session", \
        f"免问必须可观测标注，实际: {out}"


def test_approval_expires_after_ttl(monkeypatch):
    """TTL 过期后必须重新走门（不许永久免问）。"""
    srv = _set()
    srv._remember_session_approval(_key())
    # 把过期时间拨到过去
    srv._session_approvals[_key()] = time.time() - 1
    out = srv.call_tool("set_frequency", {"hz": 145_500_000})
    assert out.get("code") == "manual_gate_required", \
        f"过期后必须重新问，实际: {out}"


def test_expired_entry_is_evicted_not_left_as_zombie():
    """过期条目必须被清掉，不留僵尸授权。"""
    srv = _set()
    k = _key()
    srv._remember_session_approval(k)
    srv._session_approvals[k] = time.time() - 1
    assert srv._is_session_approved(k) is False
    assert k not in srv._session_approvals, "过期条目应被清除"


def test_fresh_approval_still_valid():
    srv = _set()
    k = _key()
    srv._remember_session_approval(k)
    assert srv._is_session_approved(k) is True


def test_unknown_key_not_approved():
    srv = _set()
    assert srv._is_session_approved(_key(hz=1)) is False


def test_ttl_constant_is_reasonable():
    """TTL 必须是有限正数，且短于"永久"（钉住语义，防止被改成 0=永不过期）。"""
    ttl = ms.SESSION_APPROVAL_TTL_S
    assert isinstance(ttl, (int, float))
    assert 0 < ttl <= 24 * 3600, f"TTL 应在 (0, 24h]，实际 {ttl}"


def test_different_args_need_separate_approval():
    """不同参数不共享批准（同工具不同频率必须分别批）。"""
    srv = _set()
    srv._remember_session_approval(_key(hz=145_500_000))
    assert srv._is_session_approved(_key(hz=145_500_000)) is True
    assert srv._is_session_approved(_key(hz=435_000_000)) is False
