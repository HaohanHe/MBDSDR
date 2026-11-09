# SPDX-License-Identifier: MIT
"""天空视图卫星受晒/地影**渲染侧**钉扎（B 项）。

后端受晒判据不在本线实现：本线只消费轨道快照可选的 ``sunlit`` 字段
（True=受晒 / False=地影 / None=无数据或半影），并保证：
  1. None（含未提供字段）→ 状态卡写「未知（无受晒数据）」，**不画阴影**；
  2. False → 本体压暗 + 暗色虚线描边，状态卡写「地影」；
  3. True → 保持原样，不画阴影；
  4. 非卫星对象 → 受晒行显示「—」；
  5. 未来后端接入口就是 SkyObject(sunlit=..., sunlit_status=...)
     → 信息卡字段原样透传；status=penumbra 显示「半影（部分受照）」

反证（若实现按默认值造假）：
  - 把 None 当受晒 → 用例 1 的 True/None 像素差为 0 仍成立，但状态卡
    文案断言会红（「未知」被写成「受晒」）；
  - None 也画阴影 → 用例 1 的像素差断言红。

运行：
    QT_QPA_PLATFORM=offscreen MBDSDR_NO_HW=1 python -m pytest tests/test_sky_sunlit_state.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import (  # noqa: E402
    RFSkyView, SkyObject, sunlit_state_label, sunlit_is_eclipsed)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

UNIX = 1790456820.0
AZ, ALT = 180.0, 45.0


@pytest.fixture(autouse=True)
def engine():
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.set_rate(1.0)
    yield eng
    eng.set_time_now()
    eng.set_rate(1.0)


def _view() -> RFSkyView:
    v = RFSkyView()
    v.resize(760, 760)
    v.set_observer(43.9, 125.3, 0.0)
    return v


def _obj(sunlit=None, obj_type="satellite", name="NOAA 19",
         status=None) -> SkyObject:
    return SkyObject(name=name, azimuth_deg=AZ, elevation_deg=ALT,
                     obj_type=obj_type, sunlit=sunlit,
                     sunlit_status=status)


def _render_objects(v: RFSkyView) -> QImage:
    img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    try:
        v._label_items = []
        v._draw_objects(p)
    finally:
        p.end()
    return img


def _marker_crop(img: QImage, pos, margin: int = 18) -> QImage:
    return img.copy(int(pos.x()) - margin, int(pos.y()) - margin,
                    margin * 2, margin * 2)


def _diff_pixels(a, b) -> int:
    n = 0
    for y in range(a.height()):
        for x in range(a.width()):
            if a.pixelColor(x, y) != b.pixelColor(x, y):
                n += 1
    return n


def _card_row_crop(v: RFSkyView, dy: int, h: int) -> QImage:
    img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    try:
        v._draw_info_overlay(p)
    finally:
        p.end()
    card_y = v.height() - RFSkyView.INFO_CARD_H - 12
    return img.copy(12 + 52, card_y + dy, 180, h)


# --------------------------------------------------------------------------
# 1. 文案映射（三态 + 非卫星）
# --------------------------------------------------------------------------
def test_label_mapping_is_honest_for_all_states():
    assert sunlit_state_label(True) == "受晒"
    assert sunlit_state_label(False) == "地影"
    # None 涵盖「后端无太阳数据」与「半影（部分受照）」，一律不冒充受晒
    assert sunlit_state_label(None) == "未知（无受晒数据）"
    assert sunlit_state_label(None, "signal") == "—"
    assert sunlit_state_label(True, "signal") == "—"
    # 后端 status 优先：半影不可二值化，不能被压成「受晒」或「地影」
    assert sunlit_state_label(None, "satellite", "penumbra") == "半影（部分受照）"
    assert sunlit_state_label(None, "satellite", "umbra") == "地影"
    assert sunlit_state_label(True, "satellite", "umbra") == "地影"
    assert sunlit_state_label(None, "satellite", "unknown") == "未知（无受晒数据）"


def test_eclipsed_only_for_umbra_not_penumbra_or_unknown():
    assert sunlit_is_eclipsed(False) is True
    assert sunlit_is_eclipsed(None) is False
    assert sunlit_is_eclipsed(True) is False
    assert sunlit_is_eclipsed(None, "umbra") is True
    assert sunlit_is_eclipsed(None, "penumbra") is False
    assert sunlit_is_eclipsed(None, "unknown") is False


# --------------------------------------------------------------------------
# 2. 绘制：只有 False 才画地影描边；None/True 一律不画
# --------------------------------------------------------------------------
def test_none_and_sunlit_draw_identically_no_fake_shadow():
    pos = None
    v = _view()
    try:
        v.set_objects([_obj(None)])
        img_none = _render_objects(v)
        pos = v._sky_to_screen(AZ, ALT)
        v.set_objects([_obj(True)])
        img_true = _render_objects(v)
    finally:
        v.close()
    assert _diff_pixels(_marker_crop(img_none, pos),
                        _marker_crop(img_true, pos)) == 0, \
        "受晒/未知的渲染必须一致；未知不得画阴影"


def test_eclipsed_draws_shadow_outline():
    v = _view()
    try:
        v.set_objects([_obj(None)])
        img_none = _render_objects(v)
        pos = v._sky_to_screen(AZ, ALT)
        v.set_objects([_obj(False)])
        img_ecl = _render_objects(v)
    finally:
        v.close()
    diff = _diff_pixels(_marker_crop(img_none, pos),
                        _marker_crop(img_ecl, pos))
    assert diff > 20, f"地影态应有可见的暗色描边/压暗，实测差异像素 {diff}"


def test_penumbra_draws_no_shadow():
    """半影是「部分受照」，不是地影：不得压暗/画阴影。"""
    v = _view()
    try:
        v.set_objects([_obj(None, status="penumbra")])
        img_pen = _render_objects(v)
        pos = v._sky_to_screen(AZ, ALT)
        v.set_objects([_obj(None)])
        img_none = _render_objects(v)
    finally:
        v.close()
    assert _diff_pixels(_marker_crop(img_pen, pos),
                        _marker_crop(img_none, pos)) == 0


def test_missing_field_defaults_to_none():
    """旧调用方不传 sunlit 时字段为 None（不是受晒），渲染等同未知。"""
    v = _view()
    try:
        v.set_objects([SkyObject(name="X", azimuth_deg=AZ,
                                 elevation_deg=ALT)])
        img_default = _render_objects(v)
        pos = v._sky_to_screen(AZ, ALT)
        v.set_objects([_obj(None)])
        img_none = _render_objects(v)
    finally:
        v.close()
    assert _diff_pixels(_marker_crop(img_default, pos),
                        _marker_crop(img_none, pos)) == 0
    assert getattr(SkyObject(name="X", azimuth_deg=AZ,
                             elevation_deg=ALT), "sunlit") is None


# --------------------------------------------------------------------------
# 3. 状态卡：三态文案不同；非卫星显示「—」
# --------------------------------------------------------------------------
def _card_state(v: RFSkyView, obj=None) -> QImage:
    v._selected_info = None
    if obj is not None:
        v._build_selected_info(obj)
    # 受晒行基线 y=card_y+202（指向 20 / 可见卫星 38 / 太阳高度 56 /
    # UTC 76 / 本地 94 / 恒星时 112 / 观测站 130 / 视轴 148 / 视角 166 /
    # 调谐 184 / 受晒 202），文字上沿约 baseline-12=190。
    # 旧值 210 截到的是银河行「无数据·不提供」，不随受晒状态变化。
    return _card_row_crop(v, 190, 20)


def test_status_card_states_differ_per_state():
    v = _view()
    try:
        before = _card_state(v)                      # 无选中 → 「—」
        sun = _card_state(v, _obj(True))
        umb = _card_state(v, _obj(False))
        unk = _card_state(v, _obj(None))
        pen = _card_state(v, _obj(None, status="penumbra"))
    finally:
        v.close()
    assert _diff_pixels(before, sun) > 2, "无选中应显示「—」，不能冒充受晒"
    assert _diff_pixels(sun, umb) > 2, "受晒与地影的文案/颜色必须不同"
    assert _diff_pixels(sun, unk) > 2, "未知不得与受晒同文案"
    assert _diff_pixels(umb, unk) > 2, "未知不得与地影同文案"
    assert _diff_pixels(pen, unk) > 2, "半影必须单独说明，不能混同未知"


def test_status_card_non_satellite_shows_dash():
    v = _view()
    try:
        sat_unk = _card_state(v, _obj(None))
        other = _card_state(v, _obj(None, obj_type="signal", name="SIG"))
    finally:
        v.close()
    assert _diff_pixels(sat_unk, other) > 2, \
        "非卫星对象的受晒行应为「—」，不能沿用卫星未知文案"


# --------------------------------------------------------------------------
# 4. 后端接入口：字段原样透传到信息卡
# --------------------------------------------------------------------------
def test_backend_sunlit_field_flows_into_info_card():
    v = _view()
    try:
        for val in (True, False, None):
            obj = _obj(val)
            v._build_selected_info(obj)
            info = v._selected_info or {}
            assert info.get("sunlit") is val
            assert info.get("obj_type") == "satellite"
        # 后端 Illumination.status 也原样透传（半影不丢信息）
        v._build_selected_info(_obj(None, status="penumbra"))
        assert (v._selected_info or {}).get("sunlit_status") == "penumbra"
    finally:
        v.close()
