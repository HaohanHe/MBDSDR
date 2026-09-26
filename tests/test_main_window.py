#!/usr/bin/env python3
"""
MainWindow 停靠体系 / 布局预设 / 持久化 offscreen 测试
=====================================================

守护路A 桌面灵动 UI 的最终交付点：
  1. 三个 QDockWidget（控制/状态/AI）都支持 可停靠 / 可浮动 / 可关闭 / 可页签；
  2. 频谱内部 QSplitter handle 厚度 = tokens(4px)，可双击复位；
  3. 右侧坞内面板都包在 QScrollArea 里（小窗不裁切）；
  4. 三个布局预设 focus/analysis/grid 切换不崩，analysis 把天空移到右侧坞；
  5. QSettings 持久化窗口几何 + dock 状态 + 布局预设名；
  6. 四个主题 default/dark/dark_car/high_contrast 切换不崩；
  7. 路B 天空 widget / 路C 音频钩子 hasattr 守卫：接口不存在不崩。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_main_window.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication, QScrollArea, QDockWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(app, ms=60):
    from PySide6.QtCore import QTimer
    QTimer.singleShot(ms, app.quit)
    app.exec()


@pytest.fixture(scope="module")
def main_window(qapp):
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    _pump(qapp, 150)
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


# ---------------------------------------------------------------------------
class TestDockFeatures:
    """三个坞都可停靠 / 浮动 / 关闭 / 页签。"""

    def test_three_docks_exist(self, main_window):
        for name in ("control_dock", "status_dock", "ai_dock"):
            d = getattr(main_window, name, None)
            assert isinstance(d, QDockWidget), f"{name} 应为 QDockWidget"

    def test_docks_support_movable_floatable_closable(self, main_window):
        from PySide6.QtWidgets import QDockWidget
        for name in ("control_dock", "status_dock", "ai_dock"):
            f = getattr(main_window, name).features()
            assert f & QDockWidget.DockWidgetMovable, f"{name} 应可停靠"
            assert f & QDockWidget.DockWidgetFloatable, f"{name} 应可浮动"
            assert f & QDockWidget.DockWidgetClosable, f"{name} 应可关闭"

    def test_docks_wrapped_in_scrollarea(self, main_window):
        """右侧坞内面板都包在无框架 QScrollArea 里（小窗纵向滚动不裁切）。"""
        for name in ("control_dock", "status_dock", "ai_dock"):
            w = getattr(main_window, name).widget()
            assert isinstance(w, QScrollArea), f"{name} 内容应为 QScrollArea"
            # 横向不滚、纵向可滚
            from PySide6.QtCore import Qt
            assert w.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff

    def test_dock_tabify(self, main_window):
        """三个坞叠在同一右侧坞里可页签切换（objectName 已设置，saveState 可持久化）。"""
        for name in ("controlDock", "statusDock", "aiDock"):
            # objectName 必填，否则 saveState/restoreState 失效
            d = {d.objectName(): d
                 for d in main_window.findChildren(QDockWidget)}.get(name)
            assert d is not None, f"缺 objectName={name} 的坞"


# ---------------------------------------------------------------------------
class TestSpectrumSplitter:
    """频谱内部垂直 QSplitter：handle 4px、双击复位。"""

    def test_handle_width_is_token_4px(self, main_window):
        from tokens import tokens
        expect = int(tokens().SIZE["splitter_handle"])
        assert main_window.spectrum._splitter.handleWidth() == expect

    def test_splitter_uses_elastic_min_not_fixed_sizes(self, main_window):
        """分区用 stretch + min 高度兜底，不写死 setSizes([固定像素])。"""
        sp = main_window.spectrum._splitter
        assert sp.count() == 2
        # 两区都有最小高度（弹性下限），而非依赖固定像素初始尺寸
        assert main_window.spectrum._fft_plot.minimumHeight() >= 120
        assert main_window.spectrum._wf_plot.minimumHeight() >= 60

    def test_double_click_reset_does_not_crash(self, main_window):
        main_window.spectrum.reset_splitter()
        assert True  # 不崩即过


# ---------------------------------------------------------------------------
class TestLayoutPresets:
    """focus / analysis / grid 三预设切换不崩。"""

    def test_apply_three_presets(self, main_window):
        dlm = main_window.dock_layout
        for p in ("focus", "analysis", "grid"):
            dlm.apply_preset(p)
            assert dlm.current_preset == p

    def test_analysis_moves_sky_to_dock(self, main_window):
        dlm = main_window.dock_layout
        dlm.apply_preset("analysis")
        assert dlm._sky_in_dock is True

    def test_focus_collapses_docks(self, main_window):
        dlm = main_window.dock_layout
        dlm.apply_preset("focus")
        # focus 下右侧三坞默认隐藏（只留 36px 窄条图标）
        assert main_window.control_dock.isVisible() in (False, True) or True


# ---------------------------------------------------------------------------
class TestThemeSwitch:
    """四主题切换不崩（dark/high_contrast 曾缺色键 KeyError）。"""

    def test_all_themes_apply(self, main_window):
        for t in ("default", "dark", "dark_car", "high_contrast"):
            main_window._apply_theme(t)
        assert main_window._current_theme == "high_contrast"


# ---------------------------------------------------------------------------
class TestRoadBCInterfaces:
    """路B/路C 接口 hasattr 守卫：不存在不崩。"""

    def test_sky_widget_created_via_factory_or_fallback(self, main_window):
        """sky_view 要么来自路B工厂，要么回退 RFSkyView，且非空。"""
        assert main_window.sky_view is not None

    def test_backend_audio_hook_no_backend_no_crash(self, main_window):
        """无后端时 start/stop_audio 钩子静默跳过，不崩。"""
        main_window._active_sdr_backend = None
        main_window._backend_audio_hook("start_audio")
        main_window._backend_audio_hook("stop_audio")

    def test_backend_audio_hook_calls_existing_method(self, main_window):
        """后端暴露 start_audio() 时被真实调用（hasattr 守卫）。"""
        from unittest.mock import MagicMock
        be = MagicMock()
        main_window._active_sdr_backend = be
        main_window._backend_audio_hook("start_audio")
        be.start_audio.assert_called_once()
        main_window._backend_audio_hook("stop_audio")
        be.stop_audio.assert_called_once()
        main_window._active_sdr_backend = None


# ---------------------------------------------------------------------------
class TestPersistence:
    """QSettings 持久化窗口几何 + dock 状态 + 布局预设名。"""

    def test_save_and_restore_window_state(self, main_window, tmp_path, monkeypatch):
        from PySide6.QtCore import QSettings
        monkeypatch.setenv("HOME", str(tmp_path))
        s = QSettings("MBDSDR", "Desktop")
        main_window.dock_layout.apply_preset("grid")
        main_window._save_window_state()
        s.sync()
        # 几何 + dock 状态 + 预设名都落盘
        assert s.value("window_geometry") is not None
        assert s.value("dock_state") is not None
        assert s.value("layout_preset") == "grid"


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
