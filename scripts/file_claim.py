#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""共享文件占用锁：用操作系统级互斥，替代「动手前看一眼 git status」。

背景（2026-11-05 同一天三次同类事故）：
  `git commit -- <path>` 提交的是**该路径工作区的全部内容**，不是「我以为
  属于我的那几块」。于是别人未提交的 hunk 被带走，HEAD 出现 ImportError /
  KeyError。「开工前看 status 干净」挡不住——看的瞬间干净，改的过程中别人
  会插进来。纪律靠不住，所以把它做成会失败的检查。

机制：
  * 每个被 claim 的文件对应一个占位文件（`<sha1(路径)>.claim`），首次占用
    用 `O_CREAT|O_EXCL` 原子创建，**第二次 claim 由操作系统裁决失败**；
    续租/超时夺取/释放都在占位文件自身的字节锁（Windows `msvcrt.locking`，
    POSIX `flock`）里完成，不做「读 JSON 再写 JSON」的竞态写法。
  * TTL 心跳：同 owner 再 claim = 续租；超过 TTL 无续租视为过期，别人可以
    夺取，并打印原持有者与超时分钟数。
  * `release` 只有原持有者能执行，别人 release 一律失败。
  * 状态文件：`D:\\mbdsdr\\.locks\\claims.json`（与既有 `qt_test.lock` 同目录），
    占位文件在 `D:\\mbdsdr\\.locks\\claims\\`。不写 C 盘。
    `MBDSDR_LOCKS_DIR` 仅供测试指向临时目录，日常不要设。

用法：
  python scripts/file_claim.py claim desktop/main_window.py --owner 操作逻辑线
  python scripts/file_claim.py claim desktop/main_window.py --owner 操作逻辑线 --ttl-min 10
  python scripts/file_claim.py release desktop/main_window.py --owner 操作逻辑线
  python scripts/file_claim.py status
  python scripts/file_claim.py status desktop/main_window.py

退出码：0 成功；2 被拒绝（被他人占用 / 非持有者 / 本就没有 claim）；1 用法错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
# 硬默认：与 qt_test.lock 同目录，且在 D 盘。环境变量只给测试用。
DEFAULT_LOCKS_DIR = Path(r"D:\mbdsdr\.locks")
LOCKS_DIR = Path(os.environ.get("MBDSDR_LOCKS_DIR") or DEFAULT_LOCKS_DIR)
REGISTRY_PATH = LOCKS_DIR / "claims.json"
MARKER_DIR = LOCKS_DIR / "claims"
REGISTRY_MUTEX = LOCKS_DIR / "claims.mutex"

LOCK_TIMEOUT_S = 15.0
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_DENIED = 2


class ClaimDenied(RuntimeError):
    """claim/release 被拒绝（对应退出码 2）。"""


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _minutes(seconds: float) -> str:
    return f"{seconds / 60.0:.1f}"


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    return f"{seconds / 60.0:.1f} 分钟"


# ---------------------------------------------------------------------------
# 操作系统级互斥
# ---------------------------------------------------------------------------

def _try_lock(fd: int, shared: bool = False) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _locked_fd(
    path: Path, *, create: bool, shared: bool = False, timeout: float = LOCK_TIMEOUT_S
) -> Iterator[int]:
    """打开 path 并对其首字节加锁（默认独占，shared=只读共享）。

    Windows 字节锁是强制的：锁住之后**别的句柄**读同一区间会失败，所以
    所有读写都必须走同一个 fd，不能另开 `read_text()`。
    """
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileNotFoundError:
        raise ClaimDenied(f"文件未加锁：{path}") from None
    deadline = time.monotonic() + timeout
    try:
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            if _try_lock(fd, shared=shared):
                break
            if time.monotonic() >= deadline:
                raise ClaimDenied(f"等待锁超时（{timeout:.0f}s）：{path}")
            time.sleep(0.01)
        try:
            yield fd
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                _unlock(fd)
            except OSError:
                pass
    finally:
        os.close(fd)


def _read_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    return os.read(fd, 65536).decode("utf-8", errors="replace")


