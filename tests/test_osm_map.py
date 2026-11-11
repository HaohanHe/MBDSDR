# SPDX-License-Identifier: MIT
"""OSM 在线瓦片地图的契约（2026-11-10）。

用户 11-10 的要求：「别内置那张自己画的地图，直接用 OpenStreetMap」。
所以这里钉的不是「画得像不像」，而是四件能验证的事：

1. Web Mercator 正反解严格互逆 —— 点在哪、坐标就是哪，不许有拉伸误差；
2. 瓦片索引范围算对（不请求极区外的 y，跨 180° 的 x 不丢）；
3. 缓存读写不串（磁盘 z/x/y.png 与内存 LRU）；
4. 交互语义：单击=选点、拖拽≠选点、滚轮以光标为锚缩放。
   全部用注入的 ``tile_source`` / ``online=False`` 跑，**测试不联网**。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QPixmap, QWheelEvent  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import osm_map as O  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _solid(color="#336699") -> QPixmap:
    pm = QPixmap(O.TILE_SIZE, O.TILE_SIZE)
    pm.fill(color)
    return pm


# ---------------------------------------------------------------------- #
# 投影
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("lat,lon,zoom", [
    (0.0, 0.0, 3), (30.0, 110.0, 5), (-33.9, 151.2, 9),
    (45.0, 120.0, 1), (84.0, -179.9, 7), (-84.0, 179.9, 12),
])
def test_projection_roundtrip(lat, lon, zoom):
    x, y = O.lonlat_to_world(lat, lon, zoom)
    back_lat, back_lon = O.world_to_lonlat(x, y, zoom)
    assert back_lat == pytest.approx(lat, abs=1e-9)
    assert back_lon == pytest.approx(lon, abs=1e-9)


def test_projection_matches_osm_tile_grid():
    """z0 的世界正好一张瓦片；z1 的 0/0 瓦片左上角是 -180,85.05。"""
    assert O.lonlat_to_world(0.0, 0.0, 0) == pytest.approx(
        (O.TILE_SIZE / 2, O.TILE_SIZE / 2))
    x, y = O.lonlat_to_world(O.MAX_LAT, -180.0, 1)
    assert (x, y) == pytest.approx((0.0, 0.0), abs=1e-6)


def test_projection_is_mercator_not_equirectangular():
    """等距圆柱把 60° 放在 y=1/6；墨卡托必须明显更靠下（≈0.2904）。"""
    _x, y_60 = O.lonlat_to_world(60.0, 0.0, 4)
    world = O.TILE_SIZE * 2 ** 4
    assert y_60 / world == pytest.approx(0.2904, abs=1e-3)   # ≠ 1/6=0.1667
    _x, y_0 = O.lonlat_to_world(0.0, 0.0, 4)
    assert y_0 / world == pytest.approx(0.5, abs=1e-6)


def test_tile_range_clips_poles_and_keeps_wrap():
    tx0, ty0, tx1, ty1 = O.tile_range(0, -500, 600, 900, 3)
    assert ty0 == 0                     # 极区负索引被裁掉
    assert ty1 == 3                     # 900px / 256 落在第 3 行
    tx0, _ty0, tx1, _ty1 = O.tile_range(-300, 0, 100, 100, 2)
    assert tx0 == -2 and tx1 == 0        # 跨 180°，原样返回给调用方取模


# ---------------------------------------------------------------------- #
# 缓存
# ---------------------------------------------------------------------- #
def test_cache_memory_lru_and_disk_roundtrip(qapp, tmp_path):
    cache = O.TileCache(root=str(tmp_path), max_mem=2)
    assert cache.get_mem(1, 1, 1) is None
    cache.put(1, 1, 1, _solid("#111111"))
    cache.put(1, 1, 2, _solid("#222222"))
    cache.put(1, 1, 3, _solid("#333333"))   # 挤掉最旧
    assert cache.get_mem(1, 1, 1) is None
    assert cache.get_mem(1, 1, 3) is not None

    png = os.path.join(tmp_path, "_src.png")
    _solid("#445566").save(png, "PNG")
    with open(png, "rb") as f:
        data = f.read()
    cache.store_disk(2, 3, 4, data)
    path = cache.path_for(2, 3, 4)
    assert os.path.exists(path)
    fresh = O.TileCache(root=str(tmp_path))
    pm = fresh.load_disk(2, 3, 4)
    assert pm is not None and not pm.isNull()


def test_cache_dir_honours_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MBDSDR_CACHE_DIR", str(tmp_path))
    assert O.default_cache_dir() == os.path.join(str(tmp_path), "osm_tiles")


# ---------------------------------------------------------------------- #
# 视图交互
# ---------------------------------------------------------------------- #
def _view(qapp, online=False, tile_source=None):
    v = O.OsmMapView(online=online, tile_source=tile_source)
    v.resize(400, 300)
    v.set_view(30.0, 110.0, 3)
    return v


def _click(view, pos: QPointF):
    press = QMouseEvent(QEvent.MouseButtonPress, pos, pos,
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    release = QMouseEvent(QEvent.MouseButtonRelease, pos, pos,
                          Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    view.mousePressEvent(press)
    view.mouseReleaseEvent(release)


def test_center_pixel_maps_to_center_lonlat(qapp):
    v = _view(qapp)
    try:
        lat, lon = v.unproject(QPointF(200, 150))
        assert lat == pytest.approx(30.0, abs=1e-6)
        assert lon == pytest.approx(110.0, abs=1e-6)
        back = v.project(30.0, 110.0)
        assert back.x() == pytest.approx(200.0, abs=1e-6)
        assert back.y() == pytest.approx(150.0, abs=1e-6)
    finally:
        v.deleteLater()


def test_single_click_picks_without_moving_site_marker(qapp):
    """单击只发选点信号：当前站址标记不该被一次点地图搬走。"""
    v = _view(qapp)
    got = []
    v.picked.connect(lambda la, lo: got.append((la, lo)))
    v.set_marker(10.0, 20.0)
    try:
        _click(v, QPointF(120, 90))
        assert len(got) == 1
        la, lo = got[0]
        assert -90 <= la <= 90 and -180 <= lo <= 180
        assert v.marker() == (10.0, 20.0)      # 站址原地不动
        assert v.pick_marker() is None         # 预览标记由面板自己决定要不要画
    finally:
        v.deleteLater()


def test_pick_marker_is_separate_from_site_marker(qapp):
    v = _view(qapp)
    try:
        v.set_marker(10.0, 20.0)
        v.set_pick_marker(31.5, 121.5)
        assert v.marker() == (10.0, 20.0)
        assert v.pick_marker() == (31.5, 121.5)
        v.set_pick_marker(None, None)
        assert v.pick_marker() is None
        assert v.marker() == (10.0, 20.0)
    finally:
        v.deleteLater()


def test_drag_pans_without_picking(qapp):
    v = _view(qapp)
    got = []
    v.picked.connect(lambda la, lo: got.append((la, lo)))
    try:
        before = v.center()
        start = QPointF(100, 100)
        v.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, start, start,
                                      Qt.LeftButton, Qt.LeftButton,
                                      Qt.NoModifier))
        mid = QPointF(160, 130)
        v.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, mid, mid,
                                     Qt.NoButton, Qt.LeftButton,
                                     Qt.NoModifier))
        v.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, mid, mid,
                                        Qt.LeftButton, Qt.NoButton,
                                        Qt.NoModifier))
        assert got == []                     # 拖拽绝不等于选点
        assert v.center() != before          # 视图确实移动了
        assert v.marker() is None
    finally:
        v.deleteLater()


def test_wheel_zoom_keeps_point_under_cursor(qapp):
    v = _view(qapp)
    try:
        anchor = QPointF(320.0, 210.0)
        lat0, lon0 = v.unproject(anchor)
        ev = QWheelEvent(anchor, anchor, QPoint(0, 0), QPoint(0, 120),
                         Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        v.wheelEvent(ev)
        assert v.zoom() == 4
        lat1, lon1 = v.unproject(anchor)
        assert lat1 == pytest.approx(lat0, abs=0.02)
        assert lon1 == pytest.approx(lon0, abs=0.02)
    finally:
        v.deleteLater()


def test_zoom_is_clamped(qapp):
    v = _view(qapp)
    try:
        v.set_view(None, None, 99)
        # 上限跟着**当前瓦片源**走（OSM 官方 19，CARTO 20），不是全局一刀切
        assert v.zoom() == O.style_max_zoom(v.style_key())
        assert v.zoom() <= O.MAX_ZOOM
        v.set_view(None, None, -5)
        assert v.zoom() == O.MIN_ZOOM
    finally:
        v.deleteLater()


def test_injected_tile_source_is_drawn_without_network(qapp):
    calls = []

    def src(z, x, y):
        calls.append((z, x % (2 ** z), y))
        return _solid("#0a2a1a")

    v = _view(qapp, online=False, tile_source=src)
    try:
        assert v._loader.online is False          # 注入源时绝不能联网
        img = v.grab()
        assert not img.isNull()
        assert calls, "paintEvent 应该向注入源要过瓦片"
    finally:
        v.deleteLater()


def test_offline_view_paints_placeholder_not_crash(qapp, tmp_path):
    v = O.OsmMapView(online=False, cache=O.TileCache(root=str(tmp_path)))
    v.resize(320, 240)
    v.set_view(45.0, 120.0, 4)
    try:
        img = v.grab()
        assert not img.isNull()
        assert v.tile_stats() == (0, 0)           # 离线不发起请求
    finally:
        v.deleteLater()


def test_attribution_click_opens_odbl_page(qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(O.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()) or True)
    v = _view(qapp)
    try:
        v.show()
        rect = v._attr_rect()
        assert v.width() > 0
        # 署名只在真有瓦片画出来时才显示/可点（零瓦片不挂来源署名）。
        # 本用例只验「点署名开版权页」，所以直接把绘制结果记为有瓦片。
        v._drawn_tiles = 1
        pos = QPointF(rect.center().x(), rect.center().y())
        _click(v, pos)
        assert opened and "openstreetmap.org" in opened[0]
        assert v.marker() is None                 # 点署名不算选点
    finally:
        v.deleteLater()


def test_tile_loader_offline_makes_no_request(qapp, tmp_path):
    cache = O.TileCache(root=str(tmp_path))
    loader = O.TileLoader(cache, online=False)
    try:
        assert loader.request(3, 6, 3) is None
        assert loader.ok_count == 0 and loader.fail_count == 0
    finally:
        loader.deleteLater()
