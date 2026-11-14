# SPDX-License-Identifier: MIT
"""审计漏记在界面上可见的判据（第四轮）。

钉住：计数为 0 时标签隐藏；达到阈值后状态栏常驻标签显示累计条数 + 最近
原因 + 时刻，tooltip 给出按原因分组、计数文件与查询入口；漏记发生在
别的进程（MCP 工作进程）时，本进程轮询也能读到。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from mbdsdr_ai import audit_health  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def _isolated_health(monkeypatch, tmp_path):
    path = tmp_path / "audit_health.json"
    monkeypatch.setenv(audit_health.AUDIT_HEALTH_ENV, str(path))
    with audit_health._OP_LOCK:
        audit_health._MEM.update({
            "pending": 0, "pending_reasons": {}, "pending_last": None,
            "last_data": {}, "active_path": "",
        })
    yield path
    with audit_health._OP_LOCK:
        audit_health._MEM.update({
            "pending": 0, "pending_reasons": {}, "pending_last": None,
            "last_data": {}, "active_path": "",
        })


def _snapshot(**over):
    snap = {
        "dropped": 0,
        "threshold": 1,
        "visible": False,
        "last_at": None,
        "last_iso": "",
        "last_reason": "",
        "last_reason_text": "",
        "last_detail": "",
        "last_ledger": "",
        "by_reason": {},
        "path": "",
    }
    snap.update(over)
    return snap


def test_hidden_when_no_drops(qapp):
    from audit_alarm import build_audit_alarm

    label = build_audit_alarm()
    assert label.apply_snapshot(_snapshot()) is False
    assert label.isHidden(), "0 条漏记不该占状态栏"


def test_visible_with_count_reason_and_time(qapp):
    from audit_alarm import build_audit_alarm

    label = build_audit_alarm()
    ok = label.apply_snapshot(_snapshot(
        dropped=3, visible=True, last_reason="lock_timeout",
        last_reason_text="等不到账本写锁（超时）",
        last_iso="2026-11-14T11:50:00+08:00"))
    assert ok is True
    assert not label.isHidden()
    text = label.text()
    assert "审计漏记 3 条" in text
    assert "等不到账本写锁" in text
    assert "2026-11-14T11:50:00+08:00" in text


def test_tooltip_has_grouping_file_and_query_entry(qapp):
    from audit_alarm import build_audit_alarm

    label = build_audit_alarm()
    label.apply_snapshot(_snapshot(
        dropped=2, visible=True, last_reason="disk_full",
        last_reason_text="磁盘空间不足",
        by_reason={"disk_full": 1, "permission": 1},
        last_detail="ENOSPC", path="/tmp/audit_health.json"))
    tip = label.toolTip()
    assert "磁盘空间不足 1 条" in tip
    assert "没有写权限 1 条" in tip
    assert "/tmp/audit_health.json" in tip
    assert "python -m mbdsdr_ai.audit_health" in tip


def test_refresh_reads_health_file(qapp, _isolated_health):
    from audit_alarm import build_audit_alarm

    audit_health.note_audit_drop("lock_timeout", "等写锁超时",
                                 ledger="C:/tmp/audit.jsonl")
    label = build_audit_alarm()
    assert label.refresh() is True
    assert "审计漏记 1 条" in label.text()
    assert "C:/tmp/audit.jsonl" in label.toolTip()


def test_install_attaches_permanent_status_bar_widget(qapp, _isolated_health):
    from audit_alarm import install_audit_alarm

    window = QMainWindow()
    audit_health.note_audit_drop("permission", "EACCES")
    label = install_audit_alarm(window, interval_ms=1234)

    assert window.statusBar() is not None
    assert not label.isHidden(), "安装后应立即显示已有漏记"
    assert label._timer.isActive()
    assert label._timer.interval() == 1234
    window.close()
