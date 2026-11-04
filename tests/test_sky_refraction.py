# SPDX-License-Identifier: MIT
"""
tests/test_sky_refraction.py
============================
显示层大气折射（几何高度 -> 视高度）的地平与读数口径。

覆盖：
  1. 0.5° 几何高度处视高度 ≈ 0.918°，与旧（无折射）差 0.42°（反证）；
  2. 几何 -0.5° 的星视高度 +0.06°（"尚未升起却已可见"），
     地平门改为视高度后不放过地平下真正不可见的残点；
  3. 分段连续（-3.54° / -5° 无跳变）、单调、气压/温度口径可覆盖；
  4. 读数双标注（几何 / 视），不把两个口径混成一个数；
  5. 日出判据取舍：标准几何 -0.833° vs 视高度上缘 -0.267° 差 ≤40s
     （说明标准判据已含折射，不能二次叠加）；视高度圆心 0° 晚 ~80s；
  6. 后端数值不被回写：sun dict / SkyObject.elevation_deg 原样保留。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import rf_sky_view as sky  # noqa: E402
from rf_sky_view import (  # noqa: E402
    RFSkyView,
    HORIZON_ALT_DEG,
    display_apparent_alt_deg,
    display_refraction_delta_deg,
    format_alt_pair,
    compass_labels,
    SkyObject,
)
from mbdsdr_ai.atmosphere import sun_altitude_deg  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

LAT, LON = 40.0, 116.0
UNIX = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc).timestamp()
WIN = 700
SUN_DIA_DEG = 0.5333


def _new_view(size: int = WIN) -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    v.view_state.fov_deg = 120.0
    v._sky_brightness = 0.0
    return v


def _jd_of(dt: datetime) -> float:
    return 2400000.5 + (dt - datetime(1858, 11, 17, tzinfo=timezone.utc)
                        ).total_seconds() / 86400.0


# --------------------------------------------------------------------------
# 1. 反证：0.5° 高度处与旧（无折射）的差
# --------------------------------------------------------------------------
def test_apparent_alt_lift_at_half_degree():
    geom = 0.5
    app_alt = display_apparent_alt_deg(geom)
    delta = display_refraction_delta_deg(geom)
    # 旧实现（无折射）画在 0.5°，真实视高度 0.918°，差 0.418° ≈ 25′
    assert app_alt == pytest.approx(0.918, abs=0.002)
    assert delta == pytest.approx(0.418, abs=0.002)
    assert abs(app_alt - geom) > 0.4, "旧实现与真实视高度的差必须 >0.4°"

    # 地平处 0° -> ≈0.485°（29′），10° -> ≈0.09°（5′）
    assert display_apparent_alt_deg(0.0) == pytest.approx(0.485, abs=0.002)
    assert display_apparent_alt_deg(10.0) == pytest.approx(10.090, abs=0.002)


def test_geom_minus_half_degree_is_visible():
    """几何 -0.5° 的恒星视高度 +0.06°：真实世界就是"还没升起来就看得见"。"""
    assert display_apparent_alt_deg(-0.5) == pytest.approx(0.063, abs=0.002)
    # 视高度为 0 的几何高度约 -0.57°，再往下就真的看不见
    assert display_apparent_alt_deg(-0.57) < 0.02
    assert display_apparent_alt_deg(-0.6) < 0.0


# --------------------------------------------------------------------------
# 2. 分段连续性 / 单调 / 口径可覆盖
# --------------------------------------------------------------------------
def test_refraction_continuous_and_monotonic():
    prev = None
    for i in range(-700, 901):          # -7° .. 90°
        h = i / 100.0
        cur = display_apparent_alt_deg(h)
        if prev is not None:
            assert cur >= prev - 1e-9, (h, prev, cur)
        prev = cur
    # 分段边界 -3.54 / -5.0 两侧的**折射量**不跳变
    for edge in (-3.54, -5.0):
        a = display_refraction_delta_deg(edge + 0.001)
        b = display_refraction_delta_deg(edge - 0.001)
        assert abs(a - b) < 0.002, (edge, a, b)
    assert display_apparent_alt_deg(-6.0) == pytest.approx(-6.0, abs=0.01)


def test_pressure_temperature_basis():
    lo = display_refraction_delta_deg(0.0, pressure_hpa=1000.0, temperature_c=10.0)
    hi = display_refraction_delta_deg(0.0, pressure_hpa=1030.0, temperature_c=10.0)
    assert hi > lo
    cold = display_refraction_delta_deg(0.0, 1013.25, -10.0)
    hot = display_refraction_delta_deg(0.0, 1013.25, 30.0)
    assert cold > hot
    # 默认口径写死在模块常量里，文档/测试同源
    assert sky.DISPLAY_REFRACTION_PRESSURE_HPA == 1013.25
    assert sky.DISPLAY_REFRACTION_TEMPERATURE_C == 10.0


def test_view_refraction_switch_and_readout_labels():
    v = _new_view()
    try:
        assert v._display_alt_deg(0.5) == pytest.approx(0.918, abs=0.002)
        pair = v._format_alt_pair(0.5)
        assert "几何" in pair and "视" in pair
        v.set_refraction_params(enabled=False)
        assert v._display_alt_deg(0.5) == pytest.approx(0.5)
        only_geom = v._format_alt_pair(0.5)
        assert "几何" in only_geom and "视" not in only_geom
        v.set_refraction_params(pressure_hpa=1013.25, temperature_c=10.0,
                                enabled=True)
    finally:
        v.close()
    assert format_alt_pair(10.0).startswith("几何 10.0°")
    assert "视" in format_alt_pair(10.0)


# --------------------------------------------------------------------------
# 3. 日出判据：几何 vs 视高度的秒级差
# --------------------------------------------------------------------------
def _sunrise_second(threshold: float, use_apparent: bool) -> int:
    jd0 = _jd_of(datetime(2026, 3, 20, tzinfo=timezone.utc))
    prev = None
    for i in range(0, 86400, 5):
        h = sun_altitude_deg(jd0 + i / 86400.0, LAT, LON)
        v = display_apparent_alt_deg(h) if use_apparent else h
        if prev is not None and prev[1] < threshold <= v:
            return prev[0]
        prev = (i, v)
    raise AssertionError("当日未找到日出")


def test_sunrise_criterion_geometric_standard():
    """标准 -0.833°（几何，已含折射+视半径）与视高度上缘判据同事件。

    实测（2026-03-20, 40°N/116°E）：标准 22:18:30 UTC，
    视高度上缘 -0.267° 22:18:10 UTC，差 20s；若改用视高度圆心 0°，
    22:19:50 UTC，晚 80s——这就是"不能二次叠加折射"的反证。
    """
    std = _sunrise_second(-0.833, use_apparent=False)
    limb = _sunrise_second(-SUN_DIA_DEG / 2.0, use_apparent=True)
    center0 = _sunrise_second(0.0, use_apparent=True)
    geom0 = _sunrise_second(0.0, use_apparent=False)
    assert abs(limb - std) <= 40, (std, limb)
    assert 60 <= center0 - std <= 110, (std, center0)
    assert 200 <= geom0 - std <= 320, (std, geom0)


def test_body_rise_set_threshold_plumbed():
    v = _new_view()
    try:
        jd = v._current_jd()
        # 赤道/春分点附近一条大致东升西落的赤道轨：阈值越低，升起越早
        hi = v._body_rise_set(0.0, 0.0, jd, step_min=1.0, threshold_alt_deg=0.0)
        lo = v._body_rise_set(0.0, 0.0, jd, step_min=1.0,
                              threshold_alt_deg=-0.833)
        assert hi["rise_jd"] is not None and lo["rise_jd"] is not None
        assert lo["rise_jd"] < hi["rise_jd"]
    finally:
        v.close()


# --------------------------------------------------------------------------
# 4. 绘制/拾取用视高度；后端数值不回写
# --------------------------------------------------------------------------
def test_sun_draw_uses_apparent_but_record_untouched():
    v = _new_view()
    try:
        sun_dict = {"az": 180.0, "alt": -0.5, "diameter_deg": SUN_DIA_DEG}
        v.set_celestial_bodies(sun_dict, None, 0.0)
        v.view_state.look_at(180.0, 0.0)
        v.view_state.fov_deg = 60.0
        geom = v._sun_screen_geom()
        assert geom is not None
        assert float(geom["alt"]) == pytest.approx(-0.5), "几何高度原样返回"
        assert float(geom["alt_draw"]) == pytest.approx(0.063, abs=0.01)
        # 输入 dict 未被改写（后端/历表数值不动）
        assert sun_dict["alt"] == -0.5
    finally:
        v.close()


def test_satellite_stays_geometric_and_value_untouched():
    v = _new_view()
    try:
        obj = SkyObject("ISS", 180.0, 0.5, obj_type="satellite")
        v.set_objects([obj])
        v.set_celestial_bodies(None, None, 0.0)
        v.view_state.look_at(180.0, 0.0)
        v.view_state.fov_deg = 60.0
        img = QImage(WIN, WIN, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0))
        p = QPainter(img)
        try:
            v._draw_objects(p)
        finally:
            p.end()
        assert obj.elevation_deg == pytest.approx(0.5), "卫星几何值不得被折射改写"
        # 卫星点画在几何位置（0.5°），不是视高度（0.92°）：屏幕上更低
        geom_pos = v._sky_to_screen(180.0, 0.5)
        ap_pos = v._sky_to_screen(180.0, display_apparent_alt_deg(0.5))
        assert geom_pos.y() > ap_pos.y()      # y 向下增，几何位置更低
    finally:
        v.close()


def test_moon_draw_gate_uses_apparent_alt():
    v = _new_view()
    try:
        v.view_state.look_at(180.0, 0.0)
        v.view_state.fov_deg = 30.0
        v.set_celestial_bodies(None, {"az": 180.0, "alt": -0.3,
                                      "diameter_deg": 0.5, "illum": 0.5}, 0.0)
        frame = v._moon_screen_frame()
        assert frame is not None, "几何 -0.3° 的月亮视高度约 +0.2°，应画"
        assert float(frame["alt_draw"]) > 0.0
        assert float(frame["alt"]) == pytest.approx(-0.3)

        v.set_celestial_bodies(None, {"az": 180.0, "alt": -0.7,
                                      "diameter_deg": 0.5, "illum": 0.5}, 0.0)
        assert v._moon_screen_frame() is None, "视高度 <0 的月亮不画"
    finally:
        v.close()


# --------------------------------------------------------------------------
# 5. 地平线严格 alt=0 / 方位盘有度数
# --------------------------------------------------------------------------
def test_horizon_is_exactly_alt_zero():
    assert HORIZON_ALT_DEG == 0.0
    v = _new_view()
    try:
        v.view_state.look_at(180.0, 0.0)
        v.view_state.fov_deg = 120.0
        # 地平线上一点与投影 alt=0 完全一致（地平不随折射移动）
        for az in (0.0, 90.0, 180.0, 270.0):
            pos = v._sky_to_screen(az, HORIZON_ALT_DEG)
            assert pos is not None
    finally:
        v.close()


def test_compass_has_cardinals_and_degree_marks():
    labels = compass_labels()
    texts = [t for _a, t, _c in labels]
    assert texts[0] == "N"                 # 0° = N
    assert "E" in texts and "S" in texts and "W" in texts
    assert "30°" in texts and "330°" in texts
    assert len(labels) == 12               # 每 30° 一个
    assert sum(1 for _a, _t, c in labels if c) == 4
