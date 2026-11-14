# SPDX-License-Identifier: MIT
"""审计账本 fail-open 漏记的判据（第四轮）。

钉住四件事：

1. 账本等不到锁（``FileLockTimeout``）时，漏记**计数 +1 且原因可查**，
   并且这个计数落在账本之外的健康文件里（不是只打 stderr）；
2. 磁盘满 / 权限不足同理；账本恢复后计数仍在，不清零；
3. 健康文件也写不进时，本次会话内存里仍有计数（界面仍能显示），下次
   写入成功时补记；
4. 累计条数有一个统一入口（``python -m mbdsdr_ai.audit_health``）。

反证：把 ``AuditLog.record`` 的异常分支改回「只打 stderr」，本文件里
``test_lock_timeout_lands_in_health_file_not_only_stderr`` 必须红——它读的是
健康文件原文，不读进程内存。边界：TX 账本仍是 fail-closed（见最后一组）。
"""

import errno
import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import audit_health  # noqa: E402
from mbdsdr_ai.hooks import Event, EventType  # noqa: E402
from mbdsdr_ai.rotating_jsonl import file_lock  # noqa: E402
from mbdsdr_ai.tool_guard import AuditLog  # noqa: E402


def _reset_memory() -> None:
    with audit_health._OP_LOCK:
        audit_health._MEM.update({
            "pending": 0,
            "pending_reasons": {},
            "pending_last": None,
            "last_data": {},
            "active_path": "",
        })


@pytest.fixture(autouse=True)
def _isolated_health(monkeypatch, tmp_path):
    """每个用例独立健康文件 + 清空进程内兜底，互不串味。"""
    path = tmp_path / "audit_health.json"
    monkeypatch.setenv(audit_health.AUDIT_HEALTH_ENV, str(path))
    _reset_memory()
    yield path
    _reset_memory()


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "audit.jsonl"


def _event() -> Event:
    return Event(EventType.AGENT_TOOL_RESULT,
                 {"tool": "weather", "trigger": "user", "ok": True},
                 source="test")


# ---------------------------------------------------------------------------
# 1. 锁超时
# ---------------------------------------------------------------------------
def test_lock_timeout_lands_in_health_file_not_only_stderr(ledger, capsys):
    """模拟锁超时：漏记 +1、原因可查，且**文件里**有记录（反证据此红）。"""
    audit = AuditLog(str(ledger), lock_timeout=0.05)
    with file_lock(str(ledger)):          # 同进程另一句柄持锁 → 等不到
        audit.record(_event())

    snap = audit.health()
    assert snap["dropped"] == 1
    assert snap["last_reason"] == "lock_timeout"
    assert snap["last_reason_text"].strip(), "原因必须是给人看的话"
    assert snap["last_iso"], "最近一次漏记的时刻必须可查"
    assert snap["last_ledger"] == str(ledger)
    assert snap["visible"] is True, "达到阈值必须在界面可见"
    assert snap["threshold"] == audit_health.AUDIT_DROP_VISIBLE_THRESHOLD

    stderr = capsys.readouterr().err
    assert "fail-open" in stderr and "漏记" in stderr


def test_lock_timeout_count_survives_ledger_recovery(ledger, _isolated_health):
    """账本恢复后该计数仍在（不清零、不复位）。"""
    audit = AuditLog(str(ledger), lock_timeout=0.05)
    with file_lock(str(ledger)):
        audit.record(_event())

    audit.record(_event())                # 锁已释放：这次正常落盘
    assert ledger.exists(), "恢复后账本应能写入"

    raw = json.loads(_isolated_health.read_text(encoding="utf-8"))
    assert raw["dropped"] == 1, "恢复写入不得把漏记计数清零"


# ---------------------------------------------------------------------------
# 2. 磁盘满 / 权限
# ---------------------------------------------------------------------------
def _fail_with(monkeypatch, audit, code, message):
    """让账本追加抛指定 errno，之后可切回真实写入。"""
    state = {"fail": True}
    original = audit._log.append_line

    def _append(line):
        if state["fail"]:
            raise OSError(code, message)
        return original(line)

    monkeypatch.setattr(audit._log, "append_line", _append)
    return state


