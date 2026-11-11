# SPDX-License-Identifier: MIT
"""
tests/test_spectrum_slider_overflow.py
======================================
「显示」弹出层三条反证的回归钉子（2026-11-11）。

背景：三根量程滑杆（缩放 / 上限 / 下限）原来常驻在面板右侧竖排，白占 30px
宽，还把面板最小高顶到 247px（旧 fixed 90/80/80 时是 347px）；窄档 331px 下
绘图区被挤、303 档还会裁掉末根滑杆。处置见
docs/research/2026-11-11-gap-spectrum.md §3.4：收进工具栏「显示」弹出层，
工具栏只保留「更多」+「显示」两个入口（合计约 122px）。

守卫：
1. 331px 窄档下面板接受 331×303、工具栏所有控件完整落在工具栏矩形内；
   缩到面板最小宽时「工具栏实际宽 == 工具栏 minimumSizeHint 宽」（不裁切）。
2. 弹出层的值改动必须真作用到绘制：喂真 IQ 后改 dB 量程，FFT 画布像素
   与逐行分布都要变化（不是只有滑杆数值变了）。
3. 操作步数：InstantPopup 一点即弹、无二级子菜单、三根滑杆首屏可见
   （点 1 次 + 拖 1 次 = 2 步；旧常驻滑杆是 1 步，多 1 步可接受）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_slider_overflow.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import spectrum_widget  # noqa: E402
from spectrum_widget import SpectrumPanel  # noqa: E402

_SLIDERS = ("_span_slider", "_db_max_slider", "_db_min_slider")


def _force_layout(panel: SpectrumPanel) -> None:
    """让 Qt 重新计算最小尺寸缓存（只 resize 不会刷新 minimumSizeHint）。"""
    lay = panel.layout()
    if lay is not None:
        lay.invalidate()
        lay.activate()
    app.processEvents()


def _make_panel(width=331, height=303) -> SpectrumPanel:
    panel = SpectrumPanel()
    panel.resize(width, height)
    panel.show()
    _force_layout(panel)
    app.processEvents()
    return panel


class TestToolbarFits331:
    """反证一：窄档不裁切 + minimumSizeHint 与实际尺寸相等。"""

    def test_panel_accepts_331x303_and_buttons_inside(self):
        panel = _make_panel()
        try:
            assert panel.width() == 331, panel.width()
            assert panel.height() == 303, (
                f"工具栏/滑杆不得再把面板顶高，实际 {panel.height()}")
            tb = panel._toolbar
            assert tb.width() == 331, tb.width()
            for name in ("_more_btn", "_display_btn"):
                btn = getattr(panel, name)
                assert btn.isVisible(), f"{name} 不可见"
                left = btn.mapTo(tb, QPoint(0, 0)).x()
                right = btn.mapTo(tb, QPoint(btn.width(), 0)).x()
                assert left >= 0, f"{name} 左边越界：{left}"
                assert right <= tb.width(), (
                    f"{name} 右边越界：{right} > 工具栏宽 {tb.width()}（被裁）")
                assert btn.width() > 0 and btn.height() > 0
        finally:
            panel.deleteLater()

    def test_toolbar_entry_is_about_120px(self):
        panel = _make_panel()
        try:
            hint = panel._toolbar.minimumSizeHint().width()
            assert 110 <= hint <= 135, (
                f"「更多」+「显示」两个入口合计应约 120px，实际 {hint}px")
        finally:
            panel.deleteLater()

    def test_minimum_hint_equals_actual_at_floor(self):
        panel = _make_panel()
        try:
            floor_w = panel.minimumSizeHint().width()
            panel.resize(floor_w, 303)
            _force_layout(panel)
            app.processEvents()
            assert panel.width() == floor_w, (
                f"面板最小宽 {floor_w} 时实际 {panel.width()}（被顶回）")
            tb = panel._toolbar
            assert tb.width() == tb.minimumSizeHint().width(), (
                f"缩到最小宽时工具栏实际 {tb.width()} 应等于 minimumSizeHint "
                f"{tb.minimumSizeHint().width()}")
        finally:
            panel.deleteLater()

    def test_slider_column_left_the_panel_layout(self):
        panel = _make_panel()
        try:
            assert panel._db_slider_col.parentWidget() is not panel, (
                "滑杆列必须挂在「显示」弹层，不得常驻占面板宽/高")
            assert panel.minimumSizeHint().height() <= 303
        finally:
            panel.deleteLater()


class TestPopupValuesStillDrivePainting:
    """反证二：弹出层的值改动后仍真实作用于绘制。"""

    @staticmethod
    def _feed(panel: SpectrumPanel, frames: int = 4) -> None:
        gen = panel.generator
        n = gen.fft_size * 4
        t = np.arange(n, dtype=np.float32)
        iq = np.exp(2j * np.pi * 0.13 * t).astype(np.complex64)
        for _ in range(frames):
            gen.push_iq(iq, 100e6, 1.024e6)
        app.processEvents()

    @staticmethod
    def _grab(panel: SpectrumPanel) -> np.ndarray:
        panel._fft_plot.update()
        app.processEvents()
        img = panel._fft_plot.grab().toImage().convertToFormat(
            QImage.Format.Format_RGB32)
        w, h = img.width(), img.height()
        return np.frombuffer(img.constBits(), dtype=np.uint8).reshape(
            h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4).copy()

    @staticmethod
    def _row_profile(frame: np.ndarray) -> np.ndarray:
        """逐行「非背景像素」计数：谱迹/填充相对画布背景的位置分布。"""
        bg = frame[0, 0, :3].astype(int)
        diff = np.abs(frame[:, :, :3].astype(int) - bg).sum(axis=2)
        return (diff > 12).sum(axis=1)

    def test_db_range_change_repaints_trace_distribution(self):
        panel = _make_panel()
        try:
            self._feed(panel)
            assert panel.generator.has_data(), "先要有真 IQ 才能断言绘制"
            panel.apply_db_range(-150.0, -20.0)
            wide = self._grab(panel)
            panel.apply_db_range(-60.0, -10.0)
            narrow = self._grab(panel)
            assert wide.shape == narrow.shape, (wide.shape, narrow.shape)
            assert not np.array_equal(wide, narrow), (
                "改 dB 范围后 FFT 画布像素完全没变：弹层只改了控件值")
            assert not np.array_equal(self._row_profile(wide),
                                      self._row_profile(narrow)), (
                "逐行像素分布没变：量程没有真正作用到绘制")
        finally:
            panel.deleteLater()

    def test_slider_drag_reaches_state_and_painting(self):
        panel = _make_panel()
        try:
            self._feed(panel)
            panel.apply_db_range(-150.0, -20.0)
            before = self._grab(panel)
            # 用户动作：拖动「下限」→ 滑杆 → 唯一状态源 → 重绘
            panel._db_min_slider.setValue(-40)
            assert panel._state.db_min == -40.0, panel._state.db_min
            after = self._grab(panel)
            assert not np.array_equal(before, after), (
                "滑杆值进了状态却没改画面（假控件）")
        finally:
            panel.deleteLater()


class TestPopupCostsOneExtraStep:
    """反证三：点开 1 步 + 拖动 1 步 = 2 步，不是 3 步。"""

    def test_display_is_instant_popup_with_flat_menu(self):
        panel = _make_panel()
        try:
            btn = panel._display_btn
            assert btn.text() == "显示"
            assert btn.popupMode() == QToolButton.InstantPopup, (
                "必须 InstantPopup：点 1 次即弹出，再拖 1 次生效（共 2 步）")
            menu = btn.menu()
            assert menu is panel._display_menu
            acts = menu.actions()
            assert len(acts) == 1, "弹层只该有一个承载控件，避免多一步导航"
            assert all(a.menu() is None for a in acts), (
                "不许有二级子菜单（会变成 3 步）")
        finally:
            panel.deleteLater()

    def test_three_sliders_visible_on_first_popup(self):
        panel = _make_panel()
        try:
            for name in _SLIDERS:
                assert getattr(panel, name).isVisible() is False, name
            btn = panel._display_btn
            panel._display_menu.popup(
                btn.mapToGlobal(QPoint(0, btn.height())))
            app.processEvents()
            try:
                for name in _SLIDERS:
                    slider = getattr(panel, name)
                    assert slider.isVisible(), f"{name} 打开弹层后仍不可见（多一步）"
                    assert slider.orientation() == Qt.Horizontal
                    assert slider.minimumWidth() == spectrum_widget._SLIDER_MIN_W
            finally:
                panel._display_menu.close()
                app.processEvents()
        finally:
            panel.deleteLater()
