# SPDX-License-Identifier: MIT
"""
C++ 端「UI 有控件、工具面无对应」反向对拍钉扎 (tests/test_cpp_tool_ui_reverse_audit.py)
======================================================================================

背景：2026-10-30 反向审计（docs/learn/cpp-tool-ui-reverse-audit.md）把
cpp/src/ui/ 全部可调控件实例化点与 cpp/src/ai/tool_schema.cpp 的
registeredToolSpecs() 逐一对照，产出：

  D1 有 UI 无工具        45 条（每条带建议工具名，照单可补）
  D2 语义不一致           9 条
  D3 参数语义反人类       8 条

本文件是纯文本钉扎（防漂移），不编译 C++、不碰硬件、offscreen 无关：

  1. 控件实例化点计数：cpp/src/ui/*.cpp 六类控件 new 计数 == 143，
     与审计清单口径（40 combo/35 checkbox/21 lineedit/19 doublespin/
     16 spin/12 slider）一致；漂移即红，提醒同步更新审计文档。
  2. 清单条数：审计文档里 D1/D2/D3 表格行数 == 45/9/8。
  3. D1 名单：45 个建议工具名必须逐个出现在审计文档里，且当前均**不在**
     tool_schema.cpp 的 s.name 集合中——将来补了工具，本测试变红，
     强制同步销账（更新文档 + 本名单），不允许"补了工具台账还挂着缺口"。

运行：
    python -m pytest tests/test_cpp_tool_ui_reverse_audit.py -v
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
UI_DIR = REPO / "cpp" / "src" / "ui"
TOOL_SCHEMA = REPO / "cpp" / "src" / "ai" / "tool_schema.cpp"
AUDIT_DOC = REPO / "docs" / "learn" / "cpp-tool-ui-reverse-audit.md"

# 审计口径：六类可调控件 new 实例化点总数（含 new QDoubleSpinBox; 无括号形态）
CTRL_PATTERN = re.compile(
    r"new\s+Q(DoubleSpinBox|ComboBox|CheckBox|SpinBox|Slider|LineEdit)\b")
EXPECTED_CTRL_TOTAL = 143
EXPECTED_BY_TYPE = {
    "QComboBox": 40,
    "QCheckBox": 35,
    "QLineEdit": 21,
    "QDoubleSpinBox": 19,
    "QSpinBox": 16,
    "QSlider": 12,
}

# 审计清单条数（docs/learn/cpp-tool-ui-reverse-audit.md 表格行）
EXPECTED_D1 = 45
EXPECTED_D2 = 9
EXPECTED_D3 = 8

# D1 全部建议工具名（清单 §1 逐条对应；补工具后同步删除并更新 EXPECTED_D1）
D1_SUGGESTED_TOOLS = [
    # A 接收链硬件参数
    "set_gain", "set_sample_rate", "set_decimation", "set_direct_sampling",
    "set_offset_tuning", "set_agc", "set_bias_tee", "set_force_mono",
    # B 音频/降噪
    "set_anr", "set_audio_output",
    # C 录制参数
    "set_recording_target", "set_recording_dir", "set_recording_template",
    "set_recording_stereo", "set_recording_ignore_squelch",
    "set_recording_max_segment", "set_gated_recording", "set_watch_recording",
    # D 扫描
    "set_scan_params",
    # E 频谱/显示
    "set_persistence", "set_waterfall_depth", "set_waterfall_speed",
    "set_builtin_palette", "set_spectrum_db_range", "set_spectrum_zoom",
    "set_db_grid_step", "set_peak_threshold", "set_peak_hold",
    "set_edge_rail_visibility",
    # F 源与网络
    "set_source_type", "set_spyserver",
    # G GNSS/图层
    "connect_gnss", "set_map_layers",
    # H 本站/任务
    "set_station_position", "set_sat_pass_template", "set_tune_step",
    # I VNA
    "vna_connect", "analyze_vna_permeability",
    # J 外设电台（TX）
    "set_cat_control", "send_cw", "send_ax25_beacon", "soapy_transmit",
    # K 外观
    "set_ui_scale", "set_language",
    # L 布局
    "set_left_groups",
]


def _read(path):
    return path.read_text(encoding="utf-8")


def test_control_inventory_matches_audit():
    """六类控件 new 计数与审计口径 143（分类型）一致。"""
    per_type = {k: 0 for k in EXPECTED_BY_TYPE}
    for f in sorted(UI_DIR.glob("*.cpp")):
        for m in CTRL_PATTERN.finditer(_read(f)):
            per_type["Q" + m.group(1)] += 1
    total = sum(per_type.values())
    assert total == EXPECTED_CTRL_TOTAL, (
        "UI 控件实例化点从 %d 变为 %d（%s）——同步更新审计文档与本测试"
        % (EXPECTED_CTRL_TOTAL, total, per_type))
    assert per_type == EXPECTED_BY_TYPE, (
        "控件分类型计数漂移：%s != %s——同步更新审计文档与本测试"
        % (per_type, EXPECTED_BY_TYPE))


def test_audit_doc_section_counts():
    """审计文档 D1/D2/D3 表格行数 == 45/9/8。"""
    doc = _read(AUDIT_DOC)
    d1 = len(re.findall(r"^\| D1-", doc, re.M))
    d2 = len(re.findall(r"^\| D2-", doc, re.M))
    d3 = len(re.findall(r"^\| D3-", doc, re.M))
    assert d1 == EXPECTED_D1, "D1 行数 %d != %d" % (d1, EXPECTED_D1)
    assert d2 == EXPECTED_D2, "D2 行数 %d != %d" % (d2, EXPECTED_D2)
    assert d3 == EXPECTED_D3, "D3 行数 %d != %d" % (d3, EXPECTED_D3)


def test_d1_suggested_tools_registered_as_gap():
    """45 个建议工具名：清单里都有、tool_schema 里都还没有。

    将来补了任一工具，本断言变红——强制同步销账（更新审计文档 +
    本名单 + EXPECTED_D1），不允许台账与工具面各说各话。
    """
    doc = _read(AUDIT_DOC)
    schema = _read(TOOL_SCHEMA)
    registered = set(re.findall(r'^\s*s\.name = "(\w+)"', schema, re.M))
    assert len(D1_SUGGESTED_TOOLS) == EXPECTED_D1, (
        "D1 名单 %d 条 != EXPECTED_D1 %d" % (len(D1_SUGGESTED_TOOLS), EXPECTED_D1))
    missing_from_doc = [t for t in D1_SUGGESTED_TOOLS if t not in doc]
    assert not missing_from_doc, (
        "审计文档缺少这些 D1 建议工具名：%s" % missing_from_doc)
    landed = [t for t in D1_SUGGESTED_TOOLS if t in registered]
    assert not landed, (
        "以下 D1 缺口已有工具落地，请从审计清单销账并更新本测试：%s" % landed)
