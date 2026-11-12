# SPDX-License-Identifier: MIT
"""模块面板增益回读：后端夹值必须告警，不能静默显示。

与 ``ControlPanel.set_gain_applied`` 同一条底线：滑杆是配置意图，标签是
设备读数；后端夹值（意图 30、实际 20）时标签显示真实值并给 ⚠。
数值格式（本面板取整、控制面板 :g）暂不统一，但「未生效」必须有出口。

回归背景：此前模块面板只回写数值、不比较意图，同一台设备在两块面板上
一块标「未生效」、一块照常显示。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QSlider, QWidget,
)


def _ensure_app():
    return QApplication.instance() or QApplication([])


def _bare_panel():
    """绕过 ModulePanel.__init__（会起后台枚举），只装配回填需要的属性。"""
    from module_panel import ModulePanel

    class _Probe(ModulePanel):
        def __init__(self):
            QWidget.__init__(self)

    panel = _Probe()
    panel._sdr_connected = True
    panel._confirmed_gain_db = None
    panel.gain_slider = QSlider()
    panel.gain_val = QLabel("—")
    return panel


def test_clamped_reading_warns():
    _ensure_app()
    panel = _bare_panel()
    panel.gain_slider.setValue(30)          # 用户意图 30 dB
    assert panel.set_gain_applied(20.0) is True
    assert "20" in panel.gain_val.text()
    tip = panel.gain_val.toolTip()
    assert "未生效" in tip, f"夹值必须告警，实际 tooltip={tip!r}"
    assert "30" in tip and "20" in tip


def test_equal_reading_has_no_warning():
    _ensure_app()
    panel = _bare_panel()
    panel.gain_slider.setValue(20)
    assert panel.set_gain_applied(20.0) is True
    assert "未生效" not in panel.gain_val.toolTip()


def test_unconnected_reading_rejected():
    _ensure_app()
    panel = _bare_panel()
    panel._sdr_connected = False
    assert panel.set_gain_applied(20.0) is False
    assert panel.gain_val.text() == "—"
