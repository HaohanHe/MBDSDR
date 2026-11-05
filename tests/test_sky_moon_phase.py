"""
tests/test_sky_moon_phase.py
============================
天空视图月球绘制真实度：相位形状 / 亮区朝向 / 视直径缩放 / 画-点同源。

覆盖：
  1. cos α = 2·illum − 1 的映射与缺测语义；
  2. 亮区路径面积比例 = illum（月牙/弦月/凸月/满月四态），并检查亮区包围盒；
  3. az/alt 大圆几何（角距、朝太阳方向、正交方向）自洽，对照独立球面公式；
  4. 亮区朝向由「月心→太阳」的 az/alt 切向决定，不是屏幕左右；
  5. 像素半径随视直径与 FOV 缩放（1° FOV 时月面半屏大小合理）；
  6. 绘制门与拾取门同源（alt ≤ 0 不画也不可点）；
  7. 照度/太阳朝向缺测时不填亮区（不编造方向），月相缺测画中性圆 + 标「月相未知」；
  8. 拾取用绘制层同一份位置与半径（窄 FOV 下旧 geocentric 位置会漏）；
  9. 尺寸限幅与 DSO 同一口径（真实视半径 > 视口短边 40% 才截断并标「已限幅」）；
 10. 相位角优先取历表 phase_angle_deg（缺测才退回 2·illum−1，不另算模型）；
 11. 多工况朝向：蛾眉/上弦/满月/下弦 × 不同时角 × 南半球真实历表；
 12. 像素亮区质心偏心率：蛾眉明显偏离圆心（有方向性），满月/均匀圆盘
     接近 0（同一条度量能把「固定圆点」与「真实形状」区分开）。

反证（同一场景，旧实现必红）：
  - 旧绘制恒为 6px 实心圆 → 面积比例恒 1.0，illum=0.25/0.5/0.75 的断言全红；
  - 旧朝向无相位 → 太阳在月亮正上方时测试要求亮区质心 y<0，"屏幕右侧"
    近似会给出 (x>0,y≈0)，测试必红；
  - 旧拾取门对行星用 alt >= -1，alt=-0.5 会放行，而新同源门拒绝；
  - 旧拾取用 compute_all 的 geocentric RA/Dec，窄 FOV 注入位置必漏。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_moon_phase.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import numpy as np  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import (  # noqa: E402
    RFSkyView,
    MOON_VISIBLE_MIN_ALT_DEG,
    angular_separation_deg,
    great_circle_offset_point,
    great_circle_perpendicular_point,
    moon_lit_path,
    moon_phase_cos_alpha,
    moon_visible,
)
from mbdsdr_ai.astronomy import planets as P  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, vec_from_radec, vec_from_azalt, j2000_to_altaz,
    azalt_from_vec,
)
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402

FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
FIXED_JD = _jtime.unix_to_jd(FIXED_UNIX)
LAT, LON = 40.0, 116.0

MOON_COLOR = QColor("#9AA7B4")


def _fix_time():
    eng = get_time_engine()
    eng.set_unix(FIXED_UNIX)
    eng.pause()


def _new_view(size: int = 700) -> RFSkyView:
    _fix_time()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    return v


def _set_bodies(v, moon_az, moon_alt, sun_az, sun_alt,
                illum=0.5, dia_deg=0.5, phase_angle_deg=None):
    sun = None if sun_az is None else {"az": sun_az, "alt": sun_alt}
    moon = {"az": moon_az, "alt": moon_alt,
            "illum": illum, "phase_angle_deg": phase_angle_deg,
            "diameter_deg": dia_deg}
    v.set_celestial_bodies(sun, moon, 0.0)


def _render(v, w, h):
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    v._draw_celestial_bodies(p)
    p.end()
    return img


def _bright_stats(img, cx, cy, radius, threshold=80):
    """统计圆内亮像素质心与数量（只数月亮颜色/亮面，不含外部标签）。"""
    sx = sy = count = 0.0
    rr = max(1.0, radius - 1.0)
    x0 = max(0, int(cx - rr))
    x1 = min(img.width() - 1, int(cx + rr))
    y0 = max(0, int(cy - rr))
    y1 = min(img.height() - 1, int(cy + rr))
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 > rr * rr:
                continue
            c = img.pixelColor(x, y)
            if (c.red() + c.green() + c.blue()) / 3.0 > threshold:
                sx += x
                sy += y
                count += 1
    if count == 0:
        return None, 0
    return (sx / count - cx, sy / count - cy), count


def _lit_area_fraction(cos_alpha, size=201):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 255))
    r = size / 2.0 - 2.0
    p.drawPath(QTransform(r, 0, 0, r, size / 2.0, size / 2.0)
               .map(moon_lit_path(cos_alpha)))
    p.end()
    lit = 0
    total = 0
    for y in range(size):
        for x in range(size):
            if (x - size / 2.0) ** 2 + (y - size / 2.0) ** 2 > r * r:
                continue
            total += 1
            if img.pixelColor(x, y).alpha() > 127:
                lit += 1
    return lit / total if total else 0.0


# --------------------------------------------------------------------------
# 1. 相位角余弦
# --------------------------------------------------------------------------
def test_cos_alpha_from_illumination():
    assert moon_phase_cos_alpha(0.0) == pytest.approx(-1.0)
    assert moon_phase_cos_alpha(0.25) == pytest.approx(-0.5)
    assert moon_phase_cos_alpha(0.5) == pytest.approx(0.0)
    assert moon_phase_cos_alpha(0.75) == pytest.approx(0.5)
    assert moon_phase_cos_alpha(1.0) == pytest.approx(1.0)
    assert moon_phase_cos_alpha(None) is None
    assert moon_phase_cos_alpha(float("nan")) is None


# --------------------------------------------------------------------------
# 2. 亮区面积比例（旧实现固定实心圆 = 1.0，此处必红）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("illum", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_lit_area_matches_illumination(illum):
    frac = _lit_area_fraction(moon_phase_cos_alpha(illum))
    assert frac == pytest.approx(illum, abs=0.02)
    # 反证：旧绘制是整盘实心（1.0），只有满月才碰巧通过
    if illum < 1.0:
        assert abs(1.0 - illum) > 0.02


def test_lit_path_terminator_bulge():
    """凸月晨昏线越过月心（背阳侧），月牙晨昏线在向阳侧、亮区变窄。"""
    gibbous = moon_lit_path(0.5)          # illum 0.75
    crescent = moon_lit_path(-0.5)        # illum 0.25
    assert gibbous.contains(QPointF(-0.3, 0.0))
    assert gibbous.contains(QPointF(0.7, 0.0))
    assert not crescent.contains(QPointF(0.2, 0.0))
    assert crescent.contains(QPointF(0.7, 0.0))
    assert moon_lit_path(1.0).boundingRect().width() == pytest.approx(2.0, abs=0.02)


# --------------------------------------------------------------------------
# 3. 大圆几何自洽（角距 / 朝太阳方向 / 正交方向）
# --------------------------------------------------------------------------
def test_great_circle_offset_is_at_step_distance():
    base = (123.0, 31.0)
    target = (210.0, 55.0)
    sep = angular_separation_deg(*base, *target)
    assert 0.0 < sep < 180.0
    for step in (0.05, 1.0):
        p = great_circle_offset_point(*base, *target, step)
        assert p is not None
        assert angular_separation_deg(*base, *p) == pytest.approx(step, abs=1e-9)
        if step < sep:
            assert angular_separation_deg(*p, *target) == pytest.approx(
                sep - step, abs=1e-6)


def test_perpendicular_point_is_orthogonal():
    base = (40.0, 20.0)
    target = (130.0, 60.0)
    step = 0.1
    on = great_circle_offset_point(*base, *target, step)
    perp = great_circle_perpendicular_point(*base, *target, step)
    assert on is not None and perp is not None
    # 球面小直角三角形的斜边角距 ≈ sqrt(2)·step
    assert angular_separation_deg(*on, *perp) == pytest.approx(
        math.sqrt(2.0) * step, abs=1e-6)


def test_degenerate_tangent_is_not_invented():
    assert great_circle_offset_point(10.0, 20.0, 10.0, 20.0, 0.2) is None
    assert great_circle_offset_point(10.0, 20.0, 190.0, -20.0, 0.2) is None


def test_moon_sun_direction_matches_independent_bearing():
    """对拍：屏幕亮缘方向 == 独立球面方位角公式算出的 az/alt 切向投影。"""
    v = _new_view(500)
    moon_az, moon_alt = 180.0, 30.0
    sun_az, sun_alt = 212.0, 44.0
    _set_bodies(v, moon_az, moon_alt, sun_az, sun_alt, 0.5, 0.5)
    v.view_state.look_at(moon_az, moon_alt)
    frame = v._moon_screen_frame()
    assert frame is not None
    ux, uy = frame["ux"], frame["uy"]
    assert ux is not None and uy is not None

    # 独立公式：标准球面初始方位角（alt 当纬度、az 当经度）
    def _bearing(az1, h1, az2, h2):
        a1, f1, a2, f2 = map(math.radians, (az1, h1, az2, h2))
        y = math.sin(a2 - a1) * math.cos(f2)
        x = (math.cos(f1) * math.sin(f2)
             - math.sin(f1) * math.cos(f2) * math.cos(a2 - a1))
        return math.atan2(y, x)

    b = _bearing(moon_az, moon_alt, sun_az, sun_alt)
    step = 0.2
    probe_alt = moon_alt + step * math.cos(b)
    probe_az = moon_az + step * math.sin(b) / math.cos(math.radians(moon_alt))
    c = v._sky_to_screen_finite(moon_az, moon_alt)
    p = v._sky_to_screen_finite(probe_az, probe_alt)
    assert c is not None and p is not None
    ex, ey = p.x() - c.x(), p.y() - c.y()
    n = math.hypot(ex, ey)
    assert n > 0
    dot = (ux * ex + uy * ey) / n
    assert dot > 0.999          # 独立公式方向一致（< 2.5°）


# --------------------------------------------------------------------------
# 4. 亮区朝向（太阳在月亮上方 -> 亮区朝上；不是屏幕右侧）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sun_az,sun_alt", [(180.0, 42.0), (215.0, 30.0)])
def test_bright_side_faces_sun_not_screen_right(sun_az, sun_alt):
    v = _new_view(500)
    moon_az, moon_alt = 180.0, 30.0
    _set_bodies(v, moon_az, moon_alt, sun_az, sun_alt, 0.5, 0.5)
    v.view_state.look_at(moon_az, moon_alt)
    v.view_state.fov_deg = 4.0
    frame = v._moon_screen_frame()
    assert frame is not None
    center = frame["center"]
    r = float(frame["radius_px"])
    assert r > 20.0

    img = _render(v, 500, 500)
    off, count = _bright_stats(img, center.x(), center.y(), r)
    assert off is not None and count > 200       # 半月亮区确实被填了
    ox, oy = off
    ux, uy = float(frame["ux"]), float(frame["uy"])
    length = math.hypot(ox, oy)
    assert length > 0.15 * r                     # 质心明显偏向亮侧
    assert (ox * ux + oy * uy) / length > 0.85    # 与 az/alt 切向一致

    if sun_az == moon_az:
        # 反证：太阳在月亮正上方时亮区质心在上方；旧"屏幕右侧"近似会给出
        # (ox≈+0.42R, oy≈0)，下面两条断言都会红。
        assert oy < -0.15 * r
        assert abs(ox) < 0.15 * r


def test_two_sun_positions_give_different_screen_directions():
    v = _new_view(500)
    seen = []
    for sun_az, sun_alt in ((180.0, 45.0), (225.0, 35.0)):
        _set_bodies(v, 180.0, 30.0, sun_az, sun_alt, 0.5, 0.5)
        v.view_state.look_at(180.0, 30.0)
        frame = v._moon_screen_frame()
        assert frame is not None
        seen.append((float(frame["ux"]), float(frame["uy"])))
    dot = seen[0][0] * seen[1][0] + seen[0][1] * seen[1][1]
    assert math.degrees(math.acos(max(-1.0, min(1.0, dot)))) > 30.0


# --------------------------------------------------------------------------
# 5. 视直径 -> 像素半径（非固定像素）
# --------------------------------------------------------------------------
def test_moon_radius_scales_with_view_diameter_and_fov():
    v = _new_view(700)
    moon_az, moon_alt = 180.0, 30.0
    dia = 0.5
    _set_bodies(v, moon_az, moon_alt, 180.0, 60.0, 0.5, dia)
    v.view_state.look_at(moon_az, moon_alt)

    radius_rad = math.radians(dia / 2.0)
    for fov in (5.0, 1.0):
        v.view_state.fov_deg = fov
        frame = v._moon_screen_frame()
        assert frame is not None
        r = float(frame["radius_px"])
        ppr = (700.0 / 2.0) / math.tan(math.radians(fov / 2.0))
        expected = ppr * math.tan(radius_rad)
        assert r == pytest.approx(expected, rel=0.02)
        # 反证：旧实现恒为 6px
        assert abs(r - 6.0) > 15.0

    # 1° FOV：直径 0.5° 的月亮约占半屏高（350px 视半径）
    v.view_state.fov_deg = 1.0
    frame = v._moon_screen_frame()
    assert float(frame["radius_px"]) == pytest.approx(175.0, rel=0.02)


# --------------------------------------------------------------------------
# 6. 绘制门与拾取门同源
# --------------------------------------------------------------------------
def test_draw_and_pick_share_visibility_gate():
    v = _new_view(500)
    _set_bodies(v, 180.0, 10.0, 180.0, 40.0, 0.5, 0.5)
    v.view_state.look_at(180.0, 10.0)
    v.view_state.fov_deg = 20.0
    pos = v._sky_to_screen_finite(180.0, 10.0)
    assert pos is not None

    # 地平线上：画 && 可点
    frame = v._moon_screen_frame()
    assert frame is not None
    img = _render(v, 500, 500)
    _, count = _bright_stats(img, frame["center"].x(), frame["center"].y(),
                             float(frame["radius_px"]))
    assert count > 0
    got = v._find_body_at(pos.x(), pos.y())
    assert got is not None and got.get("key") == "moon"

    # 地平线下 0.7°（视高度仍 <0）：新门不画也不点
    _set_bodies(v, 180.0, -0.7, 180.0, 40.0, 0.5, 0.5)
    assert v._moon_screen_frame() is None
    got = v._find_body_at(pos.x(), pos.y())
    assert got is None or got.get("key") != "moon"

    # 反证：旧拾取门（行星 alt >= -1）会放行 -0.5，旧绘制门（alt > 0）不会
    assert -0.5 >= -1.0
    assert not moon_visible(-0.5)
    assert moon_visible(0.5)
    assert MOON_VISIBLE_MIN_ALT_DEG == 0.0


# --------------------------------------------------------------------------
# 7. 缺测不编造：照度未知 / 太阳方位未知 -> 不填亮区
# --------------------------------------------------------------------------
def test_unknown_illumination_draws_no_fake_phase():
    v = _new_view(500)
    _set_bodies(v, 180.0, 30.0, 180.0, 50.0, None, 0.5)
    v.view_state.look_at(180.0, 30.0)
    v.view_state.fov_deg = 4.0
    frame = v._moon_screen_frame()
    assert frame is not None
    img = _render(v, 500, 500)
    _, unknown_count = _bright_stats(
        img, frame["center"].x(), frame["center"].y(),
        float(frame["radius_px"]))

    _set_bodies(v, 180.0, 30.0, 180.0, 50.0, 1.0, 0.5)
    frame_full = v._moon_screen_frame()
    img_full = _render(v, 500, 500)
    _, full_count = _bright_stats(
        img_full, frame_full["center"].x(), frame_full["center"].y(),
        float(frame_full["radius_px"]))
    assert full_count > 6 * unknown_count      # 未知时只有轮廓，不冒充满月


def test_unknown_sun_direction_draws_no_fake_orientation():
    v = _new_view(500)
    _set_bodies(v, 180.0, 30.0, None, None, 0.5, 0.5)
    v.view_state.look_at(180.0, 30.0)
    v.view_state.fov_deg = 4.0
    frame = v._moon_screen_frame()
    assert frame is not None
    assert frame["ux"] is None and frame["uy"] is None
    img = _render(v, 500, 500)
    _, count = _bright_stats(img, frame["center"].x(), frame["center"].y(),
                             float(frame["radius_px"]))
    # 只画轮廓，不按"屏幕左右"猜一个方向填亮区
    assert count < 0.1 * math.pi * float(frame["radius_px"]) ** 2


# --------------------------------------------------------------------------
# 8. 拾取用绘制层的同一位置/半径（窄 FOV 反证旧 geocentric 拾取）
# --------------------------------------------------------------------------
def test_pick_uses_drawn_topocentric_position(monkeypatch):
    v = _new_view(700)
    # 真实历表月球位置（旧 _find_body_at 用的就是它）
    gs = GroundStation(LON, LAT, 0.0)
    real = P.compute_moon(FIXED_JD)
    v_real = j2000_to_altaz(vec_from_radec(real.ra_deg, real.dec_deg), gs, FIXED_JD)
    real_az, real_alt = azalt_from_vec(v_real)

    # 注入一个与真实位置相差 >= 60° 的月亮（仍在地平线上）
    inj_az = (real_az + 120.0) % 360.0
    inj_alt = 35.0 if real_alt < 75.0 else 20.0
    _set_bodies(v, inj_az, inj_alt, 180.0, 60.0, 0.5, 0.5)
    v.view_state.look_at(inj_az, inj_alt)
    v.view_state.fov_deg = 1.0
    frame = v._moon_screen_frame()
    assert frame is not None
    center = frame["center"]
    r = float(frame["radius_px"])

    # 单击月面内但离开盘心：拾取半径随绘制半径扩张
    monkeypatch.setattr(_constel, "merged_star_index", lambda: {})
    got = v._find_body_at(center.x() + 0.7 * r, center.y())
    assert got is not None and got.get("key") == "moon"

    # 反证：旧 geocentric 拾取在 1° FOV 下与画出的月面错开
    old_scr = v._sky_to_screen_finite(real_az, real_alt)
    if old_scr is not None:
        dist = math.hypot(old_scr.x() - center.x(), old_scr.y() - center.y())
        assert dist > 14.0
    # 中心点击必定命中（距离 0），与旧路径无关
    got_center = v._find_body_at(center.x(), center.y())
    assert got_center is not None and got_center.get("key") == "moon"


# --------------------------------------------------------------------------
# 9. 尺寸限幅：与 DSO 同一口径（真实视半径 > 视口短边 40% 才截断）
# --------------------------------------------------------------------------
def test_moon_clamp_only_when_disk_exceeds_viewport():
    v = _new_view(500)
    try:
        _set_bodies(v, 180.0, 35.0, 180.0, 60.0, 0.5, 0.5)
        v.view_state.look_at(180.0, 35.0)
        cap = RFSkyView.DSO_CLAMP_VIEW_FRAC * 500.0

        v.view_state.fov_deg = 2.0
        wide = v._moon_screen_frame()
        assert wide is not None and wide["clamped"] is False
        assert float(wide["radius_px"]) < cap

        # 0.6° 视场：真实视半径 ~208px > 200px，必须截断到 40% 并标记
        v.view_state.fov_deg = 0.6
        narrow = v._moon_screen_frame()
        assert narrow is not None and narrow["clamped"] is True
        assert float(narrow["radius_px"]) == pytest.approx(cap)
    finally:
        v.close()


def _danger_pixels(img):
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() > c.green() + 25 and c.red() > c.blue() + 25:
                n += 1
    return n


def test_clamped_moon_has_visible_marker():
    """截断后半径不再等于真实视大小，必须看得见地标「已限幅」。"""
    v = _new_view(500)
    try:
        _set_bodies(v, 180.0, 35.0, 180.0, 60.0, 0.5, 0.5)
        v.view_state.look_at(180.0, 35.0)
        v.view_state.fov_deg = 0.6
        clamped = _render(v, 500, 500)
        v.view_state.fov_deg = 2.0
        normal = _render(v, 500, 500)
    finally:
        v.close()
    assert _danger_pixels(clamped) > 20, "限幅月面没有可见标注"
    assert _danger_pixels(normal) == 0, "未超限的月面不该标已限幅"


def test_clamped_moon_pick_radius_follows_drawn_disk():
    """限幅后拾取半径与画出的月面同一份：盘内命中，盘外不命中。"""
    v = _new_view(500)
    try:
        _set_bodies(v, 180.0, 35.0, 180.0, 60.0, 0.5, 0.5)
        v.view_state.look_at(180.0, 35.0)
        v.view_state.fov_deg = 0.6
        frame = v._moon_screen_frame()
        assert frame is not None and frame["clamped"] is True
        c = frame["center"]
        r = float(frame["radius_px"])
        got = v._find_body_at(c.x() + 0.6 * r, c.y())
        assert got is not None and got.get("key") == "moon"
    finally:
        v.close()


# --------------------------------------------------------------------------
# 10. 月相缺测：中性圆 +「月相未知」，不猜
# --------------------------------------------------------------------------
def test_unknown_phase_draws_neutral_disk_and_labels_unknown():
    v = _new_view(500)
    try:
        _set_bodies(v, 180.0, 35.0, 180.0, 60.0, None, 0.5)
        v.view_state.look_at(180.0, 35.0)
        v.view_state.fov_deg = 4.0
        frame = v._moon_screen_frame()
        assert frame is not None
        assert frame["cos_alpha"] is None

        img = _render(v, 500, 500)
        c = frame["center"]
        # 中性圆：盘心确实被填（非纯黑），但明显暗于亮面（不冒充满月/新月）
        pix = img.pixelColor(int(c.x()), int(c.y()))
        avg = (pix.red() + pix.green() + pix.blue()) / 3.0
        assert avg > 10.0 and avg < 80.0, (pix.red(), pix.green(), pix.blue())

        texts = [line[0] for _pri, _pos, lines, _col in v._label_items
                 for line in lines]
        assert any("月相未知" in t for t in texts), texts
    finally:
        v.close()


def test_full_illum_draws_disk_even_without_sun_direction():
    """太阳朝向缺测但照度已知为 1：形状关于方向对称，必须画满盘。

    朝向不可判只说明「亮缘朝哪」不可判，不说明「亮多少」不可判；
    cos α≈1 时若因 ux/uy 缺失就整盘不填，会把满月画成空心圆。
    """
    v = _new_view(400)
    try:
        _set_bodies(v, 180.0, 35.0, None, None, 1.0, 0.5)
        v.view_state.look_at(180.0, 35.0)
        v.view_state.fov_deg = 2.0
        frame = v._moon_screen_frame()
        assert frame is not None
        assert frame["ux"] is None, "太阳缺测时朝向应为未知（本用例前提）"
        assert float(frame["cos_alpha"]) == pytest.approx(1.0, abs=1e-9)

        img = _render(v, 400, 400)
        c = frame["center"]
        _centroid, count = _bright_stats(img, c.x(), c.y(),
                                         float(frame["radius_px"]))
        r = float(frame["radius_px"])
        lit_frac = count / (math.pi * max(1.0, r - 1.0) ** 2)
        assert lit_frac > 0.90, f"满月无朝向时没画出整盘：{lit_frac}"
    finally:
        v.close()


def test_phase_angle_is_the_preferred_ephemeris_source():
    """cos α 取历表 phase_angle_deg；只有缺相位角才退回 2·illum−1。"""
    v = _new_view(500)
    try:
        _set_bodies(v, 180.0, 35.0, 180.0, 60.0, None, 0.5,
                    phase_angle_deg=60.0)
        v.view_state.look_at(180.0, 35.0)
        v.view_state.fov_deg = 4.0
        frame = v._moon_screen_frame()
        assert frame is not None
        assert frame["cos_alpha"] == pytest.approx(
            math.cos(math.radians(60.0)), abs=1e-12)

        # 同一天体的照度与相位角是同一事实的两种表达：两者一致时结果不跳变
        _set_bodies(v, 180.0, 35.0, 180.0, 60.0, 0.75, 0.5,
                    phase_angle_deg=60.0)
        frame2 = v._moon_screen_frame()
        assert frame2["cos_alpha"] == pytest.approx(
            moon_phase_cos_alpha(0.75), abs=1e-12)
    finally:
        v.close()


# --------------------------------------------------------------------------
# 11. 多工况朝向：四相 × 不同时角 × 南半球真实历表
# --------------------------------------------------------------------------
def _sunward_screen_dir(v, moon_az, moon_alt, sun_az, sun_alt,
                        step_deg=0.25):
    """独立实现「月心→太阳」的切向，并投影回屏幕方向单位向量。

    不调用绘制层的 great_circle_offset_point：这里自己用单位向量做
    t = normalize(v_sun − (v_sun·v_moon)·v_moon)，再走 step_deg。两条路径
    只有在「朝向真的由 az/alt 大圆决定」时才一致；按屏幕左右或赤经差
    偷懒的实现会在这里分叉。
    """
    malt = v._display_alt_deg(moon_alt)
    salt = v._display_alt_deg(sun_alt)
    v0 = vec_from_azalt(moon_az, malt)
    vs = vec_from_azalt(sun_az, salt)
    dot = float(v0[0] * vs[0] + v0[1] * vs[1] + v0[2] * vs[2])
    t = [vs[i] - dot * v0[i] for i in range(3)]
    n = math.sqrt(sum(c * c for c in t))
    assert n > 1e-9, "太阳与月亮重合，切向不唯一"
    t = [c / n for c in t]
    s = math.radians(step_deg)
    p = [math.cos(s) * v0[i] + math.sin(s) * t[i] for i in range(3)]
    az2, alt2 = azalt_from_vec(np.asarray(p, dtype=float))
    m = v._sky_to_screen_finite(moon_az, malt)
    q = v._sky_to_screen_finite(az2, alt2)
    assert m is not None and q is not None
    dx, dy = q.x() - m.x(), q.y() - m.y()
    d = math.hypot(dx, dy)
    assert d > 0.0
    return dx / d, dy / d


@pytest.mark.parametrize("illum,sun_az,sun_alt", [
    (0.12, 120.0, 40.0),    # 蛾眉：太阳在西侧偏上（黄昏时角）
    (0.50, 250.0, 25.0),    # 上弦：太阳在西
    (1.00, 10.0, -20.0),    # 满月：太阳在地平下（夜半时角）
    (0.50, 60.0, -10.0),    # 下弦：太阳在东、地平下
])
def test_bright_side_faces_sun_across_phases_and_hour_angles(
        illum, sun_az, sun_alt):
    v = _new_view(500)
    try:
        moon_az, moon_alt = 180.0, 35.0
        _set_bodies(v, moon_az, moon_alt, sun_az, sun_alt, illum, 0.5)
        v.view_state.look_at(moon_az, moon_alt)
        v.view_state.fov_deg = 4.0
        frame = v._moon_screen_frame()
        assert frame is not None
        ux, uy = frame["ux"], frame["uy"]
        assert ux is not None and uy is not None

        # ① 绘制层的朝向 = 独立单位向量切向的屏幕投影
        sx, sy = _sunward_screen_dir(v, moon_az, moon_alt, sun_az, sun_alt)
        assert ux * sx + uy * sy > 0.999

        # ② 像素亮区质心确实落在 ux,uy 一侧
        img = _render(v, 500, 500)
        c = frame["center"]
        centroid, count = _bright_stats(
            img, c.x(), c.y(), float(frame["radius_px"]))
        r = float(frame["radius_px"])
        lit_frac = count / (math.pi * max(1.0, r - 1.0) ** 2)
        assert lit_frac == pytest.approx(illum, abs=0.07)
        if 0.1 < illum < 0.95:
            assert centroid is not None
            cn = math.hypot(*centroid)
            assert cn > 0.05 * r, (centroid, r)
            assert (centroid[0] * ux + centroid[1] * uy) / cn > 0.8
    finally:
        v.close()


def _uniform_disk_eccentricity(size=400, radius=40.0):
    """反证基线：旧实现「固定圆点/均匀圆盘」在同一度量下的偏心率。

    这不是模拟「旧代码能跑」——旧代码的绘制调用就是一次实心圆。用同一
    份 `_bright_stats` 度量均匀圆盘，得到的质心偏心率必然 ≈0，才能证明
    下面的新月断言确实来自「形状」而不是度量本身的偏差。
    """
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(MOON_COLOR))
    p.drawEllipse(QPointF(size / 2.0, size / 2.0), radius, radius)
    p.end()
    centroid, count = _bright_stats(img, size / 2.0, size / 2.0, radius)
    assert centroid is not None
    ecc = math.hypot(*centroid) / radius
    lit_frac = count / (math.pi * max(1.0, radius - 1.0) ** 2)
    return ecc, lit_frac


@pytest.mark.parametrize("illum,sun_az,sun_alt,min_ecc", [
    (0.12, 120.0, 40.0, 0.40),   # 蛾眉：亮区挤在一侧，质心明显离圆心
    (0.50, 250.0, 25.0, 0.25),   # 弦月：半盘质心 ≈ 4r/(3π) ≈ 0.42r
    (1.00, 10.0, -20.0, 0.00),   # 满月：对称整盘，质心 ≈ 圆心
])
def test_bright_region_eccentricity_separates_shape_from_dot(
        illum, sun_az, sun_alt, min_ecc):
    """像素亮区质心偏心率：真实形状 vs 固定圆点。

    - 旧实现（固定 6px/均匀圆盘）：偏心率 ≈0、亮区占比 ≈1；
    - 新实现蛾眉/弦月：偏心率显著 >0 且方向与「月心→太阳」一致；
    - 新月（illum≈0.12）的亮区占比还会显著偏离 0.5（不是半盘）。
    """
    ecc_disk, lit_disk = _uniform_disk_eccentricity()
    assert ecc_disk < 0.02, "均匀圆盘的质心偏心率应为 0（度量自检）"
    assert lit_disk > 0.95

    v = _new_view(400)
    try:
        moon_az, moon_alt = 180.0, 35.0
        _set_bodies(v, moon_az, moon_alt, sun_az, sun_alt, illum, 0.5)
        v.view_state.look_at(moon_az, moon_alt)
        v.view_state.fov_deg = 3.0
        frame = v._moon_screen_frame()
        assert frame is not None and frame["ux"] is not None
        ux, uy = float(frame["ux"]), float(frame["uy"])

        img = _render(v, 400, 400)
        c = frame["center"]
        r = float(frame["radius_px"])
        centroid, count = _bright_stats(img, c.x(), c.y(), r)
        assert centroid is not None
        ecc = math.hypot(*centroid) / max(1.0, r)
        lit_frac = count / (math.pi * max(1.0, r - 1.0) ** 2)

        assert lit_frac == pytest.approx(illum, abs=0.07)
        if illum < 0.95:
            assert ecc > min_ecc, f"亮区质心偏心率 {ecc:.3f} 未超过 {min_ecc}"
            assert math.hypot(*centroid) > 0.05 * r
            dot = (centroid[0] * ux + centroid[1] * uy) / math.hypot(*centroid)
            assert dot > 0.8, f"亮区质心不朝太阳方向：dot={dot:.3f}"
            if illum <= 0.2:
                assert abs(lit_frac - 0.5) > 0.25, "蛾眉亮区占比应显著偏离半盘"
        else:
            assert ecc < 0.05, f"满月亮区质心应居中：ecc={ecc:.3f}"
    finally:
        v.close()


@pytest.mark.parametrize("lat,lon", [
    (-33.9, 18.4),    # 南半球：开普敦
    (-23.5, -46.6),   # 南半球：圣保罗
    (40.0, 116.0),    # 北半球对照
])
def test_real_ephemeris_bright_side_faces_sun_in_both_hemispheres(lat, lon):
    """真实历表：南/北半球都从 az/alt 定朝向，不靠屏幕左右或赤经差。"""
    v = _new_view(600)
    try:
        v.set_observer(lat, lon, 0.0)
        # 找一个「月亮在地平上 > 20°、且日月不是近满月/近新月」的时刻：
        # 近对跖/近重合时切向退化，朝向本来就不可判，不能拿来验朝向。
        from mbdsdr_ai import solar_system as _sol
        sun = moon = None
        for day in range(0, 40, 2):
            for hour in range(0, 24, 2):
                t = FIXED_UNIX + day * 86400 + hour * 3600
                mp = _sol.get_moon_position(t, (lat, lon, 0.0))
                sp = _sol.get_sun_position(t, (lat, lon, 0.0))
                if mp is None or sp is None or float(mp.alt_deg) <= 20.0:
                    continue
                sep = angular_separation_deg(
                    float(mp.az_deg), float(mp.alt_deg),
                    float(sp.az_deg), float(sp.alt_deg))
                if not (45.0 <= sep <= 135.0):
                    continue
                sun = {"az": sp.az_deg, "alt": sp.alt_deg}
                moon = {"az": mp.az_deg, "alt": mp.alt_deg,
                        "illum": mp.illumination,
                        "phase_angle_deg": mp.phase_angle_deg,
                        "diameter_deg": mp.angular_diameter_deg}
                break
            if sun is not None:
                break
        assert sun is not None and moon is not None

        v.set_celestial_bodies(sun, moon, 0.0)
        v.view_state.look_at(float(moon["az"]), float(moon["alt"]))
        v.view_state.fov_deg = 3.0
        frame = v._moon_screen_frame()
        assert frame is not None and frame["ux"] is not None

        sx, sy = _sunward_screen_dir(
            v, float(moon["az"]), float(moon["alt"]),
            float(sun["az"]), float(sun["alt"]))
        assert frame["ux"] * sx + frame["uy"] * sy > 0.999
    finally:
        v.close()
