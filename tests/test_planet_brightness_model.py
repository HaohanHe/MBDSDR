# SPDX-License-Identifier: MIT
"""
tests/test_planet_brightness_model.py
=====================================
行星接入恒星同一条亮度模型（星等 → 屏幕半径/亮度/辉光，再叠大气消光与光害
极限星等可见门）的钉扎。Stellarium Web `core_get_point_for_mag` 语义，见
`docs/learn/stellarium_web_engine_architecture.md` §8.1 第 7 条：所有天体共用
一套亮度模型，视觉才一致。修复前两者完全脱节：

  · `_draw_planets` 恒画 `drawEllipse(scr, 4.0, 4.0)` 固定 4px 实心点 +
    固定 alpha 215 标签，与星等无关——金星(-4.70) 和海王星(+7.83) 看起来
    一模一样，且裸眼根本看不见的行星照样画在天上（视觉编码说谎）；
  · `star_draw` 的 `frac` 带 `min(1.0, ...)` 上限 clamp，所有 vmag < -1.5 的
    天体被压成同大同亮。恒星从不触发（两张星表最亮 -1.44/-1.46），行星一接
    进来就全触发（金星 -4.70 / 木星 -1.94 / 满月 -12.8）。模型侧的修正与
    非回归对拍钉在 `tests/test_astronomy.py::TestStarsAndSatMeta`。
  · 拾取层 `_find_body_at` 的可见性门槛与绘制层各说各话：恒星写死
    `vmag > 4.5` 剔除（绘制层走光害极限星等，默认 6.5——真星表里 Graffias
    4.84 画得出却点不中），行星完全不设门（Bortle 3 下海王星 7.83 如实不画，
    点它所在像素却弹出信息卡）。绘制门一旦生效，"看见的才点得中"必须同步，
    否则点星即听这条主交互在暗星上静默失灵。

断言分三层：
  · 源码层——`_draw_planets` 确实走 `star_draw` + `extinction_delta_mag`，
    固定 4px 圆点已绝迹，超极限时如实 continue（同 `test_selection_crosshair.py`
    的源码体钉法）；
  · 像素层——offscreen 真渲染，用「有该行星 − 无该行星」在同一取样窗的强度
    差隔离出行星自己的净贡献（背景星空/大气/地景/天线两次完全相同）。探针实测
    净增：金星 25988 > 木星 23131 > 土星 17034 >> 天王星 1664 > 海王星 0
    = 缺星等 0；Bortle 9 下天王星也从 1664 掉到 0（反证：门是随光害变化的
    极限星等，不是渲染路径死了）。
  · 拾取层——同一取样像素上 `_find_body_at` 与绘制门对拍。探针实测四处行为
    翻转（旧 f754d8c → 新）：海王星 7.83@B3 planet→None、天王星 5.73@B9
    planet→None、假星 5.20@B3 None→star、假星 4.84@B3 None→star；对照组
    （金星@B3、天王星@B3、5.20@B9）新旧两种实现下结果不变。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_planet_brightness_model.py -v
"""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import rf_sky_view  # noqa: E402
from rf_sky_view import (  # noqa: E402
    RFSkyView, j2000_to_altaz, vec_from_radec,
)
from mbdsdr_ai.celestial_geometry import azalt_from_vec  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine.stars import BrightStar  # noqa: E402

LAT, LON = 40.0, 116.0
# 固定历元（不绑墙钟）下探针实测的一处高仰角、偏心 100..250px 的天区：
# 偏心是为了避开视图正中央的天线指针（它画在行星层之后，会盖掉正中的天体）。
RA, DEC = 0.0, 23.0
# 真实历表星等（`astronomy.planets.compute_all(2460900.5)` 实测值，非编造）。
MAG_VENUS = -4.70
MAG_SATURN = 0.91
MAG_URANUS = 5.73
MAG_NEPTUNE = 7.83
# `_draw_planets` 按 `_PLANET_STYLE` 的键去历表里取项，故假历表必须挂在真键上；
# 统一用 venus 一个键＝识别色恒定，净增差异只可能来自星等。
STYLE_KEY = "venus"
STAR_NAME = "PickProbeStar"   # 假星名：拾取门用，避开真星表里任何一颗
WIN = 14              # 取样窗半径 px：罩住圆盘 + 3 圈辉光 + 标签
INVISIBLE_EPS = 200   # "如实没画"的净增上限（实测为 0，留渲染抖动余量）
VISIBLE_MIN = 1000    # "确实画了"的净增下限（实测最暗的天王星为 1664）


class _FakeEph:
    """只带渲染所需字段的假历表项（形状对齐 `compute_all` 的值）。"""

    def __init__(self, ra_deg, dec_deg, magnitude):
        self.ra_deg = ra_deg
        self.dec_deg = dec_deg
        self.magnitude = magnitude


def _new_view():
    eng = get_time_engine()
    eng.set_unix(datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp())
    eng.pause()
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(LAT, LON, 0.0)
    v.show()
    for _ in range(4):
        app.processEvents()
    return v


