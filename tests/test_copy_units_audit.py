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
    """U-02：引擎/IQ 路径 dBFS；缺值/NaN 显「—」，绝不用 0 兜底。"""
    assert sp._fmt_dbfs(None) == "—"
    assert sp._fmt_dbfs(float("nan")) == "—"
    assert sp._fmt_dbfs(12.34) == "12.3 dBFS"
    assert mw._fmt_dbfs(None) == "—"
    assert mw._fmt_dbfs(float("nan")) == "—"
    assert mw._fmt_dbfs(12.34) == "12.3 dBFS"
    assert 'status.get("rssi", 0)' not in _MAIN, "缺键不得默认成 0 造假读数"
    # 桌面 MCP 路径是 SI4732 dBµV，不是 dBFS；缺值仍是「—」。
    assert "rssi_value.setText(_fmt_rssi_mcp(rssi))" in _PANEL
    assert '" dBm"' not in _PANEL and '" dBm"' not in _MAIN


def test_u02_mcp_rssi_is_dbuV_and_missing_has_no_digits():
    """U-02：MCP 固件 RSSI = dBµV（0–127，整数）；无读数不得出现数字。"""
    assert sp._fmt_rssi_mcp(None) == "—"
    assert sp._fmt_rssi_mcp(float("nan")) == "—"
    assert sp._fmt_rssi_mcp(42) == "42 dBµV"
    assert mw._fmt_rssi_mcp(None) == "—"
    assert mw._fmt_rssi_mcp(42.4) == "42 dBµV"
    # 无读数输出里不许有阿拉伯数字（0 / 0.0 都会被读成真实读数）
    for dash in (sp._fmt_rssi_mcp(None), sp._fmt_dbfs(None),
                 mw._fmt_rssi_mcp(None), mw._fmt_dbfs(None)):
        assert not any(ch.isdigit() for ch in dash), dash


def test_u02_same_screen_has_one_rssi_unit_and_never_dBm():
    """U-02：单次渲染只出现一种 RSSI 单位；任何 RSSI 文案不得写 dBm。"""
    mcp_out = sp._fmt_rssi_mcp(42)
    engine_out = sp._fmt_dbfs(-45.2)
    assert "dBµV" in mcp_out and "dBFS" not in mcp_out
    assert "dBFS" in engine_out and "dBµV" not in engine_out
    for out in (mcp_out, engine_out):
        units = [u for u in ("dBFS", "dBµV", "dBm") if u in out]
        assert len(units) == 1, f"同屏混用单位: {out}"
        assert "dBm" not in out
    # 固件证据：SI4732 RSSI 单位注释必须留在数据源旁
    _FW = open(os.path.join(_ROOT, "ai-sdr-mini-kicad",
                            "ai_sdr_mini_firmware_v0.5_WebOTA.ino"),
               encoding="utf-8", errors="ignore").read()
    assert "0-127 dBuV" in _FW or "0-127 dBµV" in _FW


def test_u02_status_defaults_are_none_and_format_has_no_fake_minus100():
    """U-02：SDRStatus 默认不是 -100.0；状态输出缺值显「—」。"""
    from mbdsdr_ai.sdr_backend import SDRStatus
    from mbdsdr_ai.sdr_tools import _format_status
    st = SDRStatus(sample_rate_hz=2_400_000.0)
    assert st.rssi_db is None and st.snr_db is None
    txt = _format_status(st)
    assert "RSSI: —" in txt and "SNR: —" in txt
    assert "-100.0" not in txt
    assert 'self.status.rssi_unit = "dBµV"' in open(
        os.path.join(_ROOT, "mbdsdr_ai", "sdr_backend.py"),
        encoding="utf-8").read()


