#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
桌面 UI 稳定性 / 资源清理 / 异常不崩
=====================================

守护四条红线（以读码 + offscreen 实测为准，不凭空造缺陷）：

1. **关窗即回收运行期资源**：QTimer 不随 ``close()`` 停——只有 C++ 对象销毁才停。
   主窗口未设 WA_DeleteOnClose，关窗后 ``_sky_update_timer`` / ``_utc_timer`` /
   ``_gnss_timer`` / ``_sat_track_timer`` / ``_record_timer`` / ``_replay_timer``
   以及面板自备的定时器会继续按周期回调本窗口。跨文件跑测时每构造一个
   MainWindow 就多一组常驻定时器 + 一次真实 USB 热插拔枚举。
2. **关窗即摘全局钩子**：``__init__`` 里装的 ``app.installEventFilter(self)``
   和 ``touch_manager().touch_mode_changed.connect(...)`` 都是进程级副作用。
   不摘的话已关窗口永久留在 QApplication 过滤链 / 单例信号上——后者槽里还有
   ``app.setStyleSheet`` 全量重抛光（源码注释里已点名它是根 tests 累积变慢的真因）。
3. **无后端 / 极端入参不崩**：后端为 None、负频率、超大采样率、NaN 增益、
   空 IQ、超大文本都必须走显式空态，绝不抛异常。
4. **NaN 不得被伪造成真值**：NaN 比较恒为 False，``min/max`` 夹取会静默产出
   边界极值（49 dB 满增益 / 6 GHz / 满刻度 S9+），而 ``int(round(nan))`` 直接
   抛 ValueError 打断 S-meter 刷新链。两者都违反诚实红线。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_ui_stability_resources.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QEvent, QTimer, QThread  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

NAN = float("nan")
INF = float("inf")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(app, ms=60):
    QTimer.singleShot(ms, app.quit)
    app.exec()


@pytest.fixture(scope="module")
def mw(qapp):
    """构造一次 MainWindow（构造代价高，模块内共享）。"""
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    _pump(qapp, 150)
    return w


@pytest.fixture(scope="module")
def mw_closed(mw, qapp):
    """同一个窗口走完真实 closeEvent 后的状态（资源清理断言全靠它）。"""
    mw.close()
    _pump(qapp, 50)
    return mw


def _live_timers(w):
    return [t for t in w.findChildren(QTimer) if t.isActive()]


