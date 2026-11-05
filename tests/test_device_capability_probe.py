# SPDX-License-Identifier: MIT
"""
tests/test_device_capability_probe.py
=====================================
设备能力表「取到什么 / 失败回落什么 / UI 显示什么 / 能不能调谐」三态实测。

全部为**模拟**：用假 SoapySDR 模块 / 假设备 dict 打桩，不接真机。
三种情况：
  1. 正常设备（probe 成功）—— 采样率/增益/带宽/天线口都拿到，状态=probed；
  2. 能力查询失败的假设备 —— 字段必须是「未知」，**不得回落成具体数值**；
  3. 只报部分能力的假设备 —— 已知项显数值，未知项显「未知」。

红线：未知项不许显示具体数值、不许按未知范围判定「可调谐/不可调谐」。
"""
from __future__ import annotations

import sys
import types

import pytest

_ROOT_DIR = __file__.rsplit("tests", 1)[0]
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)


# ---------------------------------------------------------------------------
# 用户真实配置隔离（构造面板会触发 DesktopSettings/QSettings 写盘防抖）
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolated(isolated_profile):
    yield


# ---------------------------------------------------------------------------
# 假 SoapySDR（复刻 SWIG 形态：Range 有 minimum()/maximum()）
# ---------------------------------------------------------------------------
class _Range:
    def __init__(self, lo, hi):
        self._lo, self._hi = float(lo), float(hi)

    def minimum(self):
        return self._lo

    def maximum(self):
        return self._hi


class _Probe:
    """probe 行为可配置：正常 / 全失败 / 只报部分。"""

    def __init__(self, mode: str = "full"):
        self.mode = mode

    def getFrequencyRange(self, direction, chan):
        if self.mode == "all_fail":
            raise RuntimeError("驱动查询失败（模拟）")
        return [_Range(24e6, 1766e6)]

    def getSampleRateRange(self, direction, chan):
        if self.mode in ("all_fail", "no_rate"):
            raise RuntimeError("采样率查询失败（模拟）")
        return [_Range(225001, 3200000)]

    def getGainRange(self, direction, chan, name=None):
        if self.mode == "all_fail":
            raise RuntimeError("增益查询失败（模拟）")
        return _Range(0.0, 49.6)

    def getBandwidthRange(self, direction, chan):
        if self.mode in ("all_fail", "no_rate"):
            raise RuntimeError("带宽查询失败（模拟）")
        return _Range(200000, 3200000)

    def listAntennas(self, direction, chan):
        if self.mode in ("all_fail", "no_rate"):
            raise RuntimeError("天线口查询失败（模拟）")
        return ["RX"]

    def getNumTxChannels(self, direction):
        if self.mode == "all_fail":
            raise RuntimeError("TX 通道查询失败（模拟）")
        return 0


class _FakeSoapy(types.ModuleType):
    SOAPY_SDR_RX = 1
    SOAPY_SDR_TX = 2

    class Device:
        _MODE = "full"

        @classmethod
        def enumerate(cls, args=None):
            return [{"driver": "rtlsdr", "label": "Fake RTL",
                     "serial": "FAKE-1", "manufacturer": "Fake",
                     "product": "Fake RTL"}]

        def __init__(self, args):
            self._probe = _Probe(self._MODE)

        def __getattr__(self, name):
            return getattr(self._probe, name)


def _install(mode: str):
    fake = _FakeSoapy("SoapySDR")
    fake.Device._MODE = mode
    return fake


def _enumerate(mode: str, monkeypatch):
    fake = _install(mode)
    monkeypatch.setitem(sys.modules, "SoapySDR", fake)
    from mbdsdr_ai.sdr_backend import SoapySDRBackend
    return SoapySDRBackend.list_devices()


# ---------------------------------------------------------------------------
# 1. 能力报告数据层
# ---------------------------------------------------------------------------
def test_normal_device_probe_reports_all_fields():
    device = {
        "driver": "rtlsdr", "label": "RTL",
        "sample_rate_range": (225001, 3200000),
        "gain_range": (0.0, 49.6),
        "bandwidth_range": (200000, 3200000),
        "freq_range": (24e6, 1766e6),
        "antennas": ["RX"],
        "tx_channels": 0,
        "capability_status": {
            "sample_rate_range": "probed", "gain_range": "probed",
            "bandwidth_range": "probed", "freq_range": "probed",
            "antenna": "probed", "tx": "probed",
        },
    }
    from mbdsdr_ai.device_capabilities import build_capability_report
    report = build_capability_report(device)
    assert report["sample_rate_range"].known
    assert report["sample_rate_range"].value == (225001, 3200000)
    assert report["gain_range"].value == (0.0, 49.6)
    assert report["bandwidth_range"].known
    assert report["antenna"].known
    assert report["tx"].known
    # 已知项才许出现数值
    summary = report["sample_rate_range"].text
    assert "225.0 kHz" in summary and "3.20 MHz" in summary


