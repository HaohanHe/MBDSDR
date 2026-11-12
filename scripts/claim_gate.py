#!/usr/bin/env python
"""claim 机械闸（pre-commit 调用）：暂存区里「别人仍持有、未过期」的文件一律拒绝。

判据全部 fail-closed（宁可拒，不许当「没人持有」）：

  * 台账（`claims.json` / `claim_takeovers.log` / `claims/*.claim`）读不到、
    JSON 解析失败、字段类型不认识 → 拒绝。**只有「文件不存在」才算没有 claim。**
  * 判活 = 心跳（`heartbeat_at` + `ttl_min`）。**pid 不参与判定**：`claim` 是
    一次性 CLI，pid 记的是那个短命进程，「已死」是默认状态，拿它当放行条件
    等于闸门对真实台账全放行（2026-11-13 的 `orphan_takeover` 事故同源）。
  * claim 已过 TTL → 放行（视同无 claim）。
  * 提交者身份（`MBDSDR_CLAIM_OWNER`，其次 `git config mbdsdr.owner`）与台账
    `owner` **原文完全一致** → 放行（同 owner 提交自己持有的文件）。身份未声明
    时，任何仍在持有的 claim 都会拒——不声明就能过，等于没闸。
  * 当前 claim 由 takeover 产生时，takeover 记录的 `reason` 必须非空；没理由的
    takeover 不认（旧持有者仍视为持有者）。

绕过：`git commit --no-verify`（pre-commit 不会跑，post-commit 记 bypass 日志）。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import file_claim as fc  # noqa: E402  （复用台账路径、占位文件锁、pid 存活判定）

IDENTITY_ENV = "MBDSDR_CLAIM_OWNER"
#: takeover 记录与当前 claim 的 acquired_at 允许的时间差（同一台机器，正常 < 1s）。
TAKEOVER_MATCH_WINDOW_S = 300.0


class LedgerError(RuntimeError):
    """台账不可用/不可信：按失败关闭处理（拒绝提交）。"""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _root() -> Path:
    proc = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    return Path(proc.stdout.strip()) if proc.returncode == 0 else Path.cwd()


def _staged(root: Path) -> List[str]:
    proc = _git(root, "diff", "--cached", "--name-only", "--no-renames")
    if proc.returncode != 0:
        raise LedgerError(f"取不到暂存文件列表：{proc.stderr.strip()}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _key(rel: str) -> str:
    """与 file_claim.canonical_path / check_commit_scope._canonical 同一口径。

    注意顺序：Windows 的 `os.path.normcase` 会把 `/` 变成 `\\`，必须在它之后再
    换回 `/`，否则算出的 key 与台账里的 key 不同（查不到 claim = 闸门失效）。
    """
    return os.path.normcase(rel.strip().replace("\\", "/")).replace("\\", "/").lstrip("./")


def _identity(root: Path) -> str:
    raw = (os.environ.get(IDENTITY_ENV) or "").strip()
    if raw:
        return raw
    proc = _git(root, "config", "--get", "mbdsdr.owner")
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return ""


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _num(claim: Dict[str, Any], field: str, default: Any = None) -> float:
    value = claim.get(field, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise LedgerError(
            f"claim 占位文件字段 {field} 不是数字：{value!r}（{claim.get('path') or claim.get('key')}）"
        )


def _load_registry() -> Dict[str, Any]:
    path = fc.REGISTRY_PATH
    if not path.exists():
        return {"claims": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LedgerError(f"台账读不到：{path}（{exc.__class__.__name__}）")
    except json.JSONDecodeError as exc:
        raise LedgerError(f"台账格式不认识（坏 JSON）：{path}（{exc}）")
    if not isinstance(data, dict):
        raise LedgerError(f"台账格式不认识：{path} 顶层不是对象")
    if "claims" in data and not isinstance(data["claims"], dict):
        raise LedgerError(f"台账格式不认识：{path} 的 claims 不是对象")
    return data


def _load_takeover_log() -> List[Dict[str, Any]]:
    path = fc.TAKEOVER_LOG
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerError(f"takeover 日志读不到：{path}（{exc.__class__.__name__}）")
    rows: List[Dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"takeover 日志格式不认识：{path} 第 {lineno} 行（{exc}）")
        if not isinstance(rec, dict) or "path" not in rec or "new_owner" not in rec:
            raise LedgerError(f"takeover 日志格式不认识：{path} 第 {lineno} 行缺 path/new_owner")
        rows.append(rec)
    return rows


def _takeover_rows(registry: Dict[str, Any], log_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并两个来源的 takeover 记录：`claim_takeovers.log`（主）+ 台账 history（次）。"""
    rows: List[Dict[str, Any]] = []
    for rec in log_rows:
        rows.append(
            {
                "path": _key(str(rec.get("path") or "")),
                "new_owner": str(rec.get("new_owner") or "").strip(),
                "reason": str(rec.get("reason") or ""),
                "epoch": _num(rec, "epoch"),
                "source": "claim_takeovers.log",
            }
        )
    for key, entry in (registry.get("claims") or {}).items():
        if not isinstance(entry, dict):
            raise LedgerError(f"台账格式不认识：claims[{key!r}] 不是对象")
        current = entry.get("current") or {}
        for item in entry.get("history") or []:
            if not isinstance(item, dict):
                raise LedgerError(f"台账格式不认识：claims[{key!r}].history 含非对象")
            note = item.get("takeover")
            released = item.get("released_at")
            if not isinstance(note, dict) or not isinstance(released, (int, float)):
                continue
            rows.append(
                {
                    "path": _key(str(entry.get("path") or key)),
                    "new_owner": str(current.get("owner") or "").strip(),
                    "reason": str(note.get("reason") or ""),
                    "epoch": float(released),
                    "source": "claims.json",
                }
            )
    return rows


