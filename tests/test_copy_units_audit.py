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


# ---------------------------------------------------------------------------
# 批次 1：跨端同一物理量同一单位（C++ 状态条 / 移动端）
# ---------------------------------------------------------------------------
_CPP_STATUS = open(os.path.join(_ROOT, "cpp", "src", "ui", "status_format.h"),
                   encoding="utf-8").read()
_MOBILE_SPECTRUM = open(os.path.join(_ROOT, "mobile", "lib", "pages",
                                     "spectrum_page.dart"), encoding="utf-8").read()
_MOBILE_INFO = open(os.path.join(_ROOT, "mobile", "lib", "widgets",
                                 "device_info_card.dart"), encoding="utf-8").read()


def test_u02_cpp_status_strip_labels_rssi_and_snr_units():
    """U-02：C++ 状态条与天空信息卡同一物理量、同一单位（dBFS / dB）。"""
    assert 'QString("RSSI %1 dBFS")' in _CPP_STATUS
    assert 'QString("SNR %1 dB")' in _CPP_STATUS


def test_u02_mobile_rssi_is_dbfs_with_one_decimal():
    """U-02：移动端 RSSI 与桌面同口径（dBFS、1 位小数）。"""
    assert "'RSSI ${rssi.toStringAsFixed(1)} dBFS'" in _MOBILE_SPECTRUM
    assert "toStringAsFixed(0)} dB'" not in _MOBILE_SPECTRUM


def test_u03_mobile_sample_rate_is_ms_per_s_three_decimals():
    """U-03：移动端采样率与桌面/C++ 同口径（MS/s、3 位小数、空格）。"""
    assert "toStringAsFixed(3)} MS/s" in _MOBILE_SPECTRUM
    assert "toStringAsFixed(2)}Msps" not in _MOBILE_SPECTRUM
    assert "toStringAsFixed(2)} Msps" not in _MOBILE_SPECTRUM
    assert "toStringAsFixed(3)} MS/s" in _MOBILE_INFO


# ---------------------------------------------------------------------------
# 批次 2：术语、错误提示、数字格式
# ---------------------------------------------------------------------------
_HELP = open(os.path.join(_ROOT, "desktop", "help_panel.py"), encoding="utf-8").read()
_NTRIP = open(os.path.join(_ROOT, "desktop", "ntrip_panel.py"), encoding="utf-8").read()
_SETTINGS = open(os.path.join(_ROOT, "desktop", "settings_panel.py"),
                 encoding="utf-8").read()
_MOBILE_HELP = open(os.path.join(_ROOT, "mobile", "lib", "pages", "help_page.dart"),
                    encoding="utf-8").read()
_CTRL = open(os.path.join(_ROOT, "desktop", "control_panel.py"), encoding="utf-8").read()


def test_e06_ntrip_errors_name_next_step_without_internal_jargon():
    assert 'err = "sourcetable 中未找到 STR 挂载点记录。"' not in _NTRIP
    assert "请核对地址与端口" in _NTRIP
    assert 'err = f"未知错误: {_clean_exc(e)}"' not in _NTRIP


def test_e03_import_rejection_names_reason_and_next_step():
    assert 'res.error or "未知错误"' not in _SETTINGS
    assert "导入被拒绝：" in _SETTINGS


def test_t02_help_uses_real_topbar_demod_labels():
    assert "顶栏「模式」" not in _HELP
    assert "FM / WFM / NFM / AM / USB / LSB / CW" in _HELP


def test_t01_help_separates_sky_tab_from_planetarium():
    assert "打开天空模式" not in _HELP
    assert "星象仪模式" in _HELP
    assert "天空模式" not in _HELP


def test_t04_filter_control_is_not_called_vfo_bandwidth():
    assert 'self.vfo_bw_label = QLabel("VFO 带宽")' not in _CTRL
    assert 'self.vfo_bw_label = QLabel("解调带宽")' in _CTRL


def test_u06_mobile_help_squelch_and_vfo_terms_are_correct():
    assert "-100 为关闭" not in _MOBILE_HELP
    assert "绝对电平" in _MOBILE_HELP
    assert "预存的频率+模式预设" not in _MOBILE_HELP


def test_n01_cpp_status_freq_six_decimals():
    assert 'QString("%1 MHz").arg(hz / 1e6, 0, \'f\', 6)' in _CPP_STATUS


def test_n01_mobile_current_freq_six_decimals():
    assert "toStringAsFixed(6)} MHz" in _MOBILE_SPECTRUM
    assert "toStringAsFixed(6)} MHz" in _MOBILE_INFO


def test_t06_squelch_states_are_chinese_and_single_vocabulary():
    assert 'QStringLiteral("静噪关闭")' in _CPP_STATUS
    assert 'QStringLiteral("静噪 有信号")' in _CPP_STATUS
    assert 'QStringLiteral("静噪 静音")' in _CPP_STATUS
    assert "SQ OPEN" not in _MOBILE_SPECTRUM
    assert "'静噪 静音'" in _MOBILE_SPECTRUM
