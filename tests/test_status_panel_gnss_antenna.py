# SPDX-License-Identifier: MIT
"""Task 106：GNSS 天线告警落到状态面板 GPS 组（offscreen）。

``fix_dict`` 不带天线状态，面板每秒直接读同一份运行时快照
（``get_runtime_sources().snapshot()["gnss"]["antenna"]``）。本文件钉四件事：

1. real fix + ``antenna=open`` → 「定位」行含「天线开路」，用 warning token；
2. real fix + ``antenna=ok`` → 不出现告警词，仍是成功色「已定位」；
3. 无 fix + ``antenna=open`` → 仍「未连接」，不因天线告警变成“已定位”；
4. 快照读取抛异常 → 面板不抛，按原样显示「已定位」。
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

_FIX = {
    "source": "real",
    "latitude": 31.230416,
    "longitude": 121.473701,
    "altitude_m": 12.5,
    "satellites": 9,
    "hdop": 0.9,
    "timestamp": 1700000000,
}

_ALARM_WORDS = ("开路", "短路")


class _Snap:
    """最小快照替身：只给面板要读的 ``gnss.antenna``。"""

    def __init__(self, antenna):
        self._antenna = antenna

    def snapshot(self):
        return {"gnss": {"antenna": self._antenna}}


def _patch_antenna(monkeypatch, state):
    monkeypatch.setattr(
        runtime_sources, "get_runtime_sources", lambda: _Snap(state))


class TestAntennaAlarm:
    def test_open_antenna_appends_alarm_to_fix_label(self, monkeypatch):
        _patch_antenna(monkeypatch, "open")
        panel = StatusPanel()
        panel.update_gnss(dict(_FIX))
        text = panel.gps_fix.text()
        assert "天线开路" in text
        # 仍是“已定位”，只是追加告警——不把有 fix 的接收机标成未定位
        assert text.startswith("已定位")
        assert tokens().status_color("warning") in panel.gps_fix.styleSheet()
        # 真实坐标照常上屏，告警不挤掉定位数据
        assert panel.gps_lat.text() == "31.230416"

    def test_short_antenna_appends_alarm_to_fix_label(self, monkeypatch):
        _patch_antenna(monkeypatch, "short")
        panel = StatusPanel()
        panel.update_gnss(dict(_FIX))
        assert "天线短路" in panel.gps_fix.text()
        assert tokens().status_color("warning") in panel.gps_fix.styleSheet()

    def test_ok_antenna_shows_plain_fix(self, monkeypatch):
        _patch_antenna(monkeypatch, "ok")
        panel = StatusPanel()
        panel.update_gnss(dict(_FIX))
        text = panel.gps_fix.text()
        assert text == "已定位"
        assert not any(w in text for w in _ALARM_WORDS)
        assert tokens().COLORS["success"] in panel.gps_fix.styleSheet()

    def test_unknown_antenna_shows_plain_fix(self, monkeypatch):
        _patch_antenna(monkeypatch, "unknown")
        panel = StatusPanel()
        panel.update_gnss(dict(_FIX))
        assert panel.gps_fix.text() == "已定位"
        assert not any(w in panel.gps_fix.text() for w in _ALARM_WORDS)

    def test_no_fix_stays_disconnected_even_with_open_antenna(self, monkeypatch):
        _patch_antenna(monkeypatch, "open")
        panel = StatusPanel()
        panel.update_gnss({"source": "none", "timestamp": 1700000000})
        text = panel.gps_fix.text()
        assert text == "未连接"
        assert "已定位" not in text
        assert not any(w in text for w in _ALARM_WORDS)
        assert panel.gps_lat.text() == "—"

    def test_snapshot_failure_keeps_plain_fix(self, monkeypatch):
        def _boom():
            raise RuntimeError("快照不可用")

        monkeypatch.setattr(runtime_sources, "get_runtime_sources", _boom)
        panel = StatusPanel()
        panel.update_gnss(dict(_FIX))  # 不抛
        text = panel.gps_fix.text()
        assert text == "已定位"
        assert not any(w in text for w in _ALARM_WORDS)
        assert panel.gps_lat.text() == "31.230416"
