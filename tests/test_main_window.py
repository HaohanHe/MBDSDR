#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MainWindow 三栏「平行视界」布局 offscreen 测试
=================================================

守护路A 桌面 UI 的新交付点（替代旧 QDockWidget 三坞断言）：
  1. 中央 = QSplitter(Horizontal) 三栏：左(天空) / 中(地图) / 右(App 页签)；
  2. handle 厚度 = tokens(4px)，三栏可 setSizes 拖宽；
  3. 左栏 QTabWidget 含：星象仪模式 / 卫星跟踪 / 频谱（定轨已去功能化，
     不作为页签；本测试兼作 iron-rule 守卫，任何页签都不得含"定轨"）；
  4. 右栏 QTabWidget 含：控制 / 状态 / AI 助手 / 扫频 / 调制识别 / 设备 / 增益 ...；
  5. 中栏全出血 AdsbMapPanel + 悬浮胶囊 AI 命令栏（输入框 + 麦克风按钮）；
  6. 顶部细状态栏（连接/频率/采样率/GPS/UTC）+ 底部坞（Home/模式块/Now Bar）；
  7. 频谱内部 QSplitter handle 4px、双击复位；
  8. 四主题切换不崩；路B/路C 接口 hasattr 守卫。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_main_window.py -v
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

log = logging.getLogger(__name__)

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
def isolated_persisted_state(tmp_path_factory):
    """隔离跨运行持久化状态，保证本模块在任何机器/任何跑测顺序下同构。

    以前这里直接吃用户真机的 ``three_column_sizes``/``window_geometry``：
    天空槽尺寸随用户上次保存的布局变化（敌对环境实测 1164×536），而天空
    视图底部是否被地景剪影覆盖又取决于用户持久化的观测站坐标——于是
    "单独跑绿、跟前面用例一起跑红"。

    两处都重定向/清空：
      * QSettings 布局三键（窗口几何 + 三栏比例）→ 清空，teardown 还原；
      * DesktopSettings（app_mode 等）→ 指向临时文件，不再继承真机
        ``app_mode=planetarium`` 等状态。
    """
    from PySide6.QtCore import QSettings
    import settings as settings_mod

    qs = QSettings("MBDSDR", "Desktop")
    keys = ("window_geometry", "three_column_sizes",
            "three_column_layout_version")
    backup = {k: qs.value(k) for k in keys}
    for k in keys:
        qs.remove(k)
    qs.sync()

    mp = pytest.MonkeyPatch()
    tmp_settings = (tmp_path_factory.mktemp("desktop_settings")
                    / "desktop_settings.json")
    mp.setattr(settings_mod, "default_config_path", lambda: str(tmp_settings))
    try:
        yield
    finally:
        mp.undo()
        for k, v in backup.items():
            if v is None:
                qs.remove(k)
            else:
                qs.setValue(k, v)
        qs.sync()


@pytest.fixture(scope="module")
def main_window(qapp, isolated_persisted_state):
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    _pump(qapp, 150)
    yield w
    try:
        w._disconnect()
    except Exception as exc:  # noqa: BLE001 - 收尾失败必须留证，不静默
        log.warning("fixture 收尾失败（MainWindow._disconnect）：%s: %s",
                    type(exc).__name__, exc)
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
        # 定轨去功能化（iron rule）：左栏只有这三个页签。
        for expected in ("星象仪模式", "卫星跟踪", "频谱"):
            assert expected in texts, f"左栏缺页签 {expected}: {texts}"
        # 守卫：任何页签文本都不得含"定轨"——定轨只能由用户开口触发，
        # 绝不在 UI 上作为功能位/页签出现（用户明确要求，最高优先级）。
        assert not any("定轨" in t for t in texts), \
            f"定轨不得作为页签出现在 UI: {texts}"

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

    def test_save_and_restore_window_state(self, main_window):
        # 隔离由 isolated_persisted_state 负责（清空三键、模块结束还原）；
        # 以前这里 monkeypatch.setenv("HOME") 在 Windows 上对 QSettings
        # 注册表无效，看起来隔离了其实没有。
        from PySide6.QtCore import QSettings
        s = QSettings("MBDSDR", "Desktop")
        main_window._main_splitter.setSizes([280, 600, 380])
        main_window._save_window_state()
        s.sync()
        assert s.value("window_geometry") is not None
        assert s.value("three_column_sizes") is not None


