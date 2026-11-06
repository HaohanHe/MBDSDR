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
    assert panel.gain_val.text() == "30 dB"
    assert emitted == [], (
        "设备回执的频率又被当成用户调谐发回去，形成调谐回环")


def test_disconnect_hides_previous_gain(panel):
    panel.set_sdr_connected(True, tune_wired=True)
    panel.set_rf_params(100_000_000.0, 30.0)
    assert panel.gain_val.text() == "30 dB"
    panel.set_sdr_connected(False)
    assert panel.gain_val.text() == "—", "断开后仍显示上次增益=把历史值当读数"
    assert panel.freq_spin.isEnabled() is False
