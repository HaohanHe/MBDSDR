# SPDX-License-Identifier: MIT
"""模块面板的诚实态：未接后端不许出现假调谐 / 假增益 / 假 sink。

守门点：
1. 未连接：频率控件禁用、增益显示「—」、sink 置灰并标「仅记录」；
2. 调谐未接入后端（``tune_wired=False``）时，即使"已连接"频率仍禁用；
3. 设备真实频率/增益回填走 ``set_rf_params``，且不得反过来触发 tune_requested；
4. 断开后不再显示上次的增益数字（避免把历史值当当前读数）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(qapp):
    from desktop.module_panel import ModulePanel

    p = ModulePanel()
    yield p
    p.close()


def test_disconnected_shows_no_fake_values(panel):
    assert panel.freq_spin.isEnabled() is False, "未接后端时频率必须禁用"
    assert panel.gain_slider.isEnabled() is False, "未连接时增益滑块必须禁用"
    assert panel.gain_val.text() == "—", "未连接不许显示假 20 dB"
    assert panel.sink_combo.isEnabled() is False, "sink 切换未接入必须置灰"
    assert "仅记录" in panel.sink_note.text()
    assert "未连接" in panel.params_hint.text()
    assert panel.params_hint.isVisible() or panel.params_hint.parent() is not None


def test_connected_without_wiring_keeps_tune_disabled(panel):
    panel.set_sdr_connected(True)
    assert panel.gain_slider.isEnabled() is True
    assert panel.freq_spin.isEnabled() is False, (
        "调谐没接到 _apply_hw_action 就启用频率=假调谐")
    assert "尚未接入" in panel.params_hint.text()


def test_connected_with_wiring_enables_tune(panel):
    panel.set_sdr_connected(True, tune_wired=True)
    assert panel.freq_spin.isEnabled() is True
    assert "失败会回滚" in panel.params_hint.text()


def test_real_params_backfill_does_not_reemit_tune(panel):
    emitted = []
    panel.tune_requested.connect(emitted.append)
    panel.set_rf_params(145_800_000.0, 30.0)
    assert panel.freq_spin.value() == pytest.approx(145.8)
    assert panel.gain_val.text() == "30.0 dB"
    assert emitted == [], (
        "设备回执的频率又被当成用户调谐发回去，形成调谐回环")


def test_disconnect_hides_previous_gain(panel):
    panel.set_sdr_connected(True, tune_wired=True)
    panel.set_rf_params(100_000_000.0, 30.0)
    assert panel.gain_val.text() == "30.0 dB"
    panel.set_sdr_connected(False)
    assert panel.gain_val.text() == "—", "断开后仍显示上次增益=把历史值当读数"
    assert panel.freq_spin.isEnabled() is False


# ---------------------------------------------------------------- 枚举落地
# 注入失败路径：让后台枚举永不返回（等价于 USB open 卡死），界面必须自己
# 离开「正在枚举」并把原因和可做的下一步写出来，而不是永久转。


def test_rejected_write_shows_reason_without_duplicate_tooltip(panel):
    """会话占用期间被拒：原因只显示一次，tooltip 不再逐字复写。

    tooltip 与标签文字相同 = 没有补充信息；下一次写入成功后还会变成一条
    指向旧拒绝的残留提示（tooltip 普查 2026-11-13）。
    """
    import connect_request_store as store

    key = "rtlsdr|serial:A-0001"
    store.reset()
    try:
        store.update(key, gain_db=30.0)
        store.begin_session(key, initial=store.ConnectRequest(gain_db=33.0))
        ident = type("_Ident", (), {"key": key, "warning": ""})()

        panel._push(ident, gain_db=40.0)

        assert "会话" in panel.params_hint.text(), "被拒原因必须在标签上显示"
        assert panel.params_hint.toolTip() == "", \
            "tooltip 复写了标签里的拒绝原因（无补充信息/会残留）"
    finally:
        store.reset()


def test_reconnect_clears_stale_session_tooltip(panel):
    """重新连上后不许把「会话占用中」的旧 tooltip 留着。"""
    panel.params_hint.setText("设备会话进行中：连接期间不能改参数")
    panel.params_hint.setToolTip("设备会话进行中：连接期间不能改参数")

    panel.set_sdr_connected(True, tune_wired=True)

    assert panel.params_hint.toolTip() == "", \
        "已解除的会话限制还挂在 tooltip 上（tooltip 普查 2026-11-13）"
    assert "会话占用中" not in panel.params_hint.toolTip()


def test_enum_timeout_leaves_in_progress_and_allows_retry(qapp, monkeypatch):
    import desktop.module_panel as mp

    monkeypatch.setattr(mp._ModuleEnumTask, "run", lambda self: None)
    panel = mp.ModulePanel()
    try:
        assert panel._enum_inflight is True
        assert panel.refresh_btn.isEnabled() is False, "枚举中刷新按钮应禁用"

        panel._on_enum_timeout()

        assert panel._enum_inflight is False, "超时后 inflight 必须复位"
        assert panel.refresh_btn.isEnabled() is True, "超时后必须能再次刷新"
        assert "超时" in panel.device_info.text()
        assert "刷新" in panel.device_info.text(), "要写清现在能做什么"
        assert "正在枚举设备…" not in panel.device_combo.currentText()
    finally:
        panel.close()


def test_late_enum_result_after_timeout_is_dropped(qapp, monkeypatch):
    import desktop.module_panel as mp

    monkeypatch.setattr(mp._ModuleEnumTask, "run", lambda self: None)
    panel = mp.ModulePanel()
    try:
        panel._on_enum_timeout()
        stale_token = panel._enum_token - 1
        panel._on_devices_enumerated(
            [{"driver": "rtl_sdr", "label": "迟到的 RTL-SDR"}], None, stale_token)

        assert panel._devices == [], "超时后迟到的旧结果不得回写设备列表"
        assert "超时" in panel.device_info.text()
        assert panel.refresh_btn.isEnabled() is True
    finally:
        panel.close()