def test_disk_full_counts_and_reason_is_queryable(ledger, monkeypatch,
                                                  _isolated_health):
    audit = AuditLog(str(ledger))
    state = _fail_with(monkeypatch, audit, errno.ENOSPC,
                       "No space left on device")
    audit.record(_event())

    snap = audit.health()
    assert snap["dropped"] == 1
    assert snap["last_reason"] == "disk_full"
    assert "磁盘" in snap["last_reason_text"]
    raw = json.loads(_isolated_health.read_text(encoding="utf-8"))
    assert raw["dropped"] == 1 and raw["last_reason"] == "disk_full"

    state["fail"] = False
    audit.record(_event())
    assert audit.health()["dropped"] == 1, "恢复后计数仍在"


def test_permission_denied_counts_and_reason_is_queryable(ledger, monkeypatch,
                                                          _isolated_health):
    audit = AuditLog(str(ledger))
    _fail_with(monkeypatch, audit, errno.EACCES, "Access is denied")
    audit.record(_event())

    snap = audit.health()
    assert snap["dropped"] == 1
    assert snap["last_reason"] == "permission"
    assert snap["by_reason"] == {"permission": 1}


def test_reasons_are_grouped(ledger, monkeypatch, _isolated_health):
    audit = AuditLog(str(ledger), lock_timeout=0.05)
    with file_lock(str(ledger)):
        audit.record(_event())
    state = _fail_with(monkeypatch, audit, errno.ENOSPC, "No space left")
    audit.record(_event())

    snap = audit.health()
    assert snap["dropped"] == 2
    assert snap["by_reason"]["lock_timeout"] == 1
    assert snap["by_reason"]["disk_full"] == 1
    assert snap["last_reason"] == "disk_full", "最近一次按时刻取最新的"


# ---------------------------------------------------------------------------
# 3. 健康文件也写不进：本次会话仍可查，恢复后补记
# ---------------------------------------------------------------------------
def test_unwritable_health_file_still_visible_in_session(tmp_path, monkeypatch):
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")     # 父路径是文件 → 建目录失败
    bad = blocker / "audit_health.json"
    monkeypatch.setenv(audit_health.AUDIT_HEALTH_ENV, str(bad))

    snap = audit_health.note_audit_drop("permission", "EACCES")
    assert snap["dropped"] == 1
    assert snap["visible"] is True, "写不进文件时本次会话也必须可见"
    assert not bad.exists()


def test_pending_drops_are_flushed_on_next_success(tmp_path, monkeypatch):
    """先写不进、后来能写：中间那几条要补记，不能只记最后一次。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    bad = blocker / "audit_health.json"
    monkeypatch.setenv(audit_health.AUDIT_HEALTH_ENV, str(bad))
    audit_health.note_audit_drop("disk_full", "ENOSPC")

    good = tmp_path / "audit_health.json"
    monkeypatch.setenv(audit_health.AUDIT_HEALTH_ENV, str(good))
    snap = audit_health.note_audit_drop("permission", "EACCES")
    assert snap["dropped"] == 2
    raw = json.loads(good.read_text(encoding="utf-8"))
    assert raw["dropped"] == 2
    assert raw["by_reason"] == {"disk_full": 1, "permission": 1}


# ---------------------------------------------------------------------------
# 4. 查询入口（跨进程）
# ---------------------------------------------------------------------------
def test_query_entry_reports_cumulative_count_in_new_process(_isolated_health):
    audit_health.note_audit_drop("lock_timeout", "等写锁超时")
    env = dict(os.environ)
    env[audit_health.AUDIT_HEALTH_ENV] = str(_isolated_health)
    out = subprocess.run(
        [sys.executable, "-m", "mbdsdr_ai.audit_health"],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["dropped"] == 1
    assert data["last_reason"] == "lock_timeout"
    assert data["visible"] is True


def test_zero_drops_is_not_visible():
    snap = audit_health.audit_drop_snapshot()
    assert snap["dropped"] == 0
    assert snap["visible"] is False


# ---------------------------------------------------------------------------
# 边界：TX 账本仍是 fail-closed，不得被「统一」成 fail-open
# ---------------------------------------------------------------------------
def test_tx_audit_remains_fail_closed(tmp_path, monkeypatch):
    from mbdsdr_ai import tx_guard

    tx = tx_guard.TxAuditLog(str(tmp_path / "tx_audit.jsonl"))

    def _boom(_line):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(tx._sink, "append_line", _boom)
    with pytest.raises(tx_guard.TxAuditError):
        tx.write({"action": "transmit", "tool": "x"})
    assert tx.write_failures == 1
    # 发射账本不写健康计数：它的失败语义是「停发」，不是「记一笔继续」。
    assert audit_health.audit_drop_snapshot()["dropped"] == 0
