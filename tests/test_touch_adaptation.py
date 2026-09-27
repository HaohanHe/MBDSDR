#!/usr/bin/env python3
"""
MBDSDR 桌面端触屏/Surface 适配 offscreen 测试
=================================================

覆盖触控集成的五个交付点（全部 QT_QPA_PLATFORM=offscreen，无硬件/无显示器）：

  1. test_touch_manager_auto_detect  — TouchManager 单例 / set_override /
                                       is_touch_mode 自动检测与手动覆盖逻辑；
  2. test_touch_helpers_apply_target  — apply_touch_target 在强制触屏模式下把
                                       控件最小高度提到 ≥44px，鼠标模式不改动；
  3. test_pinch_zoom_mixin            — PinchZoomMixin init 后 pinch_scale_changed /
                                       pan_changed 信号可连接、可发射；
  4. test_touch_mode_settings_roundtrip — settings.DEFAULTS 含 touch_mode 且可读写；
  5. test_main_window_touch_init      — offscreen 下 MainWindow 创建后 touch_manager
                                       可用、splitter 在鼠标模式保持 tokens 4px、
                                       强制触屏后切到 10px（sdr_connect 失败是已知基线）。

红线：不造假触控事件；离屏无 QTouchDevice 时自动检测返回 False，不假装支持。
运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_touch_adaptation.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def _reset_touch_override():
    """每个用例前后复位 TouchManager 手动覆盖，避免跨用例污染单例。"""
    from touch_manager import touch_manager
    tm = touch_manager()
    saved = tm._mode_override
    tm.set_override(None)
    yield
    tm.set_override(saved)


# --------------------------------------------------------------------------- #
# 1. TouchManager 单例与模式逻辑
# --------------------------------------------------------------------------- #
class TestTouchManager:
    def test_singleton_identity(self):
        from touch_manager import touch_manager
        assert touch_manager() is touch_manager()

    def test_set_override_forces_mode(self):
        from touch_manager import touch_manager
        tm = touch_manager()
        tm.set_override(True)
        assert tm.is_touch_mode() is True
        tm.set_override(False)
        assert tm.is_touch_mode() is False
        tm.set_override(None)
        # 离屏无触屏设备 → 自动检测为 False
        assert tm.is_touch_mode() is False

    def test_set_from_settings(self):
        from touch_manager import touch_manager
        tm = touch_manager()
        tm.set_from_settings("on")
        assert tm.is_touch_mode() is True
        tm.set_from_settings("off")
        assert tm.is_touch_mode() is False
        tm.set_from_settings("auto")
        # auto → 自动检测（离屏为 False）
        assert tm.is_touch_mode() is False
        tm.set_override(None)

    def test_dimensions(self):
        from touch_manager import touch_manager
        tm = touch_manager()
        tm.set_override(True)
        assert tm.target_min_height() >= 44
        assert tm.splitter_handle() == 10
        assert tm.scrollbar_width() == 14
        assert tm.list_row_height() >= 40
        tm.set_override(False)
        assert tm.target_min_height() == 0
        assert tm.splitter_handle() == 4
        assert tm.scrollbar_width() == 10
        tm.set_override(None)

    def test_touch_mode_changed_signal_emits(self):
        from touch_manager import touch_manager
        tm = touch_manager()
        received = []
        tm.touch_mode_changed.connect(lambda on: received.append(on))
        tm.set_override(True)
        tm.set_override(False)
        assert received == [True, False]
        tm.set_override(None)


# --------------------------------------------------------------------------- #
# 2. apply_touch_target
# --------------------------------------------------------------------------- #
class TestApplyTouchTarget:
    def test_forced_touch_sets_min_height(self, qapp):
        from touch_manager import touch_manager
        from touch_helpers import apply_touch_target
        tm = touch_manager()
        btn = QPushButton("x")
        btn.setMinimumHeight(28)
        tm.set_override(True)
        apply_touch_target(btn)
        assert btn.minimumHeight() >= 44
        tm.set_override(None)

    def test_mouse_mode_noop(self, qapp):
        from touch_manager import touch_manager
        from touch_helpers import apply_touch_target
        tm = touch_manager()
        tm.set_override(False)
        btn = QPushButton("y")
        btn.setMinimumHeight(28)
        apply_touch_target(btn)
        # 鼠标模式不修改已有最小高度
        assert btn.minimumHeight() == 28
        tm.set_override(None)


# --------------------------------------------------------------------------- #
# 3. PinchZoomMixin
# --------------------------------------------------------------------------- #
class TestPinchZoomMixin:
    def test_signals_connectable(self, qapp):
        from PySide6.QtWidgets import QWidget
        from PySide6.QtCore import QPointF
        from touch_helpers import PinchZoomMixin

        class _Doodle(PinchZoomMixin, QWidget):
            pass

        w = _Doodle()
        w.init_pinch_zoom()
        scales = []
        pans = []
        w.pinch_scale_changed.connect(lambda s, c: scales.append(s))
        w.pan_changed.connect(lambda dx, dy: pans.append((dx, dy)))
        # 直接发射信号验证可连接（离屏不产生真实手势）
        w.pinch_scale_changed.emit(1.2, QPointF(10.0, 20.0))
        w.pan_changed.emit(5.0, -3.0)
        assert scales == [1.2]
        assert pans == [(5.0, -3.0)]

    def test_spectrum_curve_widget_has_mixin(self):
        from spectrum_widget import SpectrumCurveWidget
        from touch_helpers import PinchZoomMixin
        assert issubclass(SpectrumCurveWidget, PinchZoomMixin)

    def test_map_canvas_has_mixin(self):
        from adsb_map_panel import _MapCanvas
        from touch_helpers import PinchZoomMixin
        assert issubclass(_MapCanvas, PinchZoomMixin)


# --------------------------------------------------------------------------- #
# 4. settings touch_mode roundtrip
# --------------------------------------------------------------------------- #
class TestSettingsTouchMode:
    def test_default_present(self):
        from settings import DEFAULTS
        assert DEFAULTS["touch_mode"] in ("auto", "on", "off")

    def test_roundtrip(self, tmp_path):
        from settings import DesktopSettings
        p = str(tmp_path / "settings.json")
        s = DesktopSettings(path=p)
        assert s.get("touch_mode") == "auto"
        s.set("touch_mode", "on")
        s.flush()
        s2 = DesktopSettings.load(path=p)
        assert s2.get("touch_mode") == "on"


# --------------------------------------------------------------------------- #
# 5. MainWindow 触控初始化（offscreen；sdr_connect 失败是已知基线）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def main_window(qapp):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


class TestMainWindowTouchInit:
    def test_touch_manager_available(self, main_window):
        from touch_manager import touch_manager
        tm = touch_manager()
        # offscreen 无触屏硬件 → 自动检测为 False（不造假）
        assert tm.is_touch_mode() is False

    def test_mouse_mode_splitter_default_4px(self, main_window):
        from tokens import tokens
        assert main_window._main_splitter.handleWidth() == int(
            tokens().SIZE["splitter_handle"])

    def test_forced_touch_widens_splitter_and_targets(self, main_window):
        from touch_manager import touch_manager
        tm = touch_manager()
        tm.set_override(True)
        try:
            # _on_touch_mode_changed 已连接 → 重新适配
            assert main_window._main_splitter.handleWidth() == 10
            # 工具栏连接按钮在触屏模式下最小高度 ≥44
            assert main_window.connect_btn.minimumHeight() >= 44
        finally:
            tm.set_override(False)

    def test_touch_mode_changed_connected(self, main_window):
        from touch_manager import touch_manager
        tm = touch_manager()
        # 切到触屏再切回，不抛异常即视为适配链可用
        tm.set_override(True)
        tm.set_override(False)
        assert tm.is_touch_mode() is False
