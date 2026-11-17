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

from PySide6.QtGui import QColor  # noqa: E402
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
        from datetime import datetime, timezone
        from desktop.rf_sky_view import RFSkyView
        from mbdsdr_ai.skyengine import stars as _stars
        from mbdsdr_ai.new_spacetime import get_time_engine

        captured = []
        real_star_draw_many = _stars.star_draw_many

        def _spy(vmag, bortle=3):
            # 星空层已改为整批一次算（批量版只是标量版镜像，同一入参口径）：
            # 抓这里仍是同一事实缝——传进来的必须是星表 eff_mag。
            for v in vmag:
                captured.append(float(v))
            return real_star_draw_many(vmag, bortle)

        monkeypatch.setattr(_stars, "star_draw_many", _spy)

        eng = get_time_engine()
        eng.set_unix(datetime(2026, 9, 27, 12, 0,
                             tzinfo=timezone.utc).timestamp())
        eng.pause()
        v = RFSkyView()
        v.set_observer(40.0, 116.0, 0.0)
        v.resize(600, 600)
        v._sky_brightness = 0.0
        # 新接缝：星表选择结果（eff_mag 已含 extinction）就是 star_draw 的入参。
        sel = v._star_selection(v._ground_station(), v._current_jd())
        assert sel is not None and len(sel) > 0
        # 取一颗较高仰角的星，避免地平几何门把它裁掉
        idx = next((i for i in range(len(sel)) if float(sel.alt_deg[i]) > 15.0), 0)
        expected = float(sel.eff_mag[idx])
        assert expected > float(sel.vmag[idx]) + 0.05, (
            f"星表有效星等未含消光: vmag={sel.vmag[idx]:.2f} "
            f"eff={expected:.2f}")

        from PySide6.QtGui import QPixmap, QPainter
        pm = QPixmap(600, 600)
        pm.fill()
        painter = QPainter(pm)
        try:
            v._draw_stars(painter)
        finally:
            painter.end()

        assert captured, "star_draw 未被调用（星不在屏/未绘制）"
        assert any(abs(c - expected) < 1e-6 for c in captured), \
            f"star_draw 未收到星表 eff_mag={expected}（captured 前 5: {captured[:5]}）"


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


def _horizon_view(brightness=1.0, atmosphere=True):
    """星象仪入口视角（alt=25 / fov=90）的离屏画布。

    关闭网格与地景：本组用例校验的是**天空渐变与雾带**，网格虚线/剪影会
    覆盖采样点（2026-11-17 实测：同一列隔行出现 196→216→137 的带状跳变，
    全部来自网格与地平线描边）。
    """
    from desktop.rf_sky_view import RFSkyView
    v = RFSkyView()
    v.resize(800, 600)
    v.set_observer(40.0, 116.0, 0.0)
    v.enter_horizon_view()
    v._sky_brightness = brightness
    v._show_atmosphere = atmosphere
    v._show_landscape = False
    v._show_grid = False
    return v


def _horizon_y(view):
    p = view._sky_to_screen_finite(view.view_state.center_az, 0.0)
    assert p is not None, "星象仪入口视角下地平线应在屏内"
    return p.y()


def _lum(c):
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def _median_rgb(img, y, x_lo=120, x_hi=680, step=4):
    """横向取样带的中位色（逐通道）。

    单点取样会把**随时间移动的真实天体**（恒星/行星/月亮）和每帧随机的闪烁
    算进"天空颜色"：2026-11-17 grp1 全量跑到 03:5x 时，西天地平正有一颗亮星
    落在 (400, hy-1)，三条用例同时红；半小时后单独重跑同一文件 22 passed。
    中位数对这类少数像素免疫，而渐变/雾带在横向上是均匀的，口径不变。
    """
    rr, gg, bb = [], [], []
    for x in range(x_lo, x_hi, step):
        c = img.pixelColor(x, int(y))
        rr.append(c.red())
        gg.append(c.green())
        bb.append(c.blue())
    rr.sort(), gg.sort(), bb.sort()
    m = len(rr) // 2
    return QColor(rr[m], gg[m], bb[m])


def _median_lum(img, y, **kw):
    return _lum(_median_rgb(img, y, **kw))


class TestSkyGradientAnchoredToHorizon:
    """渐变的地平色必须落在**真实地平线**上，而不是窗口底边。

    旧实现把 stops 铺满 0→height：星象仪入口真实地平在 ~73% 高度，底边已是
    alt≈-20°，可见地平拿到的是更暗一档的颜色（Stellarium 对照见
    docs/learn/2026-11-17-stellarium-planetarium-visuals.md §3.1）。
    """

    @pytest.mark.parametrize("brightness", [0.0, 1.0])
    def test_horizon_stop_lands_on_geometric_horizon(self, brightness):
        from desktop.rf_sky_view import _twilight_stops
        v = _horizon_view(brightness, atmosphere=False)
        img = v.grab().toImage()
        hy = _horizon_y(v)
        got = _median_rgb(img, hy - 1)
        want = _twilight_stops(brightness)[-1][1]
        diff = max(abs(got.red() - want.red()),
                   abs(got.green() - want.green()),
                   abs(got.blue() - want.blue()))
        assert diff <= 8, (
            f"地平色没锚到真实地平线：y={hy:.1f} 实得 {got.getRgb()[:3]} "
            f"期望 {want.getRgb()[:3]}（差 {diff}）")

    def test_sky_still_brighter_near_horizon(self):
        v = _horizon_view(0.0, atmosphere=False)
        img = v.grab().toImage()
        hy = _horizon_y(v)
        near = _median_lum(img, hy - 2)
        high = _median_lum(img, hy - 120)
        assert near > high, "仰角越低应越亮（暮光梯度方向）"


class TestHorizonHaze:
    """近地平雾带：按等仰角分带、加法混合、只在地平以上生效。"""

    def test_haze_adds_light_above_horizon_only(self):
        on = _horizon_view(1.0).grab().toImage()
        off = _horizon_view(1.0, atmosphere=False).grab().toImage()
        v = _horizon_view(1.0)
        hy = _horizon_y(v)
        above = _median_lum(on, hy - 2) - _median_lum(off, hy - 2)
        c_on = _median_rgb(on, hy + 60)
        c_off = _median_rgb(off, hy + 60)
        below = max(abs(c_on.red() - c_off.red()),
                    abs(c_on.green() - c_off.green()),
                    abs(c_on.blue() - c_off.blue()))
        assert above > 1.0, f"地平以上没有可见雾带增量（{above:.2f}）"
        assert below <= 6, f"雾带越过了地平线，把地面提亮了（Δ={below}）"

    def test_haze_follows_twilight_not_fixed_alpha(self):
        """暮光（太阳 -4°）应明显强于天文夜（-40°）：不是一条固定灰带。"""
        def delta(sun_alt):
            v = _horizon_view(0.0)
            v._sun = {"az": 150.0, "alt": sun_alt}
            on = v.grab().toImage()
            v2 = _horizon_view(0.0, atmosphere=False)
            v2._sun = {"az": 150.0, "alt": sun_alt}
            off = v2.grab().toImage()
            hy = _horizon_y(v)
            y = int(hy) - 2
            return _median_lum(on, y) - _median_lum(off, y)

        twilight = delta(-4.0)
        night = delta(-40.0)
        assert twilight > night + 3.0, (
            f"雾带没跟随太阳高度：暮光 Δ={twilight:.2f} / 夜 Δ={night:.2f}")


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
