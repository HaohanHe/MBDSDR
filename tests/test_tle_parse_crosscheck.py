# -*- coding: utf-8 -*-
"""TLE 文本↔SGP4 解析对拍：列位异常必须报错，不能静默给出物理错误的轨道。

背景：sgp4 2.27 的解析器按固定列位读行 2。行 2 被截断/列位偏移的 TLE
（例如 56 字符的 "2 25544  51.6400 100.0000 0005000  50.0000  10.0000 1 05"）
会被静默解析成平均运动 1.00 rev/day -> 42,164 km 地球同步轨道，err=0、
无任何告警。传播器因此必须自检。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402

GOOD1 = "1 42960U 17061F   25117.46489195  .00000241  00000+0  78821-4 0  9994"
GOOD2 = ("2 42960  86.4033 198.9703 0001974 107.2578 252.8834 "
         "14.34218356395236")
# 列位偏移（行 2 仅 56 字符）：SGP4 会把它读成平均运动 1.00 rev/day
BAD2 = "2 25544  51.6400 100.0000 0005000  50.0000  10.0000 1 05"
BAD1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9004"


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def test_valid_tle_still_propagates(reg):
    r = reg.call("tle_state_ecef", {"tle_line1": GOOD1, "tle_line2": GOOD2,
                                    "timestamp": 1745764814.0})
    assert r.success is True, r.content[:200]
    st = r.data["state_ecef"]
    radius_km = (st[0] ** 2 + st[1] ** 2 + st[2] ** 2) ** 0.5
    assert 6800.0 < radius_km < 7400.0, radius_km


def test_misaligned_tle_is_rejected_not_propagated(reg):
    """列位异常的 TLE 必须报错，而不是给出一条 42,000 km 的错轨道。"""
    r = reg.call("tle_state_ecef", {"tle_line1": BAD1, "tle_line2": BAD2,
                                    "timestamp": 1704067200.0})
    assert r.success is False, "列位异常 TLE 不应被静默传播"
    msg = r.content or ""
    assert "平均运动" in msg, msg[:200]
    assert "10.00000000" in msg and "1.00000000" in msg, msg[:300]
    # 不得返回任何状态
    assert not (r.data or {}).get("state_ecef")


def test_too_short_line2_is_rejected(reg):
    r = reg.call("tle_state_ecef", {"tle_line1": GOOD1,
                                    "tle_line2": "2 42960  86.4033 198.9703",
                                    "timestamp": 1745764814.0})
    assert r.success is False
    assert "字段不足" in (r.content or ""), (r.content or "")[:200]
