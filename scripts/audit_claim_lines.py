#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""按提交信息里的 ``claim-line:`` 归线，并核对它与台账是否对得上。

背景：659 笔提交共用一个 git 身份，``git log`` 的 author/committer 区分不了
「哪条线」。2026-11-14 起，``safe_commit.py`` 会把提交者**此刻真实持有**的
claim 线名追加到说明末尾（每线一行）：

    claim-line: 收尸线

本脚本只做**事后核对**，不改历史、不判断归属：

  1. 按台账区间算出「提交时刻有哪些 claim 覆盖了本笔改动的文件」；
  2. 把提交信息里的 ``claim-line:`` 与这份台账期望逐条比对；
  3. 分三个可核对的桶：

     * 相符：写了行，且行名在期望里；
     * **有 claim 但提交信息没这行**：台账说当时有人占着，说明却没写；
     * **这行与台账不符**：写了行，但行名不在期望里（含「台账里根本没有
       覆盖本笔的 claim」这种无凭据签名）。

`claim-line:` 是**复述**，不是授权：它只说明「提交者当时持有哪个 claim」，
不证明代码是谁写的。工具不补写、不纠正——写错就是写错，审计只负责报出来。

台账区间口径：优先用 ``history[].acquired_at`` / ``released_at``；未释放的
条目用 ``last_heartbeat_at + current.ttl_min``（缺 ttl 时取 180 分钟，与默认
claim TTL 一致）。没有台账记录的提交按「无 claim」处理。

用法::

    python scripts/audit_claim_lines.py            # HEAD 全历史，统计 + 逐条
    python scripts/audit_claim_lines.py --all      # 所有 ref 可达提交
    python scripts/audit_claim_lines.py --quiet    # 只打印统计
    python scripts/audit_claim_lines.py --rev HEAD~50..HEAD

退出码：0（只读审计，不判红）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(os.environ.get("MBDSDR_REPO_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
from claim_window import line_name, parse_claim_lines  # noqa: E402  （解析口径唯一实现）

#: 未释放的 history 条目缺 ttl_min 时的兜底（与 claim 默认 TTL 一致）。
DEFAULT_TTL_MIN = 180.0

BUCKET_MATCHED = "matched"
BUCKET_CLAIM_NO_LINE = "claim_without_trailer"
BUCKET_MISMATCH = "mismatch"
BUCKET_NO_CLAIM_NO_LINE = "no_claim_no_trailer"

LABELS = {
    BUCKET_MATCHED: "行与台账相符",
    BUCKET_CLAIM_NO_LINE: "有 claim 但提交信息没这行",
    BUCKET_MISMATCH: "这行与台账不符",
    BUCKET_NO_CLAIM_NO_LINE: "无 claim 也无行（历史空档）",
}


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or f"git {' '.join(args)} 失败")
    return proc.stdout


def _norm(path: str) -> str:
    text = str(path).strip().replace("\\", "/").lower()
    while text.startswith("./"):
        text = text[2:]
    return text


def load_commits(rev: str) -> List[Dict[str, Any]]:
    """sha / 提交时刻 / 说明 / 本笔改动路径（新→旧）。"""
    raw = _git("-c", "core.quotepath=false", "log", rev, "--no-merges", "--no-renames",
               "--format=%x1e%H%x1f%ct%x1f%B%x1d", "--name-only")
    commits: List[Dict[str, Any]] = []
    for chunk in raw.split("\x1e")[1:]:
        head, _, tail = chunk.partition("\x1d")
        sha, _, rest = head.partition("\x1f")
        stamp, _, message = rest.partition("\x1f")
        paths = [_norm(p) for p in tail.strip().splitlines() if p.strip()]
        try:
            ct = float(stamp)
        except ValueError:
            continue
        commits.append({
            "sha": sha.strip(),
            "ct": ct,
            "msg": message,
            "paths": sorted(set(paths)),
        })
    return commits


def load_intervals(registry: Path) -> Dict[str, List[Dict[str, Any]]]:
    """路径 → claim 区间（owner/start/end）。台账读不到时返回空表。"""
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    if not registry.exists():
        return out
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"台账不可读：{registry}（{exc}）")
    for key, entry in (data.get("claims") or {}).items():
        if not isinstance(entry, dict):
            continue
        path = _norm(entry.get("path") or key)
        current = entry.get("current") if isinstance(entry.get("current"), dict) else {}
        try:
            ttl_s = float(current.get("ttl_min") or DEFAULT_TTL_MIN) * 60.0
        except (TypeError, ValueError):
            ttl_s = DEFAULT_TTL_MIN * 60.0
        seen = set()
        for item in entry.get("history") or []:
            if not isinstance(item, dict):
                continue
            owner = str(item.get("owner") or "").strip()
            start = item.get("acquired_at")
            if not owner or not isinstance(start, (int, float)):
                continue
            end = item.get("released_at")
            if not isinstance(end, (int, float)):
                heartbeat = item.get("last_heartbeat_at") or start
                try:
                    end = float(heartbeat) + ttl_s
                except (TypeError, ValueError):
                    end = float(start) + ttl_s
            signature = (owner, round(float(start), 3), round(float(end), 3))
            if signature in seen:
                continue
            seen.add(signature)
            out[path].append({"owner": owner, "start": float(start), "end": float(end)})
    return out


