# SPDX-License-Identifier: MIT
"""
tests/test_gestures_hotkeys.py
==============================
验证 P0-3/4 手势与键盘批：

  1. 滚轮悬停 VFO 本体 = 整体搬移通带（gqrx Shift+wheel 语义），硬件中心不动；
  2. Ctrl+滚轮悬停 VFO = 对称展宽/收窄带宽（gqrx Ctrl+wheel），VFO 中心不动；
  3. 滚轮悬停 VFO 边沿 = 非对称调带宽（既有行为保持）；
  4. Ctrl+滚轮悬停空白 = 锚点缩放（既有行为保持）；
  5. 键盘 , / . = 按模式预设表步进滤波宽窄档（无主听 VFO 时诚实提示不崩）；
  6. 单键 A/N/F/W/S/C 切解调发 mode_hotkey_requested（S=LSB，Shift+S=USB）。

运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gestures_hotkeys.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt, QPointF, QPoint  # noqa: E402
from PySide6.QtGui import QWheelEvent, QKeyEvent  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _make_panel_with_vfo(mode="NFM", bw=100_000.0):
    """带 fake IQ（VFO 命中检测要求 has_data）与一个主听 VFO 的谱面。

    默认带宽 100 kHz：12.5 kHz 在 2.4 MHz span 下只有 ~4px，整个 VFO 都在
    边沿容差内，本体手势测试需要更宽的 VFO 才有 body 区可悬停。
    """
    import numpy as np
    from spectrum_widget import SpectrumPanel
    from mbdsdr_ai.vfo_manager import VfoManager
    panel = SpectrumPanel()
    mgr = VfoManager()
    panel.vfo_manager = mgr
    panel._plot.resize(800, 600)
    panel.generator.center_freq_hz = 120e6
    panel.generator.sample_rate_hz = 2.4e6
    panel._state.snap_interval = 10_000.0
    iq = (np.random.default_rng(0).standard_normal(16384)
          + 1j * np.random.default_rng(1).standard_normal(16384)
          ).astype(np.complex64)
    panel.update_iq(iq, 120e6, 2.4e6)
    vfo = mgr.add(120e6, bw, mode)
    mgr.set_active_context(vfo, temporary=False)
    return panel, mgr, vfo


def _wheel(widget, x, y, dy, modifiers=Qt.KeyboardModifier.NoModifier):
    ev = QWheelEvent(QPointF(x, y), widget.mapToGlobal(QPointF(x, y)),
                     QPoint(0, 0), QPoint(0, dy),
                     Qt.MouseButton.NoButton, modifiers,
                     Qt.ScrollPhase.NoScrollPhase, False)
    widget.wheelEvent(ev)


def _key(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    ev = QKeyEvent(QKeyEvent.Type.KeyPress, key, modifiers, "")
    widget.keyPressEvent(ev)


class TestVfoWheelGestures:
    def test_body_wheel_moves_passband(self):
        panel, mgr, vfo = _make_panel_with_vfo()
        moved, tuned = [], []
        panel.vfo_moved.connect(lambda vid, hz: moved.append(hz))
        panel.freq_changed.connect(tuned.append)

        _wheel(panel._plot, 400, 100, 120)   # 正中央 = VFO 本体
        assert vfo.center_hz == 120e6 + 10_000.0
        assert moved == [120e6 + 10_000.0]
        assert panel.generator.center_freq_hz == 120e6   # 硬件中心不动
        assert tuned == []

    def test_ctrl_wheel_symmetric_bandwidth(self):
        panel, mgr, vfo = _make_panel_with_vfo()
        bws = []
        panel.vfo_bw_changed.connect(lambda vid, hz: bws.append(hz))

        _wheel(panel._plot, 400, 100, 120, Qt.KeyboardModifier.ControlModifier)
        assert vfo.bw_hz == 100_000.0 + 10_000.0
        assert vfo.center_hz == 120e6                    # 对称：中心不动
        assert bws == [110_000.0]

    def test_edge_wheel_asymmetric_bandwidth(self):
        from spectrum_widget import _vfo_px_range
        panel, mgr, vfo = _make_panel_with_vfo()
        state = panel._state
        rect = QRectF = None
        from PySide6.QtCore import QRectF as _QRectF
        from PySide6.QtGui import QPainter
        x0, x1 = _vfo_px_range(state, _QRectF(0, 0, 800, 300),
                               vfo.center_hz, vfo.bw_hz)
        bws = []
        panel.vfo_bw_changed.connect(lambda vid, hz: bws.append(hz))
        _wheel(panel._plot, x1, 100, 120)   # 右边沿
        assert vfo.bw_hz == 100_000.0 + 10_000.0
        assert bws

    def test_ctrl_wheel_empty_spectrum_zooms(self):
        import numpy as np
        from spectrum_widget import SpectrumPanel
        panel = SpectrumPanel()
        panel._plot.resize(800, 600)
        panel.generator.center_freq_hz = 120e6
        panel.generator.sample_rate_hz = 2.4e6
        iq = (np.random.default_rng(0).standard_normal(16384)
              + 1j * np.random.default_rng(1).standard_normal(16384)
              ).astype(np.complex64)
        panel.update_iq(iq, 120e6, 2.4e6)
        # 无 VFO → Ctrl+滚轮仍是锚点缩放
        _wheel(panel._plot, 400, 100, 120, Qt.KeyboardModifier.ControlModifier)
        assert panel.generator.sample_rate_hz != 2.4e6


class TestFilterPresetKeys:
    def test_comma_period_steps_presets(self):
        panel, mgr, vfo = _make_panel_with_vfo(mode="NFM", bw=12_500.0)
        bws = []
        panel.vfo_bw_changed.connect(lambda vid, hz: bws.append(hz))
        _key(panel._plot, Qt.Key_Period)     # 正常 -> 宽
        assert vfo.bw_hz == 25_000.0
        _key(panel._plot, Qt.Key_Comma)      # 宽 -> 正常
        _key(panel._plot, Qt.Key_Comma)      # 正常 -> 窄
        assert vfo.bw_hz == 7_500.0
        assert bws == [25_000.0, 12_500.0, 7_500.0]

    def test_off_table_snaps_first(self):
        panel, mgr, vfo = _make_panel_with_vfo(mode="AM", bw=6_000.0)
        vfo.bw_hz = 5_100.0   # 不在 AM 预设档上
        panel.vfo_bw_changed.connect(lambda vid, hz: None)
        _key(panel._plot, Qt.Key_Period)
        assert vfo.bw_hz == 6_000.0   # 先吸附最近档，不跨档

    def test_no_vfo_honest_noop(self):
        import numpy as np
        from spectrum_widget import SpectrumPanel
        panel = SpectrumPanel()
        panel._plot.resize(800, 600)
        panel.generator.center_freq_hz = 120e6
        iq = (np.random.default_rng(0).standard_normal(16384)
              + 1j * np.random.default_rng(1).standard_normal(16384)
              ).astype(np.complex64)
        panel.update_iq(iq, 120e6, 2.4e6)
        _key(panel._plot, Qt.Key_Period)   # 不崩即可


class TestModeHotkeys:
    def test_letter_keys_emit(self):
        panel, _mgr, _vfo = _make_panel_with_vfo()
        got = []
        panel.mode_hotkey_requested.connect(got.append)
        _key(panel._plot, Qt.Key_A)
        _key(panel._plot, Qt.Key_N)
        _key(panel._plot, Qt.Key_W)
        assert got == ["AM", "NFM", "WFM"]

    def test_s_key_lsb_usb(self):
        panel, _mgr, _vfo = _make_panel_with_vfo()
        got = []
        panel.mode_hotkey_requested.connect(got.append)
        _key(panel._plot, Qt.Key_S)
        _key(panel._plot, Qt.Key_S, Qt.KeyboardModifier.ShiftModifier)
        assert got == ["LSB", "USB"]

    def test_unrelated_key_passes_through(self):
        panel, _mgr, _vfo = _make_panel_with_vfo()
        got = []
        panel.mode_hotkey_requested.connect(got.append)
        _key(panel._plot, Qt.Key_Z)
        assert got == []
