# SPDX-License-Identifier: MIT
"""RTL-SDR 增益回读真值回归（无硬件，offscreen）。

缺口：``RTLSDRBackend._apply_gain`` 已把请求值吸附到离散档再下发，但基类
``set_gain`` 成功后又把 **限幅后的请求值** 写进 ``status.gain_db``，于是
请求 28.0 dB、驱动只接受 25.0 dB 时，面板仍显示 28.0。

实测（2026-11-11，本机 pyrtlsdr 0.2.93）：
``RtlSdr.set_gain`` 在 Python 层先按 ``gain_values`` 找最近档，只把该档
（0.1 dB 整数）交给 ``rtlsdr_set_tuner_gain``；``get_gain()`` 调
``rtlsdr_get_tuner_gain`` 返回 librtlsdr 记录的**实际写入档位**。请求值
根本不会原样到达驱动，因此回读值确实能揭穿“请求 28 实际 25”。

反证：删掉 ``RTLSDRBackend.set_gain`` 覆写（或删掉 ``_apply_gain`` 里的
回读），``status.gain_db`` 会退回 28.0，本文件即红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


class _SnappingSdr:
    """pyrtlsdr 语义桩：setter 按 gain_values 吸附，getter 返回实际写入档。"""

    def __init__(self, steps_tenths=(0, 250, 500)):
        self.gain_values = list(steps_tenths)
        self._written_tenths = 0

    def set_manual_gain_mode(self, _mode):
        return True

    @property
    def gain(self):
        return self._written_tenths / 10.0

    @gain.setter
    def gain(self, value):
        nearest = min(self.gain_values,
                      key=lambda g: abs(g - 10 * float(value)))
        self._written_tenths = nearest


def _backend(fake, host=None):
    from mbdsdr_ai.sdr_backend import RTLSDRBackend
    backend = RTLSDRBackend(device_index=0, host=host)
    backend._sdr = fake
    # 故意留空：本用例要证明即使我们没有自己的档位表，驱动回读也给出
    # 吸附后的真实档，而不是请求值。
    backend._gain_table_db = []
    backend.status.connected = True
    return backend


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_pyrtlsdr_never_sends_raw_request(monkeypatch):
    """实测：请求 28.0 dB 时 pyrtlsdr 只下发 250（25.0 dB），回读即 25.0。"""
    rt_rtlsdr = pytest.importorskip("rtlsdr.rtlsdr")

    sdr = object.__new__(rt_rtlsdr.RtlSdr)
    sdr.dev_p = object()
    sdr.gain_values = [0, 250, 500]
    written = []

    class _FakeLib:
        def rtlsdr_set_tuner_gain(self, _dev, gain_tenths):
            written.append(int(gain_tenths))
            return 0

        def rtlsdr_get_tuner_gain(self, _dev):
            return written[-1] if written else 0

        def rtlsdr_set_tuner_gain_mode(self, _dev, _enabled):
            return 0

    monkeypatch.setattr(rt_rtlsdr, "librtlsdr", _FakeLib())

    sdr.set_gain(28.0)

    assert written == [250], "请求 28.0 不得原样到达驱动，只能下发支持的离散档"
    assert sdr.get_gain() == pytest.approx(25.0), \
        "get_gain() 回读的是实际写入档位，不是请求值"


def test_backend_status_shows_driver_accepted_gain():
    backend = _backend(_SnappingSdr())

    assert backend.set_gain(28.0) is True

    assert backend.status.gain_db == pytest.approx(25.0), \
        "状态必须写回驱动确认的 25.0，不得停在请求的 28.0"
    assert backend.get_gain() == pytest.approx(25.0)
    assert backend.gain_readback_source(), "本地 USB 必须自报回读来源"


def test_backend_with_own_gain_table_still_reports_snapped():
    backend = _backend(_SnappingSdr())
    backend._gain_table_db = [0.0, 25.0, 50.0]

    assert backend.set_gain(28.0) is True

    assert backend.status.gain_db == pytest.approx(25.0)


def test_rtl_tcp_does_not_claim_readback():
    """rtl_tcp 协议不回执增益：只能显示下发值，并标「未回读」。"""
    backend = _backend(_SnappingSdr(), host="127.0.0.1")

    assert backend.set_gain(28.0) is True

    assert backend.gain_readback_source() == "", \
        "rtl_tcp 没有设备回执，不得冒充已回读"
    assert backend.status.gain_db == pytest.approx(28.0), \
        "无回读通道时状态是下发值（不编造第三个数）"


@pytest.mark.usefixtures("qapp")
class TestGainPanelProvenance:
    def test_panel_shows_snapped_value_and_source(self, qapp):
        from control_panel import ControlPanel
        panel = ControlPanel()
        try:
            panel.set_sdr_connected(True)
            panel.set_gain(25.0)
            assert panel.set_gain_applied(
                25.0, "驱动确认档位（librtlsdr 实际写入的离散档）") is True
            assert panel.gain_val.text() == "25 dB"
            tip = panel.gain_val.toolTip()
            assert "驱动确认" in tip, "有回读通道时必须标明读数来源"
            assert "未回读" not in tip
        finally:
            panel.deleteLater()

    def test_request_28_never_displayed_when_driver_accepts_25(self, qapp):
        from control_panel import ControlPanel
        panel = ControlPanel()
        try:
            panel.set_sdr_connected(True)
            panel.set_gain(28.0)          # 用户意图
            panel.set_gain_applied(25.0)  # 驱动实际
            assert panel.gain_val.text() == "25 dB", \
                "面板必须显示 25，不得显示请求的 28"
            assert "28 dB" in panel.gain_val.toolTip(), "滑杆意图仍要可解释"
            assert "未生效" in panel.gain_val.toolTip()
        finally:
            panel.deleteLater()

    def test_no_readback_backend_keeps_unread_marker(self, qapp):
        from control_panel import ControlPanel
        panel = ControlPanel()
        try:
            panel.set_sdr_connected(True)
            panel.set_gain(30.0)
            assert panel.set_gain_applied(30.0) is True
            tip = panel.gain_val.toolTip()
            assert "未回读" in tip, "无回读通道时必须保留「请求值，未回读」"
            assert "设备回执" not in tip
        finally:
            panel.deleteLater()