def test_u02_cli_has_no_minus_one_fake_rssi_and_labels_dbuV():
    """U-02：CLI 扫描缺值存 None 显「—」，并标 dBµV。"""
    import mbdsdr_mcp_client as cli
    _CLI = open(os.path.join(_ROOT, "mbdsdr_mcp_client.py"),
                encoding="utf-8").read()
    assert 'status.get("rssi", -1)' not in _CLI
    assert 'status.get("rssi")' in _CLI
    assert "dBµV" in _CLI
    assert cli._fmt_mcp_readout(None, 0, "dBµV") == "—"
    assert cli._fmt_mcp_readout(42, 0, "dBµV") == "42 dBµV"
    assert not any(ch.isdigit() for ch in cli._fmt_mcp_readout(None, 0, "dBµV"))


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
_CPP_SPECTRUM = open(os.path.join(_ROOT, "cpp", "src", "ui",
                                  "spectrum_widget.cpp"), encoding="utf-8").read()


def test_u02_cpp_status_strip_labels_rssi_and_snr_units():
    """U-02：C++ 状态条与天空信息卡同一物理量、同一单位（dBFS / dB）。"""
    assert 'QString("RSSI %1 dBFS")' in _CPP_STATUS
    assert 'QString("SNR %1 dB")' in _CPP_STATUS


def test_u02_mobile_rssi_is_dbfs_with_one_decimal():
    """U-02：移动端 RSSI 与桌面 IQ 路径同口径（dBFS、1 位小数）；无帧显「—」。"""
    assert "'RSSI ${rssi.toStringAsFixed(1)} dBFS'" in _MOBILE_SPECTRUM
    assert "toStringAsFixed(0)} dB'" not in _MOBILE_SPECTRUM
    assert "'RSSI —'" in _MOBILE_SPECTRUM
    assert "'RSSI --'" not in _MOBILE_SPECTRUM


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


# ---------------------------------------------------------------------------
# 批次 3：单位空格（U-07）与书签失败提示（E-04）
# ---------------------------------------------------------------------------
_BOOKMARK = open(os.path.join(_ROOT, "desktop", "bookmark_panel.py"),
                 encoding="utf-8").read()


def test_u07_gain_unit_has_a_space_but_combo_stays_layout_locked():
    # 带宽档位加空格会把 freq_readout 顶宽 13px（test_ui_layout_locks 4 档全红），
    # 视觉冻结期间维持原字面量，等解除冻结再统一。
    assert '"2.4kHz (SSB)"' in _CTRL
    assert '"2.4 kHz (SSB)"' not in _CTRL
    assert 'QLabel("0dB")' not in _CTRL
    assert 'QLabel("0 dB")' in _CTRL
    assert "G ${controller.gainDb.toStringAsFixed(1)}dB" not in _MOBILE_SPECTRUM
    assert "增益 ${controller.gainDb.toStringAsFixed(1)} dB" in _MOBILE_SPECTRUM


def test_e04_bookmark_failure_says_what_survives_and_what_to_do():
    assert "书签初始化失败" not in _BOOKMARK
    assert "书签加载失败：" in _BOOKMARK
    assert "已有书签未被改动" in _BOOKMARK
    assert "检查该目录是否可读写" in _BOOKMARK


def test_discoverability_ports_are_findable_from_settings_tab():
    """「设置」页签必须指向服务端口所在页；两处端口框必须有用途 tooltip。"""
    assert "服务开关与端口见「服务设置」页签。" in _SETTINGS
    assert "远程控制 7356" in _SETTINGS and "GQRX/hamlib" in _SETTINGS
    assert "Web 8080" in _SETTINGS and "HTTP/WS" in _SETTINGS
    assert "重启远程控制服务" in _SETTINGS
    assert "重启 Web 服务" in _SETTINGS


# ---------------------------------------------------------------------------
# 批次 4：U-03 全仓采样率量纲收口
# ---------------------------------------------------------------------------
_SDR_TOOLS = open(os.path.join(_ROOT, "mbdsdr_ai", "sdr_tools.py"),
                  encoding="utf-8").read()


