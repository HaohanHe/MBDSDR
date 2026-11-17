# SPDX-License-Identifier: MIT
"""每秒 GNSS 流程的顺序回归：后一轮只有 hhmmss 的 fix 不得清掉已生效授时。

桌面 ``_poll_gnss`` 的实际顺序是：

1. ``GNSSMonitor.get_position()`` → 完整 fix（含 ``utc_datetime``）发布给
   ``RuntimeSources.record_fix`` → 时间引擎写入 GNSS UTC，``time_source=nmea``；
2. ``sky_view.set_gnss_position(fix_dict)`` → 同一个 fix 但只剩 ``utc_time``
   （hhmmss）被再次 ``record_fix``。

修复前第 2 步会走 ``utc is None`` 分支把授时清回 ``system``。这里用假时间
引擎记录 ``set_unix`` / ``set_time_now`` 调用次数钉住正确语义：只带时刻的
fix 不能覆盖已生效的 GNSS 授时；无有效定位仍必须回退系统时钟。

不碰硬件、不开串口、不联网。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from mbdsdr_ai.runtime_sources import RuntimeSources  # noqa: E402

UTC = timezone.utc
EXPECT_UTC = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)
FIX_LAT = 48.1173
FIX_LON = 11.5167


class _FakeEngine:
    """最小时间引擎替身：只记 set_unix / set_time_now 调用。"""

    def __init__(self):
        self.unix = None
        self.rate = 1.0
        self.set_unix_calls = []
        self.set_time_now_calls = 0

    def set_unix(self, ts):
        self.unix = float(ts)
        self.rate = 0.0
        self.set_unix_calls.append(float(ts))

    def resume(self, rate=1.0):
        self.rate = float(rate)

    def set_time_now(self):
        self.unix = None
        self.rate = 1.0
        self.set_time_now_calls += 1

    def is_following_system(self) -> bool:
        return self.unix is None

    def now_unix(self) -> float:
        return self.unix if self.unix is not None else 0.0


def _fix(**overrides):
    base = {
        "source": "real",
        "latitude": FIX_LAT,
        "longitude": FIX_LON,
        "altitude_m": 545.4,
        "utc_time": "070809.00",
        "utc_datetime": EXPECT_UTC.isoformat(),
        "date": "060524",
        "timestamp": EXPECT_UTC.timestamp(),
    }
    base.update(overrides)
    return base


def test_hhmmss_only_after_applied_fix_keeps_gnss_time():
    engine = _FakeEngine()
    sources = RuntimeSources(time_engine=engine)

    first = sources.record_fix(_fix())
    assert engine.set_unix_calls == [EXPECT_UTC.timestamp()]
    assert first["gnss"]["time_source"] == "nmea"
    assert first["time"]["source"] == "gnss"

    # 同一轮里天空图入口再发一次，只剩 hhmmss
    second = sources.record_fix(_fix(utc_datetime=None, date=None))

    assert engine.set_time_now_calls == 0
    assert engine.unix == pytest.approx(EXPECT_UTC.timestamp())
    assert second["gnss"]["time_source"] == "nmea"
    assert second["time"]["source"] == "gnss"
    assert second["time"]["last_update_utc"] == first["time"]["last_update_utc"]
    assert "保留已生效授时" in second["gnss"]["reason"]


def test_no_fix_after_applied_fix_still_falls_back_to_system():
    engine = _FakeEngine()
    sources = RuntimeSources(time_engine=engine)

    sources.record_fix(_fix())
    assert sources.snapshot()["gnss"]["time_source"] == "nmea"

    snap = sources.record_fix({
        "source": "none",
        "latitude": None,
        "longitude": None,
        "altitude_m": None,
        "utc_time": None,
        "utc_datetime": None,
        "date": None,
        "timestamp": None,
    })

    # 断开/无坐标仍然清除授时并回退系统时钟（原语义不变）
    assert engine.set_time_now_calls == 1
    assert engine.is_following_system() is True
    assert snap["gnss"]["time_source"] == "none"
    assert snap["time"]["source"] == "system"


def test_hhmmss_only_never_applied_stays_system():
    engine = _FakeEngine()
    sources = RuntimeSources(time_engine=engine)

    snap = sources.record_fix(_fix(utc_datetime=None, date=None))

    # 从未应用过授时：不写时间引擎，也不制造假的 gnss 状态
    assert engine.set_unix_calls == []
    assert engine.set_time_now_calls == 0
    assert engine.is_following_system() is True
    assert snap["gnss"]["time_source"] == "none"
    assert snap["time"]["source"] == "system"
    assert "缺完整 UTC" in snap["gnss"]["reason"]
