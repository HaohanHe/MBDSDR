# SPDX-License-Identifier: MIT
"""公用地图服务的契约（2026-11-10 第二轮）。

用户这一轮的要求拆成四条可验证的事：

1. **地图公用**：ADS-B / 地点 / 未来 APRS·指向·模型展示用同一个视图，
   不许哪个面板再自己画一张。所以钉 `AdsbMapPanel.canvas` 是
   ``OsmMapView``，且业务只通过 ``add_layer`` 叠加。
2. **暗色底图**：OSM 官方标准瓦片没有深色版，深色要用 OSM 数据的暗色渲染
   （CARTO dark）。钉风格表与主题映射，别把浅色瓦片硬调暗。
3. **可离线**：能按 中心/半径/层级 算出要下哪些瓦片（纯函数），已缓存的
   跳过并如实上报；下完就能断网看。
4. **不串图**：同一 z/x/y 在不同风格下必须是不同缓存文件。

全部不联网（下载器只测计划与跳过逻辑）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import osm_map as O  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------- #
# 风格
# ---------------------------------------------------------------------- #
def test_two_styles_and_dark_flag():
    assert set(O.STYLES) == {"osm", "dark"}
    assert O.STYLES["osm"].dark is False
    assert O.STYLES["dark"].dark is True
    # 暗色不再借第三方：CARTO 现在直接返回 "API KEY REQUIRED" 水印图、
    # Stadia 401，用它们的瓦片等于把商业水印嵌进产品。改成同一份 OSM
    # 瓦片 + 本地渲染（只改颜色，不动几何）。
    assert "tile.openstreetmap.org" in O.STYLES["dark"].url
    assert O.STYLES["dark"].attribution == O.ATTRIBUTION_OSM
    assert O.STYLES["dark"].filter == "invert_dark"


def test_style_for_dark_maps_theme():
    assert O.style_for_dark(True) == "dark"
    assert O.style_for_dark(False) == "osm"
    # 未知 key 不崩，回落到标准瓦片并如实取它的署名
    assert O.get_style("no-such-style").key == "osm"


def test_max_zoom_respects_style():
    # 两种风格都走 OSM 官方瓦片，上限一致（20 级那家要 key，不用）
    assert O.style_max_zoom("osm") == 19
    assert O.style_max_zoom("dark") == 19


# ---------------------------------------------------------------------- #
# 缓存隔离
# ---------------------------------------------------------------------- #
def test_cache_is_separated_by_style(tmp_path):
    a = O.TileCache(root=str(tmp_path), style="osm")
    b = O.TileCache(root=str(tmp_path), style="dark")
    pa = a.path_for(10, 843, 388)
    pb = b.path_for(10, 843, 388)
    assert pa != pb
    assert os.sep + "osm" + os.sep in pa
    assert os.sep + "dark" + os.sep in pb
    a.store_disk(10, 843, 388, b"x")
    assert a.has_disk(10, 843, 388)
    assert not b.has_disk(10, 843, 388)     # 不串图


def test_disk_bytes_counts_real_files(tmp_path):
    c = O.TileCache(root=str(tmp_path), style="osm")
    assert c.disk_bytes() == 0
    c.store_disk(3, 1, 1, b"a" * 100)
    assert c.disk_bytes() >= 100


# ---------------------------------------------------------------------- #
# 视图：风格切换 / 图层
# ---------------------------------------------------------------------- #
def test_view_switches_style_and_attribution(qapp, tmp_path):
    v = O.OsmMapView(online=False,
                     cache=O.TileCache(root=str(tmp_path), style="osm"))
    try:
        assert v.style_key() == "osm"
        assert "OpenStreetMap" in v.style_attribution()
        v.set_style("dark")
        assert v.style_key() == "dark"
        # 暗色也是 OSM 数据（本地渲染），署名不该冒出一家底图供应商
        assert "OpenStreetMap" in v.style_attribution()
        # 换风格后缓存也跟着换目录，旧图不会被当成新风格的图
        assert v.cache().style == "dark"
    finally:
        v.deleteLater()


def test_layers_are_named_and_removable(qapp, tmp_path):
    v = O.OsmMapView(online=False,
                     cache=O.TileCache(root=str(tmp_path)))
    calls = []
    try:
        v.add_layer("adsb", lambda p, project: calls.append("adsb"))
        v.add_layer("aprs", lambda p, project: calls.append("aprs"))
        assert v.layer_names() == ["adsb", "aprs"]
        v.grab()                       # 触发一次真实绘制
        assert calls == ["adsb", "aprs"]
        v.remove_layer("adsb")
        assert v.layer_names() == ["aprs"]
        calls.clear()
        v.grab()
        assert calls == ["aprs"]
    finally:
        v.deleteLater()


def test_broken_layer_does_not_break_map(qapp, tmp_path):
    """一个图层抛异常，底图和其他图层仍要画完——地图是公用的。"""
    v = O.OsmMapView(online=False, cache=O.TileCache(root=str(tmp_path)))
    seen = []
    try:
        v.add_layer("boom", lambda p, project: 1 / 0)
        v.add_layer("ok", lambda p, project: seen.append(1))
        img = v.grab()
        assert not img.isNull()
        assert seen == [1]
    finally:
        v.deleteLater()


# ---------------------------------------------------------------------- #
# 离线下载：计划与跳过（不联网）
# ---------------------------------------------------------------------- #
def test_download_plan_shape_and_monotonicity():
    small = O.RegionDownloader.plan(45.0, 120.0, 8, 10, 20.0)
    big = O.RegionDownloader.plan(45.0, 120.0, 8, 10, 60.0)
    assert small and big
    # 层级都在请求范围内
    assert {z for (z, _x, _y) in small} == {8, 9, 10}
    # 半径越大瓦片越多（且高一级约 4 倍）
    assert len(big) > len(small)
    z8 = [t for t in small if t[0] == 8]
    z9 = [t for t in small if t[0] == 9]
    assert len(z9) > len(z8)
    # 同一层级不重复
    assert len(set(z8)) == len(z8)


def test_download_plan_near_pole_does_not_blow_up():
    """高纬经度换算要除 cos(lat)，90° 附近别算出天文数字的瓦片。"""
    jobs = O.RegionDownloader.plan(84.9, 10.0, 4, 5, 50.0)
    assert 0 < len(jobs) < 2000


def test_downloader_skips_cached_and_finishes(tmp_path):
    cache = O.TileCache(root=str(tmp_path), style="osm")
    dl = O.RegionDownloader(cache, style="osm")
    jobs = O.RegionDownloader.plan(45.0, 120.0, 5, 5, 30.0)
    for (z, x, y) in jobs:
        cache.store_disk(z, x, y, b"cached")
    msg = []
    done = []
    dl.message.connect(msg.append)
    dl.finished.connect(lambda ok, bad: done.append((ok, bad)))
    dl.start_jobs(jobs)
    assert done == [(0, 0)]
    assert "已经下过" in msg[-1]


def test_downloader_cancel_is_honest(tmp_path):
    cache = O.TileCache(root=str(tmp_path), style="osm")
    dl = O.RegionDownloader(cache, style="osm")
    # 未运行时取消是空操作，不该报错也不该假装"已取消下载"
    dl.cancel()
    assert dl.is_running() is False


# ---------------------------------------------------------------------- #
# 暗色渲染：只改颜色，不动几何
# ---------------------------------------------------------------------- #
def _light_tile() -> "O.QImage":
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QImage
    img = QImage(64, 64, QImage.Format_RGBA8888)
    img.fill(QColor(245, 240, 230))          # OSM 浅色底
    for x in range(64):
        for y in range(10, 14):
            img.setPixelColor(x, y, QColor(255, 255, 255))   # 一条白路
    return img


def _mean_luma(img) -> float:
    total = 0
    n = 0
    for y in range(0, img.height(), 4):
        for x in range(0, img.width(), 4):
            c = img.pixelColor(x, y)
            total += 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
            n += 1
    return total / max(1, n)


def test_darken_image_turns_light_map_dark(qapp):
    src = _light_tile()
    out = O.darken_image(src)
    assert (out.width(), out.height()) == (src.width(), src.height())
    assert _mean_luma(out) < _mean_luma(src) * 0.45   # 明显变暗，不是"稍微压暗"
    assert _mean_luma(out) > 5                        # 也不是全黑


def test_darken_image_keeps_contrast(qapp):
    """暗色化后道路与底色必须仍有对比——否则整张图糊成一片。

    不假设"路一定比底亮"：OSM 白路反相后是深色，能不能看见取决于
    与底色的差，所以断言的是**差值**，不是方向。
    """
    src = _light_tile()
    out = O.darken_image(src)

    def luma(c):
        return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()

    src_gap = abs(luma(src.pixelColor(32, 12)) - luma(src.pixelColor(32, 50)))
    out_gap = abs(luma(out.pixelColor(32, 12)) - luma(out.pixelColor(32, 50)))
    # OSM 里「白路 vs 米色底」本来就只差十几个灰阶（靠描边区分），所以
    # 判据是"对比度没有丢失"，而不是"差值必须大于某个绝对数"。
    assert src_gap > 5
    assert out_gap >= src_gap * 0.8


def test_processed_dark_tile_is_cached_as_image(qapp, tmp_path):
    """暗色瓦片缓存的是**处理后**的图，不是浅色原图——否则再打开还是亮的。"""
    cache = O.TileCache(root=str(tmp_path), style="dark")
    img = O.darken_image(_light_tile())
    cache.store_image(7, 100, 50, img)
    assert cache.has_disk(7, 100, 50)
    back = cache.load_disk(7, 100, 50)
    assert back is not None and not back.isNull()
    assert _mean_luma(back.toImage()) < 120