# ===========================================================================
# 1) 关窗后定时器必须全停
# ===========================================================================
class TestCloseStopsTimers:

    def test_named_window_timers_inactive_after_close(self, mw_closed):
        """_sky_update_timer / _utc_timer / _gnss_timer 等关窗后必须 inactive。

        回归钉扎：修复前 closeEvent 只停 _gnss_timer，其余（含 5s 天空刷新、
        1s UTC 钟、500ms 卫星跟踪）在关窗后照跑不误。
        """
        for attr in ("_sky_update_timer", "_utc_timer", "_gnss_timer",
                     "_iq_poll_timer"):
            t = getattr(mw_closed, attr, None)
            assert t is not None, f"{attr} 应已在 __init__ 里创建"
            assert not t.isActive(), f"{attr} 关窗后仍在跑"

    def test_no_active_timer_left_in_window_subtree(self, mw_closed):
        """整棵控件树（含面板自备定时器）关窗后不应再有活动定时器。

        panel 侧的常驻典型：status_panel 的 1s UTC 钟、device_panel 的 2s
        热插拔枚举（会周期性枚举真实 USB 硬件）、spectrum/rf_sky_view 重绘钟。
        """
        live = _live_timers(mw_closed)
        assert not live, f"关窗后仍有 {len(live)} 个活动定时器: {live}"

    def test_no_running_qthread_in_window_subtree(self, mw_closed):
        """构造期不启动后台线程；关窗后也不留 running 的 QThread。

        真实线程（扫频 _SweepWorker / 流水线 worker）都是按需启动、断开即回收，
        这里钉住"构造即开线程"这条回归。
        """
        running = [t for t in mw_closed.findChildren(QThread) if t.isRunning()]
        assert not running, f"窗口子树里仍有 running 的 QThread: {running}"

    def test_deferred_startup_callbacks_cancelled_after_close(self, mw_closed,
                                                              qapp):
        """关窗后延迟启动回调不得再打进本窗口。

        回归钉扎：修复前 _load_gui_config(100ms) / _auto_enumerate_and_connect
        (200ms) / _update_sky_satellites(1200ms) 用的是 QTimer.singleShot 静态版
        ——造出的定时器没有 QObject parent，findChildren 扫不到、关窗也停不掉，
        于是窗口早就关了、它们才跑：_auto_enumerate_and_connect 会去枚举真实
        硬件并往已关窗口的状态栏写提示，还挂一个 8s 的自动清消息定时器。
        """
        mw_closed.statusBar().clearMessage()
        startup = getattr(mw_closed, "_startup_timers", [])
        assert startup, "延迟启动定时器应挂在 self 上（可被统一停掉）"
        for t in startup:
            assert not t.isActive(), "延迟启动定时器关窗后必须停掉"
        _pump(qapp, 500)   # 远超 200ms：修复前这里会打进已关窗口
        assert mw_closed.statusBar().currentMessage() == "", \
            "关窗后仍有延迟启动回调往状态栏写消息"

    def test_disconnect_does_not_stop_window_timers(self, mw):
        """反向钉扎：_disconnect 只断 SDR，窗口还开着，定时器必须继续跑。

        防止把清理误做到 _disconnect 上——那样用户一点"断开"，UTC 钟和天空
        刷新就一起死了。
        """
        mw._utc_timer.start()
        mw._sky_update_timer.start()
        try:
            mw._disconnect()
            assert mw._utc_timer.isActive(), "_disconnect 不该停 UTC 钟"
            assert mw._sky_update_timer.isActive(), "_disconnect 不该停天空刷新"
        finally:
            mw._utc_timer.stop()
            mw._sky_update_timer.stop()


# ===========================================================================
# 2) 关窗后必须摘掉全局钩子（每实例全局副作用）
# ===========================================================================
class TestCloseReleasesGlobalHooks:

    def test_app_event_filter_removed_after_close(self, mw_closed, qapp):
        """关窗后本窗口不得再出现在 QApplication 的事件过滤链上。

        回归钉扎：修复前 installEventFilter(self) 从不 removeEventFilter，
        已关窗口永久留在链上，之后每个事件都要过一遍它的 eventFilter。
        """
        hits = {"n": 0}
        orig = mw_closed.eventFilter

        def counting(obj, event):
            hits["n"] += 1
            return orig(obj, event)

        mw_closed.eventFilter = counting
        try:
            lbl = QLabel("probe")
            qapp.sendEvent(lbl, QEvent(QEvent.Type.User))
            qapp.processEvents()
        finally:
            mw_closed.eventFilter = orig
        assert hits["n"] == 0, \
            f"关窗后 eventFilter 仍被调用 {hits['n']} 次（未摘除应用级过滤）"

    def test_touch_manager_signal_disconnected_after_close(self, mw_closed, qapp):
        """关窗后全局触屏单例信号不得再打进本窗口的槽。

        回归钉扎：touch_manager 是进程级单例，修复前从不 disconnect；模式一
        切换，已关窗口的 _on_touch_mode_changed 照被调用（槽内含
        app.setStyleSheet 全量重抛光）。
        """
        from touch_manager import touch_manager

        hits = {"n": 0}
        orig = mw_closed._on_touch_mode_changed

        def counting(on):
            hits["n"] += 1
            return orig(on)

        mw_closed._on_touch_mode_changed = counting
        tm = touch_manager()
        prev = tm._mode_override
        try:
            tm.set_override(True)
            qapp.processEvents()
        finally:
            tm.set_override(prev)
            qapp.processEvents()
            mw_closed._on_touch_mode_changed = orig
        assert hits["n"] == 0, \
            f"关窗后仍收到 touch_mode_changed {hits['n']} 次（信号未断开）"