def test_unknown_capability_has_no_numbers():
    """能力未知时不许装作知道：text 只能是「未知」，不能出现任何量程数字。"""
    from mbdsdr_ai.device_capabilities import build_capability_report
    report = build_capability_report({
        "driver": "fake",
        "capability_status": {"sample_rate_range": "unknown"},
        "capability_note": {"sample_rate_range": "打开设备查询失败：模拟"},
    })
    f = report["sample_rate_range"]
    assert not f.known
    assert f.value is None
    assert f.text.startswith("未知")
    for bad in ("225001", "3200000", "1e3", "61.44", "0.0", "49.6"):
        assert bad not in f.text


def test_partial_probe_only_numeric_fields_displayed():
    from mbdsdr_ai.device_capabilities import build_capability_report
    report = build_capability_report({
        "driver": "fake",
        "sample_rate_range": (225001, 3200000),
        "gain_range": (0.0, 49.6),
        "capability_status": {
            "sample_rate_range": "probed", "gain_range": "probed",
            "bandwidth_range": "unknown", "antenna": "unknown", "tx": "unknown",
        },
    })
    assert report["sample_rate_range"].known
    assert report["gain_range"].known
    for key in ("bandwidth_range", "antenna", "tx"):
        assert not report[key].known, f"{key} 未探测到却被当成已知"
        assert report[key].value is None
        assert "225001" not in report[key].text
        assert "49.6" not in report[key].text


def test_tunability_is_none_when_range_unknown():
    from mbdsdr_ai.device_capabilities import (
        build_capability_report, check_range,
    )
    report = build_capability_report({"driver": "fake"})
    verdict, reason = check_range(report, "sample_rate_range", 2_048_000.0)
    assert verdict is None, "范围未知却给出了可调/不可调判定 = 装懂"
    assert "未知" in reason
    assert "2048000" not in reason


def test_check_range_rejects_out_of_range_when_known():
    from mbdsdr_ai.device_capabilities import build_capability_report, check_range
    report = build_capability_report({
        "sample_rate_range": (225001, 3200000),
        "capability_status": {"sample_rate_range": "probed"},
    })
    verdict, reason = check_range(report, "sample_rate_range", 10e6)
    assert verdict is False
    assert "225" in reason or "3200000" in reason


def test_tx_channels_zero_is_known_no_tx():
    from mbdsdr_ai.device_capabilities import build_capability_report
    report = build_capability_report({
        "tx_channels": 0,
        "capability_status": {"tx": "probed"},
    })
    assert report["tx"].known
    assert "不可发" in report["tx"].text


# ---------------------------------------------------------------------------
# 2. 假 Soapy 三态：正常 / 全失败 / 部分
# ---------------------------------------------------------------------------
def test_fake_soapy_probe_success_sets_probed(monkeypatch):
    devs = _enumerate("full", monkeypatch)
    assert len(devs) == 1
    d = devs[0]
    assert d["sample_rate_range"] == (225001, 3200000)
    assert d["gain_range"] == (0.0, 49.6)
    assert d["bandwidth_range"] == (200000, 3200000)
    assert d["antennas"] == ["RX"]
    assert d["capability_status"]["sample_rate_range"] == "probed"


def test_fake_soapy_probe_failure_marks_unknown_not_default(monkeypatch):
    """查询失败 → 字段必须是 None（未知），不得回落成宽范围默认值。"""
    devs = _enumerate("all_fail", monkeypatch)
    d = devs[0]
    assert d["sample_rate_range"] is None
    assert d["gain_range"] is None
    assert d["bandwidth_range"] is None
    assert d["freq_range"] is None
    assert d["capability_status"]["sample_rate_range"] == "unknown"
    assert "失败" in d["capability_note"]["sample_rate_range"]
    # 以前这里会回落成 (1e3, 61.44e6)/(0,60)，UI 因此显示假数值
    assert d["sample_rate_range"] != (1e3, 61.44e6)
    assert d["gain_range"] != (0.0, 60.0)


