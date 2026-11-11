# SPDX-License-Identifier: MIT
"""
tests/test_spectrum_gesture_edges.py
====================================
VFO 命中与松手清理的两条手势守卫（2026-11-11，sdrpp 对照）。

背景 1（窄带 VFO 拖不动）：`_hit_vfo_at` 先判左右边沿（容差 5 px）再判 body，
没有窄条保护。2.4 MHz span 落在 1000 px 上时 1 px ≈ 2.4 kHz，2.7 kHz USB
通带只有约 1.1 px，两个 ±5 px 边沿把整个 VFO 覆盖，body 永远进不去——用户
想拖中心，实际一直在改带宽。规则：VFO 宽度 ≤ 2×edge 时整体按 body 处理。

背景 2（松手清理散在四个出口）：`mouseReleaseEvent` 各分支自己清
`_is_panning`，将来新增一个提前 `return` 就可能留下 True，表现成「松手后
鼠标一动整屏跟着平移」。规则：release 入口无条件调 `_end_all_gestures()`，
且 VFO 拖拽目标必须在清空前读出。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_gesture_edges.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import spectrum_widget as sw  # noqa: E402

_W, _H = 1000, 600
_SPAN_HZ = 2.4e6


def _make_panel(bw_hz: float, mode: str = "USB"):
    """带真 IQ（命中检测要求 has_data）与一个主听 VFO 的频谱画布。"""
    from mbdsdr_ai.vfo_manager import VfoManager

    panel = sw.SpectrumPanel()
    mgr = VfoManager()
    panel.vfo_manager = mgr
    panel._plot.resize(_W, _H)
    panel.generator.center_freq_hz = 120e6
    panel.generator.sample_rate_hz = _SPAN_HZ
    panel._state.snap_interval = 10_000.0
    iq = (np.random.default_rng(0).standard_normal(16384)
          + 1j * np.random.default_rng(1).standard_normal(16384)
          ).astype(np.complex64)
    panel.update_iq(iq, 120e6, _SPAN_HZ)
    vfo = mgr.add(120e6, bw_hz, mode)
    mgr.set_active_context(vfo, temporary=False)
    return panel, mgr, vfo


def _px_range(panel, vfo):
    return sw._vfo_px_range(
        panel._state, QRectF(0, 0, _W, _H), vfo.center_hz, vfo.bw_hz)


def _mouse(typ, x, y):
    return QMouseEvent(typ, QPointF(x, y), QPointF(x, y),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def _press(plot, x, y=100.0):
    plot.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))


def _move(plot, x, y=100.0):
    plot.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, x, y))


def _release(plot, x, y=100.0):
    plot.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y))


class TestNarrowVfoIsBody:
    """守卫一：窄带 VFO（宽 ≤ 2×edge）整体按 body 命中，拖动只动中心。"""

    def test_narrow_vfo_width_is_below_two_edge(self):
        panel, _mgr, vfo = _make_panel(2_700.0)
        try:
            x0, x1 = _px_range(panel, vfo)
            assert (x1 - x0) <= 2 * 5.0, (
                f"用例前提不成立：{x1 - x0:.2f}px 应 ≤ 10px")
        finally:
            panel.deleteLater()
            app.processEvents()

    def test_narrow_vfo_hits_body_everywhere_in_tolerance(self):
        panel, _mgr, vfo = _make_panel(2_700.0)
        try:
            x0, x1 = _px_range(panel, vfo)
            cx = (x0 + x1) / 2.0
            for x in (cx, cx + 3.0, cx - 3.0):
                assert panel._plot._hit_vfo_at(x) == (vfo, "body"), (
                    f"x={x:.2f} 落在窄带 VFO 上却不是 body（会误改带宽）")
            # 命中footprint 不放大：与旧边沿容差一致，±5px 之外不拦截
            assert panel._plot._hit_vfo_at(x1 + 5.5)[0] is None
        finally:
            panel.deleteLater()
            app.processEvents()

    def test_narrow_vfo_drag_changes_center_not_bandwidth(self):
        panel, _mgr, vfo = _make_panel(2_700.0)
        try:
            bw0, center0 = float(vfo.bw_hz), float(vfo.center_hz)
            moved, bws = [], []
            panel.vfo_moved.connect(lambda _vid, hz: moved.append(hz))
            panel.vfo_bw_changed.connect(lambda _vid, hz: bws.append(hz))
            x0, x1 = _px_range(panel, vfo)
            cx = (x0 + x1) / 2.0
            _press(panel._plot, cx)
            _move(panel._plot, cx + 20.0)
            _release(panel._plot, cx + 20.0)
            assert float(vfo.bw_hz) == bw0, (
                f"窄带拖动改了带宽：{bw0} → {vfo.bw_hz}（应按 body 移动）")
            assert float(vfo.center_hz) != center0, "窄带拖动没有移动中心频率"
            assert moved, "拖动结束没有 emit vfo_moved"
            assert all(b == bw0 for b in bws), bws
        finally:
            panel.deleteLater()
            app.processEvents()

    def test_wide_vfo_right_edge_still_resizes(self):
        panel, _mgr, vfo = _make_panel(200_000.0)
        try:
            bw0 = float(vfo.bw_hz)
            x0, x1 = _px_range(panel, vfo)
            assert (x1 - x0) > 2 * 5.0
            assert panel._plot._hit_vfo_at(x1) == (vfo, "right")
            assert panel._plot._hit_vfo_at((x0 + x1) / 2.0) == (vfo, "body")
            bws = []
            panel.vfo_bw_changed.connect(lambda _vid, hz: bws.append(hz))
            _press(panel._plot, x1)
            _move(panel._plot, x1 + 20.0)
            _release(panel._plot, x1 + 20.0)
            assert float(vfo.bw_hz) > bw0, (
                f"宽带 VFO 右边沿拖拽应展宽：{bw0} → {vfo.bw_hz}")
            assert bws
        finally:
            panel.deleteLater()
            app.processEvents()


class TestReleaseCleansAllGestures:
    """守卫二：release 入口无条件清 `_is_panning`，四个早退分支都不漏。"""

    def test_db_drag_branch(self):
        panel, _mgr, _vfo = _make_panel(200_000.0)
        try:
            plot = panel._plot
            plot._is_panning = True
            plot._db_drag_active = True
            _release(plot, 300.0)
            assert plot._is_panning is False, "dB 轴分支提前 return 漏清 _is_panning"
            assert plot._db_drag_active is False
        finally:
            panel.deleteLater()
            app.processEvents()

    def test_vfo_create_branch(self):
        panel, _mgr, _vfo = _make_panel(200_000.0)
        try:
            plot = panel._plot
            plot._is_panning = True
            plot._vfo_create_mode = True
            panel._state.vfo_create_band = (120e6, 120.05e6)
            _release(plot, 600.0)
            assert plot._is_panning is False, (
                "橡皮筋新建分支提前 return 漏清 _is_panning")
            assert plot._vfo_create_mode is False
            assert panel._state.vfo_create_band is None
        finally:
            panel.deleteLater()
            app.processEvents()

    def test_vfo_drag_branch_reads_target_before_cleanup(self):
        panel, _mgr, vfo = _make_panel(200_000.0)
        try:
            plot = panel._plot
            moved = []
            panel.vfo_moved.connect(lambda _vid, hz: moved.append(hz))
            plot._is_panning = True
            plot._vfo_drag_mode = "move"
            plot._vfo_drag_vfo = vfo
            center_before = float(vfo.center_hz)
            _release(plot, 500.0)
            assert plot._is_panning is False, (
                "VFO 拖拽分支提前 return 漏清 _is_panning")
            assert plot._vfo_drag_vfo is None
            assert moved, "先清 `_vfo_drag_vfo` 再读会丢目标（没有 vfo_moved）"
            assert float(vfo.center_hz) == center_before
        finally:
            panel.deleteLater()
            app.processEvents()

    def test_pan_branch(self):
        panel, _mgr, _vfo = _make_panel(200_000.0)
        try:
            plot = panel._plot
            plot._press_x = 100.0
            plot._is_panning = True
            _release(plot, 100.0)
            assert plot._is_panning is False
        finally:
            panel.deleteLater()
            app.processEvents()

    def test_pan_branch_rf_shift(self):
        panel, _mgr, _vfo = _make_panel(200_000.0)
        try:
            plot = panel._plot
            plot._press_x = 100.0
            plot._rf_shift = True
            plot._is_panning = True
            tuned = []
            panel.freq_changed.connect(tuned.append)
            _release(plot, 200.0)
            assert plot._is_panning is False
            assert plot._rf_shift is False
            assert tuned == [], "rf_shift 视图平移结束不应调谐"
        finally:
            panel.deleteLater()
            app.processEvents()
