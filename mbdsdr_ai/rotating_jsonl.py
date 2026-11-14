# SPDX-License-Identifier: MIT
"""按大小轮转的 JSONL 账本（唯一实现，供工具审计与发射审计共用）。

为什么抽成一个模块
------------------

``tx_guard.TxAuditLog`` 早在 2026-11-05 就有「写前看大小 → 删最旧 → 依次
后移 → 当前文件变 ``.1``」的轮转，并且有用例钉住「超限删最旧、绝不停写」。
工具审计账本要加同样的能力时，正确做法是把它抽出来共用，而不是在
``tool_guard`` 里再写一份——两份轮转早晚会在边界（``backups=1``、空文件、
清理失败）上分叉。

与既有实现相比多出来的只有一件事：**跨进程互斥**。原来的 ``threading.Lock``
只能挡住同一进程内的线程，两个进程同时写同一账本时仍会互相踩：A 判完大小
准备改名，B 正好把一行追加进即将被改名的文件；或者两者同时 ``os.replace``，
其中一方的行随旧 inode 一起消失。现在「判大小 → 轮转 → 追加」整段由
``<路径>.lock`` 上的操作系统文件锁串行化（Windows ``msvcrt.locking`` /
POSIX ``fcntl.flock``），拿不到锁按超时处理，由调用方决定 fail-open 还是
fail-closed。

轮转语义（写死在调用方的常量里，不在这里配）
--------------------------------------------

* 文件命名：``ledger.jsonl``（当前）→ ``ledger.jsonl.1``（刚轮转出去的）
  → ``.2`` …→ ``.N``（最旧）。``N = backups``。
* 轮转条件：**写前**当前文件 ``>= max_bytes`` 才轮转（不会因为一次写入把
  文件写爆后再补切；单行远小于上限，最坏超出量 < 一行）。
* 不丢记录：轮转是改名/后移，不截断、不覆盖未到期的备份；只有超过
  ``backups`` 份或超过 ``retention_days`` 的**最旧备份**会被删除，这是保留
  策略本身，不是轮转丢数据。
* 读：:meth:`RotatingJsonlLog.read_events` 把当前文件与全部备份按
  「最旧 → 最新」拼起来查，调用方不需要自己拼 ``.1`` / ``.2``。
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple


class FileLockTimeout(TimeoutError):
    """等不到跨进程写锁（调用方决定 fail-open 还是 fail-closed）。"""


def _lock_path(path: str) -> str:
    return str(path) + ".lock"


def _ensure_lock_byte(handle) -> None:
    """Windows 的字节锁需要文件里真的有那 1 个字节。"""
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()


@contextmanager
def file_lock(path: str, timeout: Optional[float] = 10.0) -> Iterator[None]:
    """``<path>.lock`` 上的跨进程排它锁；``timeout=None`` 表示一直等。

    同一个进程里的两个线程也会互相阻塞（Windows 字节锁 / POSIX flock 都按
    文件句柄算），所以它同时替代了只挡线程的 ``threading.Lock``。
    """
    lock_file = _lock_path(path)
    parent = os.path.dirname(os.path.abspath(lock_file))
    if parent:
        os.makedirs(parent, exist_ok=True)
    handle = open(lock_file, "a+b")
    try:
        _ensure_lock_byte(handle)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise FileLockTimeout(
                        f"等写锁超时（{timeout}s）：{lock_file}")
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        handle.close()


class RotatingJsonlLog:
    """一个 JSONL 文件的追加 + 按大小轮转 + 按份数/天数保留。

    线程安全与进程安全都由 :func:`file_lock` 提供；本类自身不持锁状态。
    """

    def __init__(self, path: str, *, max_bytes: int, backups: int,
                 retention_days: int = 0,
                 lock_timeout: Optional[float] = 10.0) -> None:
        self.path = str(path)
        self.max_bytes = max(4096, int(max_bytes))
        self.backups = max(1, int(backups))
        self.retention_days = max(0, int(retention_days))
        self.lock_timeout = lock_timeout

    # ── 写 ────────────────────────────────────────────────────────────────
    def append_line(self, line: str) -> None:
        """追加一行（调用方负责序列化与换行）。失败抛 OSError/FileLockTimeout。"""
        parent = os.path.dirname(os.path.abspath(self.path))
        with file_lock(self.path, self.lock_timeout):
            if parent:
                os.makedirs(parent, exist_ok=True)
            if (os.path.exists(self.path)
                    and os.path.getsize(self.path) >= self.max_bytes):
                self._rotate()
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line.rstrip("\n") + "\n")

    def _rotate(self) -> None:
        """删最旧 → 依次后移 → 当前变 ``.1``（纯改名，不截断不丢行）。"""
        oldest = f"{self.path}.{self.backups}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(self.backups - 1, 0, -1):
            src = f"{self.path}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{self.path}.{i + 1}")
        os.replace(self.path, f"{self.path}.1")
        self._purge_expired()

    def _purge_expired(self) -> None:
        """按天数清理备份；当前活动文件永不删，清理失败不阻断写入。"""
        if self.retention_days <= 0:
            return
        cutoff = time.time() - self.retention_days * 86400
        for i in range(1, self.backups + 1):
            p = f"{self.path}.{i}"
            try:
                if os.path.exists(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except OSError:
                pass

    # ── 读 ────────────────────────────────────────────────────────────────
    def files(self) -> List[str]:
        """现存文件，最旧 → 最新（``.N`` … ``.1``、当前文件）。"""
        out: List[str] = []
        for i in range(self.backups, 0, -1):
            p = f"{self.path}.{i}"
            if os.path.exists(p):
                out.append(p)
        if os.path.exists(self.path):
            out.append(self.path)
        return out

    def read_events(self, start: Optional[float] = None,
                    end: Optional[float] = None) -> Tuple[List[Dict[str, Any]],
                                                          int]:
        """跨轮转文件按 ``timestamp``（epoch 秒）过滤，返回 ``(事件, 坏行数)``。

        不加写锁：轮转是改名，读到正在写的最后一行可能是半行；这类行
        **不猜、不补**，计入返回值第二项，由调用方如实报出。时间范围两端
        均为闭区间，``None`` 表示不限。
        """
        events: List[Dict[str, Any]] = []
        skipped = 0
        for path in self.files():
            try:
                with open(path, "r", encoding="utf-8",
                          errors="replace") as f:
                    for raw in f:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            item = json.loads(raw)
                        except ValueError:
                            skipped += 1
                            continue
                        ts = item.get("timestamp")
                        if not isinstance(ts, (int, float)):
                            skipped += 1
                            continue
                        if start is not None and ts < start:
                            continue
                        if end is not None and ts > end:
                            continue
                        events.append(item)
            except FileNotFoundError:
                continue
        events.sort(key=lambda item: item.get("timestamp") or 0)
        return events, skipped
