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

    def test_builtin_tles_are_real_three_lines(self):
        """内置 TLE 必须是合法三行（标题+1+2），可被 sgp4 解析。"""
        from desktop.rf_sky_view import DEFAULT_TLES
        assert len(DEFAULT_TLES) >= 4
        for name, info in DEFAULT_TLES.items():
            tle = info["tle"]
            assert tle[1].startswith("1 "), f"{name} line1 非法"
            assert tle[2].startswith("2 "), f"{name} line2 非法"
            assert len(tle[1]) >= 69 and len(tle[2]) >= 69


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
    def test_with_station_az_alt_ranges(self):
        """配置站址 + 内置 TLE：至少一颗卫星算出合法 az/alt。"""
        from desktop.rf_sky_view import RFSkyView, SatelliteTracker
        v = RFSkyView()
        v.resize(600, 600)
        # 长春站；refresh 同步执行
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
    def test_azimuthal_equidistant_mapping(self):
        """天顶->圆心；地平北点->顶部；东点->右侧。"""
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(600, 600)
        cx, cy = v._sky_center()
        # 天顶 az=0 alt=90 -> 圆心（ViewState 把 center_alt 钳到 90-1e-4 避免极点
        # 奇异，故投影中心有 ~5e-4 px 的数值偏移，容差取 1e-3）
        pz = v._sky_to_screen(0, 90)
        assert abs(pz.x() - cx) < 1e-3 and abs(pz.y() - cy) < 1e-3
        # 地平北 az=0 alt=0 -> 圆心正上方（y < cy）
        pn = v._sky_to_screen(0, 0)
        assert abs(pn.x() - cx) < 2.0 and pn.y() < cy
        # 地平东 az=90 alt=0 -> 圆心正右方（x > cx）
        pe = v._sky_to_screen(90, 0)
        assert pe.x() > cx and abs(pe.y() - cy) < 2.0

    def test_projection_roundtrip(self):
        """屏幕->天空->屏幕 往返误差小。"""
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(600, 600)
        for az, alt in [(0, 45), (90, 30), (180, 60), (270, 10), (45, 75)]:
            s = v._sky_to_screen(az, alt)
            az2, alt2 = v._screen_to_sky(s.x(), s.y())
            assert abs(alt2 - alt) < 1.0, f"alt 往返 {alt}->{alt2}"
            daz = abs((az2 - az + 180) % 360 - 180)
            assert daz < 2.0, f"az 往返 {az}->{az2}"


# ----------------------------------------------------------------------
class TestPaintingRobust:
    def test_paint_without_data(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(400, 400)
        v.repaint()

    def test_paint_with_sim_badge(self):
        from desktop.rf_sky_view import RFSkyView, AntennaPointing
        v = RFSkyView()
        v.resize(400, 400)
        v.set_data_source("sim")
        v.set_antenna(AntennaPointing(azimuth_deg=120, elevation_deg=40))
        v.repaint()


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