def _write_fd(fd: int, text: str) -> None:
    data = text.encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, data)
    os.ftruncate(fd, len(data))
    os.fsync(fd)


# ---------------------------------------------------------------------------
# 路径规范化
# ---------------------------------------------------------------------------

def canonical_path(raw: str) -> Tuple[str, str]:
    """返回 (比较键, 展示路径)。

    `desktop/main_window.py` 与 `D:\\mbdsdr\\MBDSDR\\desktop\\main_window.py`
    归一为同一比较键（Windows 上大小写不敏感）。
    """
    text = raw.strip().strip('"').strip("'")
    if not text:
        raise ValueError("路径为空")
    p = Path(text)
    target = p if p.is_absolute() else ROOT / p
    try:
        rel = target.resolve().relative_to(ROOT.resolve())
        display = rel.as_posix()
    except (ValueError, OSError):
        display = target.resolve().as_posix()
    key = os.path.normcase(display).replace("\\", "/")
    return key, display


def _marker_for(key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return MARKER_DIR / f"{digest}.claim"


# ---------------------------------------------------------------------------
# 占位文件读写（占位文件本身即权威状态）
# ---------------------------------------------------------------------------

def _parse_marker(text: str) -> Optional[Dict[str, Any]]:
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("owner"):
        return data
    return None


def _read_marker_fd(fd: int) -> Dict[str, Any]:
    """在已加锁的 fd 上读占位文件；容忍另一进程刚建好还没写完的瞬间。"""
    for _ in range(40):
        data = _parse_marker(_read_fd(fd))
        if data is not None:
            return data
        time.sleep(0.005)
    raise ClaimDenied("占位文件存在但内容不可读（另一进程正在写入，稍后重试）")


def _peek_marker(path: Path) -> Optional[Dict[str, Any]]:
    """只读查看（共享锁）；占位文件不存在返回 None。"""
    if not path.exists():
        return None
    try:
        with _locked_fd(path, create=False, shared=True) as fd:
            return _read_marker_fd(fd)
    except (ClaimDenied, FileNotFoundError):
        return None


def _marker_payload(display: str, key: str, owner: str, ttl_min: float, now: float) -> Dict[str, Any]:
    return {
        "path": display,
        "key": key,
        "owner": owner,
        "acquired_at": now,
        "heartbeat_at": now,
        "ttl_min": ttl_min,
        "pid": os.getpid(),
    }


def _create_marker(path: Path, payload: Dict[str, Any]) -> bool:
    """O_CREAT|O_EXCL 原子创建；已被占用返回 False。"""
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
    try:
        os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        os.fsync(fd)
    except OSError:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        raise
    os.close(fd)
    return True


# ---------------------------------------------------------------------------
# 台账（人类可读 + check_commit_scope 用；权威仍是占位文件）
# ---------------------------------------------------------------------------

def _load_registry() -> Dict[str, Any]:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "claims": {}}
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "claims": {}}
    if not isinstance(data, dict):
        return {"version": 1, "claims": {}}
    data.setdefault("version", 1)
    data.setdefault("claims", {})
    return data


