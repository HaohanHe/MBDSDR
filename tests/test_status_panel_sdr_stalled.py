# SPDX-License-Identifier: MIT
"""Task 107：SDR 已连接但读不出样本时，状态面板不许只报「已连接」。

真机（2026-11-18）：RTL-SDR 连接/调谐成功，``read_bytes`` 有界超时后
``get_runtime_sources().snapshot()["sdr"]["stalled"]`` 为 True。连接行必须
显示「无样本（超时）」并用 ``status_warning`` token，不能让人以为链路正常。

本文件钉四件事（offscreen，不碰硬件）：
1. connected=True + stalled=True → 含「无样本/超时」，用 warning token；
2. connected=True + stalled=False → 保持原有连接文案，不误染；
3. connected=False → 仍「未连接」，不冒读停顿告警；
4. 快照读取抛异常 → 面板不抛，保持原样。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

import mbdsdr_ai.runtime_sources as runtime_sources  # noqa: E402
from desktop.status_panel import StatusPanel  # noqa: E402
from desktop.tokens import tokens  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


class _Snap:
    """最小快照替身：只给面板要读的 ``sdr`` 块，可原地改成别的状态。"""

    def __init__(self, sdr):
        self.sdr = sdr

    def snapshot(self):
        return {"sdr": dict(self.sdr)}


def _patch(monkeypatch, **sdr):
    base = {
        "connected": True,
        "device": "Fake SDR",
        "reason": "",
        "stalled": False,
        "last_read_reason": "",
    }
    base.update(sdr)
    snap = _Snap(base)
    monkeypatch.setattr(
        runtime_sources, "get_runtime_sources", lambda: snap)
    return snap


class TestSdrStalled:
    def test_stalled_shows_no_samples_with_warning_token(self, monkeypatch):
        _patch(monkeypatch, connected=True, stalled=True,
               last_read_reason="超时未收到样本（2.0s 内无返回）")
        panel = StatusPanel()
        panel.on_connection_changed(True, "已连接: Fake SDR")
        panel.update_from_backend(None)

        text = panel.conn_status.text()
        assert "无样本" in text
        assert "超时" in text
        assert "正常" not in text
        assert tokens().status_color("warning") in panel.conn_status.styleSheet()
        # 原因落到 tooltip，排查时不用去翻日志
        assert "超时未收到样本" in panel.conn_status.toolTip()

    def test_not_stalled_keeps_existing_text(self, monkeypatch):
        _patch(monkeypatch, connected=True, stalled=False)
        panel = StatusPanel()
        panel.on_connection_changed(True, "已连接: Fake SDR")
        panel.update_from_backend(None)

        assert panel.conn_status.text() == "已连接: Fake SDR"
        assert tokens().status_color("warning") not in panel.conn_status.styleSheet()
        assert panel.conn_status.toolTip() == ""

    def test_stall_clears_back_to_existing_text(self, monkeypatch):
        snap = _patch(monkeypatch, connected=True, stalled=True)
        panel = StatusPanel()
        panel.on_connection_changed(True, "已连接: Fake SDR")
        panel.update_from_backend(None)
        assert "无样本" in panel.conn_status.text()

        snap.sdr["stalled"] = False
        snap.sdr["last_read_reason"] = ""
        panel.update_from_backend(None)
        assert panel.conn_status.text() == "已连接: Fake SDR"
        assert panel.conn_status.toolTip() == ""

    def test_disconnected_stays_not_connected(self, monkeypatch):
        _patch(monkeypatch, connected=False, stalled=False)
        panel = StatusPanel()
        panel.update_from_backend(None)

        assert panel.conn_status.text() == "未连接"
        assert "无样本" not in panel.conn_status.text()

    def test_disconnected_with_stale_stall_flag_stays_not_connected(
            self, monkeypatch):
        snap = _patch(monkeypatch, connected=False, stalled=False)
        panel = StatusPanel()
        panel.on_connection_changed(True, "已连接: Fake SDR")
        snap.sdr.update(connected=True, stalled=True)
        panel.update_from_backend(None)
        assert "无样本" in panel.conn_status.text()

        snap.sdr.update(connected=False, stalled=False)
        panel.update_from_backend(None)
        assert panel.conn_status.text() == "未连接"

    def test_snapshot_failure_does_not_raise(self, monkeypatch):
        def _boom():
            raise RuntimeError("快照不可用")

        monkeypatch.setattr(runtime_sources, "get_runtime_sources", _boom)
        panel = StatusPanel()
        panel.on_connection_changed(True, "已连接: Fake SDR")
        panel.update_from_backend(None)  # 不抛
        assert panel.conn_status.text() == "已连接: Fake SDR"


class TestDirectBackendConnCard:
    """直连后端（不经过 MCP worker）也要点亮「连接」卡片。

    2026-11-22 真机实证：RTL-SDR 已连、频谱在动、设备信息齐全，卡片却仍
    显示 ``OFF / 未连接``——只有 worker 的 ``connection_changed`` 会写这张
    卡，直连路径只调 ``set_sdr_connected``。
    """

    def test_set_sdr_connected_true_marks_on(self, monkeypatch):
        _patch(monkeypatch, connected=True, stalled=False)
        panel = StatusPanel()
        panel.dev_name.setText("RTL-SDR #0")
        panel.set_sdr_connected(True)

        assert panel.conn_indicator.text() == "ON"
        assert panel.conn_status.text() == "已连接: RTL-SDR #0"

    def test_set_sdr_connected_false_marks_off(self, monkeypatch):
        _patch(monkeypatch, connected=False, stalled=False)
        panel = StatusPanel()
        panel.set_sdr_connected(True)
        panel.set_sdr_connected(False)

        assert panel.conn_indicator.text() == "OFF"
        assert panel.conn_status.text() == "未连接"

    def test_specific_failure_reason_not_overwritten(self, monkeypatch):
        """worker 给出的具体失败原因不能被直连路径的占位文案盖掉。"""
        _patch(monkeypatch, connected=False, stalled=False)
        panel = StatusPanel()
        panel.on_connection_changed(False, "连接失败：设备被占用")
        panel.set_sdr_connected(False)

        assert panel.conn_status.text() == "连接失败：设备被占用"
