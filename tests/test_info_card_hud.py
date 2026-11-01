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

from desktop.rf_sky_view import RFSkyView, SkyObject  # noqa: E402


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
        assert card.height() <= (n_rows + 1) * row_h + 20


# ----------------------------------------------------------------------
class TestFovControl:
    def test_fov_rects_present(self):
        v = _new_view()
        v.grab()
        assert v._fov_minus_rect is not None
        assert v._fov_plus_rect is not None

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
