# SPDX-License-Identifier: MIT
"""审计账本 fail-open 的漏记计数（与账本分离的持久落点）。

为什么需要这个模块
------------------

``tool_guard.AuditLog`` 在写不进账本时选择 fail-open（绝不阻断工具调用），
原来只把原因打到 stderr。桌面端是 GUI 程序，stderr 通常根本没有控制台；
结果是**审计悄悄失效、而没人知道失效过**——这比直接报错更危险。

所以漏记必须留下可查的痕迹，但不能写进「写不进去的那个账本」里。本模块
把计数写到一个**独立的文件**（默认 ``~/.mbdsdr/audit_health.json``）：

* 独立的 inode 与独立的 ``<路径>.lock``：账本等不到锁、账本权限不足时，
  健康文件的锁与权限不受影响；
* 磁盘满这类「整块盘都写不进去」的故障，按候选路径依次退：先用户目录，
  再系统临时目录，都不行则留在进程内存里（本次会话界面仍能看到），
  不会因为一次 ENOSPC 就让计数彻底消失；
* 健康文件自己写失败时，计数留在进程内存并在下次成功写入时补记——
  **只打 stderr 是不够的**，stderr 在 GUI 里等于没有。

记录的内容（:func:`audit_drop_snapshot` 的返回值）

* ``dropped``：累计漏记条数（只增不减，恢复后也不清零）；
* ``last_at`` / ``last_iso`` / ``last_reason`` / ``last_reason_text`` /
  ``last_detail``：最近一次漏记的时刻与原因（锁超时 / 磁盘满 / 权限 / 其他）；
* ``by_reason``：按原因分组的累计条数；
* ``threshold`` / ``visible``：界面可见阈值与当前是否应当显示。

查询入口（给人和脚本同一处）：``python -m mbdsdr_ai.audit_health`` 输出
JSON；环境变量 ``MBDSDR_AUDIT_HEALTH`` 可改落点（排查/测试用）。
"""

from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .rotating_jsonl import FileLockTimeout, file_lock

#: 健康文件落点覆盖（与 ``MBDSDR_AUDIT_LOG`` 同 pattern）。
AUDIT_HEALTH_ENV = "MBDSDR_AUDIT_HEALTH"

#: 用户目录下的默认落点。注意不能与账本同文件——那正是「一起失效」。
AUDIT_HEALTH_NAME = "audit_health.json"

#: 界面可见阈值：任何一条漏记都要可见。审计是「调用过就该有记录」的承诺，
#: 漏一条就已经让承诺失效，没有「少量漏记可以接受」的空间。
AUDIT_DROP_VISIBLE_THRESHOLD = 1

#: 健康文件是一次几条记录的小文件，拿锁不该久等；等不到就退回内存。
HEALTH_LOCK_TIMEOUT_S = 2.0

REASON_LOCK_TIMEOUT = "lock_timeout"
REASON_DISK_FULL = "disk_full"
REASON_PERMISSION = "permission"
REASON_OTHER = "other"

#: 原因 → 人话。界面与 JSON 共用，不各写一份。
REASON_TEXT: Dict[str, str] = {
    REASON_LOCK_TIMEOUT: "等不到账本写锁（超时）",
    REASON_DISK_FULL: "磁盘空间不足",
    REASON_PERMISSION: "没有写权限",
    REASON_OTHER: "其他写入错误",
}

#: 进程内兜底：健康文件也写不进时，本次会话仍能显示累计数。
_OP_LOCK = threading.RLock()
_MEM: Dict[str, Any] = {
    "pending": 0,                 # 已发生但尚未确认写盘（或写盘失败）的条数
    "pending_reasons": {},        # 同上，按原因分组
    "pending_last": None,         # (at, reason, detail, ledger)
    "last_data": {},              # 最近一次读/写成功的健康文件内容
    "active_path": "",            # 最近一次成功的落点
}


def default_path() -> str:
    """默认健康文件路径（``~`` 在调用时展开，便于测试隔离 HOME）。"""
    return os.path.join(os.path.expanduser("~"), ".mbdsdr", AUDIT_HEALTH_NAME)


def candidate_paths(path: Optional[str] = None) -> List[str]:
    """健康文件候选落点，按优先级。

    显式 ``path`` / 环境变量是唯一落点（调用方说了算）；否则先用户目录、
    再系统临时目录——后者是「用户目录写不进去（权限/磁盘满）」时的退路。
    """
    raw = path if path is not None else (os.environ.get(AUDIT_HEALTH_ENV) or "")
    raw = str(raw or "").strip()
    if raw:
        return [raw]
    out = [default_path()]
    try:
        tmp = os.path.join(tempfile.gettempdir(), "mbdsdr", AUDIT_HEALTH_NAME)
        if os.path.abspath(tmp) != os.path.abspath(out[0]):
            out.append(tmp)
    except OSError:
        pass
    return out


