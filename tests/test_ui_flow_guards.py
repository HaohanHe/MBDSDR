# SPDX-License-Identifier: MIT
"""桌面 UI 流程护栏（无硬件，offscreen）。

钉住 docs/interaction-flows.md 里的 B2 / S1 / S9 / S10：

- B2：恢复的窗口坐标必须校验屏幕可见性；外接屏拔掉后不得把窗口放到屏幕外，
  并提供「重置窗口位置」入口。
- S1：无设备引导横幅原先挂在「频谱」页，而首启默认页是「天空模式」——用户
  看不到。首启提示必须切到横幅所在页并保持可见。
- S9：连接失败/掉线后控件必须回到"未连接"（调谐/录音置灰、频谱清空）；
  连接进行中不得提前启用控件。
- S10：星象仪命令栏绕过 AI 面板忙锁，会起第二个 AIWorker 覆盖 self._worker；
  忙锁期间必须拒绝新指令。

反证方式：删掉 main_window 的坐标校验、横幅切页、`_on_connection_for_ui`
的断开同步或 ai_panel `_call_ai` 的忙锁，本文件断言即红。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    """整模块复用同一个窗口：MainWindow 构造很贵，逐测重建会把文件拖成分钟级。

    等 350ms 让启动定时器（100ms 读配置 / 200ms 枚举并提示）跑完，
    避免它们插进用例中间改几何或页签。
    """
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(350)
    yield w
    try:
        w._teardown_connection()
    except Exception:
        pass


@pytest.mark.usefixtures("qtapp", "window")
class TestUiFlowGuards:

    def _window(self, window):
        from PySide6.QtTest import QTest
        window._teardown_connection()
        QTest.qWait(20)
        return window

    # ---------------------------------------------------------------- B2
    def test_window_pos_outside_screens_is_clamped(self, window, tmp_path, monkeypatch):
        """B2：上次 (5000, 400) 的坐标在屏幕外，恢复时必须夹回可见区域。"""
        from PySide6.QtGui import QGuiApplication
        import main_window as mw

        cfg_dir = tmp_path / ".mbdsdr"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "gui_config.json"
        cfg_file.write_text(json.dumps(
            {"window": {"x": 5000, "y": 400, "width": 1200, "height": 800}}),
            encoding="utf-8")

        real_expand = os.path.expanduser

        def fake_expand(p):
            if str(p).replace("\\", "/").endswith(".mbdsdr/gui_config.json"):
                return str(cfg_file)
            return real_expand(p)

        monkeypatch.setattr(mw.os.path, "expanduser", fake_expand)
        w = self._window(window)
        w._load_gui_config()

        screen = QGuiApplication.primaryScreen()
        assert screen is not None
        g = screen.availableGeometry()
        pos = w.pos()
        assert g.contains(pos), (
            f"B2：恢复坐标 {pos.x()},{pos.y()} 不在任何屏幕可用区 {g}")

    def test_reset_window_position_action(self, window):
        """B2：应用内必须有「重置窗口位置」，把窗口移回主屏。"""
        from PySide6.QtGui import QGuiApplication
        w = self._window(window)
        w.move(9000, 9000)
        assert callable(getattr(w, "_on_reset_window_position", None)), \
            "缺少重置窗口位置入口（B2）"
        w._on_reset_window_position()
        g = QGuiApplication.primaryScreen().availableGeometry()
        assert g.contains(w.pos()), "重置后窗口仍不在主屏可用区"

    # ---------------------------------------------------------------- S1
    def test_no_device_hint_is_on_visible_page(self, window):
        """S1：首启无设备提示必须落在当前可见页，而不是隐藏的「天空模式」页。"""
        w = self._window(window)
        # 首启默认页：天空模式（index 0）
        w.left_tab.setCurrentIndex(0)
        w._show_no_rtl_hint("未找到 RTL-SDR：检查 Zadig WinUSB 驱动")
        assert w.left_tab.currentWidget() is w._spectrum_page, \
            "S1：引导横幅仍在不可见页，用户看不到"
        assert w._no_device_hint.isVisibleTo(w), \
            "S1：引导横幅对用户不可见"

    # ---------------------------------------------------------------- S9
    def test_connection_failure_restores_disconnected_ui(self, window):
        """S9：连接失败/掉线后控件必须置灰、频谱清空，不再暴露假可控状态。"""
        w = self._window(window)
        w.spectrum.set_connected(True)
        w._panels_set_sdr_connected(True)
        assert w.control_panel.tune_button.isEnabled() is True

        w._on_connection_for_ui(False, "连接失败：超时")

        assert w.control_panel.tune_button.isEnabled() is False, \
            "S9：连接失败后调谐控件仍可点"
        assert w.record_btn.isEnabled() is False, "S9：连接失败后仍可点录音"
        assert w.spectrum._connected is False, "S9：连接失败后频谱未清空"
        assert w.disconnect_btn.isEnabled() is False

    def test_connecting_state_keeps_controls_disabled(self, window):
        """S9：连接进行中不得提前把控件置为可用（假连接态）。"""
        w = self._window(window)

        class _StubManager:
            def start(self, host, port):
                return None

            def stop(self):
                pass

        w._worker_manager = _StubManager()
        w._worker = None
        w._connect_real("127.0.0.1", 1938)
        assert w.control_panel.tune_button.isEnabled() is False, \
            "S9：连接进行中就把调谐控件启用了"
        assert w.record_btn.isEnabled() is False
        assert w.spectrum._connected is False

    # ---------------------------------------------------------------- S10
    def test_busy_ai_call_is_rejected(self, window):
        """S10：忙锁期间星象仪命令栏不得再起一个 AIWorker。"""
        w = self._window(window)
        panel = w.ai_panel
        panel._is_processing = True
        panel._worker = None
        try:
            ret = panel._call_ai("再来一条")
            assert ret is False, "S10：忙锁期间仍启动了新的 AI 调用"
            assert panel._worker is None, "S10：第二个 AIWorker 覆盖了在途引用"
        finally:
            panel._is_processing = False

    def test_panel_submit_dispatches_single_ai_call(self, window, monkeypatch):
        """S10：面板提交曾被"信号 + 直调"派发两次，一次提交起两个 worker。"""
        import ai_panel as ap
        w = self._window(window)
        panel = w.ai_panel
        calls = []

        monkeypatch.setattr(panel, "agent", object())  # 普通文本不需要真 agent
        monkeypatch.setattr(ap, "AI_CORE_AVAILABLE", True)
        monkeypatch.setattr(panel, "_call_ai",
                            lambda text: calls.append(text) or True)
        panel.input_field.setText("调谐到 100 MHz")
        panel._on_submit()
        assert len(calls) == 1, (
            f"S10：一次提交派发了 {len(calls)} 次 AI 调用（应恰好 1 次）")

    # ------------------------------------------------------------ 全天模式入口
    def test_all_sky_entry_mirrors_view_state(self, window):
        """全天模式按钮/菜单必须双向跟随天空视图的真实状态，不自己记布尔。"""
        w = self._window(window)
        sv = w.sky_view
        sv.set_all_sky_mode(False)
        btn = getattr(w, "_all_sky_btn", None)
        act = getattr(w, "_all_sky_action", None)
        assert btn is not None, "天空页缺少全天模式切换按钮"
        assert act is not None, "视图菜单缺少「全天模式（160°）」"
        assert btn.isCheckable() and act.isCheckable()
        assert act.shortcut().toString() == "A", "菜单项应提示与视图内一致的 A 键"
        assert "已进入" not in btn.text()

        # 视图状态 → 按钮 / 菜单
        sv.set_all_sky_mode(True)
        assert sv.all_sky_active
        assert btn.isChecked() is True and "已进入" in btn.text()
        assert act.isChecked() is True

        # 按钮 → 视图状态（再次点击退出全天）
        btn.click()
        assert sv.all_sky_active is False
        assert "已进入" not in btn.text()
        assert act.isChecked() is False

        # 菜单 → 视图状态
        act.trigger()
        assert sv.all_sky_active is True
        act.trigger()
        assert sv.all_sky_active is False

        # 主视场 120° 不受影响：进出后恢复原值
        assert abs(float(sv.view_state.fov_deg) - 120.0) < 0.5
        assert abs(float(sv.ALL_SKY_FOV_DEG) - 160.0) < 0.5

    def test_escape_layers_all_sky_then_sky_mode(self, window):
        """Esc 分层：先退全天（仍在天空模式），再退天空模式。"""
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

        w = self._window(window)
        sv = w.sky_view
        sv.set_all_sky_mode(False)
        w._enter_planetarium()
        assert w._app_mode == "planetarium"
        sv.set_all_sky_mode(True)

        def esc():
            return QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)

        # 视图自身处理第一层（分焦点在天空图上）
        sv.keyPressEvent(esc())
        assert sv.all_sky_active is False, "Esc 第一层应先退出全天"
        assert w._app_mode == "planetarium", "第一层 Esc 不得退出天空模式"

        # 窗口层再按一次：退出天空模式
        w.keyPressEvent(esc())
        assert w._app_mode == "ide", "第二层 Esc 应退出天空模式"

    def test_escape_window_layer_keeps_order_without_view_focus(self, window):
        """焦点不在天空图时，窗口层 Esc 也必须先退全天再退天空模式。"""
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

        w = self._window(window)
        sv = w.sky_view
        sv.set_all_sky_mode(False)
        w._enter_planetarium()
        sv.set_all_sky_mode(True)

        w.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert sv.all_sky_active is False, "窗口层 Esc 跳过了全天层"
        assert w._app_mode == "planetarium"

        w.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert w._app_mode == "ide"


def test_about_dialog_contains_dso_attribution(monkeypatch):
    """许可义务：关于框必须保留 OpenNGC / CC BY-SA 4.0 署名，且文案不被截断。"""
    import main_window as mw

    captured = {}

    def fake_about(parent, title, text):
        captured["title"] = title
        captured["text"] = text

    monkeypatch.setattr(mw.QMessageBox, "about", fake_about)
    mw.MainWindow._show_about(None)

    text = captured.get("text", "")
    assert captured.get("title") == "关于 MBDSDR"
    assert "深空数据来自 OpenNGC（CC BY-SA 4.0）；中文名来自 Wikidata（CC0）。" \
        "许可说明见 docs/dso-catalog.md。" in text, "署名文案不完整或被截断"
    assert "OpenNGC" in text and "CC BY-SA 4.0" in text
    assert "github.com/mattiaverga/OpenNGC" in text
    assert "creativecommons.org/licenses/by-sa/4.0/" in text
    # 不写死版本号：正文里不应出现 "版本 vX.Y" 之类硬编码
    assert "v1." not in text and "version" not in text.lower()
