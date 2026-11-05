# SPDX-License-Identifier: MIT
"""跟踪面板文案：负零、停止提示门限值、频率 tooltip 的设备口径。"""
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


def _pos(el=40.0, dop=-0.4):
    return {"valid": True, "azimuth": 120.0, "elevation": el,
            "doppler_hz": dop, "corrected_freq_hz": 137.620e6,
            "range_km": 800.0}


def test_negative_zero_doppler_prints_zero_with_decimal(qapp, isolated_profile):
    """多普勒 -0.4 Hz 不得显示成「-0 Hz」；要能看出是 0.0。"""
    from desktop.sat_track_panel import SatTrackPanel
    p = SatTrackPanel()
    p.update_position(_pos(dop=-0.4))
    text = p.dop_label.text()
    assert "-0" not in text, f"负零/负号漏出：{text!r}"
    assert "0.0" in text, f"应显示 0.0 Hz 量级，实际 {text!r}"


def test_stop_text_uses_runtime_gate_not_literal(qapp, isolated_profile, monkeypatch):
    """停止提示必须说明「跟踪停止、卫星未落下」，且门限取实际值。"""
    from desktop import sat_track_panel as stp
    p = stp.SatTrackPanel()
    p.set_tracking_state(True)
    p.update_position(_pos(el=4.9))
    text = p.state_label.text()
    assert "跟踪已停止" in text, f"旧文案「过境结束」不准确：{text!r}"
    assert "尚未落下" in text, f"必须区分「停止跟踪」与「卫星落下」：{text!r}"
    assert "5°" in text, f"必须给出实际门限值：{text!r}"

    # 门限是可运行时替换的值，不是写死的 5：改它，文案必须跟着变。
    p._settings = {"pass_min_elevation_deg": 10.0}
    monkeypatch.setattr(stp, "VISIBLE_MIN_ALT_DEG", 7.0)
    p.update_position(_pos(el=6.9))
    assert "7°" in p.state_label.text(), (
        f"文案写死了门限，实际 {p.state_label.text()!r}")


def test_freq_tooltip_says_device(qapp, isolated_profile):
    """下行频率 tooltip 必须说明「实际范围以设备为准」。"""
    from desktop.sat_track_panel import SatTrackPanel
    p = SatTrackPanel()
    assert "设备" in p.freq_spin.toolTip(), (
        f"tooltip 未声明设备口径：{p.freq_spin.toolTip()!r}")