def classify_failure(exc: BaseException) -> str:
    """异常 → 漏记原因（锁超时 / 磁盘满 / 权限 / 其他）。

    认不出的错误归 ``other``，并且原文照样进 ``last_detail``——不因为分类
    失败就把原因吞掉。
    """
    if isinstance(exc, FileLockTimeout):
        return REASON_LOCK_TIMEOUT
    code = getattr(exc, "errno", None)
    if code == errno.ENOSPC:
        return REASON_DISK_FULL
    if code in (errno.EACCES, errno.EPERM):
        return REASON_PERMISSION
    low = str(exc).lower()
    if "no space" in low or "enospc" in low or "磁盘" in low:
        return REASON_DISK_FULL
    if ("permission" in low or "access is denied" in low
            or "eacces" in low or "权限" in low):
        return REASON_PERMISSION
    if ("timeout" in low or "timed out" in low
            or "超时" in low or "lock" in low):
        return REASON_LOCK_TIMEOUT
    return REASON_OTHER


def reason_text(reason: str) -> str:
    return REASON_TEXT.get(str(reason or "").strip(), REASON_TEXT[REASON_OTHER])


def _iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(
            timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return ""


def _read_file(path: str) -> Tuple[Dict[str, Any], str]:
    """读健康文件；返回 ``(内容, 状态)``。

    状态三值：``ok``（读到了）、``missing``（文件不存在，等于从没漏记过）、
    ``unreadable``（存在但损坏/无权限读）。后两者必须分开：把「读不到」
    当成 0 就是假读数，界面会显示「一切正常」。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, "missing"
    except (OSError, ValueError):
        return {}, "unreadable"
    if not isinstance(data, dict):
        return {}, "unreadable"
    return data, "ok"


def _write_atomic(path: str, data: Dict[str, Any]) -> None:
    """临时文件 + ``os.replace``：读者永远看不到半份 JSON。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _persist_drop(path: str, pending: int, pending_reasons: Dict[str, int],
                  pending_last: Optional[tuple]) -> Dict[str, Any]:
    """把「尚未落盘的增量」原子地并入健康文件。

    ``pending`` 含本次这一条，也含之前写盘失败、留在内存里的条数：下次
    成功写入时**一并补记**，不会因为一次故障把中间几条抹掉。
    """
    with file_lock(path, HEALTH_LOCK_TIMEOUT_S):
        data, status = _read_file(path)
        if status == "unreadable":
            # 坏文件不静默丢弃：先留副本，再按 0 起算（原样可查）。
            try:
                os.replace(path, path + ".corrupt")
            except OSError:
                pass
            data = {}
        dropped = int(data.get("dropped") or 0) + max(0, int(pending))
        by_reason = dict(data.get("by_reason") or {})
        for reason, count in (pending_reasons or {}).items():
            by_reason[str(reason)] = (int(by_reason.get(str(reason)) or 0)
                                      + int(count))
        last_at = float(data.get("last_at") or 0)
        last_iso = str(data.get("last_iso") or "")
        last_reason = str(data.get("last_reason") or "")
        last_detail = str(data.get("last_detail") or "")
        last_ledger = str(data.get("last_ledger") or "")
        if pending_last:
            at, reason, detail, ledger = pending_last
            if float(at) >= last_at:
                last_at = float(at)
                last_iso = _iso(last_at)
                last_reason = str(reason or "")
                last_detail = str(detail or "")
                last_ledger = str(ledger or last_ledger)
        out = {
            "dropped": dropped,
            "threshold": AUDIT_DROP_VISIBLE_THRESHOLD,
            "first_at": float(data.get("first_at") or last_at or 0),
            "first_iso": str(data.get("first_iso")
                             or (_iso(last_at) if last_at else "")),
            "updated_at": time.time(),
            "by_reason": by_reason,
            "last_at": last_at,
            "last_iso": last_iso,
            "last_reason": last_reason,
            "last_reason_text": (reason_text(last_reason)
                                 if last_reason else ""),
            "last_detail": last_detail[:500],
            "last_ledger": last_ledger,
            "path": path,
        }
        _write_atomic(path, out)
        return out


def _merge(file_data: Dict[str, Any], pending: int,
           pending_reasons: Dict[str, int],
           pending_last: Optional[tuple]) -> Dict[str, Any]:
    """文件内容 + 内存里尚未写盘的增量 → 对外快照。"""
    dropped = int(file_data.get("dropped") or 0) + int(pending or 0)
    by_reason: Dict[str, int] = {}
    for reason, count in dict(file_data.get("by_reason") or {}).items():
        by_reason[str(reason)] = int(count or 0)
    for reason, count in (pending_reasons or {}).items():
        by_reason[str(reason)] = by_reason.get(str(reason), 0) + int(count)
    last_at = float(file_data.get("last_at") or 0)
    last_iso = str(file_data.get("last_iso") or "")
    last_reason = str(file_data.get("last_reason") or "")
    last_detail = str(file_data.get("last_detail") or "")
    last_ledger = str(file_data.get("last_ledger") or "")
    if pending_last:
        at, reason, detail, ledger = pending_last
        if float(at) >= last_at:
            last_at = float(at)
            last_iso = _iso(last_at)
            last_reason = str(reason or "")
            last_detail = str(detail or "")
            last_ledger = str(ledger or last_ledger)
    return {
        "dropped": dropped,
        "state": "ok",
        "threshold": AUDIT_DROP_VISIBLE_THRESHOLD,
        "visible": dropped >= AUDIT_DROP_VISIBLE_THRESHOLD,
        "last_at": last_at or None,
        "last_iso": last_iso,
        "last_reason": last_reason,
        "last_reason_text": reason_text(last_reason) if last_reason else "",
        "last_detail": last_detail,
        "last_ledger": last_ledger,
        "by_reason": by_reason,
        "by_reason_text": {reason_text(r): n
                           for r, n in sorted(by_reason.items())},
    }


def note_audit_drop(reason: str, detail: str = "", *,
                    ledger: Optional[str] = None,
                    path: Optional[str] = None,
                    when: Optional[float] = None) -> Dict[str, Any]:
    """记一次「审计记录没落盘」。绝不抛异常，返回最新快照。

    写盘失败时计数留在进程内存（``_MEM``），下次成功写入时一并补记；
    所以「账本恢复后计数仍在」既指文件里没被清零，也指失败期间发生的那几条
    不会凭空消失。
    """
    reason = str(reason or "").strip() or REASON_OTHER
    if reason not in REASON_TEXT:
        reason = REASON_OTHER
    at = float(time.time() if when is None else when)
    detail = str(detail or "")
    with _OP_LOCK:
        _MEM["pending"] = int(_MEM.get("pending") or 0) + 1
        reasons = dict(_MEM.get("pending_reasons") or {})
        reasons[reason] = int(reasons.get(reason) or 0) + 1
        _MEM["pending_reasons"] = reasons
        _MEM["pending_last"] = (at, reason, detail, ledger)
        # 本次增量（含此前写盘失败留在内存里的条数）一起并入健康文件。
        pending = int(_MEM.get("pending") or 0)
        pending_reasons = dict(_MEM.get("pending_reasons") or {})
        pending_last = _MEM.get("pending_last")
        targets = candidate_paths(path)
        if _MEM.get("active_path") and _MEM["active_path"] not in targets:
            targets = [_MEM["active_path"]] + targets
        last_exc: Optional[BaseException] = None
        for target in targets:
            try:
                data = _persist_drop(target, pending, pending_reasons,
                                     pending_last)
            except Exception as exc:  # noqa: BLE001 - 计数失败不得阻断工具调用
                last_exc = exc
                continue
            _MEM["pending"] = 0
            _MEM["pending_reasons"] = {}
            _MEM["pending_last"] = None
            _MEM["last_data"] = data
            _MEM["active_path"] = target
            return _merge(data, 0, {}, None)
        # 所有落点都写不进：至少把真相留在 stderr（不是唯一痕迹，但别删）。
        print("[audit_health] 漏记计数未能落盘（保留在本次会话内存）: %s"
              % (last_exc,), file=sys.stderr)
        return _merge(dict(_MEM.get("last_data") or {}),
                      int(_MEM.get("pending") or 0),
                      dict(_MEM.get("pending_reasons") or {}),
                      _MEM.get("pending_last"))


def audit_drop_snapshot(path: Optional[str] = None) -> Dict[str, Any]:
    """累计漏记快照（查询入口，给界面与 ``python -m`` 共用）。

    ``dropped`` 只增不减；``visible`` 表示已达界面可见阈值。读不到文件时
    用进程内最近一次成功内容 + 未落盘增量，绝不因读失败而报 0；文件存在
    却读不出（损坏/无权限）时 ``state == "unknown"`` 并强制可见——宁可说
    「历史漏记状态未知」，也不能显示「0 条」冒充正常。
    """
    with _OP_LOCK:
        targets = candidate_paths(path)
        active = str(_MEM.get("active_path") or "")
        if active and active not in targets:
            targets = [active] + targets
        file_data: Dict[str, Any] = {}
        state = "ok"
        for target in targets:
            data, status = _read_file(target)
            if status == "ok":
                file_data = data
                state = "ok"
                break
            if status == "unreadable":
                state = "unknown"
        if not file_data and state == "ok":
            file_data = dict(_MEM.get("last_data") or {})
        merged = _merge(file_data, int(_MEM.get("pending") or 0),
                        dict(_MEM.get("pending_reasons") or {}),
                        _MEM.get("pending_last"))
        merged["state"] = state
        if state == "unknown":
            merged["visible"] = True
        merged["path"] = active or (targets[0] if targets else "")
        return merged


def audit_drop_count(path: Optional[str] = None) -> int:
    """累计漏记条数（给脚本/日志用的一行查询）。"""
    return int(audit_drop_snapshot(path).get("dropped") or 0)


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="查询审计账本累计漏记条数与最近一次原因")
    parser.add_argument("--path", default=None,
                        help="健康文件路径（缺省用环境变量/"
                             "~/.mbdsdr/audit_health.json）")
    args = parser.parse_args(argv)
    print(json.dumps(audit_drop_snapshot(args.path),
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
