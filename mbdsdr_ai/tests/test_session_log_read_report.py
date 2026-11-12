# SPDX-License-Identifier: MIT
"""A03：SessionLog 读回降级必须可查（第一批）。

契约：半截行仍跳过（崩溃残留属正常），但必须计入 last_read_report；
干净日志的 dropped 必须为 0，避免把正常误报成降级。

反证：把 read_events 的 JSONDecodeError 分支改回 `except Exception: continue`
（不计数）后，本文件对应用例变红。
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.session_log import SessionLog  # noqa: E402


def test_reports_dropped_lines(tmp_path):
    log = SessionLog("conv_r", directory=str(tmp_path))
    log.emit("user_message", content="ok")
    with open(log.path, "a", encoding="utf-8") as f:
        f.write('{"type": "trunc"\n')

    events = log.read_events()

    assert [e["type"] for e in events] == ["user_message"]
    report = log.last_read_report
    assert report["dropped"] == 1, "跳过的坏行必须可查，不能静默丢"
    assert "JSONDecodeError" in report["read_error"]
    assert report["events"] == 1


def test_clean_read_has_no_dropped(tmp_path):
    log = SessionLog("conv_ok", directory=str(tmp_path))
    log.emit("turn_start")
    log.read_events()
    assert log.last_read_report["dropped"] == 0
    assert log.last_read_report["read_error"] == ""
