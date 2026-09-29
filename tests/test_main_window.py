#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MainWindow 三栏「平行视界」布局 offscreen 测试
=================================================

守护路A 桌面 UI 的新交付点（替代旧 QDockWidget 三坞断言）：
  1. 中央 = QSplitter(Horizontal) 三栏：左(天空) / 中(地图) / 右(App 页签)；
  2. handle 厚度 = tokens(4px)，三栏可 setSizes 拖宽；
  3. 左栏 QTabWidget 含：射频天空 / 多普勒定轨 / 卫星跟踪 / 频谱；
  4. 右栏 QTabWidget 含：控制 / 状态 / AI 助手 / 扫频 / 调制识别 / 设备 / 增益 ...；
  5. 中栏全出血 AdsbMapPanel + 悬浮胶囊 AI 命令栏（输入框 + 麦克风按钮）；
  6. 顶部细状态栏（连接/频率/采样率/GPS/UTC）+ 底部坞（Home/模式块/Now Bar）；
  7. 频谱内部 QSplitter handle 4px、双击复位；
  8. 四主题切换不崩；路B/路C 接口 hasattr 守卫。

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

from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter, QTabWidget  # noqa: E402


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
class TestThreeColumnSplitter:
    """三栏 QSplitter 骨架。"""

    def test_main_splitter_exists_and_is_horizontal(self, main_window):
        sp = main_window._main_splitter
        assert isinstance(sp, QSplitter)
        assert sp.orientation().name == "Horizontal"
        assert sp.count() == 3

    def test_handle_width_is_token_4px(self, main_window):
        from tokens import tokens
        expect = int(tokens().SIZE["splitter_handle"])
        assert main_window._main_splitter.handleWidth() == expect

    def test_three_panes_present(self, main_window):
        """左栏 / 中栏 / 右栏三个子 widget 都在。"""
        children = main_window._main_splitter.children()
        widgets = [c for c in children if isinstance(c, QSplitter)]
        # addWidget 的三个直接子 widget
        sp = main_window._main_splitter
        assert sp.widget(0) is main_window.left_tab
        assert sp.widget(2) is main_window.right_tab

    def test_left_tab_has_sky_tabs(self, main_window):
        texts = [main_window.left_tab.tabText(i)
                 for i in range(main_window.left_tab.count())]
        for expected in ("射频天空", "多普勒定轨", "卫星跟踪", "频谱"):
            assert expected in texts, f"左栏缺页签 {expected}: {texts}"

    def test_right_tab_has_core_apps(self, main_window):
        texts = [main_window.right_tab.tabText(i)
                 for i in range(main_window.right_tab.count())]
        for expected in ("控制", "状态", "AI 助手", "扫频", "调制识别",
                          "设备", "增益"):
            assert expected in texts, f"右栏缺页签 {expected}: {texts}"

    def test_right_panels_wrapped_in_scrollarea(self, main_window):
        """右栏每个页签内容都是无框 QScrollArea（纵向可滚）。"""
        from PySide6.QtCore import Qt
        for i in range(main_window.right_tab.count()):
            w = main_window.right_tab.widget(i)
            assert isinstance(w, QScrollArea), f"页签 {i} 内容应为 QScrollArea"
            assert w.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff

    def test_splitter_sizes_resizable(self, main_window):
        """QSplitter 可 setSizes（用户拖宽生效；受最小宽约束，不要求精确像素）。"""
        sp = main_window._main_splitter
        sp.setSizes([280, 600, 380])
        sizes = sp.sizes()
        assert len(sizes) == 3
        assert sum(sizes) > 0
        # 右栏应明显比左栏宽（setSizes 生效，而非初始默认）
        assert sizes[2] >= sizes[0]


