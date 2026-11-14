# SPDX-License-Identifier: MIT
"""审计漏记在界面上可见的判据（第四轮 + 端到端补测）。

钉住：计数为 0 时标签隐藏；达到阈值后状态栏常驻标签显示累计条数 + 最近
原因 + 时刻，tooltip 给出按原因分组、计数文件与查询入口；漏记发生在
别的进程（MCP 工作进程）时，本进程轮询也能读到。

端到端三条（各自独立断言界面文本，不看内部状态）：
  1. 真造一次锁超时 → 状态栏那一行出现，文案含条数/原因/时刻；账本恢复后
     计数仍在、界面不消失；
  2. 启动时计数文件里已有历史漏记 → 一起来就显示，不等下一次失败；
  3. 计数文件存在但读不出来 → 显示「历史漏记状态未知」，绝不显示 0 条。
反证：把 ``install_audit_alarm`` 里的 ``addPermanentWidget`` 摘掉，三条必红
（断言通过 ``statusBar().findChild`` 找标签，未挂载就找不到）。
"""

import json
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow  # noqa: E402

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


# ---------------------------------------------------------------------------
# 端到端三条：只看状态栏里真实挂着的控件与其文案
# ---------------------------------------------------------------------------
def _shown_window() -> QMainWindow:
    window = QMainWindow()
    window.resize(900, 600)
    window.show()
    QTest.qWait(30)      # 只处理 show 事件，不等告警定时器
    return window


def _status_bar_alarm(window: QMainWindow):
    """从状态栏子树里找告警标签——没挂上去就是 None（反证据此红）。"""
    return window.statusBar().findChild(QLabel, "audit_drop_alarm")


def test_e2e_real_lock_timeout_appears_and_survives_recovery(qapp, tmp_path):
    """真造一次锁超时：界面出现且含条数/原因/时刻；恢复后不消失。"""
    from audit_alarm import install_audit_alarm
    from mbdsdr_ai.hooks import Event, EventType
    from mbdsdr_ai.rotating_jsonl import file_lock
    from mbdsdr_ai.tool_guard import AuditLog

    ledger = tmp_path / "audit.jsonl"
    audit = AuditLog(str(ledger), lock_timeout=0.05)

    def _record() -> None:
        audit.record(Event(EventType.AGENT_TOOL_RESULT,
                           {"tool": "weather", "ok": True}, source="test"))

    with file_lock(str(ledger)):      # 真锁超时：走的是生产里的 record 路径
        _record()

    window = _shown_window()
    install_audit_alarm(window, interval_ms=40)
    QTest.qWait(250)                  # 让真实定时器跑一拍

    label = _status_bar_alarm(window)
    assert label is not None, "告警必须挂在状态栏上（摘掉 addPermanentWidget 就找不到）"
    assert label.isVisible()
    text = label.text()
    assert "审计漏记 1 条" in text
    assert "等不到账本写锁（超时）" in text, text
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text), text

    _record()                         # 账本恢复：这次正常落盘
    QTest.qWait(250)
    label = _status_bar_alarm(window)
    assert label is not None and label.isVisible(), "账本恢复后界面不能自己消失"
    assert "审计漏记 1 条" in label.text(), "恢复后计数仍在"
    window.close()


def test_e2e_startup_with_history_shows_immediately(qapp, _isolated_health):
    """启动时计数文件已有历史漏记：一起来就显示，不等下一次失败。"""
    from audit_alarm import install_audit_alarm

    _isolated_health.write_text(json.dumps({
        "dropped": 3,
        "threshold": 1,
        "last_at": 1763072706.0,
        "last_iso": "2026-11-14T09:05:06+08:00",
        "last_reason": "disk_full",
        "last_reason_text": "磁盘空间不足",
        "by_reason": {"disk_full": 3},
    }, ensure_ascii=False), encoding="utf-8")

    window = _shown_window()
    install_audit_alarm(window, interval_ms=60_000)   # 不指望定时器
    QTest.qWait(30)                                   # 只让状态栏完成布局

    label = _status_bar_alarm(window)
    assert label is not None, "启动即挂载（摘掉 addPermanentWidget 就找不到）"
    assert label.isVisible()
    text = label.text()
    assert "审计漏记 3 条" in text
    assert "磁盘空间不足" in text
    assert "2026-11-14T09:05:06+08:00" in text
    window.close()


def test_e2e_unreadable_count_file_shows_unknown_not_zero(qapp, _isolated_health):
    """计数文件损坏/读不到：显示「历史漏记状态未知」，绝不显示 0 条。"""
    from audit_alarm import install_audit_alarm

    _isolated_health.write_text("{ 这不是合法 JSON", encoding="utf-8")

    window = _shown_window()
    install_audit_alarm(window, interval_ms=60_000)
    QTest.qWait(30)      # 只让状态栏完成布局

    label = _status_bar_alarm(window)
    assert label is not None, "读不到计数也要挂载（摘掉 addPermanentWidget 就找不到）"
    assert label.isVisible()
    text = label.text()
    assert "历史漏记状态未知" in text
    assert "审计漏记 0 条" not in text, "读不到不等于 0，不能冒充正常"
    window.close()
