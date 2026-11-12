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
#: 带数值的文案里，这几类不是「量值声明」，不要求来源标注：
#: 快捷键描述（「已自定义：F11（出厂是 F11）」）与文件路径（路径本身就是来源）。
_IDENT_RE = re.compile(r"^已自定义：")
_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]|^[/~]")
_DIGITS_RE = re.compile(r"\d")
_EXEMPT_PATH = os.path.join(REPO_ROOT, "docs", "tooltip-runtime-exemptions.json")


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


def _load_exemptions():
    import json
    with open(_EXEMPT_PATH, encoding="utf-8") as fh:
        return json.load(fh)["exemptions"]


def _norm(text):
    """数字归一成 #，让「可见 1 颗」与「可见 12 颗」算同一条文案。"""
    return _DIGITS_RE.sub("#", (text or "").strip())


def _is_exempt(file_rel, text):
    t = _norm(text)
    for e in _load_exemptions():
        if e["file"] != file_rel:
            continue
        if e["match"].endswith("*"):
            if t.startswith(_norm(e["match"][:-1])):
                return True
        elif t == _norm(e["match"]):
            return True
    return False


def _assert_self_describing(text, where, file_rel):
    t = (text or "").strip()
    assert t, f"{where}：透传 tooltip 渲染后为空，用户看不到任何说明"
    if _is_exempt(file_rel, t):
        return
    assert not _BARE_VALUE_RE.match(t), (
        f"{where}：渲染成裸数值 {t!r}，没说清它是什么、从哪来")
    # 不卡长度：这一层守的是诚实性，不是文案字数（「卫星层」这类短语是合法的）
    if UNIT_RE.search(t) and not QUAL_RE.search(t):
        raise AssertionError(f"{where}：有量纲却缺来源/置信标注：{t!r}")
    # 带数字但既无量纲也无来源的文案，必须先进冻结豁免表（人工判定为
    # 动作/状态说明）才放行——豁免不许在运行时悄悄长出来。
    if (_DIGITS_RE.search(t) and not UNIT_RE.search(t)
            and not QUAL_RE.search(t)
            and not _IDENT_RE.match(t) and not _PATH_RE.match(t)):
        raise AssertionError(
            f"{where}：带数值却无量纲也无来源标注，且不在冻结豁免表"
            f"（新增豁免需评审）：{t!r}")


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
                _assert_self_describing(text, where, file_rel)
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
        # 能力收紧：不支持 AGC 走「基础说明 + 原因」那条，增益仍支持走
        # 「只写基础说明」那条，两条透传分支都要渲染（control_panel.py:392/394）。
        p.set_device_capabilities(
            gain=True, agc=False, ppm=True, offset_tuning=True)
        # 启动恢复用的三个 setter：只有「请求值在范围内、无需钳位」时才走
        # 无 ⚠ 的那条分支，即登记表里的透传行。
        p.set_frequency_hz(145_500_000)
        p.set_mode("WFM")
        p.set_gain(20)
        # 步进 tooltip 在构造时写一次（control_panel.py:506），切档位时
        # 再按新档位重写一次（:1045）；两处都登记为 passthrough，必须都渲染。
        p._on_step_combo_changed(p.step_combo.currentIndex())

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
    from mbdsdr_ai import usb_hotplug

    device = {
        "label": "FakeSDR", "driver": "rtlsdr",
        "sample_rate_range": (1_000_000, 2_560_000),
        "capability_status": {"sample_rate_range": "declared"},
    }

    class _Monitor:
        """只回放指定事件，不碰真探针。"""

        def __init__(self):
            self.events = []

        def expect(self, key, dev):
            pass

        def observe(self, now, err):
            return list(self.events)

    def fire(panel, monitor, *events):
        monitor.events = list(events)
        panel._enum_inflight = True
        panel._on_hotplug_devices([], "", token=panel._enum_token)

    def triggers(p):
        p._apply_devices([])
        p._apply_devices([device])
        p._on_device_selected(0)
        monitor = _Monitor()
        p._hotplug_mon = monitor
        # 连接：无序列号 → 身份说明走「按显示名识别」那一支（identity_note）。
        p.set_connected(True, device=device)
        # 监听失败、拔出（含 set_connected(False) 复写后的第二次落文案）、
        # 「在位但打不开」、歧义，四支各来一发，逐处验证 detail 渲染。
        fire(p, monitor, usb_hotplug.HotplugEvent(
            kind=usb_hotplug.EVENT_POLL_PROBLEM,
            user_text="热插拔监听不可用",
            detail="热插拔轮询不可用（枚举依赖缺失） · 设备状态暂不更新"))
        fire(p, monitor, usb_hotplug.HotplugEvent(
            kind=usb_hotplug.EVENT_UNPLUGGED, key=p._current_dev_key,
            user_text="设备已拔出",
            detail="序列号没变，只是 USB 口变了：旧口链路断了，不是另一台设备"))
        p.set_connected(True, device=device)
        fire(p, monitor, usb_hotplug.HotplugEvent(
            kind=usb_hotplug.EVENT_PRESENT_UNOPENABLE, key=p._current_dev_key,
            user_text="设备在位但这一拍读不到",
            detail="物理探针确认还在，只是这一拍枚举读不到（可能被本程序占用）"))
        fire(p, monitor, usb_hotplug.HotplugEvent(
            kind=usb_hotplug.EVENT_AMBIGUOUS, key=p._current_dev_key,
            user_text="无法确认设备是否还在",
            detail="同名设备无法区分：只报歧义，不判定拔出"))

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

    def triggers(p):
        # 设置页是懒构建：控件在 _ensure_ui_built() 里才建，而它由 showEvent
        # 触发。只构造不 show 的话一条透传 tooltip 都不会落地，运行时兜底
        # 会被「空转」骗过（K21）。
        p.show()
        qapp.processEvents()

    _, calls = _record(SettingsPanel, [triggers])
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


def test_runtime_exemptions_are_frozen():
    """豁免只减不增：出现第 7 条必须评审。

    这 6 条是「带量值/设备口径词，但实为动作或状态说明」的文案，靠人工
    判断放行——正因为它不可机械区分，才必须冻结成名单：任何新增都要走
    评审（判断是该真补来源标注，还是确属动作说明），并同步改这里的条数。
    """
    ex = _load_exemptions()
    assert len(ex) == 6, (
        f"运行时豁免现有 {len(ex)} 条，冻结名单是 6 条。新增豁免需评审："
        "先判断它是「动作/状态说明」还是真该补来源标注，确认后同步 "
        "docs/tooltip-runtime-exemptions.json 与交接文档。")
    seen = set()
    for e in ex:
        for key in ("file", "widget", "match", "why"):
            assert e.get(key), f"豁免条目缺字段 {key}：{e}"
        assert os.path.exists(os.path.join(REPO_ROOT, e["file"])), e["file"]
        key = (e["file"], e["match"])
        assert key not in seen, f"豁免条目重复：{key}"
        seen.add(key)
