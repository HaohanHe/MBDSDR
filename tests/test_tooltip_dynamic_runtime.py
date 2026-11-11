#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""纯透传 tooltip 的运行时兜底（`docs/tooltip-dynamic-baseline.json` 的 runtime_test）。

背景：`scan_tooltips()` / `scan_dynamic_tooltips()` 都只看源码。像
`self.ppm_spin.setToolTip(self._ppm_base_tip)` 这种「整段文案由变量传进来」的
调用点，静态侧看不到一个字，只有渲染后才知道它说了什么。

这里用「拦截 `setToolTip` + 记调用栈行号」把登记项和渲染文本精确对上：
构造面板（必要时点一下会改 tooltip 的方法）时，凡是 `desktop/*.py` 第 N 行
发出的 `setToolTip` 都会被记下文本，再按登记表逐条断言「渲染后必须是自描述
的句子」：

  * 非空、不是裸数值（`2048000` / `2.4 MS/s` 这种只有数值没有说明）；
  * 有量纲（Hz/dB/ppm…）时必须带来源或置信标注（未确认 / 以设备为准 / 配置
    意图…），与静态守卫同一套词表。

反证：把任一条透传文案改成 `str(hz)` 这种裸数值，本文件立刻红。
"""

import inspect
import os
import re
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "desktop"), os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication, QTreeWidgetItem, QWidget  # noqa: E402

from test_tooltip_guard import (  # noqa: E402
    QUAL_RE, UNIT_RE, _load_dynamic_baseline, scan_dynamic_tooltips,
)

RUNTIME_TEST = "tests/test_tooltip_dynamic_runtime.py::"
_BARE_VALUE_RE = re.compile(
    r"^\s*[±+\-–—~]?\s*[\d.,]+\s*[A-Za-z/%°]*(?:\s*/\s*[A-Za-z]+)?\s*$")


@pytest.fixture(scope="module")
def qapp(isolated_profile):
    app = QApplication.instance() or QApplication([])
    yield app


def _record(panel_factory, triggers=()):
    """构造面板并记录每次 setToolTip 的 (文件名, 行号, 文本)。"""
    calls = {}
    orig_widget = QWidget.setToolTip
    orig_item = QTreeWidgetItem.setToolTip

    def _note(text):
        frame = inspect.currentframe().f_back.f_back
        key = (os.path.basename(frame.f_code.co_filename), frame.f_lineno)
        calls.setdefault(key, []).append(str(text))

    def widget_spy(self, text, *args, **kwargs):
        _note(text)
        return orig_widget(self, text, *args, **kwargs)

    def item_spy(self, column, text, *args, **kwargs):
        _note(text)
        return orig_item(self, column, text, *args, **kwargs)

    QWidget.setToolTip = widget_spy
    QTreeWidgetItem.setToolTip = item_spy
    try:
        panel = panel_factory()
        for trigger in triggers:
            trigger(panel)
    finally:
        QWidget.setToolTip = orig_widget
        QTreeWidgetItem.setToolTip = orig_item
    return panel, calls


def _assert_self_describing(text, where):
    t = (text or "").strip()
    assert t, f"{where}：透传 tooltip 渲染后为空，用户看不到任何说明"
    assert not _BARE_VALUE_RE.match(t), (
        f"{where}：渲染成裸数值 {t!r}，没说清它是什么、从哪来")
    # 不卡长度：这一层守的是诚实性，不是文案字数（「卫星层」这类短语是合法的）
    if UNIT_RE.search(t) and not QUAL_RE.search(t):
        raise AssertionError(f"{where}：有量纲却缺来源/置信标注：{t!r}")


def _check_file(calls, file_rel):
    """按登记表逐条核对某个文件的透传条目。"""
    entries = [e for e in _load_dynamic_baseline() if e["file"] == file_rel]
    assert entries, f"{file_rel} 在登记表里没有条目"
    passthrough = {}
    for item in scan_dynamic_tooltips()[0]:
        if item["file"] == file_rel and not item["text"]:
            passthrough.setdefault(item["expr"], []).append(item)
    # ast.walk 是广度优先，同一表达式出现在多处时桶内顺序不是源码顺序；
    # 登记表按行号升序生成，这里必须同序配对，否则会把 A 行记成 B 行。
    for bucket in passthrough.values():
        bucket.sort(key=lambda it: it["line"])
    used, missing, bad = {}, [], []
    for entry in entries:
        bucket = passthrough.get(entry["expr"], [])
        idx = used.get(entry["expr"], 0)
        used[entry["expr"]] = idx + 1
        if idx >= len(bucket):
            missing.append(f"{entry['file']} :: {entry['expr']}")
            continue
        line = bucket[idx]["line"]
        where = f"{entry['file']}:{line}"
        # 同一行可能在循环里复位多个控件的 tooltip（如 set_sdr_connected 的
        # ``w.setToolTip(base)``），本来就没有 tooltip 的控件会渲染空串——
        # 那是合法清空，不是「没有说明」。只要求该行至少有一条真文案，
        # 且非空文案全部自描述。
        texts = [t for t in (calls.get((os.path.basename(entry["file"]), line))
                             or []) if (t or "").strip()]
        if not texts:
            missing.append(where)
            continue
        for text in texts:
            try:
                _assert_self_describing(text, where)
            except AssertionError as exc:
                bad.append(str(exc))
    assert not missing, (
        "以下透传条目在构造/触发后没有渲染出 tooltip（运行时兜底失效，"
        "请补触发步骤或从登记表移除）：\n  " + "\n  ".join(missing))
    assert not bad, "透传 tooltip 不自描述：\n  " + "\n  ".join(bad)


# ======================================================================
# 各面板
# ======================================================================
def test_control_panel_passthrough_tooltips_self_describe(qapp):
    from control_panel import ControlPanel

    def triggers(p):
        p.set_sdr_connected(True)
        p.set_gain_range(0, 49)
        p.set_sample_rate(12_345)
        p._set_freq_hz(145_500_000)
        p._on_gain_changed(20)
        p._on_vfo_bw_changed(0)
        # set_ppm_applied / set_offset_tuning_applied 只在「施加值 == 请求值」
        # 时才写目标行，必须传控件当前值，否则走的是另一条提示分支。
        p.set_ppm_applied(p.ppm_spin.value())
        p.set_offset_tuning_applied(p.offset_check.isChecked())
        p.set_vfo_bandwidth(12_500)
        # 启动恢复用的三个 setter：只有「请求值在范围内、无需钳位」时才走
        # 无 ⚠ 的那条分支，即登记表里的透传行。
        p.set_frequency_hz(145_500_000)
        p.set_mode("WFM")
        p.set_gain(20)

    _, calls = _record(ControlPanel, [triggers])
    _check_file(calls, "desktop/control_panel.py")


def test_main_window_passthrough_tooltips_self_describe(qapp):
    from main_window import MainWindow

    def triggers(w):
        w._build_planetarium_page()
        w._sync_planetarium_link()
        w._refresh_tx_capability_label()
        w._show_no_rtl_hint(
            "SDR 未连接：按 Esc 回主界面，点工具栏「连接」选设备。")

    _, calls = _record(MainWindow, [triggers])
    _check_file(calls, "desktop/main_window.py")


def test_device_panel_passthrough_tooltips_self_describe(qapp):
    from device_panel import DevicePanel

    def triggers(p):
        p._apply_devices([])
        p._apply_devices([{
            "label": "FakeSDR", "driver": "rtlsdr",
            "sample_rate_range": (1_000_000, 2_560_000),
            "capability_status": {"sample_rate_range": "declared"},
        }])
        p._on_device_selected(0)

    _, calls = _record(DevicePanel, [triggers])
    _check_file(calls, "desktop/device_panel.py")


def test_rf_sky_view_passthrough_tooltips_self_describe(qapp):
    from rf_sky_view import RFSkyViewPanel

    def triggers(p):
        p.sky_view._apply_gimbal_tooltip()
        p._build_control_bar()

    _, calls = _record(RFSkyViewPanel, [triggers])
    _check_file(calls, "desktop/rf_sky_view.py")


def test_settings_panel_passthrough_tooltips_self_describe(qapp):
    from settings_panel import SettingsPanel

    _, calls = _record(SettingsPanel)
    _check_file(calls, "desktop/settings_panel.py")


def test_spectrum_widget_passthrough_tooltips_self_describe(qapp):
    from spectrum_widget import SpectrumPanel

    _, calls = _record(SpectrumPanel)
    _check_file(calls, "desktop/spectrum_widget.py")


def test_constellation_panel_passthrough_tooltips_self_describe(qapp):
    from constellation_panel import ConstellationPanel

    def triggers(p):
        # 空星表时 _gnss_status_detail 返回 ""（清空 tooltip，属合法状态）；
        # 要验证渲染文案，得给一份有内容的星表。签名是
        # update_gnss(gsv_frames, gsa_frame=None, connected=False)，GSV 帧按
        # talker 分组、每颗星的 id 必须是 int；gsa_frame 只能是 dict 或 None。
        p.update_gnss([{"talker": "GP", "sats": [
            {"id": 5, "elevation": 40, "azimuth": 120, "snr_db": 42},
        ]}], None, True)

    _, calls = _record(ConstellationPanel, [triggers])
    _check_file(calls, "desktop/constellation_panel.py")


def test_weather_panel_passthrough_tooltips_self_describe(qapp, tmp_path):
    from weather_panel import WeatherPanel

    iq = tmp_path / "sample.iq"
    iq.write_bytes(b"\x00" * 64)

    def triggers(p):
        p.set_iq_file(str(iq))

    _, calls = _record(WeatherPanel, [triggers])
    _check_file(calls, "desktop/weather_panel.py")


def test_shortcuts_panel_passthrough_tooltips_self_describe(qapp, monkeypatch):
    import shortcuts_panel as sp

    monkeypatch.setattr(sp, "is_overridden", lambda _sid: True)

    def triggers(p):
        p._refresh()

    _, calls = _record(sp.ShortcutsPanel, [triggers])
    _check_file(calls, "desktop/shortcuts_panel.py")
