# SPDX-License-Identifier: MIT
"""SatelliteGroundMap 绘制验收：图层存在、真实画出像素、失败有诚实空态。"""
import os
import sys

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
    assert widget.view.layer_names() == ["sat_footprint", "sat_ground_track"]
    assert widget.update_satellite("ISS (ZARYA)", minutes=90,
                                   min_elevation_deg=5) is True
    assert "覆盖半径" in widget.summary_text

    pix = QPixmap(320, 220)
    pix.fill(Qt.black)
    painter = QPainter(pix)
    widget._paint_footprint(painter, _project)
    widget._paint_track(painter, _project)
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
