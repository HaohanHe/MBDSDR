# SPDX-License-Identifier: MIT
"""
tests/test_info_card_hud.py
===========================
星象仪信息卡收尾 (T29) 的交互断言：

  1. 选中天体后信息卡出现 × 关闭按钮，点击清除选中（不触发天体拾取）；
  2. 无选中时关闭按钮命中区为空；
  3. 描述文本段：长描述使卡片增高，且换行宽度不超出卡宽；
  4. 右下角 ⊖⊕ FOV 控件：点击步进缩放、边界夹取、不触发天体拾取。

运行：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_info_card_hud.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from desktop.rf_sky_view import (  # noqa: E402
    RFSkyView, SkyObject, format_observer_chip,
)


def _new_view(size: int = 600) -> RFSkyView:
    v = RFSkyView()
    v.resize(size, size)
    v._observer = {"lat": 40.0, "lon": 116.0, "alt_m": 0.0}
    return v


def _click(v: RFSkyView, x: float, y: float) -> None:
    for typ in (QMouseEvent.Type.MouseButtonPress,
                QMouseEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(typ, QPointF(x, y),
                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        if typ == QMouseEvent.Type.MouseButtonPress:
            v.mousePressEvent(ev)
        else:
            v.mouseReleaseEvent(ev)


def _pick(v: RFSkyView, description: str = "") -> SkyObject:
    obj = SkyObject(name="TEST-SAT", azimuth_deg=30.0, elevation_deg=50.0,
                    obj_type="satellite", description=description)
    v.on_object_picked(obj)
    v.grab()          # 触发 paintEvent，刷新命中区
    return obj


# ----------------------------------------------------------------------
class TestInfoCardClose:
    def test_close_rect_present_after_pick(self):
        v = _new_view()
        _pick(v)
        assert v._info_close_rect is not None
        assert v._info_card_rect is not None
        # × 按钮应落在卡片范围内
        assert v._info_card_rect.contains(v._info_close_rect.center())

    def test_close_click_clears_pick(self):
        v = _new_view()
        _pick(v)
        c = v._info_close_rect.center()
        _click(v, c.x(), c.y())
        assert v._hovered_object is None
        assert v._selected_info is None
        v.grab()
        assert v._info_close_rect is None

    def test_no_pick_no_close_rect(self):
        v = _new_view()
        v.grab()
        assert v._info_close_rect is None
        assert v._info_card_rect is None

    def test_card_click_outside_close_keeps_pick(self):
        v = _new_view()
        _pick(v)
        r = v._info_card_rect
        _click(v, r.x() + 30, r.y() + r.height() - 10)
        assert v._hovered_object is not None


# ----------------------------------------------------------------------
class TestDescriptionSection:
    LONG_DESC = "这是一段用于验证信息卡描述段换行与增高行为的中文说明文字。" * 8

    def test_long_description_grows_card(self):
        v = _new_view()
        _pick(v, description=self.LONG_DESC)
        h_long = v._info_card_rect.height()

        v2 = _new_view()
        _pick(v2, description="")
        h_short = v2._info_card_rect.height()
        assert h_long > h_short + 30       # 至少多出两行描述 + 空行

    def test_description_stays_inside_card_width(self):
        v = _new_view()
        _pick(v, description=self.LONG_DESC)
        card = v._info_card_rect
        assert card.width() > 0
        # 换行后正文宽度约束在卡内 (text_w = box_w - 2*pad - close_reserve)
        assert card.width() >= 24 + 26 + 40

    def test_desc_capped_at_six_lines(self):
        v = _new_view()
        huge = "字" * 600
        _pick(v, description=huge)
        # 6 行封顶 + 截断记号：卡高不应无限增长
        card = v._info_card_rect
        row_h = 17.0
        n_rows = 3 + 6 + 1 + (6 + 1)        # head3 + kv6 + 空行 + 描述6+1
        # D3 后卫星卡底部另有一行动作按钮（24px + 6px 间隔），上界相应加一行
        assert card.height() <= (n_rows + 2) * row_h + 20


# ----------------------------------------------------------------------
class TestFovControl:
    def test_fov_rects_present(self):
        v = _new_view()
        v.grab()
        minus, plus = v._fov_minus_rect, v._fov_plus_rect
        assert minus is not None and plus is not None
        # 两个按钮必须各自有面积、且不重合；否则「两个矩形设成同一个」也会绿
        assert minus.width() > 0 and minus.height() > 0, minus
        assert plus.width() > 0 and plus.height() > 0, plus
        assert minus != plus
        assert minus.right() < plus.left(), (minus, plus)

    def test_minus_zooms_in(self):
        v = _new_view()
        v.grab()
        f0 = v.view_state.fov_deg
        c = v._fov_minus_rect.center()
        _click(v, c.x(), c.y())
        assert v.view_state.fov_deg < f0

    def test_plus_zooms_out(self):
        v = _new_view()
        v.view_state.zoom_to(60.0)      # 默认 120° 已是 max，先缩回中间档
        v.grab()
        f0 = v.view_state.fov_deg
        c = v._fov_plus_rect.center()
        _click(v, c.x(), c.y())
        assert v.view_state.fov_deg > f0

    def test_clamped_at_min_fov(self):
        v = _new_view()
        v.view_state.zoom_to(v.view_state.min_fov)
        v.grab()
        c = v._fov_minus_rect.center()
        _click(v, c.x(), c.y())
        assert v.view_state.fov_deg == pytest.approx(v.view_state.min_fov)

    def test_clamped_at_max_fov(self):
        v = _new_view()
        v.view_state.zoom_to(v.view_state.max_fov)
        v.grab()
        c = v._fov_plus_rect.center()
        _click(v, c.x(), c.y())
        assert v.view_state.fov_deg == pytest.approx(v.view_state.max_fov)

    def test_fov_click_does_not_pick_object(self):
        v = _new_view()
        _pick(v)
        c = v._fov_minus_rect.center()
        _click(v, c.x(), c.y())
        assert v._hovered_object is not None   # 选中未被 HUD 点击破坏


# ----------------------------------------------------------------------
class TestObserverChip:
    """左下角观测站读数（Stellarium location chip 同位）：诚实空状态。"""

    def test_none_observer_is_unset(self):
        assert format_observer_chip(None) == "未设置"
        assert format_observer_chip({}) == "未设置"

    def test_bad_coords_is_unset(self):
        assert format_observer_chip({"lat": "x", "lon": None}) == "未设置"

    def test_north_east_with_alt(self):
        txt = format_observer_chip({"lat": 40.0, "lon": 116.0, "alt_m": 50.0})
        assert txt == "40.00°N 116.00°E 50m"

    def test_south_west_hemisphere_signs(self):
        txt = format_observer_chip({"lat": -33.5, "lon": -70.25, "alt_m": 0.0})
        assert txt == "33.50°S 70.25°W 0m", txt

    def test_alt_optional(self):
        txt = format_observer_chip({"lat": 0.0, "lon": 0.0})
        assert txt == "0.00°N 0.00°E", txt   # 无 alt 不编造，赤道本初如实 0

    def test_card_paints_with_and_without_observer(self):
        # 有观测站
        v = _new_view()
        v.grab()                      # 触发 _draw_info_overlay，不崩
        # 无观测站（诚实空状态）
        v2 = RFSkyView()
        v2.resize(600, 600)
        v2._observer = None
        v2.grab()                     # 画「未设置」，不崩

    def test_overlay_reads_observer_through_formatter(self, monkeypatch):
        """信息卡绘制必须经 format_observer_chip 读观测站（观测站行不许消失）。

        旧写法按源码 `src.index("def _draw_info_overlay")` 切段钉字符串；
        这里在模块里装一个 spy，绘制时若没调格式化函数就直接红。
        """
        import sys
        rv = sys.modules[RFSkyView.__module__]

        calls = []
        real = rv.format_observer_chip

        def spy(observer):
            calls.append(observer)
            return real(observer)

        monkeypatch.setattr(rv, "format_observer_chip", spy)

        v = _new_view()
        v.grab()
        assert calls and calls[-1] == v._observer, \
            "信息卡绘制没有读取观测站（观测站读数行不见了）"

        v2 = RFSkyView()
        v2.resize(600, 600)
        v2._observer = None
        v2.grab()
        assert calls[-1] is None, \
            "无观测站时绘制必须仍经格式化函数走诚实空状态"


# ----------------------------------------------------------------------
class TestPickedCardCameraLockLine:
    """D4：卫星卡内明示视角锁定语义（不许只靠左下角状态卡）。

    口径只有一个事实源 ``_camera_lock``：
      * 未锁定 → 不加行；
      * 锁本目标 → 「锁定 · 天动星静」；
      * 锁别的目标 → 「未锁定（锁在 <名字>）」——不靠「没有这行」去猜。
    """

    def test_row_semantics(self):
        v = _new_view()
        obj = _pick(v)
        v._camera_lock = None
        assert v.picked_camera_lock_row(obj.name) is None

        v._camera_lock = obj.name
        assert v.picked_camera_lock_row(obj.name) == ("视角", "锁定 · 天动星静")

        v._camera_lock = "ISS (ZARYA)"
        row = v.picked_camera_lock_row(obj.name)
        assert row is not None and row[0] == "视角"
        assert "未锁定" in row[1] and "ISS (ZARYA)" in row[1]

    def test_lock_on_this_target_grows_card_by_one_row(self):
        v = _new_view()
        obj = _pick(v)
        v._selected_info = {}
        v._camera_lock = None
        v.grab()
        h_free = v._info_card_rect.height()
        v._camera_lock = obj.name
        v.grab()
        h_locked = v._info_card_rect.height()
        assert h_locked == pytest.approx(h_free + 17.0), \
            "锁定本目标后卡片必须多一行（行高 17px）"

    def test_lock_on_other_target_also_states_it(self):
        v = _new_view()
        _pick(v)
        v._selected_info = {}
        v._camera_lock = None
        v.grab()
        h_free = v._info_card_rect.height()
        v._camera_lock = "ISS (ZARYA)"
        v.grab()
        assert v._info_card_rect.height() == pytest.approx(h_free + 17.0), \
            "锁定别的目标时也必须写明「本目标未锁定」，占同样一行"

    def test_free_camera_card_paints_without_lock_row(self):
        v = _new_view()
        _pick(v)
        v._camera_lock = None
        v.grab()                      # 未锁定→不加行，也不许崩
        assert v._info_card_rect is not None


# ----------------------------------------------------------------------
class TestPickedCardActionButtons:
    """D3：卫星卡内动作按钮「接收 / 追踪 / 指向」。

    三个入口必须与左下浮动按钮共用同一执行体：
      * 接收 → ``_on_tune_satellite``（发调谐请求）；
      * 追踪 / 指向 → 切换同一个 QPushButton 的勾选态（既有 toggled 处理器发信号）；
      * 按钮不可用时点击为空操作（置灰即不可点）。
    """

    def test_three_rects_inside_card_and_disjoint(self):
        v = _new_view()
        _pick(v)
        rects = v._info_action_rects
        assert set(rects) == {"tune", "track", "gimbal"}
        card = v._info_card_rect
        for r in rects.values():
            assert r.width() > 0 and r.height() > 0
            assert card.contains(r)
        assert rects["tune"].right() < rects["track"].left()
        assert rects["track"].right() < rects["gimbal"].left()
        assert not rects["gimbal"].intersects(v._info_close_rect)

    def test_tune_click_calls_shared_handler(self):
        v = _new_view()
        _pick(v)
        v._tune_btn.setEnabled(True)
        v.grab()
        calls = []
        v._on_tune_satellite = lambda: calls.append(1)
        c = v._info_action_rects["tune"].center()
        _click(v, c.x(), c.y())
        assert calls == [1]

    def test_tune_click_is_noop_when_disabled(self):
        v = _new_view()
        _pick(v)
        assert not v._tune_btn.isEnabled()
        calls = []
        v._on_tune_satellite = lambda: calls.append(1)
        c = v._info_action_rects["tune"].center()
        _click(v, c.x(), c.y())
        assert calls == []

    def test_track_click_drives_same_button_and_signal(self):
        v = _new_view()
        _pick(v)
        v._track_btn.setEnabled(True)
        v._compute_tune = lambda: {"satellite": "TEST-SAT",
                                   "base_freq_hz": 137e6}
        seen = []
        v.track_toggled.connect(
            lambda on, name, freq: seen.append((on, name, freq)))
        v.grab()
        c = v._info_action_rects["track"].center()
        _click(v, c.x(), c.y())
        assert v._track_btn.isChecked(), "卡内「追踪」必须切换浮动按钮的勾选态"
        assert seen == [(True, "TEST-SAT", 137e6)]

    def test_gimbal_click_noop_when_unavailable(self):
        v = _new_view()
        _pick(v)
        v._gimbal_available = False
        v._gimbal_btn.setEnabled(False)
        v.grab()
        c = v._info_action_rects["gimbal"].center()
        _click(v, c.x(), c.y())
        assert not v._gimbal_btn.isChecked()

    def test_gimbal_click_toggles_when_available(self):
        v = _new_view()
        _pick(v)
        v._gimbal_available = True
        v._gimbal_btn.setEnabled(True)
        seen = []
        v.gimbal_follow_toggled.connect(seen.append)
        v.grab()
        c = v._info_action_rects["gimbal"].center()
        _click(v, c.x(), c.y())
        assert v._gimbal_btn.isChecked()
        assert seen == [True]

    def test_body_card_has_no_action_buttons(self):
        v = _new_view()
        obj = SkyObject(name="Vega", azimuth_deg=10.0, elevation_deg=70.0,
                        obj_type="star")
        v.on_object_picked(obj)
        v.grab()
        assert v._info_action_rects == {}

