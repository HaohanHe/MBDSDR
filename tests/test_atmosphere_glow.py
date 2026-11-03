# SPDX-License-Identifier: MIT
"""
tests/test_atmosphere_glow.py
=============================
SWE §4 大气加法辉光（「日落方位暖红」+「加法混合 (ONE,ONE) 画在星空后」）
的落地断言：

  sun_glow_strength(太阳高度) —— 梯形帽：地平 [-6°,+3°] 最强，晨昏之外为 0。
  _draw_atmosphere_glow       —— CompositionMode_Plus 把暖色 *加* 到星空上，
                                 辉光中心红通道抬升且抬得比蓝多（暖=偏红）；
                                 太阳高挂/深夜（strength=0）时一笔不画。

纯函数用精确值锚定；widget 层用已知暗底 + 加法后的像素增量断言（锚在
"红比蓝抬得多"这一暖光本质，不依赖具体色值阈值）。

运行：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_atmosphere_glow.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from desktop.rf_sky_view import RFSkyView, sun_glow_strength  # noqa: E402

_BASE = QColor(20, 30, 50)          # 已知暗底，便于读加法增量


def _new_view(size: int = 400) -> RFSkyView:
    v = RFSkyView()
    v.resize(size, size)
    v._observer = {"lat": 40.0, "lon": 116.0, "alt_m": 0.0}
    return v


def _render_glow(v: RFSkyView, sun, size: int = 400) -> QImage:
    v._sun = sun
    v._show_atmosphere = True
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(_BASE)
    p = QPainter(img)
    v._draw_atmosphere_glow(p)
    p.end()
    return img


# ----------------------------------------------------------------------
class TestSunGlowStrengthPure:
    def test_zero_outside_twilight(self):
        assert sun_glow_strength(-18.0) == pytest.approx(0.0)
        assert sun_glow_strength(-30.0) == pytest.approx(0.0)
        assert sun_glow_strength(15.0) == pytest.approx(0.0)
        assert sun_glow_strength(40.0) == pytest.approx(0.0)

    def test_peak_plateau_at_horizon(self):
        for alt in (-6.0, -3.0, 0.0, 2.0, 3.0):
            assert sun_glow_strength(alt) == pytest.approx(1.0)

    def test_linear_shoulders(self):
        assert sun_glow_strength(-12.0) == pytest.approx(0.5)
        assert sun_glow_strength(9.0) == pytest.approx(0.5)

    def test_rise_then_fall(self):
        rise = [sun_glow_strength(a) for a in (-18, -15, -12, -9, -6)]
        fall = [sun_glow_strength(a) for a in (3, 6, 9, 12, 15)]
        assert all(b >= a for a, b in zip(rise, rise[1:]))     # 单调升
        assert all(b <= a for a, b in zip(fall, fall[1:]))     # 单调降

    def test_range_unit(self):
        for alt in range(-40, 41, 3):
            assert 0.0 <= sun_glow_strength(float(alt)) <= 1.0


# ----------------------------------------------------------------------
class TestAtmosphereGlowAdditive:
    def test_horizon_sun_adds_warm(self):
        v = _new_view()
        v.view_state.look_at(90.0, 0.0)     # 视心对准太阳方位的地平
        img = _render_glow(v, {"az": 90.0, "alt": 0.0})
        c = img.pixelColor(200, 200)         # 辉光中心 = 屏幕中心
        # 加法暖光：红通道明显抬升
        assert c.red() > _BASE.red() + 20
        # 暖 = 偏红：红的增量 > 蓝的增量
        assert (c.red() - _BASE.red()) > (c.blue() - _BASE.blue())

    def test_below_horizon_still_glows(self):
        # 太阳略低于地平（civil twilight）：仍贴地平发暖辉
        v = _new_view()
        v.view_state.look_at(90.0, 0.0)
        img = _render_glow(v, {"az": 90.0, "alt": -4.0})
        c = img.pixelColor(200, 200)
        assert c.red() > _BASE.red() + 20

    def test_high_sun_no_glow(self):
        v = _new_view()
        v.view_state.look_at(90.0, 60.0)
        img = _render_glow(v, {"az": 90.0, "alt": 60.0})   # strength=0
        c = img.pixelColor(200, 200)
        assert abs(c.red() - _BASE.red()) <= 2
        assert abs(c.blue() - _BASE.blue()) <= 2

    def test_night_no_glow(self):
        v = _new_view()
        v.view_state.look_at(90.0, 0.0)
        img = _render_glow(v, {"az": 90.0, "alt": -30.0})  # strength=0
        c = img.pixelColor(200, 200)
        assert abs(c.red() - _BASE.red()) <= 2

    def test_no_sun_no_crash(self):
        v = _new_view()
        img = _render_glow(v, None)                         # 无太阳：跳过
        c = img.pixelColor(200, 200)
        assert c.red() == _BASE.red()

    def test_glow_falls_off_with_distance(self):
        # 远角落比辉光中心抬升小（径向衰减）
        v = _new_view()
        v.view_state.look_at(90.0, 0.0)
        img = _render_glow(v, {"az": 90.0, "alt": 0.0})
        center_dr = img.pixelColor(200, 200).red() - _BASE.red()
        corner_dr = img.pixelColor(5, 5).red() - _BASE.red()
        assert center_dr > corner_dr
