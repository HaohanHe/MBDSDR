# SPDX-License-Identifier: MIT
"""Offset Tuning 真值回归（无硬件，offscreen）。

缺口 G02：``RTLSDRBackend.set_offset_tuning`` 旧实现在**检查驱动能力之前**
就写 ``self._offset_tuning = enabled``，驱动不提供该方法时只记 debug 并
``return True``。结果是"勾上了、面板显示开、硬件完全没动"。

反证方式：把 ``self._offset_tuning = bool(enabled)`` 移回能力检查之前，
本文件即红——不支持的后端上状态会变成「开」，下发失败也会把状态改成新值。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


class _DriverNoOffset:
    """真实情形：本机 pyrtlsdr 的 RtlSdrAio 没有 set_offset_tuning。"""


class _DriverWithOffset:
    def __init__(self):
        self.calls = []

    def set_offset_tuning(self, enabled):
        self.calls.append(bool(enabled))


class _DriverThatFails:
    """能调用但下发失败（USB 掉线 / 驱动报错）。"""

    def __init__(self):
        self.fail = True

    def set_offset_tuning(self, enabled):
        if self.fail:
            raise OSError("usb timeout")
        return True


def _backend_with(sdr):
    from mbdsdr_ai.sdr_backend import RTLSDRBackend
    backend = RTLSDRBackend()
    backend._sdr = sdr
    return backend


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.mark.usefixtures("qtapp")
class TestOffsetTruthfulness:
    def test_unsupported_driver_returns_failure_and_keeps_state_off(self, qtapp):
        backend = _backend_with(_DriverNoOffset())

        assert backend.supports_offset_tuning() is False
        assert backend.set_offset_tuning(True) is False, \
            "驱动没有该方法必须返回失败（明确「不支持」），不能再假装成功"
        assert backend.offset_tuning is False, "不支持时状态不得变成开"

        from status_panel import StatusPanel
        panel = StatusPanel()
        panel.update_from_backend(backend)
        assert panel.rx_offset.text() == "关", \
            "状态面板不得把「勾选过」显示为「开」"

    def test_supported_driver_applies_and_shows_on(self, qtapp):
        drv = _DriverWithOffset()
        backend = _backend_with(drv)

        assert backend.supports_offset_tuning() is True
        assert backend.set_offset_tuning(True) is True
        assert drv.calls == [True]
        assert backend.offset_tuning is True

        from status_panel import StatusPanel
        panel = StatusPanel()
        panel.update_from_backend(backend)
        assert panel.rx_offset.text() == "开"

    def test_failed_write_does_not_claim_new_state(self, qtapp):
        drv = _DriverThatFails()
        drv.fail = False
        backend = _backend_with(drv)
        assert backend.set_offset_tuning(True) is True

        drv.fail = True
        assert backend.set_offset_tuning(False) is False, "下发失败必须返回 False"
        assert backend.offset_tuning is True, \
            "下发失败时状态保持上一次真正生效值，不得改成刚被拒的新值"