def owners_at(intervals: Dict[str, List[Dict[str, Any]]],
              paths: List[str], at: float) -> List[str]:
    owners: List[str] = []
    for path in paths:
        for item in intervals.get(path, ()):
            if item["start"] <= at <= item["end"] and item["owner"] not in owners:
                owners.append(item["owner"])
    return owners


def classify(commit: Dict[str, Any],
             intervals: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    owners = owners_at(intervals, commit["paths"], commit["ct"])
    expected = sorted({line_name(o) for o in owners if line_name(o)})
    written = parse_claim_lines(commit["msg"])
    row = {
        "sha": commit["sha"], "ct": commit["ct"], "paths": commit["paths"],
        "owners": owners, "expected": expected, "written": written,
    }
    if written and expected:
        matched = [n for n in written if n in expected]
        extra = [n for n in written if n not in expected]
        if matched and not extra:
            row["bucket"] = BUCKET_MATCHED
        else:
            row["bucket"] = BUCKET_MISMATCH
            row["reason"] = ("行名不在台账期望里：" + "、".join(extra)) if extra \
                else "行名不在台账期望里"
    elif written:
        row["bucket"] = BUCKET_MISMATCH
        row["reason"] = "台账里没有覆盖本笔改动的 claim"
    elif expected:
        row["bucket"] = BUCKET_CLAIM_NO_LINE
        row["reason"] = "台账当时有 claim，说明里没写 claim-line"
    else:
        row["bucket"] = BUCKET_NO_CLAIM_NO_LINE
        row["reason"] = "台账无覆盖 claim，说明也没写"
    return row


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按 claim-line 归线并核对台账")
    ap.add_argument("--rev", default="HEAD", help="git log 范围（默认 HEAD）")
    ap.add_argument("--all", action="store_true", help="改为所有 ref 可达提交")
    ap.add_argument("--registry", default="", help="台账路径（默认 MBDSDR_LOCKS_DIR/claims.json）")
    ap.add_argument("--quiet", action="store_true", help="只打印统计")
    args = ap.parse_args(argv)

    import file_claim as fc  # noqa: E402  （与 claim 工具同一处台账路径）
    registry = Path(args.registry) if args.registry else fc.REGISTRY_PATH
    rev = "--all" if args.all else args.rev
    commits = load_commits(rev)
    intervals = load_intervals(registry)
    rows = [classify(c, intervals) for c in commits]

    counts = Counter(r["bucket"] for r in rows)
    first_line = min((r["ct"] for r in rows if r["written"]), default=None)
    after_rule = [r for r in rows if first_line is not None and r["ct"] >= first_line]

    def count(bucket: str, subset: List[Dict[str, Any]]) -> int:
        return sum(1 for r in subset if r["bucket"] == bucket)

    print(f"审计范围：{rev}，{len(rows)} 笔提交；台账：{registry}")
    if first_line is None:
        print("claim-line 规则：本范围内尚未出现任何 claim-line 行")
    else:
        print(f"claim-line 规则：首次出现于 {_iso(first_line)}；"
              f"此后 {len(after_rule)} 笔用于分时点统计")
    print()
    print("按提交信息归线（一笔多行按多行计）：")
    per_line = Counter()
    for r in rows:
        if r["written"]:
            for name in r["written"]:
                per_line[name] += 1
        else:
            per_line["<无 claim-line>"] += 1
    for name, n in per_line.most_common():
        print(f"  {name:<28} {n} 笔")
    print()
    print("核对：")
    for bucket in (BUCKET_MATCHED, BUCKET_CLAIM_NO_LINE, BUCKET_MISMATCH,
                   BUCKET_NO_CLAIM_NO_LINE):
        total = counts.get(bucket, 0)
        tail = ""
        if bucket in (BUCKET_CLAIM_NO_LINE, BUCKET_MISMATCH) and first_line is not None:
            tail = f"（规则后 {count(bucket, after_rule)} 笔）"
        print(f"  {LABELS[bucket]:<18} {total} 笔{tail}")
    mismatch_no_claim = sum(1 for r in rows if r["bucket"] == BUCKET_MISMATCH
                            and not r["expected"])
    if mismatch_no_claim:
        print(f"    其中「台账无覆盖 claim」的签名 {mismatch_no_claim} 笔")

    if args.quiet:
        return 0

    problems = [r for r in rows if r["bucket"] in (BUCKET_CLAIM_NO_LINE, BUCKET_MISMATCH)]
    if not problems:
        print()
        print("没有需要逐条核对的问题提交。")
        return 0
    print()
    print("逐条（新→旧）：")
    for r in sorted(problems, key=lambda x: x["ct"], reverse=True):
        print(f"{r['sha'][:12]}  {_iso(r['ct'])}  [{LABELS[r['bucket']]}]")
        if r["written"]:
            print(f"  写：{'、'.join(r['written'])}；"
                  f"台账期望：{'、'.join(r['expected']) or '<无覆盖 claim>'}")
        else:
            print(f"  写：<无>；台账期望：{'、'.join(r['expected']) or '<无覆盖 claim>'}")
        print(f"  台账 owner：{'、'.join(r['owners']) or '<无>'}；"
              f"路径 {len(r['paths'])} 个：{', '.join(r['paths'][:5])}"
              + (" …" if len(r["paths"]) > 5 else ""))
        print(f"  原因：{r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
