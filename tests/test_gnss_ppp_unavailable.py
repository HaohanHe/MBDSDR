# SPDX-License-Identifier: MIT
"""D5 Task 28：PPP 未实现时必须如实上报，不得留空接口冒充能力。

本仓没有 PPP（需要精密星历/钟差、无电离层组合、相位模糊度），
调用 ``ppp_locate`` 必须得到结构化 ``unavailable``，不能返回 None、
也不能用 SPP/RTK 结果顶替。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.rtklib_adapter import (  # noqa: E402
    PPP_UNAVAILABLE_REASON,
    ppp_locate,
)


def test_ppp_returns_structured_unavailable():
    res = ppp_locate([{"prn": 1, "system": "GPS", "pr": 2.0e7}])
    assert res["ok"] is False
    assert res["status"] == "unavailable"
    assert res["fix_type"] == "NONE"
    assert "未实现" in res["reason"]


def test_ppp_default_call_is_honest():
    res = ppp_locate()
    assert res["ok"] is False
    assert res["reason"] == PPP_UNAVAILABLE_REASON
    assert "未实现" in res["reason"]