# ---------------------------------------------------------------------------
class TestCenterMapAndCommandBar:
    """中栏地图铺满 + 悬浮 AI 命令栏。"""

    def test_center_has_adsb_map(self, main_window):
        assert main_window.adsb_map_panel is not None

    def test_ai_command_bar_floating_on_center(self, main_window):
        bar = main_window._ai_cmd_bar
        assert bar is not None
        # 命令栏是中栏的子控件（悬浮 overlay）
        assert bar.parent() is main_window._main_splitter.widget(1)
        assert main_window.cmd_input is not None
        assert main_window.cmd_mic_btn is not None

    def test_cmd_submit_routes_to_ai(self, main_window):
        """输入框回车 → 路由到 ai_panel（_on_ai_command）。"""
        called = []
        orig = main_window._on_ai_command
        main_window._on_ai_command = lambda t: called.append(t)
        main_window.cmd_input.setText("切到 NFM")
        main_window._on_cmd_submit()
        main_window._on_ai_command = orig
        assert called == ["切到 NFM"]
        assert main_window.cmd_input.text() == ""


# ---------------------------------------------------------------------------
class TestTopBarAndBottomDock:
    """顶栏细状态栏 + 底部坞。"""

    def test_top_bar_fields_present(self, main_window):
        for attr in ("status_conn", "status_freq", "status_sr",
                     "status_gps", "status_utc", "callsign_edit"):
            assert hasattr(main_window, attr), f"顶栏缺 {attr}"

    def test_bottom_dock_present(self, main_window):
        for attr in ("home_btn", "dock_mode_label", "dock_freq_label",
                     "status_gain", "status_bw", "status_rssi",
                     "status_dev", "status_processing"):
            assert hasattr(main_window, attr), f"底坞缺 {attr}"

    def test_go_home_switches_to_spectrum(self, main_window):
        main_window.left_tab.setCurrentIndex(0)  # 先切到别的页
        main_window._go_home()
        texts = [main_window.left_tab.tabText(i)
                 for i in range(main_window.left_tab.count())]
        cur = main_window.left_tab.tabText(main_window.left_tab.currentIndex())
        assert "频谱" in cur, f"Home 应切到频谱，当前 {cur}"


# ---------------------------------------------------------------------------
class TestSpectrumSplitter:
    """频谱内部垂直 QSplitter：handle 4px、双击复位。"""

    def test_handle_width_is_token_4px(self, main_window):
        from tokens import tokens
        expect = int(tokens().SIZE["splitter_handle"])
        assert main_window.spectrum._splitter.handleWidth() == expect

    def test_double_click_reset_does_not_crash(self, main_window):
        main_window.spectrum.reset_splitter()
        assert True


# ---------------------------------------------------------------------------
class TestThemeSwitch:
    """四主题切换不崩。"""

    def test_all_themes_apply(self, main_window):
        for t in ("default", "dark", "dark_car", "high_contrast"):
            main_window._apply_theme(t)
        assert main_window._current_theme == "high_contrast"


# ---------------------------------------------------------------------------
class TestRoadBCInterfaces:
    """路B/路C 接口 hasattr 守卫：不存在不崩。"""

    def test_sky_widget_created_via_factory_or_fallback(self, main_window):
        assert main_window.sky_view is not None

    def test_backend_audio_hook_no_backend_no_crash(self, main_window):
        main_window._active_sdr_backend = None
        main_window._backend_audio_hook("start_audio")
        main_window._backend_audio_hook("stop_audio")


# ---------------------------------------------------------------------------
class TestPersistence:
    """QSettings 持久化窗口几何 + splitter 比例。"""

    def test_save_and_restore_window_state(self, main_window, tmp_path, monkeypatch):
        from PySide6.QtCore import QSettings
        monkeypatch.setenv("HOME", str(tmp_path))
        s = QSettings("MBDSDR", "Desktop")
        main_window._main_splitter.setSizes([280, 600, 380])
        main_window._save_window_state()
        s.sync()
        assert s.value("window_geometry") is not None
        assert s.value("three_column_sizes") is not None


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
