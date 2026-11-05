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


def _qt_app():
    """离屏 GUI 断言用：只在行为用例里创建 QApplication。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def test_u03_sample_rate_is_msps_three_decimals():
    """U-03：采样率一律 MS/s、3 位小数，缺失显「—」。"""
    assert format_sample_rate(2_400_000) == "2.400 MS/s"
    assert format_sample_rate(250_000) == "0.250 MS/s"
    assert format_sample_rate(2_048_000) == "2.048 MS/s"
    assert format_sample_rate(None) == "—"
    assert format_sample_rate(float("nan")) == "—"
    assert sp._fmt_rate(2_400_000) == format_sample_rate(2_400_000)
    # 真实控件：下拉档位直接显示 format_sample_rate 的输出
    _qt_app()
    from control_panel import ControlPanel
    panel = ControlPanel()
    texts = [panel.sample_rate_combo.itemText(i)
             for i in range(panel.sample_rate_combo.count())]
    assert "2.400 MS/s" in texts
    assert "2.048 MS/s" in texts
    assert "format_sample_rate" in _MAIN  # 主窗口仍走共享格式化（构造 MainWindow 成本高）


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
    # 真实面板：缺键显「—」而不是 0；MCP 路径写 dBµV；引擎路径写 dBFS；
    # 屏上任何读数都不得出现 dBm。
    _qt_app()
    from PySide6.QtWidgets import QLabel
    from status_panel import StatusPanel
    panel = StatusPanel()
    panel.on_status_updated({})
    assert panel.rssi_value.text() == "—", "缺键不得默认成 0 造假读数"
    panel.on_status_updated({"rssi": 42})
    assert panel.rssi_value.text() == "42 dBµV"
    panel.update_signal_level(-45.2)
    assert panel.rssi_value.text() == "-45.2 dBFS"
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert all("dBm" not in t for t in labels), labels
    for out in (mw._fmt_rssi_mcp(42), mw._fmt_dbfs(-45.2)):
        assert "dBm" not in out


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
    # 真实扫频路径：固件没给 rssi 时结果必须是 None，不能拿 -1 冒充信号强度
    client = cli.MBDSDRClient()
    client.tune_fm = lambda *args, **kwargs: {}
    client.get_status = lambda: {}
    rows = client.scan_fm(100.0, 100.0, 0.1, 0)
    assert rows == [{"freq_mhz": 100.0, "rssi": None, "snr": None}], rows
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
_ORCH = open(os.path.join(_ROOT, "mbdsdr_ai", "orchestrator.py"),
             encoding="utf-8").read()
_WEATHER = open(os.path.join(_ROOT, "desktop", "weather_panel.py"),
                encoding="utf-8").read()


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
    _qt_app()
    from control_panel import ControlPanel
    panel = ControlPanel()
    assert panel.vfo_bw_label.text() == "解调带宽"


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
    # 真实控件：带宽档位字面量与增益标签都必须按冻结版式显示。
    _qt_app()
    from control_panel import ControlPanel
    panel = ControlPanel()
    bw = [panel.vfo_bw_combo.itemText(i)
          for i in range(panel.vfo_bw_combo.count())]
    assert "2.4kHz (SSB)" in bw
    assert not any("2.4 kHz (SSB)" in t for t in bw), bw
    # 增益标签走真实信号处理路径：数字与单位之间必须有空格
    panel._on_gain_changed(0)
    assert panel.gain_val.text() == "0 dB"
    # 移动端是跨语言文案，保留文本检查（本仓无 Dart 运行时）
    assert "G ${controller.gainDb.toStringAsFixed(1)}dB" not in _MOBILE_SPECTRUM
    assert "增益 ${controller.gainDb.toStringAsFixed(1)} dB" in _MOBILE_SPECTRUM


def test_e04_bookmark_failure_says_what_survives_and_what_to_do():
    assert "书签初始化失败" not in _BOOKMARK
    assert "书签加载失败：" in _BOOKMARK
    assert "已有书签未被改动" in _BOOKMARK
    assert "检查该目录是否可读写" in _BOOKMARK


def test_discoverability_ports_are_findable_from_settings_tab():
    """「设置」页签必须指向服务端口所在页；两处端口框必须有用途 tooltip。"""
    _qt_app()
    from PySide6.QtWidgets import QGroupBox, QLabel, QWidget
    from settings_panel import ServiceSettingsPanel, SettingsPanel
    panels = (SettingsPanel(), ServiceSettingsPanel())
    blob = "\n".join(
        [w.text() for p in panels for w in p.findChildren(QLabel)]
        + [w.title() for p in panels for w in p.findChildren(QGroupBox)]
        + [w.toolTip() for p in panels for w in p.findChildren(QWidget)])
    for needle in ("服务开关与端口见「服务设置」页签。",
                   "远程控制 7356", "GQRX/hamlib",
                   "Web 8080", "HTTP/WS",
                   "重启远程控制服务", "重启 Web 服务"):
        assert needle in blob, needle


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


# ---------------------------------------------------------------------------
# 批次 5：同族收口（U-03 残余：模块面板 / AI 工具描述 / 启动读数 / 文档）
# ---------------------------------------------------------------------------
_MODULE = open(os.path.join(_ROOT, "desktop", "module_panel.py"),
               encoding="utf-8").read()


def test_u03_module_panel_combo_renders_ms_per_s_three_decimals():
    """U-03：模块面板「采样率」下拉的可见文本必须是 MS/s，不是 MHz。"""
    _qt_app()
    from module_panel import ModulePanel
    panel = ModulePanel()
    texts = [panel.sr_combo.itemText(i) for i in range(panel.sr_combo.count())]
    assert texts == ["1.024 MS/s", "1.440 MS/s", "2.048 MS/s",
                     "2.400 MS/s", "3.200 MS/s"], texts
    # 反证：旧的频率量纲标签一个都不许留
    assert not any("MHz" in t for t in texts), texts


def test_u03_bladerf_sample_rate_range_is_ms_per_s():
    """同一 bladeRF2 采样率区间：参数 docstring 与 AI 工具描述都写 MS/s。"""
    import inspect

    from mbdsdr_ai import bladerf_params as bp
    p = bp.DEFAULT_PARAMS
    # 真实边界行为：0.520834-61.44 MS/s，两侧越界一位都必须拒绝
    assert p.is_valid_sample_rate(520_834) is True
    assert p.is_valid_sample_rate(61_440_000) is True
    assert p.is_valid_sample_rate(520_833) is False
    assert p.is_valid_sample_rate(61_440_001) is False
    assert "0.520834-61.44 MS/s" in inspect.getdoc(p.is_valid_sample_rate)
    # 运行期注册的工具描述（不是源码文本）：量程单位必须 MS/s，不得回退 MHz
    from mbdsdr_ai.tool_registry import ToolRegistry
    reg = ToolRegistry()
    reg.register_bladerf_params_tools()
    desc = reg.tools["bladerf_get_freq_range"]["definition"]["function"][
        "description"]
    assert "SR 0.520834-61.44 MS/s" in desc, desc
    assert "SR 520834-61.44MHz" not in desc, desc


def test_u03_startup_step_reports_fs_in_ms_per_s():
    """启动步骤读数里的采样率不得回退成裸 Hz（2400000Hz）。"""
    from types import SimpleNamespace

    from mbdsdr_ai.startup_sequence import STEP_CONFIGURE, StartupSequence

    class _Backend:
        def set_sample_rate(self, rate_hz):
            return True

        def set_frequency(self, freq_hz):
            return True

        def set_gain(self, gain_db):
            return True

    seq = StartupSequence(backend_factory=lambda index, info: _Backend())
    seq.device_info = SimpleNamespace(name="fake", sample_rates=[2_400_000])
    seq.backend = _Backend()
    seq.configure(98_500_000.0, 2_400_000.0, 20.0)
    assert seq.steps[STEP_CONFIGURE].detail == \
        "freq=98.500MHz sr=2.400 MS/s gain=20.0dB"


def _cli_doc_example_rate_hz() -> int:
    """从 CLI 文档解析 --sample-rate 示例的取值（不在测试里另抄一份）。"""
    import re
    doc = open(os.path.join(_ROOT, "docs", "cli_usage.md"),
               encoding="utf-8").read()
    match = re.search(r"--sample-rate\s+(\d+)", doc)
    assert match, "CLI 文档缺 --sample-rate 示例"
    return int(match.group(1))


def _cli_doc_rate_unit_tokens():
    """CLI 文档里所有「数值+采样率单位」串，用于查旧量纲是否残留。"""
    import re
    doc = open(os.path.join(_ROOT, "docs", "cli_usage.md"),
               encoding="utf-8").read()
    return re.findall(r"\d[\d.]*\s?(?:MSps|MS/s)", doc)


def test_u03_cli_usage_doc_says_ms_per_s_not_msps():
    """文档示例里采样率不再写 MSps（与桌面/CLI 输出同口径）。"""
    rate_hz = _cli_doc_example_rate_hz()
    # 文档示例的取值经共享格式化器渲染后必须是 MS/s；改示例值而忘了单位即红
    assert format_sample_rate(rate_hz) == "2.400 MS/s"
    tokens = _cli_doc_rate_unit_tokens()
    assert not any("MSps" in token for token in tokens), tokens


def test_u03_orchestration_and_advice_copy_use_ms_per_s():
    """U-03：编排任务标题与 SDR 采样率建议串不得写 MSPS；天气面板采样率行标单位。"""
    from mbdsdr_ai.orchestrator import Orchestrator
    from mbdsdr_ai.sdr_tools import _meteor_demod_setup
    orch = Orchestrator()
    orch.create_sdr_pipeline(145_800_000, sample_rate=2_400_000)
    descs = "\n".join(t.description for t in orch.tasks.values())
    assert "设置采样率 2.400 MS/s" in descs
    assert "MSPS" not in descs
    out_rtl = _meteor_demod_setup(
        {"satellite": "meteor_m2_hrpt", "sdr_type": "rtlsdr"})
    out_pluto = _meteor_demod_setup(
        {"satellite": "meteor_m2_hrpt", "sdr_type": "plutosdr"})
    assert "采样率 2.048 MS/s（ oversample ~16x）" in out_rtl
    assert "采样率 2.5 MS/s" in out_pluto
    assert "MSPS" not in out_rtl + out_pluto
    # 真实天气面板：采样率行的标签必须写明 MS/s
    _qt_app()
    from PySide6.QtWidgets import QLabel
    from weather_panel import WeatherPanel
    panel = WeatherPanel()
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert "采样率 (MS/s)" in labels


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
    _qt_app()
    from weather_panel import WeatherPanel
    panel = WeatherPanel()
    panel._current_sat = lambda: {}
    panel._on_satellite_changed(0)
    assert panel.freq_edit.text() == ""
    assert panel.sym_edit.text() == ""
    assert panel.sr_edit.text() == ""


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
    # 真实路径：用低采样率触发 ADS-B 校验/降采样失败，读返回的规则文案；
    # 能力目录读运行时导出的 sample_rate_rule / min_sample_condition。
    import pytest

    from mbdsdr_ai.adsb import _validate_tx_fs
    with pytest.raises(ValueError) as exc:
        _validate_tx_fs(2.4e6)
    assert "fs ≥ 2 MS/s" in str(exc.value)

    from mbdsdr_ai.decode_router import capability_matrix
    adsb = next(r for r in capability_matrix() if r["mode"] == "adsb")
    assert "fs ≥ 2 MS/s" in adsb["sample_rate_rule"]
    # 频率仍用 MHz（1090MHz 是频点，不是采样率）
    assert "1090MHz" in adsb["min_sample_condition"]

    import numpy as np

    from mbdsdr_ai.decode_pipeline import _Trace, _downconvert
    _, fail = _downconvert(_Trace(), np.zeros(1024, dtype=np.complex64),
                           1e6, 8e6, 0, "adsb")
    assert fail and "fs ≥ 2 MS/s" in fail["detail"]


def test_u03_troubleshooting_next_steps_use_ms_per_s_for_adsb_fs():
    """U-03：排障「下一步」里 ADS-B 的 fs 必须 MS/s（fs=2MHz / 2/4MHz 是错量纲）。"""
    _TR = open(os.path.join(_ROOT, "desktop", "troubleshooting.py"),
               encoding="utf-8").read()
    for bad in ("fs=2/4MHz", "fs=2MHz", "fs=2/4 MHz", "2/4 MHz"):
        assert bad not in _TR, f"troubleshooting.py: 仍写 {bad}"
    assert "ADS-B 保持 1090MHz 与 fs=2 MS/s 或 4 MS/s。" in _TR
    assert "（ADS-B 需 fs=2 MS/s 或 4 MS/s）" in _TR


def test_u02_gnss_psd_fields_never_labeled_dbm():
    """U-02：GNSS PSD 字段名沿用 *_dbm，但注释与展示必须写「相对读数，非 dBm」。"""
    _GNSS = open(os.path.join(_ROOT, "mbdsdr_ai", "gnss_monitor.py"),
                 encoding="utf-8").read()
    _TOOLS = open(os.path.join(_ROOT, "mbdsdr_ai", "sdr_tools.py"),
                  encoding="utf-8").read()
    assert "**不是 dBm**" in _GNSS
    assert "平均功率（相对读数 dB，非 dBm）" in _GNSS
    # 真实格式化路径：把假监测结果喂给工具，输出必须是 dB + 「非 dBm」说明
    from types import SimpleNamespace
    import mbdsdr_ai.gnss_monitor as gm
    import mbdsdr_ai.sdr_tools as tools
    fake = SimpleNamespace(power_dbm=-40.0, noise_floor_dbm=-90.0,
                           inr_db=12.0, peak_freq_hz=1.57542e9,
                           peak_power_dbm=-38.0,
                           interference_type=SimpleNamespace(value="cw"),
                           confidence=0.9)
    gm.monitor_gnss_band = lambda *a, **k: fake
    out = tools._gnss_monitor_band(
        {"band_name": "L1", "center_freq_hz": 1575.42e6})
    assert "平均功率: -40.0 dB（相对读数，非 dBm）" in out
    assert "峰值功率: -38.0 dB（相对读数，非 dBm）" in out
    gm.interference_direction_finding = lambda d: {
        "estimated_direction_deg": 90.0, "peak_rssi_dbm": -70,
        "confidence": 0.8, "note": "", "symmetric_check": ""}
    txt, data = tools._gnss_direction_find({"rssi_by_azimuth": {"0": -82}})
    assert "峰值RSSI: -70（相对读数 dB，非 dBm）" in txt
    assert data["interference_az"] == 90.0
