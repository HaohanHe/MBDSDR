# SPDX-License-Identifier: MIT
"""SatelliteGroundMap 绘制验收：图层存在、真实画出像素、失败有诚实空态。"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ISS_TLE = (
    "1 25544U 98067A   26297.20915682  .00007800  00000+0  14938-3 0  9991",
    "2 25544  51.6335  21.6229 0006884 302.9098  57.1228 15.49030149591752",
)


def _project(lat, lon):
    return QPointF((lon + 180.0) / 360.0 * 320.0,
                   (90.0 - lat) / 180.0 * 220.0)


def _non_black(pix):
    img = pix.toImage()
    return sum(1 for y in range(img.height()) for x in range(img.width())
               if img.pixelColor(x, y).lightness() > 20)


def test_draws_track_and_footprint_pixels(monkeypatch):
    app = QApplication.instance() or QApplication(sys.argv)
    import mbdsdr_ai.ground_track as gt
    monkeypatch.setattr(gt, "fetch_tle", lambda catnr, max_age_hours=24.0: ISS_TLE)
    from sat_ground_map import SatelliteGroundMap

    widget = SatelliteGroundMap(online=False)
    assert widget.view is not None
    # 基线重定标（2026-11-14）：新增 spacetime_features 图层后，图层清单
    # 由两层变三层。不是放宽断言，而是把新层也钉进契约。
    assert widget.view.layer_names() == [
        "sat_footprint", "sat_ground_track", "spacetime_features"]
    assert widget.update_satellite("ISS (ZARYA)", minutes=90,
                                   min_elevation_deg=5) is True
    assert "覆盖半径" in widget.summary_text
    assert widget.spacetime_layer is not None
    assert widget.spacetime_layer.result is not None

    pix = QPixmap(320, 220)
    pix.fill(Qt.black)
    painter = QPainter(pix)
    widget._paint_footprint(painter, _project)
    widget._paint_track(painter, _project)
    # 时空要素层也必须在同一张画布上真画出东西（角标/空态至少占像素）。
    feature_cb = dict(widget.view._layers).get("spacetime_features")
    assert feature_cb is not None
    feature_cb(painter, _project)
    painter.end()
    assert _non_black(pix) > 200, "图层回调没有画出可见像素"
    app.processEvents()


def test_tle_unavailable_is_reported_not_drawn(monkeypatch):
    _app = QApplication.instance() or QApplication(sys.argv)
    import mbdsdr_ai.ground_track as gt
    from mbdsdr_ai.orbit import TLEUnavailableError

    def _boom(catnr, max_age_hours=24.0):
        raise TLEUnavailableError("离线且无缓存")

    monkeypatch.setattr(gt, "fetch_tle", _boom)
    from sat_ground_map import SatelliteGroundMap

    widget = SatelliteGroundMap(online=False)
    assert widget.update_satellite("ISS (ZARYA)") is False
    assert "TLEUnavailableError" in widget.error_text
    assert widget.summary_text == ""
    assert widget._track == {} and widget._footprint == {}


# ======================================================================
# 多星态势 · 时间广播重算 · 位置中枢上图（2026-11-16）
# ======================================================================
def test_constellation_multi_sat_paints_and_summary(monkeypatch):
    app = QApplication.instance() or QApplication(sys.argv)
    import mbdsdr_ai.ground_track as gt
    monkeypatch.setattr(gt, "fetch_tle", lambda catnr, max_age_hours=24.0: ISS_TLE)
    from sat_ground_map import SatelliteGroundMap

    widget = SatelliteGroundMap(online=False)
    tle_map = {"ISS (ZARYA)": ISS_TLE, "NOAA 19": ISS_TLE}
    ok = widget.update_constellation(
        ["ISS (ZARYA)", "NOAA 19"], when_unix=1763000000.0,
        tle_map=tle_map, recenter=False)
    assert ok is True
    assert "2 星" in widget.summary_text
    assert "覆盖半径" in widget.summary_text
    assert "injected" in widget.summary_text, "注入 TLE 不许冒充联网"
    assert len(widget.constellation) == 2

    pix = QPixmap(320, 220)
    pix.fill(Qt.black)
    painter = QPainter(pix)
    widget._paint_footprint(painter, _project)
    widget._paint_track(painter, _project)
    painter.end()
    assert _non_black(pix) > 200, "两个圈/两条轨迹必须真画出像素"
    widget.shutdown()
    app.processEvents()


def test_constellation_follows_temporal_broadcast(monkeypatch):
    app = QApplication.instance() or QApplication(sys.argv)
    import mbdsdr_ai.ground_track as gt
    monkeypatch.setattr(gt, "fetch_tle", lambda catnr, max_age_hours=24.0: ISS_TLE)
    from sat_ground_map import SatelliteGroundMap
    from mbdsdr_ai.spacetime_features import (
        TemporalContext, TemporalMode, TimeInterval, get_temporal_bus)

    widget = SatelliteGroundMap(online=False)
    t1 = 1763000000.0
    widget.update_constellation(["ISS (ZARYA)"], when_unix=t1,
                                tle_map={"ISS (ZARYA)": ISS_TLE},
                                recenter=False)
    before = widget.constellation[0]["footprint"]["subpoint"]
    assert before.unix_s == pytest.approx(t1)

    bus = get_temporal_bus()
    t2 = t1 + 600.0
    bus.publish(TemporalContext(
        at_unix=t2, interval=TimeInterval(t2, t2),
        basis="simulated", mode=TemporalMode.RANGE_FILTER,
        has_time_data=True))

    after = widget.constellation[0]["footprint"]["subpoint"]
    assert after.unix_s == pytest.approx(t2), "圈必须按广播时刻重算"
    assert (abs(after.lat_deg - before.lat_deg) > 0.01
            or abs(after.lon_deg - before.lon_deg) > 0.01), "星下点没跟着动"
    widget.shutdown()
    app.processEvents()


def test_constellation_partial_failure_is_honest(monkeypatch):
    app = QApplication.instance() or QApplication(sys.argv)
    import mbdsdr_ai.ground_track as gt
    from mbdsdr_ai.orbit import TLEUnavailableError

    def boom(catnr, max_age_hours=24.0):
        raise TLEUnavailableError("离线且无缓存")

    monkeypatch.setattr(gt, "fetch_tle", boom)
    from sat_ground_map import SatelliteGroundMap

    widget = SatelliteGroundMap(online=False)
    ok = widget.update_constellation(
        ["ISS (ZARYA)", "NOAA 19"], when_unix=1763000000.0,
        tle_map={"ISS (ZARYA)": ISS_TLE}, recenter=False)
    assert ok is True, "ISS 注入了 TLE 应该画得出来"
    assert len(widget.constellation) == 1
    assert "NOAA 19" in widget.error_text
    assert "TLEUnavailableError" in widget.error_text
    assert "取不到" in widget.summary_text
    widget.shutdown()
    app.processEvents()


def test_position_report_lands_on_map_collection(monkeypatch):
    """真实位置上报 → 要素集合 → 图层刷新；没有位置就什么都不发生。"""
    app = QApplication.instance() or QApplication(sys.argv)
    from mbdsdr_ai.position_service import (
        ingest_aircraft, reset_position_service)
    from mbdsdr_ai.adsb_map import Aircraft
    from mbdsdr_ai.spacetime_features import get_ground_features
    from sat_ground_map import SatelliteGroundMap

    reset_position_service()
    col = get_ground_features()
    col.remove("pos:QTMAP1")
    widget = SatelliteGroundMap(online=False)  # 订阅位置中枢
    # 图层必须拿住同一个集合对象：空集合 falsy 曾让图层悄悄换私有集合，
    # 全局上报的要素一个都画不出来（2026-11-16 实测踩坑）。
    assert widget.spacetime_layer.collection is col
    assert all(f.feature_id != "pos:QTMAP1" for f in col.all())

    ingest_aircraft(Aircraft(icao_hex="QTMAP1", callsign="ONEMAP",
                             lat=39.9, lon=116.4, last_seen=1763000000.0))
    feat = [f for f in col.all() if f.feature_id == "pos:QTMAP1"]
    assert feat, "位置上报必须进要素集合"
    assert feat[0].kind == "adsb" and feat[0].label == "ONEMAP"
    assert widget.spacetime_layer is not None
    assert widget.spacetime_layer.result is not None, "图层应已刷新查询"

    # 没位置的报告：集合不长草
    ingest_aircraft(Aircraft(icao_hex="QTMAP2", callsign="GHOST"))
    assert all(f.feature_id != "pos:QTMAP2" for f in col.all())
    widget.shutdown()
    app.processEvents()


def test_constellation_colors_are_distinct_and_tagged(monkeypatch):
    """多星可分辨：色弱口径=颜色只做辅助，主识别是文字标签。"""
    app = QApplication.instance() or QApplication(sys.argv)
    import mbdsdr_ai.ground_track as gt
    monkeypatch.setattr(gt, "fetch_tle", lambda catnr, max_age_hours=24.0: ISS_TLE)
    from sat_ground_map import SatelliteGroundMap

    widget = SatelliteGroundMap(online=False)
    tle_map = {"ISS (ZARYA)": ISS_TLE, "NOAA 19": ISS_TLE}
    assert widget.update_constellation(
        ["ISS (ZARYA)", "NOAA 19"], when_unix=1763000000.0,
        tle_map=tle_map, recenter=False)
    colors = [s["color"] for s in widget.constellation]
    assert colors[0] != colors[1], "两颗星必须不同色"
    assert all(c.startswith("#") and len(c) == 7 for c in colors)

    # 星名标签必须真的画到画布上（不是只在摘要里）
    pix = QPixmap(320, 220)
    pix.fill(Qt.black)
    painter = QPainter(pix)
    widget._paint_track(painter, _project)
    painter.end()
    assert _non_black(pix) > 100
    widget.shutdown()
    app.processEvents()