# ---------------------------------------------------------------------------
class TestPlanetariumSkyVisibility:
    """星象仪形态：从非当前页签进形态，天空不得整片黑（hidden 标记须清）。"""

    # 地景剪影色（夜三层 ground_far/mid/near + 昼 ground_shadow_day）。
    # 星象仪入口 enter_horizon_view() 朝地平看，_draw_landscape 从剪影轮廓
    # 一直填到控件底边——底部出现这些颜色是正常构图，不是"黑边"缺陷。
    _GROUND_RGB = ((12, 19, 32), (7, 12, 21), (3, 6, 12), (58, 74, 90))

    @classmethod
    def _is_ground(cls, color) -> bool:
        rgb = (color.red(), color.green(), color.blue())
        return any(max(abs(rgb[i] - g[i]) for i in range(3)) <= 2
                   for g in cls._GROUND_RGB)

    def test_enter_from_non_sky_tab_clears_hidden(self, main_window, qapp):
        lt = main_window.left_tab
        sky_idx = lt.indexOf(main_window.sky_view)
        assert sky_idx >= 0
        other = next(i for i in range(lt.count()) if i != sky_idx)
        lt.setCurrentIndex(other)
        _pump(qapp, 50)
        assert main_window.sky_view.isHidden(), "非当前页签的天空应 hidden"
        try:
            main_window.set_app_mode("planetarium")
            assert not main_window.sky_view.isHidden(), \
                "进星象仪后天空仍 hidden → 用户看到整片天黑"
            assert main_window.sky_view.parentWidget() is \
                main_window._planetarium_sky_slot
        finally:
            main_window.set_app_mode("ide")
            main_window.showNormal()
            main_window.show()
            _pump(qapp, 50)
        assert lt.indexOf(main_window.sky_view) >= 0, "退出后天空应归位左栏"

    def test_planetarium_sky_paints_not_black_void(self, main_window, qapp):
        """像素级守卫：星象仪天空必须真画出夜空，而非纯黑空槽。

        产品口径：天空槽尺寸由用户保存的三栏比例决定（任何比例都合法，
        视图按比例自适应）；星象仪入口朝地平看，底部被地景剪影覆盖属
        设计构图。所以断言分两支：
          * 底部是地景 → 只要证明中段天空确实亮于地景（天空层画了）；
          * 底部是天空 → 保留"地平方向比天顶亮"的夜景渐变判据。
        """
        lt = main_window.left_tab
        sky_idx = lt.indexOf(main_window.sky_view)
        other = next(i for i in range(lt.count()) if i != sky_idx)
        lt.setCurrentIndex(other)
        _pump(qapp, 50)
        # 钉住几何（窗口尺寸 + 三栏比例）与中性示例观测站：这两项以前都
        # 来自用户持久化状态（布局尺寸 / gui_config 观测站），导致跑测顺序
        # 与个人环境决定"底部是天空还是地景"。
        main_window.resize(1200, 792)
        main_window._main_splitter.setSizes([392, 300, 300])
        sky = main_window.sky_view
        old_observer = getattr(sky, "_observer", None)
        sky.set_observer(0.0, 0.0)  # 中性示例站点，不写死真实坐标
        _pump(qapp, 50)
        try:
            main_window.set_app_mode("planetarium")
            _pump(qapp, 50)
            img = sky.grab().toImage()
            w, h = img.width(), img.height()
            assert w > 50 and h > 50
            top = img.pixelColor(w // 2, 4)
            mid = img.pixelColor(w // 2, h // 2)
            bot = img.pixelColor(w // 2, h - 4)

            # 1) 真·黑空槽（hidden 标记未清的旧 bug）：整片没有任何被绘制的像素
            grid = [img.pixelColor(int(w * fx), int(h * fy))
                    for fx in (0.1, 0.5, 0.9)
                    for fy in (0.05, 0.3, 0.6, 0.95)]
            assert any(c.lightness() > 0 for c in grid), \
                "星象仪天空疑似纯黑空槽（整片无像素被绘制）"

            # 2) 底部是地景还是天空，都要求"天空层真的画出来"
            if self._is_ground(bot) or self._is_ground(
                    img.pixelColor(w // 2, h - 1)):
                assert mid.lightness() > bot.lightness() + 2, \
                    ("底部已是地景剪影，但中段与地景同色 → 疑似空槽: "
                     f"mid={mid.getRgb()} bot={bot.getRgb()}")
            else:
                # 无地景时，夜空渐变本质：地平方向比天顶亮。
                assert bot.lightness() > top.lightness(), \
                    (f"星象仪天空疑似黑空槽: top={top.getRgb()} "
                     f"bot={bot.getRgb()}")
        finally:
            if old_observer:
                sky.set_observer(old_observer.get("lat"),
                                 old_observer.get("lon"),
                                 old_observer.get("alt_m", 0.0))
            else:
                sky.set_observer(None, None)
            main_window.set_app_mode("ide")
            main_window.showNormal()
            main_window.show()
            _pump(qapp, 50)


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