def _read_claim(key: str) -> Optional[Dict[str, Any]]:
    """读 claim 占位文件（共享锁）；不存在 → None，读不到/格式不认识 → LedgerError。"""
    path = fc._marker_for(key)
    if not path.exists():
        return None
    try:
        with fc._locked_fd(path, create=False, shared=True, timeout=5.0) as fd:
            text = fc._read_fd(fd)
    except fc.ClaimDenied as exc:
        if not path.exists():
            return None  # 与 release 撞车：文件已删，视同无 claim
        raise LedgerError(f"claim 占位文件读不到：{path}（{exc}）")
    claim = fc._parse_marker(text)
    if claim is None:
        raise LedgerError(f"claim 占位文件格式不认识：{path}")
    return claim


def _pid_note(claim: Dict[str, Any]) -> str:
    """pid 诊断行（**不参与判定**，只帮人判断要不要等 TTL）。"""
    raw_pid = claim.get("pid")
    if raw_pid is None:
        return "台账未记录 pid"
    try:
        state, _create, _cmd = fc._proc_snapshot(int(raw_pid))
    except (TypeError, ValueError, OSError) as exc:
        return f"pid {raw_pid} 状态取不到（{exc.__class__.__name__}）"
    return (
        f"pid {raw_pid} 状态={state}（仅诊断：claim 是一次性 CLI，"
        "pid 已退出是默认状态，不代表持有会话结束）"
    )


def _matching_takeovers(
    rows: List[Dict[str, Any]], key: str, owner: str, acquired: float
) -> List[Dict[str, Any]]:
    return [
        r
        for r in rows
        if r["path"] == key
        and r["new_owner"] == owner
        and abs(r["epoch"] - acquired) <= TAKEOVER_MATCH_WINDOW_S
    ]


def _inspect(
    rel: str, me: str, takeovers: List[Dict[str, Any]], now: float
) -> Optional[Dict[str, Any]]:
    """返回 None = 该文件放行；返回 dict = 证据行（state 决定放行还是拒绝）。"""
    key = _key(rel)
    claim = _read_claim(key)
    if claim is None:
        return None
    owner = str(claim.get("owner") or "").strip()
    if not owner:
        raise LedgerError(f"claim 占位文件格式不认识：{rel} 没有 owner")
    acquired = _num(claim, "acquired_at")
    heartbeat = _num(claim, "heartbeat_at", acquired)
    ttl_s = _num(claim, "ttl_min") * 60.0
    if ttl_s <= 0:
        raise LedgerError(f"claim 占位文件格式不认识：{rel} 的 ttl_min 不是正数")
    expires = heartbeat + ttl_s
    row: Dict[str, Any] = {
        "path": rel,
        "owner": owner,
        "acquired_at": acquired,
        "heartbeat": heartbeat,
        "expires_at": expires,
        "ttl_s": ttl_s,
    }
    matches = _matching_takeovers(takeovers, key, owner, acquired)
    if matches:
        row["takeover"] = matches[0]
        if any(not r["reason"].strip() for r in matches):
            return {
                **row,
                "state": "blocked",
                "why": "当前 claim 来自没写理由的 takeover（"
                + matches[0]["source"]
                + "），按纪律不认：旧持有者仍视为持有者",
            }
    if now > expires:
        return {**row, "state": "expired"}
    if me and me == owner:
        return {**row, "state": "self"}
    row["pid_note"] = _pid_note(claim)
    return {
        **row,
        "state": "blocked",
        "why": (
            f"claim 未过期（已持有 {(now - heartbeat) / 60.0:.1f} 分钟 / "
            f"TTL {ttl_s / 60.0:.0f} 分钟）；{row['pid_note']}"
        ),
    }


