# SPDX-License-Identifier: MIT
"""Task 74（非 main_window 路径）：GNSS 每秒轮询入口 → 运行时快照。

用假 reader，不开串口、不启后台线程、不联网、不碰硬件。

语义：``RealGNSSMonitor.get_position()`` 是桌面 ``_poll_gnss`` 每秒调用的
**单一入口**。real fix 在这里 best-effort 发布给 ``RuntimeSources.record_fix``
（原始 fix 字典，含 ``utc_datetime``/``date``），因此不改
``desktop/main_window.py`` 也能让实机时间源变成 ``gnss``。数据仍来自同一个
reader 的 ``get_fix()``，不是第二套数据源。

覆盖：
1. real fix + 完整 UTC → 快照 connected=true、time_source=nmea、站址 gnss；
2. 只有 hhmmss 无日期 → 不写授时（时间源仍 system），站址仍来自 GNSS；
3. reader 抛异常 → ``get_position()`` 仍返回 source="none"，不抛；
4. 发布本身抛异常 → 不影响 ``get_position()`` 返回值；
5. real → 无 fix 的边沿 → 快照如实变回“未连接”（不每轮重置）。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from mbdsdr_ai import runtime_sources as rs_mod  # noqa: E402
from mbdsdr_ai.gnss_monitor import RealGNSSMonitor  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

UTC = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
FIX_LAT = 48.1173
FIX_LON = 11.5167


class _FakeReader:
    """最小 reader 替身：只实现 get_position() 依赖的 get_fix()。"""

    def __init__(self, fix=None, error=None):
        self._fix = fix
        self._error = error
        self.calls = 0

    def get_fix(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._fix


def _monitor(reader) -> RealGNSSMonitor:
    m = RealGNSSMonitor()
    m._reader = reader
    return m


def _fix(**overrides):
    base = {
        "source": "real",
        "latitude": FIX_LAT,
        "longitude": FIX_LON,
        "altitude_m": 545.4,
        "satellites": 9,
        "hdop": 0.9,
        "speed_kmh": 5.0,
        "course_deg": 30.0,
        "utc_time": "070809.00",
        "utc_datetime": UTC.isoformat(),
        "date": "060524",
        "timestamp": UTC.timestamp(),
    }
    base.update(overrides)
    return base


@pytest.fixture
def engine():
    """干净的单例；时间引擎在用例前后都回系统时钟。"""
    eng = get_time_engine()
    eng.set_time_now()
    rs_mod.reset_runtime_sources()
    try:
        yield eng
    finally:
        eng.set_time_now()
        rs_mod.reset_runtime_sources()


def test_real_fix_publishes_full_utc_and_site(engine):
    sources = rs_mod.get_runtime_sources()

    pos = _monitor(_FakeReader(_fix())).get_position()
    assert pos.source == "real"
    assert pos.utc_datetime == UTC.isoformat()

    snap = sources.snapshot()
    assert snap["gnss"]["connected"] is True
    assert snap["gnss"]["fix"] is True
    assert snap["gnss"]["time_source"] == "nmea"
    assert snap["time"]["source"] == "gnss"
    assert snap["gnss"]["site_source"] == "gnss"
    assert snap["site"]["source"] == "gnss"
    assert snap["site"]["lat_deg"] == pytest.approx(FIX_LAT)
    assert snap["site"]["lon_deg"] == pytest.approx(FIX_LON)
    # 时间引擎真的被写到 GNSS UTC，且 1x 前进
    assert engine.is_following_system() is False
    assert engine.now_unix() == pytest.approx(UTC.timestamp(), abs=1.0)
    assert engine.get_rate() == pytest.approx(1.0)


def test_hhmmss_only_does_not_forge_time_but_site_still_gnss(engine):
    sources = rs_mod.get_runtime_sources()

    _monitor(_FakeReader(_fix(utc_datetime=None, date=None))).get_position()

    snap = sources.snapshot()
    # 定位照收、站址照写；但没有日期不写授时
    assert snap["gnss"]["fix"] is True
    assert snap["gnss"]["time_source"] != "nmea"
    assert snap["time"]["source"] == "system"
    assert engine.is_following_system() is True
    assert snap["site"]["source"] == "gnss"
    assert snap["site"]["lat_deg"] == pytest.approx(FIX_LAT)


def test_reader_exception_returns_none_without_raising(engine):
    sources = rs_mod.get_runtime_sources()

    pos = _monitor(_FakeReader(error=OSError("串口已拔出"))).get_position()

    assert pos.source == "none"
    assert pos.lat is None
    assert pos.lon is None
    # 从未发布过 real fix：快照如实为未连接/系统时钟
    snap = sources.snapshot()
    assert snap["gnss"]["connected"] is False
    assert snap["time"]["source"] == "system"


def test_publish_failure_does_not_affect_position(engine, monkeypatch):
    sources = rs_mod.get_runtime_sources()

    def boom(_fix):
        raise RuntimeError("运行时快照写入失败")

    monkeypatch.setattr(sources, "record_fix", boom)

    pos = _monitor(_FakeReader(_fix())).get_position()
    assert pos.source == "real"
    assert pos.lat == pytest.approx(FIX_LAT)
    assert pos.utc_datetime == UTC.isoformat()


def test_lost_fix_edge_marks_unconnected_once(engine):
    sources = rs_mod.get_runtime_sources()
    reader = _FakeReader(_fix())
    monitor = _monitor(reader)

    monitor.get_position()
    assert sources.snapshot()["gnss"]["connected"] is True

    reader._fix = {"source": "none", "latitude": None, "longitude": None,
                   "utc_time": None, "utc_datetime": None, "date": None,
                   "timestamp": None}
    pos = monitor.get_position()
    assert pos.source == "none"

    snap = sources.snapshot()
    assert snap["gnss"]["connected"] is False
    assert snap["gnss"]["fix"] is False
    assert snap["time"]["source"] == "system"
    assert engine.is_following_system() is True


def test_real_fix_keeps_serial_port_in_snapshot(engine):
    """有 fix 之后快照也要报得出串口：connected=true 却 port=null 是自相矛盾。

    reader 的 fix 字典不含端口（它是观测，不是连接信息）；由
    ``RealGNSSMonitor._publish_fix`` 补上实际生效的口。断开后不留旧端口。
    """
    sources = rs_mod.get_runtime_sources()
    monitor = _monitor(_FakeReader(_fix()))
    monitor.port = "COM4"

    monitor.get_position()
    assert sources.snapshot()["gnss"]["port"] == "COM4"

    # fix 自己带了 port 时以观测为准，不覆盖
    monitor2 = _monitor(_FakeReader(_fix(port="COM9")))
    monitor2.port = "COM4"
    monitor2.get_position()
    assert sources.snapshot()["gnss"]["port"] == "COM9"


def test_lost_fix_does_not_keep_stale_port(engine):
    sources = rs_mod.get_runtime_sources()
    reader = _FakeReader(_fix())
    monitor = _monitor(reader)
    monitor.port = "COM4"
    monitor.get_position()
    assert sources.snapshot()["gnss"]["port"] == "COM4"

    reader._fix = {"source": "none", "latitude": None, "longitude": None,
                   "utc_time": None, "utc_datetime": None, "date": None,
                   "timestamp": None}
    monitor.get_position()
    assert sources.snapshot()["gnss"]["port"] is None
