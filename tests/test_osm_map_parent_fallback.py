# SPDX-License-Identifier: MIT
"""上级瓦片回退 + 主动离线的诚实态（tests/test_osm_map_parent_fallback.py）
==========================================================================

学 QGIS / OpenLayers 的同一套做法：当前层级瓦片还没到时，用**已缓存的
上级瓦片放大**顶替这一格，而不是满屏虚线占位。三点硬约束：

1. 只读缓存（内存→磁盘），**绝不在绘制里发请求**——绘制路径不该有网络副作用；
2. 顶替格画的是真实 OSM 像素，所以署名仍然必须显示（不是伪造来源）；
3. 上溯最多 4 级，再往上放大会糊成色块，宁可留占位。

另：``online=False``（主动离线）且本区域零缓存时必须给一句人话，不能一声
不吭留一屏虚线——那会被读成「地图坏了」。

反证：去掉 ``_ancestor_tile`` 分支 → 顶替用例红（_drawn_tiles 归零）；
把离线态改回 idle → 离线用例红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import osm_map as O  # noqa: E402

#: 写进上级瓦片的识别色：蓝远大于红，与浅色底(233,238,244)/占位都不同。
_FILL = QColor("#3366cc")


@pytest.fixture(scope="module")
def qtapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _write_tile(cache: O.TileCache, z: int, x: int, y: int) -> None:
    pm = QPixmap(O.TILE_SIZE, O.TILE_SIZE)
    pm.fill(_FILL)
    path = cache.path_for(z, x, y)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert pm.save(path, "PNG")


def _render(view) -> QImage:
    img = QImage(view.size(), QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    view.render(img)          # 离屏绘制到 QImage（PySide6 的 QPaintDevice 重载）
    return img


def _view_centered_on_tile(qtapp, tmp_path, *, zoom: int, tile_z: int,
                           tx: int, ty: int):
    """视口中心对准 (tile_z, tx, ty) 那格的中心，画布 512×384。"""
    cache = O.TileCache(root=str(tmp_path), style="osm")
    view = O.OsmMapView(online=False, cache=cache)
    view.resize(512, 384)
    cx = (tx + 0.5) * O.TILE_SIZE
    cy = (ty + 0.5) * O.TILE_SIZE
    lat, lon = O.world_to_lonlat(cx, cy, tile_z)
    view.set_view(lat, lon, zoom)
    return view


def test_missing_child_is_covered_by_cached_parent(qtapp, tmp_path):
    """z5 有缓存、z6 没有：z6 的格子必须由 z5 放大顶替，且不发请求。"""
    cache = O.TileCache(root=str(tmp_path), style="osm")
    _write_tile(cache, 5, 10, 12)
    view = _view_centered_on_tile(qtapp, tmp_path, zoom=6, tile_z=5,
                                  tx=10, ty=12)
    # 绘制里一旦发请求就是路径违规：记下来判红（不用 raise——Qt 会吞掉
    # 槽里的异常，测试会假装通过）。
    sent = []
    view._loader.request = lambda *a, **k: sent.append(a)
    try:
        img = _render(view)
        assert not sent, f"绘制路径里发了 {len(sent)} 次瓦片请求（应只读缓存）"
        assert view._drawn_tiles > 0, "上级瓦片没有顶替，屏幕上只剩占位"
        assert view._drawn_fallback > 0, "顶替格数应 > 0"
        assert view._loader.inflight() == 0
        # 真像素：离中心 100px 处必须落在写入的蓝色里（不在网格线上）。
        c = img.pixelColor(img.width() // 2 + 100, img.height() // 2 + 100)
        assert c.blue() - c.red() > 60, f"顶替格没画出真实瓦片像素：{c.name()}"
        assert view._attr_visible() is True, "画的是真实 OSM 像素，署名必须保留"
    finally:
        view.deleteLater()


def test_fallback_stops_after_four_levels(qtapp, tmp_path):
    """最多上溯 4 级：z2 的瓦片能顶 z6，z1 的（第 5 级）不能。"""
    # z6 的第 (20, 24) 格：k=5 的祖先就是 z1 (0,0)。
    lat, lon = O.world_to_lonlat((20 + 0.5) * O.TILE_SIZE,
                                 (24 + 0.5) * O.TILE_SIZE, 6)

    cache_far = O.TileCache(root=str(tmp_path / "far"), style="osm")
    _write_tile(cache_far, 1, 0, 0)
    view_far = O.OsmMapView(online=False, cache=cache_far)
    view_far.resize(512, 384)
    view_far.set_view(lat, lon, 6)
    try:
        _render(view_far)
        assert view_far._drawn_fallback == 0, "超过 4 级的祖先不该被拿来放大"
    finally:
        view_far.deleteLater()

    # k=4 的祖先：z2，20>>4 == 1、24>>4 == 1，属于上限内。
    cache_near = O.TileCache(root=str(tmp_path / "near"), style="osm")
    _write_tile(cache_near, 2, 1, 1)
    view_near = O.OsmMapView(online=False, cache=cache_near)
    view_near.resize(512, 384)
    view_near.set_view(lat, lon, 6)
    try:
        _render(view_near)
        assert view_near._drawn_fallback > 0, "第 4 级祖先应仍可顶替"
    finally:
        view_near.deleteLater()


def test_offline_without_cache_says_so(qtapp, tmp_path):
    """主动离线 + 本区域零缓存：HUD 必须给「离线」人话，且不挂署名。"""
    view = _view_centered_on_tile(qtapp, tmp_path, zoom=6, tile_z=5,
                                  tx=10, ty=12)
    try:
        _render(view)
        assert view._drawn_tiles == 0 and view._drawn_fallback == 0
        state, msg = view._loading_hud()
        assert state == "offline", f"离线空态没被说出来：{(state, msg)!r}"
        assert "离线" in msg
        assert view._attr_visible() is False
    finally:
        view.deleteLater()


def test_project_roundtrip_unchanged_by_fallback(qtapp, tmp_path):
    """顶替只影响像素来源，不动坐标换算（标注/图层位置不能被带偏）。"""
    view = _view_centered_on_tile(qtapp, tmp_path, zoom=6, tile_z=5,
                                  tx=10, ty=12)
    try:
        pt = view.project(30.0, 110.0)
        lat, lon = view.unproject(pt)
        assert lat == pytest.approx(30.0, abs=1e-5)
        assert lon == pytest.approx(110.0, abs=1e-5)
    finally:
        view.deleteLater()
