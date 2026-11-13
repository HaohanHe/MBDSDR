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

另有一节「可疑占位（只报不拦）」：活跃 claim 里带 `owner_mismatch`（写着别人
名字）或 `reason_missing`（没写为什么占）的单独列出。它们是**比没有占位更糟**
的占位——别人会以为这里安全。只报，不拦，也不替人判断归属。
`--json` 输出 `{"unclaimed": [...], "flagged_claims": [...]}`。
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
from claim_window import claim_active  # noqa: E402  （判活唯一口径，与闸共用）


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


def _active_owner(path: str, now: float, marker_dir=None) -> str:
    """按占位文件判活；无活跃 claim 返回空。

    判活调 ``claim_window.claim_active``——与 pre-commit ④ 的闸、以及
    ``append_coordination_row`` 用的是同一个函数。这里**不许**再写一遍
    TTL 比较：两边各写一份时，「正好到期」那一刻会给出相反答案。
    """
    try:
        key, _display = fc.canonical_path(path)
    except Exception:
        return ""
    marker = fc._marker_for(key)
    if marker_dir is not None:
        marker = Path(marker_dir) / marker.name
    payload = fc._peek_marker(marker)
    if not claim_active(payload, now):
        return ""
    return str(payload.get("owner") or "")


def _flagged_claims(now: float, marker_dir=None) -> list[dict]:
    """活跃 claim 里带 ``owner_mismatch`` / ``reason_missing`` 的，单列一项。

    为什么单列：一条写着别人名字、又没有理由的占位，等于**不知道谁占的、
    也不知道为什么占**——它比没有占位更糟，因为别人会以为这里是安全的。

    口径（只报不拦）：
      * ``owner_mismatch`` 只认显式 ``true``；旧占位没有这个键时输出
        「未记录」，**不**把「读不到」当成「冒名」（会把正常占位全标红）；
      * ``reason_missing`` 缺键时退回读 ``claim_reason``：老占位也能算出来；
      * 只列 ``claim_active`` 为真的，判活与闸共用同一个函数。
    """
    directory = Path(marker_dir) if marker_dir is not None else fc.MARKER_DIR
    out: list[dict] = []
    if not directory.exists():
        return out
    for marker in sorted(directory.glob("*.claim")):
        payload = fc._peek_marker(marker)
        if not claim_active(payload, now):
            continue
        recorded = payload.get("owner_mismatch")
        mismatch = True if recorded is True else None if recorded is None else False
        if "reason_missing" in payload:
            reason_missing = bool(payload.get("reason_missing"))
        else:
            reason_missing = not str(payload.get("claim_reason") or "").strip()
        if not (mismatch or reason_missing):
            continue
        heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
        out.append({
            "path": str(payload.get("path") or marker.stem),
            "owner": str(payload.get("owner") or ""),
            "issued_by": str(payload.get("issued_by") or ""),
            "owner_mismatch": mismatch,
            "reason_missing": reason_missing,
            "claim_reason": str(payload.get("claim_reason") or ""),
            "held_min": round(max(0.0, now - heartbeat) / 60.0, 1),
        })
    return out


def _flags(row: dict) -> str:
    tags = []
    if row["owner_mismatch"]:
        tags.append("冒名")
    if row["reason_missing"]:
        tags.append("空理由")
    return "+".join(tags)


def _print_flagged(rows: list[dict]) -> None:
    print()
    print(f"可疑占位（只报不拦）：{len(rows)} 条")
    if not rows:
        print("  （无：活跃 claim 里没有冒名或空理由的占位）")
        return
    for row in rows:
        issuer = row["issued_by"] or "未记录"
        mismatch = {True: "true", False: "false", None: "未记录（旧占位无此键）"}[
            row["owner_mismatch"]
        ]
        print(f"  [{_flags(row)}] {row['path']}")
        print(f"    owner={row['owner']}  issued_by={issuer}  "
              f"owner_mismatch={mismatch}  reason_missing={row['reason_missing']}  "
              f"已持有={row['held_min']}min")
        print(f"    理由={row['claim_reason'] or '（空）'}")
    print("  说明：这两类占位只知道「名字」，不知道「谁签的 / 为什么占」；"
          "按需核对后再决定是否 takeover，脚本不替你判断。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="列出无活跃 claim 的在途改动")
    parser.add_argument("--all", action="store_true", help="连有活跃 claim 的也列出")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "--markers", default="", help="占位目录（缺省 D:\\mbdsdr\\.locks\\claims，仅测试用）"
    )
    args = parser.parse_args(argv)

    now = time.time()
    rows = []
    for xy, path in _changed_paths():
        owner = _active_owner(path, now)
        if owner and not args.all:
            continue
        rows.append({"status": xy, "path": path, "owner": owner})
    flagged = _flagged_claims(now, args.markers.strip() or None)

    if args.json:
        print(json.dumps(
            {"unclaimed": rows, "flagged_claims": flagged},
            ensure_ascii=False, indent=2,
        ))
        return 0

    print("候选集：工作区有改动 / 未提交进 HEAD / 当前无活跃 claim")
    for row in rows:
        if row["owner"]:
            print(f"  {row['status']} {row['path']}  [有 claim: {row['owner']}]")
        else:
            print(f"  {row['status']} {row['path']}")
    print(f"共 {len(rows)} 个路径。清单只说明没人占位，不说明无主；"
          "逐条归属须给凭据（claim / 台账 / 补丁单），给不出写「无凭据」。")
    _print_flagged(flagged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
