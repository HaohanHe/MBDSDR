# SPDX-License-Identifier: MIT
"""时空要素**在地图画布上**的行为钉扎（offscreen Qt）。

交付物是画布像素，不是抽屉文字：图层必须注册到公用地图、真画出东西、
时间跳变后跟着重算、点选能报出详情与原因。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mbdsdr_ai import spacetime_features as sf  # noqa: E402
from spacetime_map_layer import (  # noqa: E402
    LAYER_NAME, SpacetimeFeatureLayer, footprint_from_satellite)


class _FakeView:
    """只实现 OsmMapView 的 add_layer / remove_layer / update 契约。"""

    def __init__(self):
        self.layers = {}
        self.updates = 0

    def add_layer(self, name, cb):
        self.layers[name] = cb

    def remove_layer(self, name):
        self.layers.pop(name, None)

    def update(self):
        self.updates += 1

    def width(self):
        return 640


def _project(lat, lon):
    return QPointF(320.0 + (lon - 116.4) * 20.0,
                   120.0 - (lat - 39.9) * 20.0)


def _visible_pixels(cb, bg=QColor("#101820")):
    img = QImage(640, 240, QImage.Format_ARGB32)
    img.fill(bg)
    p = QPainter(img)
    try:
        cb(p, _project)
    finally:
        p.end()
    base = bg.rgb()
    return sum(
        1 for y in range(0, img.height(), 2)
        for x in range(0, img.width(), 2)
        if img.pixelColor(x, y).rgb() != base)


def _app():
    return QApplication.instance() or QApplication(sys.argv)


def _layer(collection=None, bus=None):
    _app()
    view = _FakeView()
    col = collection or sf.FeatureCollection()
    bus = bus or sf.TemporalBus()
    layer = SpacetimeFeatureLayer(view, col, bus=bus)
    return view, layer, bus


def _ctx(t=1763000000.0, basis="simulated",
         mode=sf.TemporalMode.RANGE_FILTER, has=True):
    return sf.TemporalContext(
        t, sf.TimeInterval(t, t), basis=basis, mode=mode,
        has_time_data=has)


def test_layer_registers_on_the_map_and_unregisters_on_close():
    view, layer, _bus = _layer()
    assert list(view.layers) == [LAYER_NAME]
    layer.close()
    assert view.layers == {}


def test_honest_empty_state_is_drawn_not_guessed():
    view, layer, _bus = _layer(collection=sf.FeatureCollection([
        sf.SpatialFeature("gs", "ground_station", 39.9, 116.4, label="本站")]))
    assert "未设定时间范围" in layer.result.unknown_reason
    assert len(layer.result.unknown) == 1
    assert _visible_pixels(view.layers[LAYER_NAME]) > 100, "空态角标没画出来"
    layer.close()


def test_features_and_highlight_are_real_pixels_on_the_map():
    col = sf.FeatureCollection([
        sf.SpatialFeature("gs", "ground_station", 39.9, 116.4,
                          label="本站",
                          track=((39.8, 116.3), (39.9, 116.4))),
        sf.SpatialFeature("far", "beacon", 0.0, 0.0, label="远处"),
    ])
    view, layer, bus = _layer(collection=col)
    empty_pixels = _visible_pixels(view.layers[LAYER_NAME])

    bus.publish(_ctx())
    layer.set_footprint(sf.Footprint(39.9, 116.4, 20.0, at_unix=1763000000.0))
    assert layer.result.counts == {"inside": 1, "outside": 1}
    assert [i.feature.feature_id for i in layer.result.inside] == ["gs"]
    filled_pixels = _visible_pixels(view.layers[LAYER_NAME])
    assert filled_pixels > empty_pixels, "要素点/轨迹没在地图上多画出像素"
    layer.close()


def test_time_jump_recomputes_the_map_state():
    col = sf.FeatureCollection([
        sf.SpatialFeature("gs", "ground_station", 39.9, 116.4)])
    view, layer, bus = _layer(collection=col)
    bus.publish(_ctx(t=1000.0, basis="simulated"))
    layer.set_footprint(sf.Footprint(39.9, 116.4, 20.0))
    before = (layer.result.at_unix, layer.result.basis, view.updates)

    bus.publish(_ctx(t=2000.0, basis="real"))
    after = (layer.result.at_unix, layer.result.basis, view.updates)
    assert after[0] == 2000.0 and after[1] == "real"
    assert after[2] > before[2], "时间跳了但地图没请求重绘"
    assert layer.context.basis == "real"
    layer.close()


def test_pick_reports_detail_and_reason_without_new_widgets():
    details = []
    col = sf.FeatureCollection([
        sf.SpatialFeature("gs", "ground_station", 39.9, 116.4,
                          label="本站", properties={"source": "gnss"})])
    view, layer, bus = _layer(collection=col)
    layer._detail_cb = lambda t, s, lvl: details.append((t, s, lvl))
    bus.publish(_ctx())
    layer.set_footprint(sf.Footprint(39.9, 116.4, 20.0))

    hit = layer.on_picked(39.9, 116.4)
    assert hit is not None and hit.feature.feature_id == "gs"
    assert len(details) == 1
    text, suggestion, level = details[0]
    assert "本站" in text and "inside" in text
    assert "gnss" in suggestion and "全程有效" in suggestion
    assert level == "info"
    assert layer.on_picked(0.0, 0.0) is None
    layer.close()


def test_footprint_from_real_ground_track_shape():
    fp = footprint_from_satellite({
        "subpoint": SimpleNamespace(lat_deg=39.9, lon_deg=116.4),
        "central_angle_deg": 18.5,
        "radius_km": 2058.0,
        "ring": [(39.9, 116.4)] * 3,
    }, when_unix=123.0)
    assert fp.lat_deg == 39.9 and fp.lon_deg == 116.4
    assert fp.central_angle_deg == 18.5 and fp.at_unix == 123.0
    assert fp.contains(39.9, 116.4)
    assert footprint_from_satellite({}) is None


def test_redraw_only_mode_keeps_every_feature_on_the_map():
    """天空语义档：时间/空间都不筛要素，地图也不许少画。"""
    col = sf.FeatureCollection([
        sf.SpatialFeature("a", "star", 39.9, 116.4),
        sf.SpatialFeature("b", "star", 0.0, 0.0,
                          valid=sf.TimeInterval(100.0, 200.0)),
    ])
    view, layer, bus = _layer(collection=col)
    bus.publish(_ctx(t=9999.0, mode=sf.TemporalMode.REDRAW_ONLY))
    layer.set_footprint(sf.Footprint(39.9, 116.4, 0.001))
    assert layer.result.counts == {"shown": 2}
    assert len(layer.result.highlighted) == 2
    layer.close()