# ===========================================================================
# 3) 无后端 / 极端入参不崩
# ===========================================================================
class TestNoBackendAndExtremeInputs:

    def test_status_bar_refresh_without_backend_shows_dash(self, mw, qapp):
        """后端为 None：状态栏全部回落 '--'，不抛异常、不保留旧值。"""
        mw._active_sdr_backend = None
        mw._update_status_bar()
        qapp.processEvents()
        for attr in ("status_freq", "status_sr", "status_bw",
                     "status_gain", "status_dev", "status_rssi"):
            assert "--" in getattr(mw, attr).text(), attr

    def test_iq_sky_gnss_poll_paths_without_backend(self, mw, qapp):
        """无后端/无串口时各条轮询回调直接跑也必须不崩。"""
        mw._active_sdr_backend = None
        mw._poll_sdr_iq()
        mw._update_sky_satellites()
        mw._poll_gnss()
        mw._update_utc_clock()
        qapp.processEvents()

    def test_status_panel_update_from_backend_none(self, mw, qapp):
        mw.status_panel.update_from_backend(None)
        qapp.processEvents()
        assert mw.status_panel.dev_name.text() == "未连接"

    def test_spectrum_extreme_inputs_no_crash(self, mw, qapp):
        import numpy as np

        mw.spectrum.set_center_freq(-1e9)
        mw.spectrum.set_center_freq(NAN)
        mw.spectrum.update_iq(np.zeros(0, dtype=np.complex64), 0.0, 0.0)
        mw.spectrum.update_iq(np.ones(3, dtype=np.complex64), 0.0, 0.0)
        mw.spectrum.update_iq(
            np.ones(4096, dtype=np.complex64), -100e6, -2.4e6)
        mw.spectrum.update_iq(
            np.full(4096, 1e30 + 1e30j, dtype=np.complex64), 1e30, 1e30)
        mw.spectrum.set_markers([])
        mw.spectrum.set_markers([NAN, 1e9])
        mw.spectrum.reset_splitter()
        qapp.processEvents()

    def test_control_panel_extreme_inputs_no_crash(self, mw, qapp):
        mw.control_panel.set_frequency_hz(-1e9)
        mw.control_panel.set_frequency_hz(1e30)
        mw.control_panel.set_sample_rate(1e30)
        mw.control_panel.set_sample_rate(-2.4e6)
        mw.control_panel.set_vfo_bandwidth(NAN)
        mw.control_panel.set_volume(10 ** 9)
        qapp.processEvents()
        # 负/超大频率必须被夹进合法区间，不得漏出去
        assert 10_000.0 <= mw.control_panel._freq_hz <= 6.0e9

    def test_huge_text_does_not_crash(self, mw, qapp):
        mw.statusBar().showMessage("A" * 200_000, 10)
        mw.status_freq.setText("F" * 200_000)
        mw.dock_freq_label.setText("D" * 200_000)
        qapp.processEvents()
        assert mw.status_freq.text().startswith("FFF")


