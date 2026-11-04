"""
tests/test_sky_planet_disk.py
=============================
天空视图行星绘制真实度：视直径缩放 / 相位亮区 / 星等显示一致性。

覆盖：
  1. 相位角 -> 照度 = 0.5·(1+cos α)，与信息卡同一算式；
  2. 视直径 -> 投影像素半径随 FOV 真实缩放（5° vs 1°，比例 5）；
  3. 5° 中档缩放下行星仍不可分辨（r < 2px）走点源；min_fov=0.5° 必须可分辨；
  4. 滚轮等比缩放走到 0.5° 平滑不跳，光标锚定在极限附近不漂；
  5. 可分辨时按真实圆面 + 历表相位画亮区（金星 130° 相位 ≈ 17.7% 月牙）；
  6. 太阳方位缺测时不填亮区（不按"屏幕左右"编造朝向）；
  7. 信息卡星等 / 视直径 / 相位角与绘制层同源（compute_all）。

反证（同一场景，旧实现必红）：
  - 旧实现行星恒为 star_draw 小圆点：1° FOV 下半径 ~5px 且恒为满亮，
    "物理圆面半径 ≥ 20px"与"亮区比例 ≈ 17.7%"两条断言都会红；
  - 旧实现没有相位朝向，太阳在正上方时亮区质心 y<0 必红。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_planet_disk.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import dataclasses  # noqa: E402
import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import (  # noqa: E402
    RFSkyView,
    planet_phase_illumination,
    angular_separation_deg,
)
from mbdsdr_ai.astronomy import planets as P  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, vec_from_radec, j2000_to_altaz, azalt_from_vec,
    altaz_to_j2000, vec_from_azalt, radec_from_vec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.sky_interaction import screen_to_sky  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402

# 金星在 2026-09-28 02:00 UTC 已升起（alt≈23°），相位角≈130°
PLANET_UNIX = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc).timestamp()
PLANET_JD = _jtime.unix_to_jd(PLANET_UNIX)
LAT, LON = 40.0, 116.0


def _fix_time():
    eng = get_time_engine()
    eng.set_unix(PLANET_UNIX)
    eng.pause()


def _new_view(size: int = 500) -> RFSkyView:
    _fix_time()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    return v


def _planet_altaz(pos):
    gs = GroundStation(LON, LAT, 0.0)
    v = j2000_to_altaz(vec_from_radec(pos.ra_deg, pos.dec_deg), gs, PLANET_JD)
    return azalt_from_vec(v)


def _render_planets(v, w, h):
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    v._draw_planets(p)
    p.end()
    return img


def _bright_stats(img, cx, cy, radius, threshold=120):
    sx = sy = count = 0.0
    rr = max(1.0, radius - 1.0)
    for y in range(max(0, int(cy - rr)), min(img.height() - 1, int(cy + rr)) + 1):
        for x in range(max(0, int(cx - rr)), min(img.width() - 1, int(cx + rr)) + 1):
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


# --------------------------------------------------------------------------
# 1. 相位角 -> 照度
# --------------------------------------------------------------------------
def test_phase_angle_to_illumination():
    assert planet_phase_illumination(0.0) == pytest.approx(1.0)
    assert planet_phase_illumination(90.0) == pytest.approx(0.5)
    assert planet_phase_illumination(180.0) == pytest.approx(0.0, abs=1e-12)
    assert planet_phase_illumination(None) is None
    assert planet_phase_illumination(float("nan")) is None
    # 与信息卡同一算式：130.3° -> 约 17.7%
    assert planet_phase_illumination(130.3) == pytest.approx(0.177, abs=0.003)


# --------------------------------------------------------------------------
# 2. 视直径 -> 像素半径随 FOV 缩放
# --------------------------------------------------------------------------
def test_planet_disc_radius_scales_with_fov():
    v = _new_view(500)
    pos = P.compute_planet("venus", PLANET_JD)
    az, alt = _planet_altaz(pos)
    v.view_state.look_at(az, alt)
    radius_deg = pos.angular_diameter_arcsec / 7200.0
    rs = {}
    for fov in (5.0, 1.0):
        v.view_state.fov_deg = fov
        disc = v._planet_screen_disc(az, alt, radius_deg * 2.0)
        assert disc is not None
        rs[fov] = float(disc["r"])
        ppr = (500.0 / 2.0) / math.tan(math.radians(fov / 2.0))
        assert rs[fov] == pytest.approx(ppr * math.tan(math.radians(radius_deg)),
                                        rel=0.02)
    assert rs[1.0] / rs[5.0] == pytest.approx(5.0, rel=0.02)


# --------------------------------------------------------------------------
# 3. 缩放分辨率下限：中档 5° 是点源；允许的最窄 min_fov=0.5° 必须可分辨
# --------------------------------------------------------------------------
def test_planets_are_unresolved_at_mid_zoom():
    """5° FOV（中档）下被画出的行星仍是点源（r < 2px）——分辨率下限。

    逐颗居中：离屏天体在透视投影靠近奇异面处局部尺度会爆炸，那不是
    "画出来的圆面"（_draw_planets 已先过 _on_screen 门），不应混进这条断言。
    """
    v = _new_view(700)
    positions = P.compute_all(PLANET_JD)
    v.view_state.fov_deg = 5.0
    checked = 0
    for key, pos in positions.items():
        if key in ("moon", "earth"):
            continue
        az, alt = _planet_altaz(pos)
        if alt < 0.0:
            continue                    # 地平线下不画（与绘制层同一门）
        v.view_state.look_at(az, alt)
        disc = v._planet_screen_disc(az, alt,
                                     pos.angular_diameter_arcsec / 3600.0)
        assert disc is not None
        assert float(disc["r"]) < 2.0, (key, float(disc["r"]))
        checked += 1
    assert checked >= 2                 # 至少两颗在地平线上，断言非空壳


def _ink_at(img, cx, cy, radius) -> int:
    n = 0
    for y in range(max(0, int(cy - radius)), min(img.height(), int(cy + radius)) + 1):
        for x in range(max(0, int(cx - radius)), min(img.width(), int(cx + radius)) + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 > radius * radius:
                continue
            c = img.pixelColor(x, y)
            if (c.red() + c.green() + c.blue()) / 3.0 > 90:
                n += 1
    return n


def test_planet_below_horizon_not_drawn_nor_picked():
    """地平线下（alt<0）行星既不画也不给点：与恒星/月亮同一条地平门。

    反证：绘制/拾取门旧值 alt<-1.0 时，alt=-0.5° 的金星会落笔（ink>0）
    且可点中——地平线以下冒出一颗"看不见的行星"，与恒星/月亮口径打架。
    """
    v = _new_view(500)
    gs = GroundStation(LON, LAT, 0.0)
    jd = v._current_jd()
    base = P.compute_planet("venus", PLANET_JD)
    out = {}
    try:
        for tag, alt in (("below", -0.7), ("above", 1.0)):
            ra, dec = radec_from_vec(
                altaz_to_j2000(vec_from_azalt(90.0, alt), gs, jd))
            pos = dataclasses.replace(base, ra_deg=ra, dec_deg=dec)
            v._planet_positions = lambda _jd, _p=pos: {"venus": _p}
            v.view_state.look_at(90.0, 0.0)
            v.view_state.fov_deg = 20.0
            # 绘制/拾取按视高度；-0.7° 的视高度仍 <0（真的看不见）
            scr = v._sky_to_screen(90.0, v._display_alt_deg(alt))
            out[tag] = (_ink_at(_render_planets(v, 500, 500), scr.x(), scr.y(), 14),
                        v._find_body_at(scr.x(), scr.y()))
    finally:
        v.close()
    assert out["below"][0] == 0, f"地平线下不应落笔: {out['below']}"
    assert out["below"][1] is None or out["below"][1].get("kind") != "planet"
    assert out["above"][0] > 0, f"地平线上必须画: {out['above']}"
    assert out["above"][1] is not None \
        and out["above"][1].get("kind") == "planet", out["above"]


def test_planets_resolved_at_allowed_min_fov():
    """UI 缩放能到的 min_fov（0.5°）下，金星必须真的可分辨（r >= 2px）。

    反证：min_fov 旧值 5° 时金星 r≈0.7px，_draw_planets 永远只画点源——
    真实圆面/相位代码在产品里不可达。
    """
    v = _new_view(700)
    v.view_state.zoom_to(v.view_state.min_fov)
    assert v.view_state.fov_deg == pytest.approx(0.5)
    resolved = {}
    for key, pos in P.compute_all(PLANET_JD).items():
        if key in ("moon", "earth"):
            continue
        az, alt = _planet_altaz(pos)
        if alt < 0.0:
            continue
        v.view_state.look_at(az, alt)
        disc = v._planet_screen_disc(az, alt,
                                     pos.angular_diameter_arcsec / 3600.0)
        assert disc is not None
        resolved[key] = float(disc["r"])
    assert resolved.get("venus", 0.0) >= 2.0, resolved
    assert sum(1 for r in resolved.values() if r >= 2.0) >= 2, resolved


def test_wheel_zoom_walks_to_min_fov_smoothly():
    """滚轮等比缩放：单调、逐步比恒定、到下限精确收住且不抖不跳。"""
    v = _new_view(700)
    v.view_state.zoom_to(v.view_state.max_fov)
    c0 = (v.view_state.center_az, v.view_state.center_alt)
    fovs = [v.view_state.fov_deg]
    for _ in range(40):
        if v.view_state.fov_deg <= v.view_state.min_fov:
            break
        v.view_state.zoom(1.0)
        fovs.append(v.view_state.fov_deg)
    assert v.view_state.fov_deg == pytest.approx(0.5)
    assert len(fovs) <= 13, f"120°→0.5° 应约 11 步，实测 {len(fovs) - 1}"
    step = math.exp(-30.0 / 60.0)          # exp(-zoomSpeed/60)
    for a, b in zip(fovs, fovs[1:-1]):
        assert b / a == pytest.approx(step, rel=1e-6)
    # 末步本会破下限，被精确钳到 min_fov（不越过、不回弹）
    assert fovs[-2] * step < v.view_state.min_fov
    assert fovs[-1] == pytest.approx(v.view_state.min_fov)
    assert (v.view_state.center_az, v.view_state.center_alt) == c0
    # 反向滚轮能平滑退回（不会卡在下限附近）
    v.view_state.zoom(-1.0)
    assert v.view_state.fov_deg > 0.5


def test_zoom_at_cursor_stable_at_min_fov():
    """光标锚定缩放：在 0.5° 极限附近连续放大，锚点仍钉在指下（残差小）。"""
    v = _new_view(700)
    pos = P.compute_planet("venus", PLANET_JD)
    az, alt = _planet_altaz(pos)
    v.view_state.look_at(az, alt)
    v.view_state.zoom_to(2.0)
    cursor = (520.0, 260.0)               # 明显偏心，考验大偏移下的反补

    def sky_at(px, py):
        return screen_to_sky(px, py, v.view_state, v.projection,
                             (float(v.width()), float(v.height())))

    az0, alt0 = sky_at(*cursor)
    for _ in range(6):
        v.zoom_at_cursor(1.0, cursor)
    az1, alt1 = sky_at(*cursor)
    resid = angular_separation_deg(az0, alt0, az1, alt1)
    assert v.view_state.fov_deg == pytest.approx(0.5)
    assert resid < 0.05, f"窄 FOV 锚定残差 {resid:.4f}° 过大（会漂/抖）"


# --------------------------------------------------------------------------
# 4. 可分辨行星：真实圆面 + 历表相位亮区（朝向太阳）
# --------------------------------------------------------------------------
def test_resolved_planet_draws_size_and_phase():
    v = _new_view(500)
    pos = dataclasses.replace(
        P.compute_planet("venus", PLANET_JD),
        angular_diameter_arcsec=900.0, phase_angle_deg=130.3)
    az, alt = _planet_altaz(pos)
    alt_draw = v._display_alt_deg(alt)
    v._planet_positions = lambda jd: {"venus": pos}
    v.view_state.look_at(az, alt_draw)
    v.view_state.fov_deg = 2.0
    # 太阳置于行星正上方（亮区应朝上），与 az/alt 切向一致
    v.set_celestial_bodies({"az": az, "alt": min(89.0, alt_draw + 30.0)},
                           None, 0.0)

    disc = v._planet_screen_disc(az, alt_draw, 900.0 / 3600.0)
    assert disc is not None
    r = float(disc["r"])
    assert r > 20.0                 # 反证：旧实现恒为 star_draw 小圆点
    ux, uy = float(disc["ux"]), float(disc["uy"])

    img = _render_planets(v, 500, 500)
    off, count = _bright_stats(img, disc["center"].x(), disc["center"].y(), r)
    assert off is not None and count > 0
    ox, oy = off
    assert (ox * ux + oy * uy) / math.hypot(ox, oy) > 0.8
    assert oy < -0.1 * r            # 太阳在上方 -> 亮区在上方

    # 亮区比例 ≈ 17.7%（旧实现整盘满亮 = 100%，此处必红）
    disk_area = math.pi * (r - 1.0) ** 2
    assert count / disk_area == pytest.approx(0.177, abs=0.04)


# --------------------------------------------------------------------------
# 5. 太阳方位缺测 -> 不填亮区，不编造朝向
# --------------------------------------------------------------------------
def test_planet_without_sun_direction_draws_outline_only():
    v = _new_view(500)
    pos = dataclasses.replace(
        P.compute_planet("venus", PLANET_JD),
        angular_diameter_arcsec=900.0, phase_angle_deg=130.3)
    az, alt = _planet_altaz(pos)
    v._planet_positions = lambda jd: {"venus": pos}
    v.view_state.look_at(az, alt)
    v.view_state.fov_deg = 2.0
    v.set_celestial_bodies(None, None, 0.0)

    disc = v._planet_screen_disc(az, alt, 900.0 / 3600.0)
    assert disc is not None and disc["ux"] is None and disc["uy"] is None
    r = float(disc["r"])
    img = _render_planets(v, 500, 500)
    _, count = _bright_stats(img, disc["center"].x(), disc["center"].y(), r)
    assert count < 0.1 * math.pi * r * r     # 只有轮廓，没按左右猜亮区


# --------------------------------------------------------------------------
# 6. 已分辨圆面的拾取半径跟随画出的圆面（看得到就点得中）
# --------------------------------------------------------------------------
def test_resolved_planet_disc_is_pickable_off_center(monkeypatch):
    """点击距圆心 0.75r（> 旧固定 14px 容差、但在圆面内）必须命中金星。

    反证：旧实现 `_find_body_at` 恒用 14px 容差——2° FOV 下金星圆面 r≈31px，
    圆面边缘那圈"画出来了却点不中"。
    """
    v = _new_view(500)
    pos = dataclasses.replace(
        P.compute_planet("venus", PLANET_JD),
        angular_diameter_arcsec=900.0, phase_angle_deg=130.3)
    az, alt = _planet_altaz(pos)
    alt_draw = v._display_alt_deg(alt)
    v._planet_positions = lambda jd: {"venus": pos}
    v.view_state.look_at(az, alt_draw)
    v.view_state.fov_deg = 2.0
    monkeypatch.setattr("rf_sky_view._constel.merged_star_index", lambda: {})

    disc = v._planet_screen_disc(az, alt_draw, 900.0 / 3600.0)
    assert disc is not None
    r = float(disc["r"])
    center = disc["center"]
    assert r > 20.0
    click = (center.x() + 0.75 * r, center.y())
    assert 0.75 * r > 14.0          # 旧固定容差够不到的位置
    body = v._find_body_at(click[0], click[1])
    assert body is not None and body.get("key") == "venus", \
        f"圆面内 (0.75r={0.75 * r:.0f}px) 应命中金星，实测 {body}"


# --------------------------------------------------------------------------
# 7. 信息卡与绘制同源
# --------------------------------------------------------------------------
def test_planet_info_card_matches_ephemeris_used_for_drawing():
    v = _new_view(500)
    pos = P.compute_planet("venus", PLANET_JD)
    v._build_body_info({"kind": "planet", "key": "venus", "zh": "金星"})
    info = v._body_info
    assert info["mag"] == pytest.approx(pos.magnitude)
    assert f"{pos.angular_diameter_arcsec:.1f}″" in info["desc"]
    assert f"{pos.phase_angle_deg:.1f}°" in info["desc"]
    # 绘制层读的是同一个 pos.magnitude（star_draw 输入）
    assert isinstance(pos.magnitude, float)