def _screen_pos(v):
    """被测代码同一条链算出假行星的地平坐标与屏幕位置，并守住前提条件。"""
    vv = j2000_to_altaz(vec_from_radec(RA, DEC), v._ground_station(),
                        v._current_jd())
    az, alt = azalt_from_vec(vv)
    assert alt > 30.0, f"取样天区应高仰角（消光最小），实测 alt={alt:.2f}"
    p = v._sky_to_screen(az, alt)
    assert v._on_screen(p), f"取样天区应在屏内，实测 ({p.x():.1f},{p.y():.1f})"
    off = math.hypot(p.x() - v.width() / 2.0, p.y() - v.height() / 2.0)
    assert 60.0 < off < 300.0, \
        f"取样点须偏心以避开中央天线指针，实测偏心 {off:.1f}px"
    return int(round(p.x())), int(round(p.y())), alt


def _window_intensity(img, cx, cy):
    total = 0
    for dy in range(-WIN, WIN + 1):
        for dx in range(-WIN, WIN + 1):
            if dx * dx + dy * dy > WIN * WIN:
                continue
            c = img.pixelColor(cx + dx, cy + dy)
            total += c.red() + c.green() + c.blue()
    return total


def _planet_intensity(v, mag, bortle=3):
    """渲染两次（无该行星 / 有该行星），返回取样窗内的净强度增量。"""
    cx, cy, _alt = _screen_pos(v)
    v._bortle = bortle

    def render(planets):
        # patch 打在消费方实例上（类里是 staticmethod，实例属性直接遮蔽）
        v._planet_positions = lambda jd: planets
        img = v.grab().toImage()
        return _window_intensity(img, cx, cy)

    base = render({})
    if mag is None:
        eph = _FakeEph(RA, DEC, None)
        del eph.magnitude      # 历表没给星等：字段缺失，不是给了个 None
    else:
        eph = _FakeEph(RA, DEC, mag)
    return render({STYLE_KEY: eph}) - base


class TestSourceRoutesThroughSharedModel:
    """源码层：行星确实共用恒星的亮度模型，固定 4px 圆点已绝迹。"""

    def _body(self):
        src = open(os.path.join(_ROOT, "desktop", "rf_sky_view.py"),
                   encoding="utf-8").read()
        i = src.index("def _draw_planets")
        j = src.index("\n    def ", i + 10)
        return src[i:j]

    def test_draw_planets_calls_star_draw_and_extinction(self):
        body = self._body()
        assert "star_draw(" in body, \
            "行星必须走恒星同一条 star_draw 星等→半径/亮度模型"
        assert "extinction_delta_mag(" in body, \
            "行星也要吃大气消光（地平附近变暗变小），与恒星同语义"

    def test_no_fixed_radius_disc_left(self):
        body = self._body()
        assert "drawEllipse(scr, 4.0, 4.0)" not in body, \
            "固定 4px 圆点＝星等无关的视觉编码说谎，必须已被 star_draw 取代"

    def test_invisible_planet_is_skipped_not_clamped(self):
        body = self._body()
        assert "if r <= 0:" in body, \
            "超裸眼极限星等要如实不画（与恒星同规则），不能夹成最小可见"


class TestPixelEvidence:
    """像素层：真渲染下亮度序、极限门、诚实空态都成立。"""

    def test_brighter_planet_paints_stronger(self):
        v = _new_view()
        try:
            strong = _planet_intensity(v, MAG_VENUS)
            faint = _planet_intensity(v, MAG_SATURN)
        finally:
            v.close()
        assert faint > VISIBLE_MIN, \
            f"土星(0.91 等)在 Bortle 3 应画得出，实测净增 {faint}"
        assert strong > faint * 1.15, \
            (f"金星(-4.70)必须明显强于土星(0.91)：实测 {strong} vs {faint}"
             "——若两者接近，说明星等没接进渲染（回到固定 4px 老缺陷）")

    def test_neptune_beyond_naked_eye_limit_not_painted(self):
        v = _new_view()
        try:
            delta = _planet_intensity(v, MAG_NEPTUNE)
        finally:
            v.close()
        assert abs(delta) < INVISIBLE_EPS, \
            (f"海王星(7.83 等)超 Bortle 3 裸眼极限 6.5 等，应如实不画，"
             f"实测净增 {delta}")

    def test_bortle_limit_governs_visibility(self):
        """反证：同一颗天王星，暗空画得出、重光害画不出——门是极限星等。"""
        v = _new_view()
        try:
            dark = _planet_intensity(v, MAG_URANUS, bortle=3)
            city = _planet_intensity(v, MAG_URANUS, bortle=9)
        finally:
            v.close()
        assert dark > VISIBLE_MIN, \
            f"天王星(5.73 等) < Bortle 3 极限 6.5，应画得出，实测 {dark}"
        assert abs(city) < INVISIBLE_EPS, \
            (f"天王星(5.73 等) > Bortle 9 极限 4.4，应如实不画，实测 {city}"
             "——若这里也画得出，说明极限门没生效，上一条绿是巧合")

    def test_missing_magnitude_is_not_faked(self):
        v = _new_view()
        try:
            delta = _planet_intensity(v, None)
        finally:
            v.close()
        assert abs(delta) < INVISIBLE_EPS, \
            f"历表没给星等就不该画（不能拿默认亮度冒充），实测净增 {delta}"


