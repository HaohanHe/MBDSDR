# SPDX-License-Identifier: MIT
"""
tests/test_sky_layer_toggles.py
===============================
验证天空模式控制条的图层开关（2026-11-03 从 Stellarium Web 录屏学到：
右缘 layer toggles）。顺着本视图既有底部控制条 seam 落，把此前只能
编程调用、用户够不着的 set_show_* 能力接到可见按钮上：

  1. 四个图层按钮存在，初值与视图默认一致（方位网格/罗盘/轨迹 开，赤道网格 关）；
  2. 点按钮真翻视图标志位（按钮 ↔ sky_view 状态双向一致）；
  3. 新增 set_show_eqgrid setter 生效（此前赤道网格无开关）；
  4. 热力图不在此手控（数据驱动，避免与 set_heatmap 打架）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_layer_toggles.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView, RFSkyViewPanel  # noqa: E402

# 按钮标签 -> (视图属性, 默认值)
_EXPECTED = {
    "方位网格": ("_show_grid", True),
    "赤道网格": ("_show_eqgrid", False),
    "罗盘": ("_show_compass", True),
    "轨迹": ("_show_trajectories", True),
}


def _panel():
    p = RFSkyViewPanel()
    p.resize(800, 600)
    return p


class TestLayerButtonsExist:
    def test_four_layer_buttons_present(self):
        p = _panel()
        assert hasattr(p, "_layer_buttons"), "控制条应建图层按钮组"
        for label in _EXPECTED:
            assert label in p._layer_buttons, f"缺图层按钮 {label}"
        p.close()

    def test_initial_checked_matches_view_defaults(self):
        p = _panel()
        for label, (attr, default) in _EXPECTED.items():
            btn = p._layer_buttons[label]
            assert btn.isCheckable(), f"{label} 应可勾选"
            assert btn.isChecked() == default, \
                f"{label} 初值应 {default}，实际 {btn.isChecked()}"
            assert getattr(p.sky_view, attr) == default, \
                f"视图 {attr} 默认应 {default}"
        p.close()

    def test_heatmap_not_manual(self):
        p = _panel()
        assert "热力图" not in p._layer_buttons, \
            "热力图数据驱动，不应给手动开关（会与 set_heatmap 打架）"
        p.close()


class TestLayerToggleWiring:
    def test_click_flips_view_flag(self):
        p = _panel()
        for label, (attr, default) in _EXPECTED.items():
            btn = p._layer_buttons[label]
            btn.click()                       # 勾选态翻转 + 发 clicked
            assert btn.isChecked() == (not default)
            assert getattr(p.sky_view, attr) == (not default), \
                f"点 {label} 后 sky_view.{attr} 应翻成 {not default}"
            btn.click()                       # 再点回默认
            assert getattr(p.sky_view, attr) == default
        p.close()

    def test_button_state_tracks_view(self):
        p = _panel()
        btn = p._layer_buttons["罗盘"]
        btn.click()
        assert p.sky_view._show_compass is False
        assert btn.isChecked() is False
        p.close()


class TestEqGridSetter:
    def test_set_show_eqgrid_exists_and_works(self):
        v = RFSkyView()
        assert hasattr(v, "set_show_eqgrid"), "应新增赤道网格 setter"
        assert v._show_eqgrid is False
        v.set_show_eqgrid(True)
        assert v._show_eqgrid is True
        v.set_show_eqgrid(False)
        assert v._show_eqgrid is False
        v.close()

    def test_panel_repaints_with_eqgrid_on(self):
        p = _panel()
        p._layer_buttons["赤道网格"].click()   # 打开赤道网格
        assert p.sky_view._show_eqgrid is True
        p.sky_view.grab()                      # 触发重绘，不崩
        p.close()