def _print_blocked(row: Dict[str, Any], me: str) -> None:
    remaining = max(0.0, (row["expires_at"] - time.time()) / 60.0)
    print("[claim-gate] 拒绝提交：暂存文件仍被别的 owner 持有（未过期且无有效 takeover）")
    print(f"  文件：{row['path']}")
    print(f"  持有者：{row['owner']}")
    print(f"  claim 时间：{_iso(row['acquired_at'])}（心跳 {_iso(row['heartbeat'])}）")
    print(
        f"  到期时间：{_iso(row['expires_at'])}"
        f"（TTL {row['ttl_s'] / 60.0:.0f} 分钟，剩余 {remaining:.1f} 分钟）"
    )
    print(f"  判据：{row['why']}")
    if row.get("takeover"):
        print(f"  已有 takeover 记录：{row['takeover']['source']} reason={row['takeover']['reason']!r}")
    print("  三条出路：")
    print(f"    1) 等 TTL 到期后重试（剩余 {remaining:.1f} 分钟；走 takeover 还需过期满 30 分钟）")
    print(
        "    2) 走 takeover 并写明理由："
        f'python scripts/file_claim.py takeover {row["path"]} '
        f'--owner "<你的 owner>" --reason "<为什么接管>"'
    )
    print(
        f"    3) 找持有者 release：python scripts/file_claim.py release {row['path']} "
        f'--owner "{row["owner"]}"'
    )
    if not me:
        print(
            f"  本次提交身份未声明（{IDENTITY_ENV} 与 git config mbdsdr.owner 都为空）；"
            f"自己持有的文件也要声明身份才放行：$env:{IDENTITY_ENV}=\"<台账里的 owner 原文>\""
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="claim 机械闸：拒绝提交别人仍持有的文件")
    parser.add_argument("--staged", action="store_true", help="检查暂存区（默认行为）")
    parser.parse_args(argv)

    root = _root()
    try:
        names = _staged(root)
    except LedgerError as exc:
        print(f"[claim-gate] 拒绝：{exc}")
        return 2
    if not names:
        print("[claim-gate] 暂存区为空，放行。")
        return 0

    me = _identity(root)
    now = time.time()
    try:
        registry = _load_registry()
        takeovers = _takeover_rows(registry, _load_takeover_log())
        inspected = [(rel, _inspect(rel, me, takeovers, now)) for rel in names]
    except LedgerError as exc:
        print(f"[claim-gate] 拒绝：台账不可用/不可信，按失败关闭 —— {exc}")
        return 2

    blocked = [(rel, row) for rel, row in inspected if row and row["state"] == "blocked"]
    counts = {"self": 0, "expired": 0, "blocked": len(blocked)}
    for _rel, row in inspected:
        if row:
            counts[row["state"]] += 1

    if blocked:
        for _rel, row in blocked:
            _print_blocked(row, me)
        print(
            f"[claim-gate] 结论：拒绝（{len(blocked)}/{len(names)} 个暂存文件被他人持有）；"
            f"本次提交身份：{me or '<未声明>'}"
        )
        return 1

    for rel, row in inspected:
        if row and row["state"] == "expired":
            print(f"[claim-gate] 放行（claim 已过 TTL）：{rel} 原 owner={row['owner']}")
    print(
        f"[claim-gate] 通过：{len(names)} 个暂存文件；"
        f"自己持有 {counts['self']} / 已过期 {counts['expired']}；"
        f"身份：{me or '<未声明，无活跃 claim 时不影响>'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
