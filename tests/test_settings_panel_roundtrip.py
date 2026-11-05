#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""设置三环验收：保存 → 应用 → 读回（真面板控件 / 真应用函数 / 真读回值）。

挑 5 个高频项：theme、squelch_default_db、tuning_step_hz、
waterfall_depth_rows、fft_size。每项都走：
  1. 改 SettingsPanel 里的真实控件（信号 → _on_changed → settings）；
  2. 断言真实消费点状态（主窗口/控制面板/频谱面板，不许只改配置）；
  3. flush 后新实例 load 读回磁盘值，并把面板控件改脏再按权威源刷新
     读回——三环任一断链即失败。

不使用 _FakeHost：这里要证明的是「真窗口 + 真消费点 + 真落盘」。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from config_schema import SCHEMA  # noqa: E402
from main_window import MainWindow  # noqa: E402
from settings import DesktopSettings  # noqa: E402


@pytest.fixture(scope="module")
def window(isolated_profile):
    w = MainWindow()
    yield w
    w._active_sdr_backend = None


def _control(w, key):
    assert w.settings_panel is not None, "主窗口没有创建设置面板"
    control = w.settings_panel._rows.get(key)
    assert control is not None, f"设置面板没有渲染 {key}"
    return control


def _drive(w, key, value):
    """按真实控件类型模拟用户操作（会触发控件信号 → _on_changed）。"""
    control = _control(w, key)
    item = SCHEMA[key]
    kind = item.kind
    if kind == "enum":
        # 面板枚举下拉只写标签、不写 itemData；值靠位置映射到 choice_values。
        idx = list(item.choice_values).index(value)
        control.setCurrentIndex(idx)
    elif kind == "int":
        control.setValue(int(value))
    elif kind == "bool":
        control.setChecked(bool(value))
    else:
        control.setValue(float(value))


def _widget_value(control, item):
    if item.kind == "enum":
        return list(item.choice_values)[control.currentIndex()]
    if item.kind == "bool":
        return control.isChecked()
    return control.value()


def _three_rings(w, key, value, check_applied):
    """保存 → 应用 → 读回；check_applied 里断言真实消费点。"""
    panel = w.settings_panel
    item = SCHEMA[key]

    # 环 1：保存——真实控件的改动进了权威源。
    _drive(w, key, value)
    stored = w.settings.get(key)
    assert stored == value, f"{key}: 控件改成 {value!r}，settings 仍是 {stored!r}"

    # 环 2：应用——真实消费点必须真的变了。
    check_applied()

    # 环 3a：磁盘读回——防抖 flush 后用新实例从同一路径 load。
    w.settings.flush()
    fresh = DesktopSettings.load(w.settings.path)
    reloaded = fresh.get(key)
    assert reloaded == value, f"{key}: 落盘读回 {reloaded!r}，不是 {value!r}"

    # 环 3b：面板读回——先把控件静默改脏，再按权威源刷新。
    control = _control(w, key)
    control.blockSignals(True)
    try:
        if item.kind == "enum":
            control.setCurrentIndex(0 if control.currentIndex() != 0 else 1)
        elif item.kind == "int":
            control.setValue(int(value) + 1)
        else:
            control.setValue(float(value) + 1.0)
    finally:
        control.blockSignals(False)
    panel._refresh_from_storage()
    back = _widget_value(control, item)
    assert back == value, (
        f"{key}: 刷新后控件读回 {back!r}，权威源是 {value!r}（保存了但没读回）")


# ---------------------------------------------------------------- 面板构建守卫
def test_enum_controls_position_mapping(window):
    """枚举下拉只写标签、不写 itemData：数量与位置映射必须和 schema 严丝合缝。

    将来改枚举（加/删一项、换顺序却漏改 choice_values）会立刻红，
    而不是按位置悄悄把相邻档的值存进 settings。
    """
    panel = window.settings_panel
    checked = 0
    for key, control in panel._rows.items():
        item = SCHEMA[key]
        if item.kind != "enum":
            continue
        checked += 1
        assert control.count() == len(item.choice_values), (
            f"{key}: 下拉 {control.count()} 项，schema {len(item.choice_values)} 个值"
            "（只写标签不写 itemData，位置映射会错位）")
        pos_value = list(item.choice_values)[control.currentIndex()]
        stored = window.settings.get(key)
        assert pos_value == stored, (
            f"{key}: 第 {control.currentIndex()} 项按位置是 {pos_value!r}，"
            f"settings 存的是 {stored!r}")
    assert checked >= 1, "设置面板没有渲染任何枚举项（守卫空转）"


# ---------------------------------------------------------------- 主题
def test_theme_save_apply_readback(window):
    w = window
    _three_rings(
        w, "theme", "dark",
        lambda: (
            None if w._current_theme == "dark" else pytest.fail(
                f"主题没应用：_current_theme={w._current_theme!r}")
        ),
    )
    # 主题下拉（主窗口工具栏）也必须跟着读回同一档。
    assert w.theme_combo.currentData() == "dark"


# ---------------------------------------------------------------- 静噪门限
def test_squelch_save_apply_readback(window):
    w = window
    _three_rings(
        w, "squelch_default_db", -30,
        lambda: (
            None
            if (w._squelch_db == -30.0
                and w.control_panel.squelch_slider.value() == -30
                and w.control_panel.squelch_val.text() == "-30 dB")
            else pytest.fail(
                f"静噪没应用：_squelch_db={w._squelch_db!r}，"
                f"滑杆={w.control_panel.squelch_slider.value()}，"
                f"标签={w.control_panel.squelch_val.text()!r}")
        ),
    )


# ---------------------------------------------------------------- 调谐步进
def test_tuning_step_save_apply_readback(window):
    w = window
    _three_rings(
        w, "tuning_step_hz", 1_000,
        lambda: (
            None
            if (float(w.control_panel.step_combo.currentData() or 0) == 1_000.0
                and float(w.spectrum._state.snap_interval) == 1_000.0)
            else pytest.fail(
                f"步进没应用：顶部下拉="
                f"{w.control_panel.step_combo.currentData()!r}，"
                f"频谱 snap={w.spectrum._state.snap_interval!r}")
        ),
    )


# ---------------------------------------------------------------- 瀑布历史深度
def test_waterfall_depth_save_apply_readback(window):
    w = window
    _three_rings(
        w, "waterfall_depth_rows", 512,
        lambda: (
            None
            if (w.spectrum.generator.max_waterfall_lines == 512
                and w.spectrum._state.wf_history_end is None)
            else pytest.fail(
                f"瀑布深度没应用："
                f"max_waterfall_lines={w.spectrum.generator.max_waterfall_lines!r}")
        ),
    )


# ---------------------------------------------------------------- FFT 点数
def test_fft_size_save_apply_readback(window):
    w = window
    _three_rings(
        w, "fft_size", 4096,
        lambda: (
            None
            if w.spectrum.generator.fft_size == 4096
            else pytest.fail(
                f"FFT 点数没应用：generator.fft_size="
                f"{w.spectrum.generator.fft_size!r}")
        ),
    )
