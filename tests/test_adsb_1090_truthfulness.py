# SPDX-License-Identifier: MIT
"""1090 MHz 真话护栏（对应补丁 2026-11-06-desktop-adsb-1090-truthfulness）。

四处行为断言，改回旧行为即红：
  1. `AdsbPanel`：设备上界 < 1090 MHz 时不得再显示「等待 1090MHz 数据」；
  2. `MainWindow._apply_hw_action`：硬件写入返回 False 必须在状态栏说人话；
  3. `capability_catalog`：ADS-B 面板标 conditional 且写明原因；
  4. `bandplan.json`：ADS-B 1090 条目写明需要覆盖 1090 MHz 的前端。

为什么钉行为而不是文案常量：
  FC0012 实测上界 948.5 MHz，1090 MHz 连写频都拿不到。旧实现把
  `set_frequency` 的 False 当成功，界面显示 1090.000 MHz、配置落盘 1090，
  用户看到的全是假的；面板则一直「等待 1090MHz 数据」，等一台物理上收不到
  1090 的设备。
"""
import json
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="module")
def window(isolated_profile):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    w._active_sdr_backend = None


class _Fc0012Backend:
    """FC0012 实测上界 948.5 MHz：1090 MHz 写频被后端拒绝并留一句人话。"""

    def __init__(self):
        self.hz = 98e6
        self.error = ""
        self.calls = []

    def get_frequency_bounds(self):
        return (22e6, 948.5e6)

    def set_frequency(self, hz):
        self.calls.append(("set_frequency", float(hz)))
        if hz > 948.5e6:
            self.error = ("当前调谐器 FC0012 可调范围 22.000–948.500 MHz，"
                          f"无法调谐到 {hz / 1e6:.3f} MHz；未向硬件下发该请求")
            return False
        self.hz = float(hz)
        self.error = ""
        return True

    def get_frequency(self):
        return self.hz

    def get_status(self):
        return types.SimpleNamespace(error=self.error)

    def set_demod(self, mode):
        self.calls.append(("set_demod", mode))
        return True


# ---------------------------------------------------------------- 1. ADS-B 面板
def test_panel_shows_tuner_ceiling_instead_of_waiting():
    """设备上界 948.5 MHz → 必须显示上限与「无法接收」，不得显示等待文案。

    反证：把 `_refresh_header` 的 unreachable 分支删掉 → 本用例红。
    """
    from adsb_panel import AdsbPanel, ADSB_WAIT_TEXT

    p = AdsbPanel()
    p.set_sdr_connected(True)
    p.set_tuner_max_hz(948.5e6)

    text = p.header.text()
    assert ADSB_WAIT_TEXT not in text, "设备收不到 1090 时仍显示「等待 1090MHz 数据」"
    assert "948.5" in text and "无法接收 1090 MHz" in text
    assert "需覆盖 1090 MHz 的前端" in text


def test_panel_keeps_waiting_when_tuner_reaches_1090():
    """上界 ≥ 1090 MHz 的设备仍显示等待文案（不误报收不到）。"""
    from adsb_panel import AdsbPanel, ADSB_WAIT_TEXT

    p = AdsbPanel()
    p.set_sdr_connected(True)
    p.set_tuner_max_hz(1_200_000_000.0)
    assert p.header.text() == ADSB_WAIT_TEXT


def test_panel_unknown_ceiling_does_not_claim_unreachable():
    """上限未知（None）不替设备下结论：保持等待文案。"""
    from adsb_panel import AdsbPanel, ADSB_WAIT_TEXT

    p = AdsbPanel()
    p.set_sdr_connected(True)
    p.set_tuner_max_hz(None)
    assert p.header.text() == ADSB_WAIT_TEXT


def test_panel_disconnected_wording_unchanged():
    """断开态仍是「未连接」，不拿上限提示冒充连接态。"""
    from adsb_panel import AdsbPanel

    p = AdsbPanel()
    p.set_tuner_max_hz(948.5e6)
    p.set_sdr_connected(False)
    text = p.header.text()
    assert "未连接" in text
    assert "无法接收 1090 MHz" not in text


