# SPDX-License-Identifier: MIT
"""天空状态卡本地时钟必须带时区降级说明（tests/test_sky_local_clock_degraded_note.py）
====================================================================================

从 ``mbdsdr_ai/tests/test_timezone_fallback_detection.py`` 迁出：该用例要
``import rf_sky_view``（Qt 界面层），而 ``mbdsdr_ai`` 是纯逻辑层，分层守卫
（``tests/test_import_direction_contract.py``）禁止包内出现任何 Qt import，
包括测试里的函数体 import。验收层（``tests/``）本来就允许驱动真实界面对象，
所以这条用例放这里，断言一条不改。

判据：系统时区带规则时本地时钟不带说明；探测不到规则（固定偏移）时，卡片上
必须出现「固定偏移」「不随夏令时调整」——不能让用户以为时间是对的。
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import time_format as tf  # noqa: E402

UTC = timezone.utc


def _system_tz_unavailable(monkeypatch):
    """构造「系统时区不可发现」：探测不到 DST 规则。"""
    monkeypatch.setattr(
        tf, "_probe_system_tz", lambda: (None, "测试注入：系统时区不可发现"))


def _system_tz_with_rules(monkeypatch):
    """构造「系统时区带规则」：探测直接给出一个真实 tzinfo。"""
    monkeypatch.setattr(
        tf, "_probe_system_tz", lambda: (ZoneInfo("UTC"), "zoneinfo:UTC"))


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_sky_status_card_local_clock_shows_note(qtapp, monkeypatch):
    import rf_sky_view as rsv

    when = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

    _system_tz_with_rules(monkeypatch)
    normal = rsv.format_local_time(when)
    assert "固定偏移" not in normal

    _system_tz_unavailable(monkeypatch)
    degraded = rsv.format_local_time(when)
    assert "固定偏移" in degraded
    assert "不随夏令时调整" in degraded