# ===========================================================================
# 4) NaN 必须走空态，不得被伪造成真值
# ===========================================================================
class TestNanIsEmptyNotFabricated:

    def test_dbfs_to_smeter_nan_returns_dash(self):
        """回归钉扎：修复前 int(round(nan)) 抛 ValueError，整条 S-meter 刷新被打断。"""
        from status_panel import _dbfs_to_smeter

        assert _dbfs_to_smeter(NAN) == "--"
        assert _dbfs_to_smeter(INF) == "--"
        assert _dbfs_to_smeter(-INF) == "--"
        assert _dbfs_to_smeter(None) == "--"
        # 有效值不受影响（口径不变，不放宽比较）
        assert _dbfs_to_smeter(-73.0) == "S9 +0 dB"
        assert _dbfs_to_smeter(-127.0) == "S0"

    def test_status_panel_nan_dbfs_shows_empty(self, mw, qapp):
        mw.status_panel.update_signal_level(NAN)
        qapp.processEvents()
        assert mw.status_panel.rssi_value.text() == "--"
        assert mw.status_panel.smeter_value.text() == "--"
        assert mw.status_panel.level_bar.value() == 0, \
            "NaN 电平不得被夹成满刻度（会把空值伪装成爆表信号）"

    def test_control_panel_nan_dbfs_shows_empty(self, mw, qapp):
        mw.control_panel.update_signal_level(NAN)
        qapp.processEvents()
        assert mw.control_panel.smeter_label.text() == "--"
        assert mw.control_panel.smeter_bar.value() == \
            mw.control_panel.smeter_bar.minimum()

    def test_control_panel_nan_gain_keeps_previous(self, mw, qapp):
        """回归钉扎：修复前 int(max(0, min(49, nan))) == 49，NaN 增益被显示成满增益。"""
        mw.control_panel.set_gain(20.0)
        assert mw.control_panel.gain_val.text() == "20dB"
        mw.control_panel.set_gain(NAN)
        assert mw.control_panel.gain_val.text() == "20dB", \
            "NaN 增益必须保持原值，绝不静默夹到 49 dB"

    def test_control_panel_nan_frequency_keeps_previous(self, mw, qapp):
        """回归钉扎：修复前 max(FREQ_MIN, min(FREQ_MAX, nan)) == FREQ_MAX(6 GHz)。"""
        mw.control_panel.set_frequency_hz(98.5e6)
        assert mw.control_panel._freq_hz == 98.5e6
        mw.control_panel.set_frequency_hz(NAN)
        assert mw.control_panel._freq_hz == 98.5e6, \
            "NaN 频率必须保持原值，绝不静默夹到 6 GHz"


# ===========================================================================
# 5) 布局 / 面板身份一致性
# ===========================================================================
class TestPanelIdentityConsistency:

    def test_panel_registry_ids_all_ascii(self):
        """面板 id 是全仓布局落盘的身份键，必须全 ASCII（译文不得混进来）。"""
        from panel_registry import registry

        ids = registry().ids()
        assert ids, "注册表不得为空"
        for pid in ids:
            assert pid.isascii(), f"面板 id 含非 ASCII: {pid!r}"
            assert pid.strip() == pid and pid, f"面板 id 有空白/空串: {pid!r}"

    def test_dock_layout_hardcoded_ids_are_registered(self, qapp, tmp_path):
        """DockLayoutManager 里硬编码的面板 id 必须都在注册表里。

        静态扫源码里的字面量调用点（_raise_center_tab / _tab_key 比较）+ 动态取
        docks 属性，两头对拍；抓不到 id 视为正则失效（测试空转）直接判红。
        """
        import inspect
        import re

        import dock_layout_manager as dlm
        from layout_store import LayoutStore
        from panel_registry import registry

        known = set(registry().ids())
        src = inspect.getsource(dlm)
        hardcoded = set(re.findall(
            r'_raise_center_tab\(\s*"([A-Za-z_][A-Za-z0-9_]*)"', src))
        hardcoded |= set(re.findall(
            r'_tab_key\([^)]*\)\s*==\s*"([A-Za-z_][A-Za-z0-9_]*)"', src))
        assert hardcoded, "没抓到任何硬编码 id（正则失效，测试会空转）"
        assert hardcoded <= known, \
            f"硬编码 id 未注册: {sorted(hardcoded - known)}"

        class _Stub:
            control_dock = object()
            status_dock = object()
            ai_dock = object()

        mgr = dlm.DockLayoutManager(
            _Stub(), store=LayoutStore(path=str(tmp_path / "layouts.json")))
        dock_ids = set(mgr.docks)
        assert dock_ids, "docks 属性没取到任何 dock"
        assert dock_ids <= known, f"dock id 未注册: {sorted(dock_ids - known)}"

    def test_builtin_layout_names_are_not_panel_ids(self):
        """内置布局显示名是用户可见译文，绝不能当面板 id 用（切语言即失效）。"""
        from layout_store import BUILTIN_LAYOUTS
        from panel_registry import registry

        known = set(registry().ids())
        assert not (set(BUILTIN_LAYOUTS) & known), \
            "内置布局名与面板 id 撞名，身份会混淆"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
