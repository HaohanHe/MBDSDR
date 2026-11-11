# SPDX-License-Identifier: MIT
"""Soapy 采样率/增益回读真值回归（无硬件，offscreen）。

缺口 G03：``_apply_sample_rate`` 丢弃 ``setSampleRate()`` 的返回值，基类
``_resolve_applied_sample_rate`` 默认返回请求值；``setGain()`` 后也不读
``getGain()``。Soapy 会按硬件档位吸附，于是界面长期显示一个设备并未接受的
请求值，而 DSP 用另一套速率。

反证方式：删掉返回值捕获（或删掉 ``_resolve_applied_sample_rate`` /
``get_gain`` 覆写），本文件断言即红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


class _FakeSoapy:
    """驱动会吸附：请求 2.4 MS/s，实际只接受 1.0 MS/s；增益实际 19.2 dB。"""

    def __init__(self, actual_rate=1_000_000.0, actual_gain=19.2):
        self.actual_rate = actual_rate
        self.actual_gain = actual_gain
        self.rate_requests = []
        self.gain_requests = []

    def setSampleRate(self, _rx, _chan, rate):
        self.rate_requests.append(float(rate))
        return self.actual_rate

    def setGain(self, _rx, _chan, db):
        self.gain_requests.append(float(db))

    def getGain(self, _rx, _chan):
        return self.actual_gain


def _backend(fake):
    from mbdsdr_ai.sdr_backend import SoapySDRBackend
    backend = SoapySDRBackend(
        device_args="driver=fake",
        device_info={"driver": "fake", "label": "Fake Soapy",
                     "sample_rate_range": (225_001, 3_200_000),
                     "gain_range": (0.0, 60.0)},
    )
    backend._sdr = fake
    backend.status.connected = True
    return backend


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.mark.usefixtures("qtapp")
class TestSoapyReadbackTruth:
    def test_snapped_rate_is_shown_not_requested(self, qtapp):
        fake = _FakeSoapy(actual_rate=1_000_000.0)
        backend = _backend(fake)

        assert backend.set_sample_rate(2_400_000.0) is True
        assert fake.rate_requests == [2_400_000.0], "请求值仍要下发"
        assert backend.get_sample_rate() == pytest.approx(1_000_000.0), \
            "采样率读数必须是驱动实际接受值，不是请求值"

        from constants import format_sample_rate
        from status_panel import StatusPanel
        panel = StatusPanel()
        panel.update_from_backend(backend)
        assert panel.rx_srate.text() == format_sample_rate(1_000_000.0), \
            "界面必须显示实际值而不是 2.400 MS/s"

    def test_no_driver_reply_falls_back_to_request(self, qtapp):
        """驱动不给回值（返回 None）时才退回请求值，不编造第三个数。"""
        fake = _FakeSoapy(actual_rate=None)
        backend = _backend(fake)
        assert backend.set_sample_rate(2_400_000.0) is True
        assert backend.get_sample_rate() == pytest.approx(2_400_000.0)

    def test_gain_readback_overrides_request(self, qtapp):
        fake = _FakeSoapy(actual_gain=19.2)
        backend = _backend(fake)

        assert backend.set_gain(30.0) is True
        assert backend.get_gain() == pytest.approx(19.2), \
            "增益读数必须回读 getGain()，不得停在请求的 30 dB"
        assert backend.status.gain_db == pytest.approx(19.2), \
            "软件状态也要对齐驱动实际值，避免状态面板另说一套"