def test_u03_core_sample_rate_formatter_is_ms_per_s_and_missing_dash():
    """U-03：核心工具采样率也走 MS/s、3 位小数；缺值「—」。"""
    from mbdsdr_ai.sdr_tools import _fmt_sample_rate
    assert _fmt_sample_rate(2_400_000) == "2.400 MS/s"
    assert _fmt_sample_rate(2_048_000) == "2.048 MS/s"
    assert _fmt_sample_rate(250_000) == "0.250 MS/s"
    assert _fmt_sample_rate(None) == "—"
    assert _fmt_sample_rate(float("nan")) == "—"


def test_u03_sample_rate_copy_never_uses_mhz_or_msps():
    """U-03：任何「采样率 + 数字单位」不得出现 MHz/Msps/kHz。

    豁免同行的带宽值（如滤波带宽 12.5kHz）、音频采样率（音频惯例 kHz）
    与 RDS 副载波偏移（57 kHz，是频率不是采样率）。
    """
    import re
    bad = re.compile(r"采样率[^\n]{0,40}\d(?:[^\n]{0,8})(MHz|Msps|kHz)")
    excused = ("带宽", "音频", "副载波")
    hits = [ln for ln in _SDR_TOOLS.splitlines()
            if bad.search(ln) and not any(e in ln for e in excused)]
    assert not hits, "采样率用了频率量纲:\n" + "\n".join(hits)
    for src in (_MOBILE_SPECTRUM, _MOBILE_INFO):
        assert "Msps" not in src and "MSps" not in src
    assert "Msps" not in _CPP_STATUS


def test_u03_same_value_renders_identically_on_three_fronts():
    """U-03：同一采样率在桌面 / C++ / 移动端写法一致（2.400 MS/s）。"""
    desktop = format_sample_rate(2_400_000)
    cpp = "2.400 MS/s"      # fmtStripSampleRate: hz/1e6, f, 3 + " MS/s"
    mobile = "2.400 MS/s"   # toStringAsFixed(3) + " MS/s"
    assert desktop == cpp == mobile
    # C++ 与移动端确实按 3 位小数 + MS/s 构造，而非只是常量巧合
    assert "QString(\"%1 MS/s\").arg(hz / 1e6, 0, 'f', 3)" in _CPP_STATUS
    assert "toStringAsFixed(3)} MS/s" in _MOBILE_SPECTRUM


def test_u03_cpp_spectrum_info_strip_labels_fs_as_ms_per_s():
    """U-03：C++ 频谱信息条里的 Fs 是采样率，必须 MS/s + 3 位小数（不是 MHz）。"""
    assert 'QString("%1  Fs=%2 MS/s  F0=%3 MHz  N=%4")' in _CPP_SPECTRUM
    assert "Fs=%2 MHz" not in _CPP_SPECTRUM
    assert ".arg(frame.sampleRateHz / 1e6, 0, 'f', 3)" in _CPP_SPECTRUM


def test_u03_status_output_uses_ms_per_s_for_sample_rate():
    from mbdsdr_ai.sdr_backend import SDRStatus
    from mbdsdr_ai.sdr_tools import _format_status
    txt = _format_status(SDRStatus(sample_rate_hz=2_048_000.0))
    assert "采样率: 2.048 MS/s" in txt
    assert "采样率: 2.048 MHz" not in txt


# ---------------------------------------------------------------------------
# 批次 5：缺值不得冒充读数（APT 统计 / 表单预填）
# ---------------------------------------------------------------------------
def test_u02_apt_stats_missing_values_render_dash():
    """U-02：放射性统计（行数/锁相）缺值显示「—」，不显示 0 / 0%。"""
    _SAT_IMG = open(os.path.join(_ROOT, "desktop", "satellite_image_panel.py"),
                    encoding="utf-8").read()
    _WEATHER = open(os.path.join(_ROOT, "desktop", "weather_panel.py"),
                    encoding="utf-8").read()
    for src in (_SAT_IMG, _WEATHER):
        assert "get('lines_aligned',0)" not in src
        assert "get('lock_ratio',0)" not in src
        assert 'get("lock_ratio", 0.0)' not in src
        assert 'get("lines_aligned", 0)' not in src
    assert "行数: {'—' if lines is None else lines}" in _SAT_IMG
    assert "同步: {'—' if lock is None else f'{lock:.0%}'}" in _SAT_IMG


