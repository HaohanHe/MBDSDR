# SPDX-License-Identifier: MIT
"""A03：AISDR Mini 的 MCP 失败必须可归因（第二批）。

_send_mcp 仍返回 None（不改接口、不让连接流程崩），但原因写入
_last_rpc_error，避免「未连接 / 超时 / 帧坏」被折叠成同一个 None。

反证：把 except 改回 ``except Exception: return None``（不记原因）后，
本用例 _last_rpc_error 为空 → 变红。
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.sdr_backend import AISDRMiniBackend  # noqa: E402


class _BrokenWS:
    def send(self, payload):
        raise OSError("模拟链路断开")

    def settimeout(self, t):
        pass

    def recv(self):
        raise OSError("模拟链路断开")


def test_not_connected_is_attributed():
    b = AISDRMiniBackend()
    assert b._send_mcp("list_tools") is None
    assert "未连接" in (b._last_rpc_error or "")


def test_transport_error_is_attributed():
    b = AISDRMiniBackend()
    b._ws = _BrokenWS()

    assert b._send_mcp("get_status") is None
    err = b._last_rpc_error or ""
    assert "get_status" in err, "失败原因必须点名是哪次调用"
    assert "OSError" in err, "失败原因必须保留异常类型，便于区分超时/帧坏"
