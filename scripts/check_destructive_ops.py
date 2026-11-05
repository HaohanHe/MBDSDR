#!/usr/bin/env python3
"""事后审计：reflog 里发生过的破坏性操作，必须能在 destructive_ops.log 找到放行记录。

能查到的：
  * `git reflog --all` 里每条 `reset: moving to ...`——reflog **不记录 reset 模式**
    （soft/mixed/hard 的 message 完全相同），因此按「所有 reset 都是破坏性候选」保守审计；
  * reflog 里的 `forced-update`（远端跟踪引用被强制覆盖的痕迹），按强推候选审计。

对账：
  * 只看 `destructive_ops.log` 里 `allowed=true` 的记录（被拒的操作没有发生，不该对账）；
  * 先按 `head_before`→`head_after` 精确匹配，再退化为「新提交相同 + 时间 ±180s」。

输出：凡是仓库里发生过、日志里没有的破坏性操作，一律 `UNAUDITED` 并列出时间与提交号，
退出码 1；全部有据可查则 `PASS`，退出码 0。

做不到的（明说）：
  * reflog 不含 push 动作本身；force push 到远端若未在本地留下 reflog，查不到；
  * reset 模式无法从 reflog 分辨，no-op 也会被算进候选（宁可多报，不漏报）。

用法：python scripts/check_destructive_ops.py [--repo <path>] [--locks-dir <path>] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_LOCKS_DIR = Path(r"D:\mbdsdr\.locks")
LOG_NAME = "destructive_ops.log"


def _run_git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc.stdout


def _log_entries(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("allowed"):
            entries.append(rec)
    return entries


def _streams(root: Path) -> Dict[str, List[tuple]]:
    """按 ref 拆 reflog 流：(date, new_sha, msg)，新的在前。"""
    text = _run_git(root, "reflog", "show", "--date=iso", "--all", "--format=%gd|%H|%gs")
    streams: Dict[str, List[tuple]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        gd, sha, msg = parts
        ref, _, date = gd.partition("@{")
        streams.setdefault(ref, []).append((date.rstrip("}"), sha, msg))
    return streams


def _events(root: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    streams = _streams(root)
    # HEAD 与当前分支会把同一次操作记两遍，且 HEAD 的 old 可能因其他操作错位；
    # 以分支流为准，HEAD 仅在没有任何分支流时兜底。
    branch_streams = {ref: items for ref, items in streams.items() if not ref.startswith("HEAD")}
    for ref, items in (branch_streams or streams).items():
        for index, (date, sha, msg) in enumerate(items):
            kind = None
            if "reset: moving to" in msg:
                kind = "reset"
            elif "forced-update" in msg:
                kind = "force-update"
            if kind is None:
                continue
            old = items[index + 1][1] if index + 1 < len(items) else "?"
            events.append(
                {"ref": ref, "kind": kind, "date": date, "old": old, "new": sha, "msg": msg}
            )
    # HEAD 与当前分支会重复记录同一次操作，按 (date, old, new) 去重
    seen = set()
    unique: List[Dict[str, Any]] = []
    for event in events:
        key = (event["date"], event["old"], event["new"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    unique.sort(key=lambda item: item["date"])
    return unique


def _event_epoch(event: Dict[str, Any]) -> Optional[float]:
    try:
        return datetime.strptime(event["date"], "%Y-%m-%d %H:%M:%S %z").timestamp()
    except (ValueError, TypeError):
        return None


def _log_epoch(rec: Dict[str, Any]) -> Optional[float]:
    try:
        return datetime.strptime(str(rec.get("ts", "")), "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (ValueError, TypeError):
        return None


def _match(event: Dict[str, Any], entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for rec in entries:
        if rec.get("head_after") == event["new"] and rec.get("head_before") == event["old"]:
            return rec
    moment = _event_epoch(event)
    if moment is None:
        return None
    for rec in entries:
        if rec.get("head_after") != event["new"]:
            continue
        rec_moment = _log_epoch(rec)
        if rec_moment is not None and abs(rec_moment - moment) <= 180:
            return rec
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="破坏性操作事后审计")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--locks-dir", default=str(Path(os.environ.get("MBDSDR_LOCKS_DIR") or DEFAULT_LOCKS_DIR)))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.repo)
    log_path = Path(args.locks_dir) / LOG_NAME
    try:
        events = _events(root)
    except (OSError, RuntimeError) as exc:
        print(f"审计失败：{exc}")
        return 2
    entries = _log_entries(log_path)

    rows: List[Dict[str, Any]] = []
    unaudited: List[Dict[str, Any]] = []
    for event in events:
        rec = _match(event, entries)
        row = {
            "kind": event["kind"],
            "ref": event["ref"],
            "date": event["date"],
            "old": event["old"],
            "new": event["new"],
            "audited": bool(rec),
            "reason": rec.get("reason") if rec else None,
            "log_ts": rec.get("ts") if rec else None,
        }
        rows.append(row)
        if rec is None:
            unaudited.append(row)

    if args.json:
        print(
            json.dumps(
                {
                    "repo": str(root),
                    "log": str(log_path),
                    "events": len(rows),
                    "audited": len(rows) - len(unaudited),
                    "unaudited": unaudited,
                    "limitations": [
                        "reflog 不记录 reset 模式（soft/mixed/hard 同一条 message），全部按候选审计",
                        "reflog 不含 push 动作；force push 若未留下 forced-update 痕迹则查不到",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if unaudited else 0

    print(f"REPO {root}")
    print(f"LOG  {log_path}（放行记录 {len(entries)} 条）")
    for row in unaudited:
        print(
            f"  UNAUDITED {row['kind']:<12} {row['date']}  "
            f"{row['old'][:10]} -> {row['new'][:10]}  [{row['ref']}]"
        )
    print()
    print(f"reflog 破坏性候选 {len(rows)} 笔；有放行记录 {len(rows) - len(unaudited)} 笔；未审计 {len(unaudited)} 笔")
    print("口径：reflog 不记录 reset 模式，soft/mixed/hard/no-op 一律算候选；只对账 allowed=true 的日志。")
    return 1 if unaudited else 0


if __name__ == "__main__":
    sys.exit(main())