def _update_registry(mutator: Callable[[Dict[str, Any]], None]) -> None:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    with _locked_fd(REGISTRY_MUTEX, create=True):
        data = _load_registry()
        mutator(data)
        data["updated_at"] = _iso(_now())
        tmp = REGISTRY_PATH.with_name(f"claims.json.tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, REGISTRY_PATH)


def _entry(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    claims = data.setdefault("claims", {})
    entry = claims.setdefault(key, {"path": key, "current": None, "history": []})
    entry.setdefault("history", [])
    entry.setdefault("current", None)
    return entry


def _close_active(entry: Dict[str, Any], now: float, reason: str) -> None:
    for item in reversed(entry["history"]):
        if item.get("released_at") is None:
            item["released_at"] = now
            item["closed_reason"] = reason
            return


def _registry_record_claim(payload: Dict[str, Any], replaced: Optional[Dict[str, Any]]) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        entry = _entry(data, payload["key"])
        entry["path"] = payload["path"]
        if replaced is not None:
            _close_active(entry, payload["heartbeat_at"], "expired_steal")
        entry["history"].append(
            {
                "owner": payload["owner"],
                "acquired_at": payload["acquired_at"],
                "released_at": None,
                "last_heartbeat_at": payload["heartbeat_at"],
                "pid": payload.get("pid"),
            }
        )
        entry["history"] = entry["history"][-50:]
        entry["current"] = payload

    _update_registry(mutate)


def _registry_renew(payload: Dict[str, Any]) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        entry = _entry(data, payload["key"])
        entry["path"] = payload["path"]
        entry["current"] = payload
        for item in reversed(entry["history"]):
            if item.get("released_at") is None and item["owner"] == payload["owner"]:
                item["last_heartbeat_at"] = payload["heartbeat_at"]
                break

    _update_registry(mutate)


def _registry_release(key: str, display: str, now: float) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        entry = _entry(data, key)
        entry["path"] = display
        _close_active(entry, now, "released")
        entry["current"] = None

    _update_registry(mutate)


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def _ttl(ttl_min: float, now: float) -> float:
    return ttl_min * 60.0


def cmd_claim(path: str, owner: str, ttl_min: float, quiet: bool = False) -> int:
    owner = owner.strip()
    if not owner:
        print("OWNER 不能为空", file=sys.stderr)
        return EXIT_USAGE
    if ttl_min <= 0:
        print("--ttl-min 必须大于 0", file=sys.stderr)
        return EXIT_USAGE

    key, display = canonical_path(path)
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    now = _now()
    payload = _marker_payload(display, key, owner, ttl_min, now)
    marker = _marker_for(key)

    if _create_marker(marker, payload):
        try:
            _registry_record_claim(payload, None)
        except ClaimDenied as exc:  # 台账只是记录，占位文件才是权威
            print(f"WARN 台账写入失败（claim 仍有效）：{exc}", file=sys.stderr)
        print(f"CLAIM OK {display} owner={owner} ttl={ttl_min:g}min 到期={_iso(now + _ttl(ttl_min, now))}")
        return EXIT_OK

    with _locked_fd(marker, create=False) as fd:
        data = _read_marker_fd(fd)
        holder = str(data.get("owner", "<unknown>"))
        heartbeat = float(data.get("heartbeat_at") or data.get("acquired_at") or now)
        held_s = max(0.0, now - heartbeat)
        ttl_holder = float(data.get("ttl_min") or 0.0) * 60.0

        if holder == owner:
            renewed = _marker_payload(display, key, owner, ttl_min, now)
            renewed["acquired_at"] = float(data.get("acquired_at") or now)
            _write_fd(fd, json.dumps(renewed, ensure_ascii=False))
            try:
                _registry_renew(renewed)
            except ClaimDenied as exc:
                print(f"WARN 台账写入失败（续租仍有效）：{exc}", file=sys.stderr)
            if not quiet:
                print(
                    f"CLAIM RENEWED {display} owner={owner} ttl={ttl_min:g}min "
                    f"上次心跳={_iso(heartbeat)} 已持有={_minutes(held_s)}min"
                )
            return EXIT_OK

        expired_s = held_s - ttl_holder
        if expired_s <= 0:
            remain = ttl_holder - held_s
            raise ClaimDenied(
                f"CLAIM DENIED {display}\n"
                f"  当前持有者={holder}\n"
                f"  已持有={_minutes(held_s)}min（TTL {_minutes(ttl_holder)}min，剩余 {_minutes(remain)}min）\n"
                f"  开始于={_iso(float(data.get('acquired_at') or heartbeat))} pid={data.get('pid')}\n"
                f"  处理：等对方 release，或换文件；禁止 stash / checkout 别人的改动。"
            )

        # 过期夺取：在占位文件自身的锁里完成，避免与续租并发写坏。
        stolen_from = holder
        old = dict(data)
        renewed = _marker_payload(display, key, owner, ttl_min, now)
        _write_fd(fd, json.dumps(renewed, ensure_ascii=False))

    try:
        _registry_record_claim(payload, old)
    except ClaimDenied as exc:
        print(f"WARN 台账写入失败（夺取仍有效）：{exc}", file=sys.stderr)
    print(
        f"原持有者 {stolen_from} 已超时 {_duration(expired_s)}，请确认它真的停了\n"
        f"CLAIM OK (steal) {display} owner={owner} ttl={ttl_min:g}min "
        f"（原持有者开始于 {_iso(float(old.get('acquired_at') or 0))}，上次心跳 {_iso(heartbeat)}）"
    )
    return EXIT_OK


def cmd_release(path: str, owner: str) -> int:
    owner = owner.strip()
    key, display = canonical_path(path)
    marker = _marker_for(key)
    if not marker.exists():
        raise ClaimDenied(f"RELEASE DENIED {display}\n  该文件当前没有 claim，无需释放。")

    with _locked_fd(marker, create=False) as fd:
        data = _read_marker_fd(fd)
    holder = str(data.get("owner", "<unknown>"))
    if holder != owner:
        now = _now()
        heartbeat = float(data.get("heartbeat_at") or data.get("acquired_at") or now)
        held_s = max(0.0, now - heartbeat)
        raise ClaimDenied(
            f"RELEASE DENIED {display}\n"
            f"  当前持有者={holder}（你声明的是 {owner}）\n"
            f"  已持有={_minutes(held_s)}min\n"
            f"  只有原持有者能释放；互相解锁等于没有锁。"
        )

    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    _registry_release(key, display, _now())
    print(f"RELEASE OK {display} owner={owner}")
    return EXIT_OK


def _describe(payload: Dict[str, Any], now: float) -> str:
    owner = payload.get("owner", "<unknown>")
    ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
    heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
    held_s = max(0.0, now - heartbeat)
    expired_s = held_s - ttl_s
    if expired_s > 0:
        state = f"EXPIRED(+{_minutes(expired_s)}min)"
    else:
        state = f"剩余 {_minutes(ttl_s - held_s)}min"
    return (
        f"{payload.get('path', '?'):<44} owner={owner:<18} held={_minutes(held_s)}/{_minutes(ttl_s)}min "
        f"{state} pid={payload.get('pid')} since={_iso(float(payload.get('acquired_at') or heartbeat))}"
    )


def cmd_status(path: Optional[str]) -> int:
    now = _now()
    if path:
        key, display = canonical_path(path)
        payload = _peek_marker(_marker_for(key))
        if payload is None:
            print(f"STATUS {display}: 未占用")
            return EXIT_OK
        print(f"STATUS {display}: {_describe(payload, now)}")
        return EXIT_OK

    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for marker in sorted(MARKER_DIR.glob("*.claim")):
        payload = _peek_marker(marker)
        if payload is None:
            continue
        items.append(payload)
    if not items:
        print("STATUS: 无任何 claim")
        return EXIT_OK
    expired = 0
    for payload in items:
        ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
        heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
        if now - heartbeat > ttl_s:
            expired += 1
        print(_describe(payload, now))
    print(f"STATUS: {len(items)} 条 claim，其中 {expired} 条已过期（过期后他人可夺取）")
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="共享文件占用锁（原子占位 + TTL 心跳 + 原持有者释放）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim", help="占用/续租一个共享文件")
    claim.add_argument("path", help="仓库相对路径，如 desktop/main_window.py")
    claim.add_argument("--owner", required=True, help="线名/会话名")
    claim.add_argument("--ttl-min", type=float, default=10.0, help="租期（分钟），默认 10")

    release = sub.add_parser("release", help="释放（仅原持有者）")
    release.add_argument("path")
    release.add_argument("--owner", required=True)

    status = sub.add_parser("status", help="查看占用状态")
    status.add_argument("path", nargs="?")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "claim":
            return cmd_claim(args.path, args.owner, args.ttl_min)
        if args.command == "release":
            return cmd_release(args.path, args.owner)
        return cmd_status(args.path)
    except ClaimDenied as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_DENIED
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
