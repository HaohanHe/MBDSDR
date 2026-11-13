#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""按需审计：列出「工作区有改动 / 未提交进 HEAD / 当前无活跃 claim」的路径。

**这不是闸，也不要把它做成闸。** 「没有活跃 claim」只回答「此刻没人占位」，
不回答「没有主人」；归属是人的判断，脚本只负责把候选集摆出来，不替人签字。
判据来自 `docs/incidents/2026-11-13-safe-commit-swept-inflight-work.md`
（`052828d1`：把「未提交 + 配着测试」当成孤儿，卷走另一条线 1400 行在途活）。

用法::

    python scripts/audit_unclaimed_changes.py        # 只列无活跃 claim 的
    python scripts/audit_unclaimed_changes.py --all  # 连有 claim 的也列（带 owner）
    python scripts/audit_unclaimed_changes.py --json

输出只是候选集：每一条的最终归属仍须人工给出证据（claim 记录 / 台账声明 /
`docs/pending-patches/` 补丁单）；都拿不出来就写「无凭据」，不许猜。
本脚本恒以 0 退出，不阻断任何流程。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(
    os.environ.get("MBDSDR_REPO_ROOT") or Path(__file__).resolve().parents[1]
)
sys.path.insert(0, str(ROOT / "scripts"))
import file_claim as fc  # noqa: E402  （与 claim 工具同一套占位解析）


def _changed_paths() -> list[tuple[str, str]]:
    """`git status --porcelain -z` → [(XY, path)]，重命名取新路径。"""
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=str(ROOT), capture_output=True,
    )
    if proc.returncode != 0:
        raise SystemExit("git status 失败：" + proc.stderr.decode("utf-8", "replace"))
    raw = proc.stdout.decode("utf-8", "replace")
    items = raw.split("\0")
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(items):
        item = items[i]
        i += 1
        if not item or len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        if xy[0] in ("R", "C") and i < len(items):  # 重命名/复制：下一条是新路径
            path = items[i]
            i += 1
        out.append((xy, path))
    return out


def _active_owner(path: str, now: float) -> str:
    """按占位文件判活（与闸/工具同一规则：heartbeat + ttl）；无活跃 claim 返回空。"""
    try:
        key, _display = fc.canonical_path(path)
    except Exception:
        return ""
    payload = fc._peek_marker(fc._marker_for(key))
    if not payload:
        return ""
    ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
    heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
    if now - heartbeat > ttl_s:
        return ""
    return str(payload.get("owner") or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="列出无活跃 claim 的在途改动")
    parser.add_argument("--all", action="store_true", help="连有活跃 claim 的也列出")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    now = time.time()
    rows = []
    for xy, path in _changed_paths():
        owner = _active_owner(path, now)
        if owner and not args.all:
            continue
        rows.append({"status": xy, "path": path, "owner": owner})

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print("候选集：工作区有改动 / 未提交进 HEAD / 当前无活跃 claim")
    for row in rows:
        if row["owner"]:
            print(f"  {row['status']} {row['path']}  [有 claim: {row['owner']}]")
        else:
            print(f"  {row['status']} {row['path']}")
    print(f"共 {len(rows)} 个路径。清单只说明没人占位，不说明无主；"
          "逐条归属须给凭据（claim / 台账 / 补丁单），给不出写「无凭据」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
