# SPDX-License-Identifier: MIT
"""
tests/test_sky_daylight_stars.py
================================
天空视图的白天恒星消隐（**保守近似**，非标准天空亮度模型）。

口径（与 `rf_sky_view.stars_hidden_by_daylight` 注释、`docs/limitations-register.md`
L24 同一条）：太阳高度 ≥ -0.833°（日落标准定义：34′ 大气折射 + 16′ 日面半径）
→ 判白天 → 恒星整层不画、不可点选，状态卡明示「白天·无恒星」。
本视图**不做**天空亮度→极限星等模型，也不做暮光渐变。

覆盖：
  1. 纯函数边界：-0.834°(夜) / -0.833°(昼) / None / nan / 非数值；
  2. 太阳历表缺失 -> 不消隐（不拿默认值冒充白天）；
  3. 绘制层：白天 `_draw_stars` 零落笔，夜里同样输入必须有落笔；
  4. 拾取层：夜里能点中亮星，白天同一像素点不中（绘制/拾取同源）；
  5. 状态卡：白天多出「白天·无恒星」一行，夜里没有。

反证：把 `_draw_stars`/`_find_body_at` 的 `_stars_hidden_by_daylight()` 门
去掉（旧实现全天画星），第 3、4 条必红；把阈值改成 0° 或恒定 False，
第 1 条必红。
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

import rf_sky_view  # noqa: E402
from rf_sky_view import (  # noqa: E402
    RFSkyView,
    STAR_DAYLIGHT_SUN_ALT_DEG,
    stars_hidden_by_daylight,
)
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, vec_from_radec, j2000_to_altaz, azalt_from_vec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402

LAT, LON = 40.0, 116.0
UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()   # 夜侧
WIN = 640


# --------------------------------------------------------------------------
# 1. 纯函数边界（阈值本身要钉住：-0.833° 是日落标准定义）
# --------------------------------------------------------------------------
def test_daylight_threshold_is_standard_sunset_definition():
    assert STAR_DAYLIGHT_SUN_ALT_DEG == pytest.approx(-0.833)


@pytest.mark.parametrize("alt,hidden", [
    (-0.834, False),      # 刚落到日落定义之下 -> 夜
    (-0.833, True),       # 恰好等于阈值 -> 按保守口径算白天
    (-1.0, False),
    (-18.0, False),
    (0.0, True),
    (30.0, True),
])
def test_daylight_gate_boundary(alt, hidden):
    assert stars_hidden_by_daylight(alt) is hidden


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"),
                                 "abc", object()])
def test_unknown_sun_alt_never_claims_daylight(bad):
    """太阳高度未知/非数值 -> 不消隐（不能把「不知道」当成白天）。"""
    assert stars_hidden_by_daylight(bad) is False


# --------------------------------------------------------------------------
# 2. 视图层：太阳历表缺失 -> 不消隐；有值 -> 跟随
# --------------------------------------------------------------------------
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


def test_view_gate_follows_sun_alt():
    v = _new_view()
    try:
        v.set_celestial_bodies({"az": 0.0, "alt": 30.0}, None, 0.0)
        assert v._sun_alt_deg() == pytest.approx(30.0)
        assert v._stars_hidden_by_daylight() is True

        v.set_celestial_bodies({"az": 0.0, "alt": -20.0}, None, 0.0)
        assert v._stars_hidden_by_daylight() is False

        v.set_celestial_bodies({"az": 0.0}, None, 0.0)      # 只有 az 没 alt
        assert v._sun_alt_deg() is None
        assert v._stars_hidden_by_daylight() is False

        v.set_celestial_bodies(None, None, 0.0)             # 历表未就绪
        assert v._sun_alt_deg() is None
        assert v._stars_hidden_by_daylight() is False
    finally:
        v.close()


# --------------------------------------------------------------------------
# 3. 绘制层：白天零落笔，夜里同样输入有落笔
# --------------------------------------------------------------------------
def _star_ink(v) -> int:
    img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    try:
        v._draw_stars(p)
    finally:
        p.end()
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() + c.green() + c.blue() > 90:
                n += 1
    return n


def test_daylight_star_layer_draws_nothing_night_draws_many():
    v = _new_view()
    try:
        v.set_celestial_bodies({"az": 180.0, "alt": -30.0}, None, 0.0)
        night = _star_ink(v)
        v.set_celestial_bodies({"az": 180.0, "alt": 30.0}, None, 0.0)
        day = _star_ink(v)
    finally:
        v.close()
    assert night > 200, f"对照前提失败：夜里应画满星点，实测 {night}"
    assert day == 0, f"白天必须整层零落笔，实测 {day} 像素"


def test_missing_sun_ephemeris_keeps_stars_drawn():
    """太阳历表缺失 -> 按夜处理（诚实空态，不假装白天）。"""
    v = _new_view()
    try:
        v.set_celestial_bodies(None, None, 0.0)
        ink = _star_ink(v)
    finally:
        v.close()
    assert ink > 200, f"太阳未知时仍应画星，实测 {ink}"


# --------------------------------------------------------------------------
# 4. 拾取层：夜可点中亮星，白天同像素点不中（绘制/拾取同源）
# --------------------------------------------------------------------------
def _pick_a_bright_star_on_screen(v):
    """找一颗在地平线上、屏幕内、且亮到一定画得出来的恒星。"""
    gs = v._ground_station()
    jd = v._current_jd()
    for name, s in _constel.merged_star_index().items():
        if s.vmag > 1.0:
            continue
        try:
            az, alt = azalt_from_vec(
                j2000_to_altaz(vec_from_radec(s.ra_deg, s.dec_deg), gs, jd))
        except Exception:
            continue
        if alt < 10.0:
            continue
        pos = v._sky_to_screen(az, alt)
        if 30 <= pos.x() <= v.width() - 30 and 30 <= pos.y() <= v.height() - 30:
            return name, pos
    return None, None


def test_daylight_pick_matches_draw_gate():
    v = _new_view()
    try:
        name, pos = _pick_a_bright_star_on_screen(v)
        assert name is not None, "取样失败：应有一颗亮星落在屏幕内"
        v.set_celestial_bodies({"az": 180.0, "alt": -30.0}, None, 0.0)
        night = v._find_body_at(pos.x(), pos.y())
        v.set_celestial_bodies({"az": 180.0, "alt": 30.0}, None, 0.0)
        day = v._find_body_at(pos.x(), pos.y())
    finally:
        v.close()
    assert night is not None and night.get("kind") == "star", night
    assert str(night.get("key")) == name
    assert day is None or day.get("kind") != "star", \
        f"白天没画恒星却给点了：{day}"


# --------------------------------------------------------------------------
# 5. 状态卡明示「白天·无恒星」
# --------------------------------------------------------------------------
def _card_crop(v) -> QImage:
    img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    try:
        v._draw_info_overlay(p)
    finally:
        p.end()
    card_x = 12
    # 卡片高度取实现常量（状态卡尺寸唯一事实源，避免测试写死像素值）
    card_y = v.height() - RFSkyView.INFO_CARD_H - 12
    # 太阳高度行（第 3 行）右侧、避开左侧文案与数值的 x 区间
    return img.copy(card_x + 145, card_y + 40, 100, 22)


def _diff_pixels(a, b) -> int:
    """两张同尺寸裁剪图逐像素不同的个数（卡片底色不变，只数新落笔的字）。"""
    n = 0
    for y in range(a.height()):
        for x in range(a.width()):
            if a.pixelColor(x, y) != b.pixelColor(x, y):
                n += 1
    return n


def test_info_card_states_daylight_stars_are_hidden():
    v = _new_view()
    try:
        v.set_celestial_bodies({"az": 180.0, "alt": -30.0}, None, 0.0)
        night = _card_crop(v)
        v.set_celestial_bodies({"az": 180.0, "alt": 30.0}, None, 0.0)
        day = _card_crop(v)
    finally:
        v.close()
    # 太阳高度行右侧只有白天会多出「白天·无恒星」五个字
    assert _diff_pixels(day, night) > 30, \
        f"白天状态卡必须明示「白天·无恒星」（差异像素 {_diff_pixels(day, night)}）"