def test_u02_satellite_form_prefill_uses_empty_placeholder():
    """U-02：卫星参数表单缺值不得预填 0.000 / 0.0。"""
    _WEATHER = open(os.path.join(_ROOT, "desktop", "weather_panel.py"),
                    encoding="utf-8").read()
    assert "get('freq_mhz', 0)" not in _WEATHER
    assert "get('symrate_ksps', 0)" not in _WEATHER


def test_u03_troubleshooting_error_hint_uses_ms_per_s():
    """U-03：错误建议里的采样率档位不得写 MHz。"""
    _TROUBLE = open(os.path.join(_ROOT, "desktop", "troubleshooting.py"),
                    encoding="utf-8").read()
    assert "如 2.048 MHz" not in _TROUBLE
    assert "如 2.048 MS/s" in _TROUBLE


# ---------------------------------------------------------------------------
# 批次 6：解码能力规则文案的采样率量纲（desktop/capability_catalog.py:180 直接展示）
# ---------------------------------------------------------------------------
def test_u03_decoder_rule_copy_uses_ms_per_s_not_mhz():
    """U-03：ADS-B 采样率规则文案不得写 MHz；能力目录会原样展示 sample_rate_rule。"""
    for name in ("adsb.py", "decode_router.py", "decode_pipeline.py"):
        src = open(os.path.join(_ROOT, "mbdsdr_ai", name), encoding="utf-8").read()
        for bad in ("fs ≥ 2MHz", "fs=2MHz", "fs=2/4MHz", "2MHz 或 4MHz"):
            assert bad not in src, f"{name}: 仍写 {bad}"
    _ADSB = open(os.path.join(_ROOT, "mbdsdr_ai", "adsb.py"), encoding="utf-8").read()
    _ROUTER = open(os.path.join(_ROOT, "mbdsdr_ai", "decode_router.py"),
                   encoding="utf-8").read()
    _PIPE = open(os.path.join(_ROOT, "mbdsdr_ai", "decode_pipeline.py"),
                 encoding="utf-8").read()
    assert "fs ≥ 2 MS/s 且为 1 MS/s 整数倍" in _ADSB
    assert "fs ≥ 2 MS/s 且为 1 MS/s 整数倍（Mode-S 1Mbit/s 前导 8us）" in _ROUTER
    assert "fs=2 MS/s 或 4 MS/s" in _ROUTER
    assert "fs ≥ 2 MS/s 且为 1 MS/s 整数倍（1Mbit/s PPM）" in _PIPE


def test_u02_gnss_psd_fields_never_labeled_dbm():
    """U-02：GNSS PSD 字段名沿用 *_dbm，但注释与展示必须写「相对读数，非 dBm」。"""
    _GNSS = open(os.path.join(_ROOT, "mbdsdr_ai", "gnss_monitor.py"),
                 encoding="utf-8").read()
    _TOOLS = open(os.path.join(_ROOT, "mbdsdr_ai", "sdr_tools.py"),
                  encoding="utf-8").read()
    assert "**不是 dBm**" in _GNSS
    assert "平均功率（相对读数 dB，非 dBm）" in _GNSS
    assert "平均功率: {result.power_dbm:.1f} dB（相对读数，非 dBm）" in _TOOLS
    assert "峰值RSSI: {result['peak_rssi_dbm']}（相对读数 dB，非 dBm）" in _TOOLS
