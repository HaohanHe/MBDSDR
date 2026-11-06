# SPDX-License-Identifier: MIT
"""分档增益：后端**返回 False** 也必须可见失败，不许把滑杆当硬件读数。

既有 `tests/test_hw_rejection_visible.py` 只覆盖了「setter 抛异常」那一半；
但 `GainStager._set_stage` 内部 catch 住 `device.setGain` 异常后是
**返回 False**（不抛），这才是硬件拒绝的主要形态。旧 `_on_stage_changed`
完全不看返回值 → 100% 静默成功：LNA 标签已改、总增益已变、状态栏说已应用，
硬件其实一动没动。本文件把这条真实路径钉死。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gain_panel import GainPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Device:
    """Soapy 风格假设备：可让 setGain 抛异常、可给 getGain 真回读。"""

    def __init__(self, fail=False, current=12.0, has_get=True, hi=40.0):
        self.fail = fail
        self.current = current
        self.has_get = has_get
        self.hi = hi
        self.writes = []

    def listGains(self, direction, channel):
        return ["LNA", "MIXER", "VGA"]

    def getGainRange(self, direction, channel, name):
        return (0.0, self.hi)

    def setGain(self, direction, channel, value, name):
        if self.fail:
            raise OSError("设备拒绝增益写入")
        self.writes.append((name, float(value)))
        self.current = float(value)
        return True

    def getGain(self, direction, channel, name):
        return self.current


class _RejectingStager:
    """只靠返回值表达失败的桩（连 stage_info/device 都没有）。"""

    def set_lna(self, db):
        return False

    def set_mixer(self, db):
        return False

    def set_vga(self, db):
        return False


def _watch(panel):
    failed, applied, values = [], [], []
    panel.stage_gain_failed.connect(failed.append)
    panel.stage_gain_changed.connect(lambda l, d: applied.append((l, d)))
    panel.value_changed.connect(lambda l, d: values.append((l, d)))
    return failed, applied, values


# ------------------------------------------------------- 真实 GainStager 路径
class TestGainStagerRejection:
    def test_setgain_exception_returns_false_and_is_visible(self, qapp):
        """设备 setGain 抛异常 → stager 返回 False → 面板必须报失败。"""
        dev = _Device(fail=True)
        panel = GainPanel()
        panel.set_backend(dev)
        assert panel._stager is not None
        assert panel._stager.set_lna(25.0) is False, "前置：stager 确实返回 False"
        failed, applied, values = _watch(panel)
        before = panel.total_label.text()

        panel._on_stage_changed("LNA", 25.0)

        assert failed and "未生效" in failed[0]
        assert applied == [] and values == [], "失败不得发任何成功信号"
        assert panel.total_label.text() == before, "失败不得刷新总增益"
        assert "⚠" in panel.lna.value_label.text()
        assert "未生效" in panel.lna.value_label.text()

    def test_rejected_stage_shows_device_readback(self, qapp):
        """有 getGain 的设备：标签显示设备真值，不显示用户请求值。"""
        dev = _Device(fail=True, current=12.0)
        panel = GainPanel()
        panel.set_backend(dev)
        panel._on_stage_changed("LNA", 25.0)
        assert "12.0 dB" in panel.lna.value_label.text()
        assert "25.0" not in panel.lna.value_label.text()
        assert "设备回读" in panel.lna.value_label.toolTip()
        assert "滑杆仍是配置意图" in panel.lna.value_label.toolTip()

    def test_no_readback_shows_unknown_not_zero(self, qapp):
        """无回读能力：不许拿 stager 缓存的 0.0 冒充设备当前值。"""
        panel = GainPanel()
        panel._stager = _RejectingStager()
        failed, applied, values = _watch(panel)
        panel._on_stage_changed("VGA", 30.0)
        assert failed and applied == [] and values == []
        assert panel.vga.value_label.text() == "-- dB ⚠ 未生效"
        assert "未知" in panel.vga.value_label.toolTip()

    def test_no_stager_is_visible_failure(self, qapp):
        panel = GainPanel()
        failed, _, _ = _watch(panel)
        panel._on_stage_changed("LNA", 8.0)
        assert failed and "未生效" in failed[0]
        assert "⚠" in panel.lna.value_label.text()


# ------------------------------------------------------------- 成功路径
class TestGainAppliedTruth:
    def test_success_emits_and_labels_actual_value(self, qapp):
        dev = _Device(current=0.0)
        panel = GainPanel()
        panel.set_backend(dev)
        failed, applied, values = _watch(panel)

        panel._on_stage_changed("LNA", 18.0)

        assert not failed
        assert applied == [("LNA", 18.0)] and values == [("LNA", 18.0)]
        assert panel.lna.value_label.text() == "18.0 dB"
        assert "⚠" not in panel.lna.value_label.text()
        assert panel._applied["LNA"] == 18.0
        assert ("LNA", 18.0) in dev.writes

    def test_clamped_value_is_shown_with_warning(self, qapp):
        """外部（AI/CLI）直接请求 30 dB，设备夹到 20：标签写 20 不写 30，
        滑杆保持原值不被回写。GainPanel 滑杆范围本就等于设备范围，越界
        请求只可能来自绕过 UI 的调用方。"""
        dev = _Device(hi=20.0)
        panel = GainPanel()
        panel.set_backend(dev)
        panel.lna.set_value(15.0)
        slider_before = panel.lna.slider.value()
        panel._on_stage_changed("LNA", 30.0)

        assert panel.lna.value_label.text() == "20.0 dB"
        assert "设备夹到 20.0 dB" in panel.lna.value_label.toolTip()
        assert panel.lna.slider.value() == slider_before, "滑杆不被下发结果回写"
        assert panel._applied["LNA"] == 20.0
        assert ("LNA", 20.0) in dev.writes

    def test_backend_change_clears_stale_failure_mark(self, qapp):
        panel = GainPanel()
        panel._stager = _RejectingStager()
        panel._on_stage_changed("LNA", 20.0)
        assert "⚠" in panel.lna.value_label.text()
        panel.set_backend(None)
        assert panel._applied == {}
        assert panel.lna.value_label.toolTip() == ""
