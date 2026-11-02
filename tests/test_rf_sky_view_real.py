# SPDX-License-Identifier: MIT
"""
tests/test_rf_sky_view_real.py
==============================
验证 desktop/rf_sky_view.py 已从合成假数据彻底切换为真实天文数据驱动：

  1. 模块可导入（offscreen 平台），关键类名保留；
  2. 无地面站坐标 / 无 TLE 时：不崩溃，卫星连接=False，显示“未连接”；
  3. 配置地面站 + 内置真实 TLE：sgp4+skyfield 算出卫星 az∈[0,360]、alt∈[-90,90]；
  4. 方位角等距投影往返正确（天顶居中、地平为边缘圆、北在顶部）；
  5. paintEvent 在有/无数据下均不崩溃。

运行：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_rf_sky_view_real.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


# ----------------------------------------------------------------------
class TestImportsAndInterfaces:
    def test_import_main_classes(self):
        from desktop.rf_sky_view import (
            RFSkyView, SkyObject, AntennaPointing, HeatmapCell, SatelliteTracker,
        )
        assert RFSkyView is not None
        assert SkyObject is not None
        assert AntennaPointing is not None
        assert HeatmapCell is not None
        assert SatelliteTracker is not None

    def test_data_classes_preserved(self):
        from desktop.rf_sky_view import SkyObject, AntennaPointing
        o = SkyObject("NOAA 15", 180.0, 45.0, frequency_hz=137620000.0)
        assert o.azimuth_deg == 180.0 and o.elevation_deg == 45.0
        assert o.is_above_horizon() is True
        a = AntennaPointing(azimuth_deg=90, elevation_deg=30, is_tracking=True,
                            target_name="ISS")
        assert a.is_tracking and a.target_name == "ISS"

    def test_no_hardcoded_tle_catalog(self):
        """不再内置硬编码 TLE：模块不导出 DEFAULT_TLES；TLE 只能从用户文件
        (~/.mbdsdr/tle/) 或 Celestrak 在线/缓存加载，二者皆无则显“无 TLE 数据”。"""
        import desktop.rf_sky_view as mod
        assert not hasattr(mod, "DEFAULT_TLES"), "硬编码 TLE 必须移除"
        assert callable(getattr(mod, "_load_user_tle_catalog", None))
        assert callable(getattr(mod, "_load_celestrak_tle_catalog", None))
        # 两个加载器都必须返回 list（可能为空，但绝不抛异常、不造假）
        assert isinstance(mod._load_user_tle_catalog(), list)
        assert isinstance(mod._load_celestrak_tle_catalog(), list)


# ----------------------------------------------------------------------
class TestNoStationDisconnected:
    def test_no_station_shows_disconnected_no_crash(self):
        """无地面站坐标：不启动计算，连接=False，data_source=none（显示未连接）。"""
        from desktop.rf_sky_view import RFSkyView, SatelliteTracker
        v = RFSkyView()
        v.resize(500, 500)
        t = SatelliteTracker(v, None, None)
        assert v.get_observer() is None
        assert v._satellites_connected is False
        assert v._data_source == "none"
        assert v._objects == []
        # 绘制不崩溃
        v.repaint()

    def test_set_location_none_clears(self):
        """运行中撤销站址：清空卫星并回到未连接。"""
        from desktop.rf_sky_view import RFSkyView, SatelliteTracker
        v = RFSkyView()
        v.resize(500, 500)
        t = SatelliteTracker(v, 39.9, 116.4)
        t.set_location(None, None)
        assert v._satellites_connected is False
        assert v._data_source == "none"
        v.repaint()


# ----------------------------------------------------------------------
class TestRealSatelliteComputation:
    def test_with_station_az_alt_ranges(self, tmp_path, monkeypatch):
        """配置站址 + 自带 TLE：至少一颗卫星算出合法 az/alt。

        TLE 由测试自己放进临时 HOME 的 ~/.mbdsdr/tle/（rf_sky_view 明确
        "无硬编码 TLE 元素"，卫星只来自用户目录或 celestrak）——不依赖网络、
        不依赖用户目录残留、也不依赖套件执行顺序。
        """
        from desktop.rf_sky_view import RFSkyView, SatelliteTracker
        home = tmp_path / "home"
        tle_dir = home / ".mbdsdr" / "tle"
        tle_dir.mkdir(parents=True, exist_ok=True)
        fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "fixtures", "iridium107", "tle.txt")
        with open(fixture, "r", encoding="utf-8") as f:
            lines = [ln.rstrip() for ln in f if ln.strip()]
        # 夹具是 "名称 + TLE 两行"（可能带多行）——取名称行与两条 TLE 行
        name = lines[0]
        l1 = next(ln for ln in lines if ln.startswith("1 "))
        l2 = next(ln for ln in lines if ln.startswith("2 "))
        (tle_dir / "iridium107.tle").write_text(
            f"{name}\n{l1}\n{l2}\n", encoding="utf-8")
        # expanduser：POSIX 看 HOME，Windows 看 USERPROFILE，两个都指过去
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))

        v = RFSkyView()
        v.resize(600, 600)
        # 示例站；refresh 同步执行
        t = SatelliteTracker(v, 43.817, 125.323, alt_km=0.2, interval_ms=10**9)
        t.refresh()
        assert v._satellites_connected is True, "sgp4 应至少成功算出一颗卫星"
        assert len(v._objects) >= 1
        for o in v._objects:
            assert 0.0 <= o.azimuth_deg <= 360.0, f"{o.name} az 越界"
            assert -90.0 <= o.elevation_deg <= 90.0, f"{o.name} alt 越界"
            assert o.obj_type == "satellite"
        v.repaint()

    def test_sun_brightness_computed(self):
        """太阳高度与昼夜亮度因子被计算（0..1），不崩溃。"""
        from desktop.rf_sky_view import RFSkyView, SatelliteTracker
        v = RFSkyView()
        v.resize(600, 600)
        t = SatelliteTracker(v, 39.9, 116.4, interval_ms=10**9)
        t.refresh()
        assert 0.0 <= v._sky_brightness <= 1.0
        # 太阳必在地平附近某处算出（离线 Meeus）
        assert v._sun is not None
        assert 0.0 <= v._sun["az"] <= 360.0


# ----------------------------------------------------------------------
class TestProjection:
    def test_perspective_mapping(self):
        """透视投影 (StelProjector): 天顶->屏幕中心; 北/东方向正确。

        视图中心在天顶 (alt=90), FOV=120° (半视场 60°)。视场内的点
        (与天顶夹角 < 60°, 即 alt > 30°) 应按方向落到中心上/右方。
        """
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(600, 600)
        cx, cy = v._sky_center()
        # 天顶 az=0 alt=90 -> 屏幕中心
        pz = v._sky_to_screen(0, 90)
        assert abs(pz.x() - cx) < 1e-3 and abs(pz.y() - cy) < 1e-3
        # 正北方向 alt=30 (与天顶夹角 60°, 恰在半视场边缘) -> 中心上方
        pn = v._sky_to_screen(0, 30)
        assert abs(pn.x() - cx) < 2.0 and pn.y() < cy
        # 正东方向 alt=30 -> 中心右方
        pe = v._sky_to_screen(90, 30)
        assert pe.x() > cx and abs(pe.y() - cy) < 2.0

    def test_projection_roundtrip(self):
        """屏幕->天空->屏幕 往返误差小 (点取在半视场 60° 内, alt>=30°)。"""
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(600, 600)
        for az, alt in [(0, 60), (90, 45), (180, 60), (270, 45), (45, 75)]:
            s = v._sky_to_screen(az, alt)
            az2, alt2 = v._screen_to_sky(s.x(), s.y())
            assert abs(alt2 - alt) < 1.0, f"alt 往返 {alt}->{alt2}"
            daz = abs((az2 - az + 180) % 360 - 180)
            assert daz < 2.0, f"az 往返 {az}->{az2}"


class TestStarExtinctionWiring:
    """_draw_stars 必须把大气消光灌进 star_draw（有效星等 = 视星等 + Δm(alt)）。"""

    def test_effective_vmag_includes_extinction(self, monkeypatch):
        from desktop.rf_sky_view import RFSkyView
        from mbdsdr_ai.skyengine import stars as _stars
        from mbdsdr_ai.skyengine import constellations as _constel

        captured = []
        real_star_draw = _stars.star_draw

        def _spy(vmag, bortle=3):
            captured.append(float(vmag))
            return real_star_draw(vmag, bortle)

        # 北极星：纬度 40° 处 alt≈40°、vmag=1.98，必在星表内
        polaris = next(s for s in _stars.BRIGHT_STARS if s.name == "Polaris")
        monkeypatch.setattr(_stars, "star_draw", _spy)
        # 2026-11-02 修缝：_draw_stars 遍历的是 constellations.merged_star_index()
        # （亮星 + 星座扩员），不是 _stars.BRIGHT_STARS；constellations 对后者是
        # import 值绑定，patch _stars.BRIGHT_STARS 传不进去——captured[0] 曾是
        # 天狼星当下的低空消光值（随时间漂 0.01+）。按真实接缝钉死单星。
        monkeypatch.setattr(
            _constel, "merged_star_index", lambda: {"Polaris": polaris})

        v = RFSkyView()
        v.set_observer(40.0, 116.0, 0.0)
        v.resize(600, 600)
        v._sky_brightness = 0.0
        from PySide6.QtGui import QPixmap, QPainter
        pm = QPixmap(600, 600)
        pm.fill()
        painter = QPainter(pm)
        try:
            v._draw_stars(painter)
        finally:
            painter.end()

        assert captured, "star_draw 未被调用（星不在屏/未绘制）"
        expected = polaris.vmag + _stars.extinction_delta_mag(40.0)
        assert abs(captured[0] - expected) < 0.35, \
            f"有效星等 {captured[0]} ≠ vmag+Δm(40°)≈{expected}"
        # 消光确实加了正增量（不是裸 vmag）
        assert captured[0] > polaris.vmag + 0.2


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
class TestTwilightGradient:
    """S4 暮光渐变 hex 表：多段 stops、夜/昼混合、画出来下亮上暗。"""

    def test_stops_cover_unit_interval(self):
        from desktop.rf_sky_view import _twilight_stops
        stops = _twilight_stops(0.0)
        assert stops[0][0] == 0.0
        assert stops[-1][0] == 1.0
        pos = [p for p, _ in stops]
        assert pos == sorted(pos)
        assert len(stops) >= 5            # 多于旧 3-stop 直通 lerp

    def test_night_and_day_endpoints(self):
        from desktop.rf_sky_view import _twilight_stops
        night_top = _twilight_stops(0.0)[0][1]
        day_top = _twilight_stops(1.0)[0][1]
        assert (night_top.red(), night_top.green(), night_top.blue()) == (0x05, 0x08, 0x0F)
        assert (day_top.red(), day_top.green(), day_top.blue()) == (0x3F, 0x6E, 0x9E)

    def test_brightness_mixes_linearly(self):
        from desktop.rf_sky_view import _twilight_stops
        lo = _twilight_stops(0.0)
        hi = _twilight_stops(1.0)
        mid = _twilight_stops(0.5)
        for (_, c0), (_, c1), (_, cm) in zip(lo, hi, mid):
            assert abs(cm.red() - (c0.red() + c1.red()) / 2) <= 1
            assert abs(cm.blue() - (c0.blue() + c1.blue()) / 2) <= 1

    def test_brightness_clamped(self):
        from desktop.rf_sky_view import _twilight_stops
        a = _twilight_stops(0.0)[0][1]
        b = _twilight_stops(-5.0)[0][1]
        assert (a.red(), a.green(), a.blue()) == (b.red(), b.green(), b.blue())

    def test_painted_night_sky_brighter_at_horizon(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(300, 300)
        v._sky_brightness = 0.0
        img = v.grab().toImage()
        top = img.pixelColor(150, 5)
        bottom = img.pixelColor(150, 295)
        lum = lambda c: 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        assert lum(bottom) > lum(top), "夜空应上暗下亮（地平线微亮）"


class TestPaintingRobust:
    def test_paint_without_data(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(400, 400)
        v.repaint()

    def test_paint_with_antenna_overlay(self):
        from desktop.rf_sky_view import RFSkyView, AntennaPointing
        v = RFSkyView()
        v.resize(400, 400)
        # 模拟模式已移除：数据源只允许 none/real；天线指向叠加层在无数据下也能绘制
        v.set_data_source("none")
        v.set_antenna(AntennaPointing(azimuth_deg=120, elevation_deg=40))
        v.repaint()


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