def _pick_planet(v, mag, bortle=3):
    """把假行星挂到取样天区，返回该像素处的拾取结果。"""
    cx, cy, _alt = _screen_pos(v)
    v._bortle = bortle
    v._planet_positions = lambda jd: {STYLE_KEY: _FakeEph(RA, DEC, mag)}
    return v._find_body_at(float(cx), float(cy))


def _pick_star(v, monkeypatch, vmag, bortle=3):
    """星表只留一颗假星（排除真星干扰），返回取样像素处的拾取结果。"""
    cx, cy, _alt = _screen_pos(v)
    v._bortle = bortle
    v._planet_positions = lambda jd: {}
    fake = {STAR_NAME: BrightStar(STAR_NAME, RA, DEC, vmag, 0.0)}
    monkeypatch.setattr(rf_sky_view._constel, "merged_star_index",
                        lambda: fake)
    return v._find_body_at(float(cx), float(cy))


class TestPickGateMatchesDrawGate:
    """拾取层：画出来的才点得中，没画的不给点（与绘制层同一条可见性门）。

    修复前 `_find_body_at` 的门槛与绘制层各说各话：恒星写死 vmag>4.5 剔除
    （绘制层走 Bortle 极限星等，默认 6.5——真星表里 Graffias 4.84 画得出却
    点不中），行星则完全不设门（Bortle 3 下海王星 7.83 如实不画，但点空白
    天区照样弹出它的信息卡）。
    """

    def _body(self):
        src = open(os.path.join(_ROOT, "desktop", "rf_sky_view.py"),
                   encoding="utf-8").read()
        i = src.index("def _find_body_at")
        j = src.index("\n    def ", i + 10)
        return src[i:j]

    def test_pick_gate_routes_through_shared_model(self):
        body = self._body()
        assert "star_draw(" in body, \
            "拾取候选必须走绘制层同一条 star_draw 可见性门"
        assert "star.vmag > 4.5" not in body, \
            "写死 4.5 等的拾取门槛＝画得出却点不中，必须已绝迹"

    def test_visible_planet_is_pickable(self):
        v = _new_view()
        try:
            res = _pick_planet(v, MAG_VENUS)
        finally:
            v.close()
        assert res is not None and res.get("kind") == "planet", \
            f"金星(-4.70)在 Bortle 3 画得出，就该点得中，实测 {res}"
        assert res.get("key") == STYLE_KEY

    def test_invisible_planet_is_not_pickable(self):
        v = _new_view()
        try:
            res = _pick_planet(v, MAG_NEPTUNE)
        finally:
            v.close()
        assert res is None or res.get("kind") != "planet", \
            (f"海王星(7.83)超 Bortle 3 极限如实不画，点它所在像素不该冒出"
             f"行星信息卡，实测 {res}")

    def test_planet_pick_gate_follows_bortle(self):
        """反证：同一颗天王星，暗空点得中、重光害点不中——门随光害变化。"""
        v = _new_view()
        try:
            dark = _pick_planet(v, MAG_URANUS, bortle=3)
            city = _pick_planet(v, MAG_URANUS, bortle=9)
        finally:
            v.close()
        assert dark is not None and dark.get("kind") == "planet", \
            f"天王星(5.73) < Bortle 3 极限 6.5，应点得中，实测 {dark}"
        assert city is None or city.get("kind") != "planet", \
            (f"天王星(5.73) > Bortle 9 极限 4.4，画不出也就点不中，实测 {city}"
             "——若这里也点得中，说明拾取门没接上极限星等，上一条绿是巧合")

    def test_faint_star_inside_limit_is_pickable(self, monkeypatch):
        """4.5 < vmag < 极限的星：画得出（旧写死门槛下点不中，本条即修复）。"""
        v = _new_view()
        try:
            res = _pick_star(v, monkeypatch, 5.2, bortle=3)
        finally:
            v.close()
        assert res is not None and res.get("key") == STAR_NAME, \
            f"5.2 等 < Bortle 3 极限 6.5，画得出就该点得中，实测 {res}"

    def test_star_beyond_limit_is_not_pickable(self, monkeypatch):
        v = _new_view()
        try:
            res = _pick_star(v, monkeypatch, 5.2, bortle=9)
        finally:
            v.close()
        assert res is None or res.get("key") != STAR_NAME, \
            f"5.2 等 > Bortle 9 极限 4.4，如实不画也就不给点，实测 {res}"
