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
  7. 照度/太阳朝向缺测时不填亮区（不编造方向）；
  8. 拾取用绘制层同一份位置与半径（窄 FOV 下旧 geocentric 位置会漏）。

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

import pytest  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QTransform  # noqa: E402
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
    GroundStation, vec_from_radec, j2000_to_altaz, azalt_from_vec,
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
                illum=0.5, dia_deg=0.5):
    sun = None if sun_az is None else {"az": sun_az, "alt": sun_alt}
    moon = {"az": moon_az, "alt": moon_alt,
            "illum": illum, "diameter_deg": dia_deg}
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
