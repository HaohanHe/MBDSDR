"""文案/单位审计红测试（U-02 / U-03 / N-01~N-03 / E-02）。

每条断言对应 docs/copy-audit.md 里的一条要求；改代码前这些断言必须红。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from constants import format_sample_rate  # noqa: E402
import status_panel as sp  # noqa: E402
import main_window as mw  # noqa: E402
from mbdsdr_ai import error_catalog  # noqa: E402

_MAIN = open(os.path.join(_ROOT, "desktop", "main_window.py"),
             encoding="utf-8").read()
_PANEL = open(os.path.join(_ROOT, "desktop", "status_panel.py"),
              encoding="utf-8").read()
_CTRL = open(os.path.join(_ROOT, "desktop", "control_panel.py"),
             encoding="utf-8").read()


def test_u03_sample_rate_is_msps_three_decimals():
    """U-03：采样率一律 MS/s、3 位小数，缺失显「—」。"""
    assert format_sample_rate(2_400_000) == "2.400 MS/s"
    assert format_sample_rate(250_000) == "0.250 MS/s"
    assert format_sample_rate(2_048_000) == "2.048 MS/s"
    assert format_sample_rate(None) == "—"
    assert format_sample_rate(float("nan")) == "—"
    assert sp._fmt_rate(2_400_000) == format_sample_rate(2_400_000)
    assert "format_sample_rate(_rate)" in _CTRL
    assert "format_sample_rate" in _MAIN


def test_n01_freq_precision_is_constant_within_unit():
    """N-01：同一单位内精度恒定，不随数值大小跳变。"""
    assert sp._fmt_freq(145_800_000.0) == "145.800000 MHz"
    assert sp._fmt_freq(98_500_000.0) == "98.500000 MHz"
    assert sp._fmt_freq(500_000.0) == "500.000 kHz"
    assert sp._fmt_freq(None) == "—"
    assert sp._fmt_freq(float("nan")) == "—"


def test_u02_rssi_is_dbfs_and_missing_is_dash_not_zero():
    """U-02：RSSI 一律 dBFS；缺值/NaN 显「—」，绝不用 0 兜底。"""
    assert sp._fmt_dbfs(None) == "—"
    assert sp._fmt_dbfs(float("nan")) == "—"
    assert sp._fmt_dbfs(12.34) == "12.3 dBFS"
    assert mw._fmt_dbfs(None) == "—"
    assert mw._fmt_dbfs(float("nan")) == "—"
    assert mw._fmt_dbfs(12.34) == "12.3 dBFS"
    assert 'status.get("rssi", 0)' not in _MAIN, "缺键不得默认成 0 造假读数"
    assert "rssi_value.setText(_fmt_dbfs(rssi))" in _PANEL
    assert '" dBm"' not in _PANEL and '" dBm"' not in _MAIN


def test_n02_negative_zero_displays_as_zero():
    """N-02：-0.0 / -0 Hz 一律显示为 0，不得让用户读成反向偏移。"""
    assert sp._fmt_dbfs(-0.04) == "0.0 dBFS"
    txt = mw.format_doppler_status(
        {"applied_by": "python", "applied_hz": -0.04})
    assert "-0.0 Hz" not in txt and "0.0 Hz" in txt
    tx = mw.format_tx_doppler({"applied": True, "mode": "on",
                               "nominal_hz": 145_000_000.0,
                               "freq_hz": 145_000_000.0,
                               "delta_hz": -0.04})
    assert "-0.0 Hz" not in tx and "0.0 Hz" in tx


def test_n03_nan_none_never_leak_raw_text():
    """N-03：NaN/None 只能显式空态，不得把 "nan"/"None" 画到界面上。"""
    outputs = [
        sp._fmt_dbfs(float("nan")), sp._fmt_dbfs(None),
        sp._fmt_freq(float("nan")), sp._fmt_rate(None),
        mw._fmt_dbfs(float("nan")), mw.format_tx_doppler(None),
        mw.format_doppler_status({"applied_by": "python", "applied_hz": None}),
    ]
    for t in outputs:
        assert "nan" not in t and "None" not in t, t


def test_e02_parameter_errors_name_object_and_param():
    """E-02：三条「参数不合法」各自说清对象；detail 给出参数名与范围。"""
    tool = error_catalog.user_message(
        "invalid_arguments", tool="tune_fm",
        detail="freq_mhz 需在 88–108 MHz，收到 150")
    field = error_catalog.user_message("bad_field", detail="wer 不存在")
    lib = error_catalog.user_message("bad_param", detail="freq 需 > 0")
    assert "工具" in tool and "tune_fm" in tool and "freq_mhz" in tool
    assert "录音库字段不合法" in field and "wer" in field
    assert "录音库参数不合法" in lib and "freq" in lib
    assert tool != field and field != lib, "同一段文案不得覆盖两种对象"
