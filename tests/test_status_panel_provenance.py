# SPDX-License-Identifier: MIT
"""P2：状态卡 12 条读数必须自带来源/不确定度口径（Task 144）。

spec P2 要求「任何界面数值都要带来源与不确定度」。状态面板统一走
``status_panel._provenance()`` 追加中文后缀，本文件钉两件事：

1. 12 条口径（本机时钟/实测/设备报告/后端报告/设定值/S表校准参考/自动/
   用户配置/GNSS 椭球高/接收机报告/未校准原始读数）逐条落到文本；
2. 空态不许被包装成读数：``—`` 仍是 ``—``，带宽的「自动」不会变成
   「自动（自动）」——加来源不许把「没数据」伪装成「有数据」。

只做纯函数 + 单面板构造，不建 MainWindow。
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import status_panel as sp  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# 12 条读数各自的口径：值 → 带后缀的文本。
_READINGS = [
    ("utc", sp._SRC_LOCAL_CLOCK, "UTC: 12:34:56",
     "UTC: 12:34:56（本机时钟（未校准））"),
    ("rssi-measured", sp._SRC_MEASURED, "-73.0 dBFS",
     "-73.0 dBFS（实测）"),
    ("rssi-mcp", sp._SRC_DEVICE, "42 dBµV", "42 dBµV（设备报告）"),
    ("snr", sp._SRC_BACKEND, "12.0 dB", "12.0 dB（后端报告）"),
    ("frequency", sp._SRC_SETPOINT, "145.500000 MHz",
     "145.500000 MHz（设定值）"),
    ("s-meter", sp._SRC_SMETER, "S9 +0 dB",
     "S9 +0 dB（按 dBFS 校准参考）"),
    ("sample-rate", sp._SRC_DEVICE, "2.400 MS/s", "2.400 MS/s（设备报告）"),
    ("bandwidth", sp._SRC_AUTO, "2.4 kHz", "2.4 kHz（自动）"),
    ("ppm", sp._SRC_USER, "+7 ppm", "+7 ppm（用户配置）"),
    ("gps-alt", sp._SRC_GNSS_ELLIPSOID, "12.5 m",
     "12.5 m（GNSS 椭球高）"),
    ("gps-sats", sp._SRC_RECEIVER, "9", "9（接收机报告）"),
    ("sat-snr", sp._SRC_RECEIVER, "#12 41dB", "#12 41dB（接收机报告）"),
    ("imu", sp._SRC_IMU_RAW, "9.81", "9.81（未校准原始读数）"),
]


@pytest.mark.parametrize(
    "label,source,value,expected", _READINGS,
    ids=[row[0] for row in _READINGS])
def test_provenance_suffix_for_each_reading(label, source, value, expected):
    assert sp._provenance(value, source) == expected


def test_provenance_never_wraps_empty_state():
    """空态一律原样：— 仍是 —；带宽的「自动」不会叠成「自动（自动）」。"""
    assert sp._provenance(None, sp._SRC_MEASURED) == "—"
    assert sp._provenance("", sp._SRC_MEASURED) == "—"
    assert sp._provenance("—", sp._SRC_RECEIVER) == "—"
    assert sp._provenance("自动", sp._SRC_AUTO) == "自动"


@pytest.mark.usefixtures("qapp")
def test_backend_readings_keep_value_and_add_source():
    """后端路径：数值/量纲/精度不变，只多一个来源后缀。"""
    status = SimpleNamespace(
        frequency_hz=145_500_000.0,
        sample_rate_hz=2_400_000.0,
        bandwidth_hz=2_400.0,
        gain_db=20.0,
        agc_enabled=False,
        squelch_db=None,
        volume=0.5,
        snr_db=12.0,
    )
    backend = SimpleNamespace(get_status=lambda: status, ppm=7)
    panel = sp.StatusPanel()
    try:
        panel.update_from_backend(backend)
        assert panel.rx_freq.text() == "145.500000 MHz（设定值）"
        assert panel.freq_value.text() == "145.500000 MHz（设定值）"
        assert panel.rx_srate.text() == "2.400 MS/s（设备报告）"
        assert panel.rx_bw.text() == "2.4 kHz（自动）"
        assert panel.snr_value.text() == "12.0 dB（后端报告）"
        assert panel.rx_ppm.text() == "+7 ppm（用户配置）"

        panel.update_signal_level(-73.0)
        assert panel.rssi_value.text() == "-73.0 dBFS（实测）"
        assert panel.smeter_value.text() == "S9 +0 dB（按 dBFS 校准参考）"

        # 未连接 / 无 status：空态保持「—」，不因加来源而填 0。
        # 注意：update_from_backend(None) 只清 SDR 参数，信号质量组由
        # set_sdr_connected(False) 清（既有分工，本任务不改更新频率/路径）。
        panel.update_from_backend(None)
        for value in (panel.rx_freq, panel.rx_srate, panel.rx_bw,
                      panel.rx_ppm):
            assert value.text() == "—"
        panel.set_sdr_connected(False)
        for value in (panel.snr_value, panel.rssi_value, panel.smeter_value):
            assert value.text() == "—"
    finally:
        panel.deleteLater()


@pytest.mark.usefixtures("qapp")
def test_mcp_status_path_marks_device_report_and_setpoint():
    """MCP 固件路径：RSSI 是设备报告，频率是设定值。"""
    panel = sp.StatusPanel()
    try:
        panel.on_status_updated({
            "rssi": 42, "snr": 9.5, "mode_name": "NFM",
            "freq_display": "145.500 MHz",
        })
        assert panel.rssi_value.text() == "42 dBµV（设备报告）"
        assert panel.snr_value.text() == "9.5 dB（后端报告）"
        assert panel.freq_value.text() == "145.500 MHz（设定值）"
    finally:
        panel.deleteLater()


@pytest.mark.usefixtures("qapp")
def test_gnss_alt_is_ellipsoid_height_and_counts_are_receiver_reported():
    panel = sp.StatusPanel()
    try:
        panel.update_gnss({
            "source": "real",
            "latitude": 31.230416,
            "longitude": 121.473701,
            "altitude_m": 12.5,
            "satellites": 9,
            "hdop": 0.9,
            "satellite_signals": [
                {"id": 12, "snr_db": 41.0},
                {"id": 3, "snr_db": 35.0},
            ],
        })
        assert panel.gps_alt.text() == "12.5 m（GNSS 椭球高）"
        assert panel.gps_sats.text() == "9（接收机报告）"
        assert panel.gps_signals.text() == (
            "#12 41dB  #3 35dB（接收机报告）")

        panel.update_gnss({"source": "none"})
        assert panel.gps_alt.text() == "—"
        assert panel.gps_sats.text() == "—"
        assert panel.gps_signals.text() == "—"
    finally:
        panel.deleteLater()


@pytest.mark.usefixtures("qapp")
def test_imu_axes_are_uncalibrated_raw_readings():
    panel = sp.StatusPanel()
    try:
        panel.on_imu_updated({
            "source": "real",
            "acc": [0.1, 0.2, 9.81],
            "gyr": [1.0, 2.0, 3.0],
            "mag": [10.0, -5.0, 0.0],
            "temp": 31.5,
            "timestamp": 1700000000,
        })
        assert panel.acc_z.text() == "9.81（未校准原始读数）"
        assert panel.gyr_x.text() == "1.0（未校准原始读数）"
        assert panel.mag_y.text() == "-5.0（未校准原始读数）"
        assert panel.imu_temp.text() == "31.5（未校准原始读数）"

        panel.on_imu_updated({"source": "none"})
        assert panel.acc_z.text() == "—"
        assert "[未连接]" in panel.imu_group.title()
    finally:
        panel.deleteLater()


@pytest.mark.usefixtures("qapp")
def test_utc_clock_declares_local_uncalibrated():
    panel = sp.StatusPanel()
    try:
        text = panel.utc_label.text()
        assert text.startswith("UTC: ")
        assert text.endswith("（本机时钟（未校准））")
    finally:
        panel._utc_timer.stop()
        panel.deleteLater()