# ---------------------------------------------------------------- 3. 能力目录
def test_catalog_marks_adsb_panel_conditional_with_reason():
    """ADS-B 面板入口存在 ≠ 设备收得到：必须 conditional 且写原因。

    反证：删掉 `_panel_capabilities` 里的 adsb 分支 → status 退回 available，红。
    """
    from capability_catalog import build_catalog

    rows = {c.source: c for c in build_catalog()}
    row = rows["panel_registry:adsb"]
    assert row.status == "conditional", "ADS-B 面板仍标 available（替设备吹牛）"
    assert "1090" in row.note and "948.5" in row.note

    digital = rows["decode_router:adsb"]
    assert "不代表本机前端能覆盖 1090 MHz" in digital.note


# ---------------------------------------------------------------- 4. 频段表
def test_bandplan_adsb_notes_frontend_requirement():
    """bandplan.json 的 ADS-B 1090 条目必须写明前端覆盖条件。"""
    path = os.path.join(_ROOT, "desktop", "bandplan.json")
    data = json.loads(open(path, encoding="utf-8").read())
    bands = data["bands"] if isinstance(data, dict) else data
    adsb = [b for b in bands if b.get("label") == "ADS-B 1090"]
    assert adsb, "频段表里没有 ADS-B 1090"
    assert "需覆盖 1090 MHz 的前端" in adsb[0]["desc"]


# ---------------------------------------------------------------- 2. 硬件写入失败
def test_apply_hw_action_false_is_visible(window):
    """后端返回 False → 状态栏必须说人话，且返回值必须是 False。

    反证：把 `_apply_hw_action` 改回只看异常的旧实现 → 本用例红。
    """
    w = window
    be = _Fc0012Backend()
    w._active_sdr_backend = be
    w.statusBar().clearMessage()

    assert w._apply_hw_action(
        "设置频率", lambda: be.set_frequency(1_090_000_000.0)) is False

    msg = w.statusBar().currentMessage()
    assert "设置频率未生效" in msg, "后端拒绝写频后状态栏没有任何提示"
    assert "948.500" in msg, "失败原因没有显示设备真实上限"
    assert "保持上次成功值" in msg


def test_apply_hw_action_true_stays_silent(window):
    """成功路径不得冒出失败提示（防误报为失败）。"""
    w = window
    be = _Fc0012Backend()
    w._active_sdr_backend = be
    w.statusBar().clearMessage()

    assert w._apply_hw_action(
        "设置频率", lambda: be.set_frequency(98_000_000.0)) is True
    assert "未生效" not in w.statusBar().currentMessage()


def test_tune_1090_keeps_ui_and_config(window):
    """真实链路走一遍：请求 1090 → 界面读数/配置保持 98 MHz，面板显示上限。

    反证：`_apply_hw_action` 忽略 False → freq_label 变 1090.000 MHz、
    settings 被写成 1090，本用例三处断言同时红。
    """
    w = window
    be = _Fc0012Backend()
    w._active_sdr_backend = be
    w._backend_center_hz = 98e6
    w.freq_label.setText("98.000 MHz")
    w.settings.set("frequency_hz", 98e6)
    w.settings.set("demod_mode", "AM")

    w._on_tune_sdr(1_090_000_000.0, "AM")

    assert be.calls == [("set_frequency", 1_090_000_000.0)], \
        "1090 MHz 请求没有到达后端（被桌面侧吞掉）"
    assert w.freq_label.text() == "98.000 MHz", "失败后频率标签仍显示 1090=假成功"
    assert float(w.settings.get("frequency_hz")) == 98e6, \
        "失败后配置被改写成 1090 MHz"
    assert "未生效" in w.statusBar().currentMessage()

    w._sync_adsb_tuner_limit()
    w._panels_set_sdr_connected(True)   # 真实链路里上限提示只出现在已连接态
    w._sync_adsb_tuner_limit()
    panel = w.digital_adsb_panel
    assert panel is not None, "ADS-B 面板不存在，上限提示无从接线"
    assert "948.5" in panel.header.text() and "无法接收 1090 MHz" in panel.header.text()