def test_fake_soapy_partial_only_rate_and_gain(monkeypatch):
    devs = _enumerate("no_rate", monkeypatch)
    d = devs[0]
    assert d["capability_status"]["sample_rate_range"] == "unknown"
    assert d["capability_status"]["gain_range"] == "probed"
    assert d["sample_rate_range"] is None
    assert d["gain_range"] == (0.0, 49.6)


def test_cli_enumeration_marks_capabilities_unknown(monkeypatch):
    from unittest import mock
    stdout = ("Found device 0:\n"
              "  :driver=rtlsdr\n"
              "  :label=Fake RTL\n"
              "  :serial=FAKE-1\n")
    proc = mock.Mock(returncode=0, stdout=stdout, stderr="")
    with mock.patch("subprocess.run", return_value=proc):
        from mbdsdr_ai.sdr_backend import SoapySDRBackend
        devs = SoapySDRBackend._enumerate_via_cli()
    d = devs[0]
    assert d["sample_rate_range"] is None
    assert d["capability_status"]["sample_rate_range"] == "unknown"
    assert "未打开设备" in d["capability_note"]["sample_rate_range"]


# ---------------------------------------------------------------------------
# 3. UI 显示：module_panel 与 device_panel
# ---------------------------------------------------------------------------
def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def test_no_device_empty_state_has_no_numbers(monkeypatch):
    """无设备（模拟空枚举）：如实空态，不许出现任何能力数字。"""
    _app()
    import mbdsdr_ai.sdr_backend as sdr_backend
    from module_panel import ModulePanel

    monkeypatch.setattr(sdr_backend, "enumerate_all_sdr_devices", lambda: [])
    p = ModulePanel()
    p.refresh_devices()
    text = p.device_info.text()
    assert "未检测到" in text or "未发现" in text
    assert "225001" not in text and "49.6" not in text
    p.close()


def test_module_panel_shows_unknown_not_zero_tuple(monkeypatch):
    _app()
    import mbdsdr_ai.sdr_backend as sdr_backend
    from module_panel import ModulePanel

    monkeypatch.setattr(sdr_backend, "enumerate_all_sdr_devices",
                        lambda: [{"driver": "fake", "label": "Fake"}])
    p = ModulePanel()
    p.refresh_devices()
    text = p.device_info.text()
    assert "未知" in text
    assert "(0.0, 0.0)" not in text
    assert "None" not in text
    assert "225001" not in text
    p.close()


def test_module_panel_shows_probed_ranges(monkeypatch):
    _app()
    import mbdsdr_ai.sdr_backend as sdr_backend
    from module_panel import ModulePanel

    monkeypatch.setattr(sdr_backend, "enumerate_all_sdr_devices",
                        lambda: [{"driver": "fake", "label": "Fake",
                                  "sample_rate_range": (225001, 3200000),
                                  "gain_range": (0.0, 49.6),
                                  "capability_status": {
                                      "sample_rate_range": "probed",
                                      "gain_range": "probed"}}])
    p = ModulePanel()
    p.refresh_devices()
    text = p.device_info.text()
    assert "225.0 kHz" in text and "3.20 MHz" in text
    assert "0.0–49.6 dB" in text
    p.close()


def test_device_panel_unknown_range_does_not_block_and_records_unknown(monkeypatch):
    """未知范围：不拦连接（无法校验），但必须记录「未知」而不是假装校验通过。"""
    _app()
    import desktop.device_panel as dp

    monkeypatch.setattr(dp, "enumerate_all_sdr_devices",
                        lambda: [{"driver": "fake", "label": "Fake"}])
    panel = dp.DevicePanel()
    seen = []
    panel.connect_requested.connect(lambda d: seen.append(d))
    panel.sr_edit.setText("2048000")
    panel._on_connect_clicked()
    assert seen, "范围未知时不应阻断连接"
    verdict, reason = panel.last_capability_verdict()
    assert verdict is None
    assert "未知" in reason
    assert "2048000" not in reason
    panel.close()


def test_device_panel_known_range_rejects_out_of_range(monkeypatch):
    _app()
    import desktop.device_panel as dp

    monkeypatch.setattr(dp, "enumerate_all_sdr_devices",
                        lambda: [{"driver": "fake", "label": "Fake",
                                  "sample_rate_range": (225001, 3200000),
                                  "capability_status": {
                                      "sample_rate_range": "probed"}}])
    panel = dp.DevicePanel()
    seen = []
    panel.connect_requested.connect(lambda d: seen.append(d))
    warned = []
    monkeypatch.setattr(dp.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a))
    panel.sr_edit.setText("10000000")
    panel._on_connect_clicked()
    assert not seen
    assert warned
    assert "超出" in warned[-1][1]
    panel.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
