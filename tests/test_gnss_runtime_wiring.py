# SPDX-License-Identifier: MIT
"""D10：天空图 GNSS 入口 → 运行时快照 / 时间引擎的接线。

只用离屏 Qt 与假 fix 字典：不开串口、不启后台线程、不联网。

覆盖：
1. 有效 real fix + 完整 UTC：写时间引擎、写站址，快照标 gnss；
2. 无 fix：来源回退（system/manual），旧值不再标 gnss，手动站址不被清；
3. 控制器 ``get_status()["runtime_sources"]`` 与进程级快照一致；
4. ``record_fix`` 抛异常时不得影响天空图绘制；
5. 只有 hhmmss 没有日期：无法锚定 UTC，不写授时（不伪造）。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from mbdsdr_ai import runtime_sources as rs_mod  # noqa: E402
from mbdsdr_ai.core.controller import SDRController  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

UTC = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
FIX_LAT = 48.1173
FIX_LON = 11.5167


def _real_fix(utc_time=UTC.isoformat(), lat=FIX_LAT, lon=FIX_LON):
    return {
        "source": "real",
        "latitude": lat,
        "longitude": lon,
        "altitude_m": 545.4,
        "utc_time": utc_time,
        "timestamp": UTC.timestamp(),
    }


@pytest.fixture
def sources():
    """干净的单例；时间引擎在用例前后都回系统时钟。"""
    engine = get_time_engine()
    engine.set_time_now()
    rs_mod.reset_runtime_sources()
    try:
        yield rs_mod.get_runtime_sources()
    finally:
        engine.set_time_now()
        rs_mod.reset_runtime_sources()


@pytest.fixture
def ctrl(sources, tmp_path):
    return SDRController(backend=None, config_dir=str(tmp_path))


def test_real_fix_drives_time_and_site(sources, ctrl):
    from desktop.rf_sky_view import RFSkyView

    ctrl.set_ground_station(12.5, 34.5, 100.0, source="user")
    engine = get_time_engine()
    assert engine.is_following_system() is True

    view = RFSkyView()
    view.set_gnss_position(_real_fix())

    snap = sources.snapshot()
    assert snap["gnss"]["connected"] is True
    assert snap["gnss"]["fix"] is True
    assert snap["gnss"]["time_source"] == "nmea"
    assert snap["gnss"]["site_source"] == "gnss"
    assert snap["gnss"]["lat_deg"] == pytest.approx(FIX_LAT)
    assert snap["gnss"]["lon_deg"] == pytest.approx(FIX_LON)
    assert snap["time"]["source"] == "gnss"
    assert snap["site"]["source"] == "gnss"
    assert snap["site"]["lat_deg"] == pytest.approx(FIX_LAT)
    assert snap["site"]["lon_deg"] == pytest.approx(FIX_LON)

    # 时间引擎真的被推到 GNSS UTC，且没有停在暂停态
    assert engine.is_following_system() is False
    assert engine.now_unix() == pytest.approx(UTC.timestamp(), abs=1.0)
    assert engine.get_rate() == pytest.approx(1.0)

    # 天空图自身也看到了同一 fix
    assert view.get_observer()["lat"] == pytest.approx(FIX_LAT)


def test_view_only_fix_reports_gnss_site(sources):
    """没有控制器时，天空图入口的有效 fix 也应体现为 gnss 站址。"""
    from desktop.rf_sky_view import RFSkyView

    RFSkyView().set_gnss_position(_real_fix())

    snap = sources.snapshot()
    assert snap["gnss"]["connected"] is True
    assert snap["gnss"]["site_source"] == "gnss"
    assert snap["site"]["source"] == "gnss"
    assert snap["site"]["lat_deg"] == pytest.approx(FIX_LAT)
    assert snap["site"]["lon_deg"] == pytest.approx(FIX_LON)


def test_no_fix_falls_back_and_keeps_manual_site(sources, ctrl):
    from desktop.rf_sky_view import RFSkyView

    ctrl.set_ground_station(45.0, 120.0, 10.0, source="user")
    engine = get_time_engine()
    view = RFSkyView()
    view.set_gnss_position(_real_fix())
    assert sources.snapshot()["time"]["source"] == "gnss"

    view.set_gnss_position({
        "source": "none",
        "latitude": None,
        "longitude": None,
        "altitude_m": None,
        "utc_time": None,
        "timestamp": None,
    })

    snap = sources.snapshot()
    assert snap["gnss"]["connected"] is False
    assert snap["gnss"]["fix"] is False
    assert snap["gnss"]["time_source"] == "none"
    assert snap["gnss"]["site_source"] == "manual"
    # 时间回退系统时钟，不再标 gnss
    assert snap["time"]["source"] == "system"
    assert snap["time"]["time_source"] == "none"
    assert engine.is_following_system() is True
    # 用户手填站址原样保留，不被清掉
    assert snap["site"]["source"] == "manual"
    assert snap["site"]["lat_deg"] == pytest.approx(45.0)
    assert snap["site"]["lon_deg"] == pytest.approx(120.0)
    assert view.get_observer() is None


def test_real_source_without_coords_keeps_connected(sources, ctrl):
    """收到语句但未定位（source=real、无坐标）：在线但不授时、不造站址。"""
    from desktop.rf_sky_view import RFSkyView

    RFSkyView().set_gnss_position({
        "source": "real",
        "latitude": None,
        "longitude": None,
        "altitude_m": None,
        "utc_time": None,
        "timestamp": UTC.timestamp(),
    })

    snap = sources.snapshot()
    assert snap["gnss"]["connected"] is True
    assert snap["gnss"]["fix"] is False
    assert snap["gnss"]["lat_deg"] is None
    assert snap["gnss"]["time_source"] == "none"
    assert snap["time"]["source"] == "system"
    assert snap["site"]["source"] == "none"


def test_controller_status_sees_the_same_snapshot(sources, ctrl):
    from desktop.rf_sky_view import RFSkyView

    RFSkyView().set_gnss_position(_real_fix())

    snap = sources.snapshot()
    status = ctrl.get_status().to_dict()["runtime_sources"]
    assert status == snap
    assert status["gnss"]["time_source"] == "nmea"
    assert status["site"]["source"] == "gnss"


def test_record_failure_does_not_break_drawing(sources, monkeypatch):
    from desktop.rf_sky_view import RFSkyView

    def boom(_fix):
        raise RuntimeError("boom")

    monkeypatch.setattr(sources, "record_fix", boom)
    view = RFSkyView()
    view.set_gnss_position(_real_fix())
    # 绘制路径照常更新观测站，异常不外泄
    assert view.get_observer()["lat"] == pytest.approx(FIX_LAT)
    assert view._data_source == "real"


def test_time_only_utc_does_not_forge_time(sources, ctrl):
    from desktop.rf_sky_view import RFSkyView

    RFSkyView().set_gnss_position(_real_fix(utc_time="072545.00"))

    snap = sources.snapshot()
    # 只有 hhmmss 无法锚定 UTC 日期：定位照收，授时不写
    assert snap["gnss"]["fix"] is True
    assert snap["gnss"]["time_source"] == "none"
    assert snap["time"]["source"] == "system"
    assert get_time_engine().is_following_system() is True
    assert snap["site"]["source"] == "gnss"
