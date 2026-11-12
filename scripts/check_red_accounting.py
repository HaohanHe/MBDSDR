#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""无账红机检：守卫非 0 退出，必须在 K 账里有条目或有一条带期限的豁免。

判据（可复跑）：
  * 守卫清单来自 ``docs/guard-accounting.json``：每条给 ``script`` + ``args``，
    机检按同样的命令实跑，拿到真实退出码，不读缓存、不读记忆。
  * ``mode=run`` 的守卫非 0 退出时，必须二选一：
      1. ``k``：K 编号列表，且每个编号都能在 ``docs/known-red-register.md``
         的标题里找到（``## K06 — ...`` / ``## K01–K04：...`` 都认）；
      2. ``exemption``：``reason`` + ``owner`` + ``review_by``（YYYY-MM，
         当月仍有效，过月即判过期）。
    两者都没有 → 「无账红」，退出码 1。
  * ``mode=manual`` 的守卫无法从当前仓库独立执行（缺参数/硬件/GUI/钩子上下文），
    必须写 ``reason`` + ``review_by``；reason 为空或豁免过期同样判红。
  * 跑绿的守卫不需要账；但如果它同时在账里，账不会被当成「已修」——本机检只
    回答「红有没有账」，不回答「账有没有修」。

反证：``tests/test_red_accounting.py`` 用临时仓库造一个退出 1 的假守卫，
不放 K 条目 → 本机检必须报「无账红」；放 K 条目/有效豁免 → 才允许通过。

用法：
  python scripts/check_red_accounting.py                # 跑全部 run 项（约 5 分钟）
  python scripts/check_red_accounting.py --fast         # 跳过 slow 项
  python scripts/check_red_accounting.py --only lint_swallow_all,check_destructive_ops
  python scripts/check_red_accounting.py --json
  python scripts/check_red_accounting.py --root <仓库>   # 测试用

退出码：0 无无账红；1 有无账红 / 账目悬空 / 豁免过期；2 用法或配置错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = "docs/guard-accounting.json"
REVIEW_RE = re.compile(r"^(\d{4})-(\d{2})$")
K_HEADING_RE = re.compile(r"^#{1,6}\s+K(\d+)(?:\s*[–—-]\s*K?(\d+))?", re.M)


def _today_ym() -> Tuple[int, int]:
    import datetime
    now = datetime.date.today()
    return now.year, now.month


def ledger_ids(path: Path) -> Set[str]:
    """从 K 账标题里抽编号，展开 ``K01–K04`` 这种区间。"""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    ids: Set[str] = set()
    for match in K_HEADING_RE.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if end < start or end - start > 50:
            end = start
        for number in range(start, end + 1):
            ids.add(f"K{number:02d}")
    return ids


def _check_review(value: Any) -> Optional[str]:
    """返回 None 表示有效，否则返回错误原因。"""
    if not isinstance(value, str):
        return "缺少 review_by"
    match = REVIEW_RE.match(value.strip())
    if not match:
        return f"review_by={value!r} 不是 YYYY-MM"
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return f"review_by={value!r} 月份非法"
    today = _today_ym()
    if (year, month) < today:
        return f"豁免已过期（review_by={value}）"
    return None


def _run_guard(root: Path, entry: Dict[str, Any], timeout: int) -> Tuple[int, str]:
    script = root / entry["script"]
    if not script.exists():
        return 127, f"脚本不存在：{entry['script']}"
    env = dict(os.environ)
    env.setdefault("MBDSDR_OFFLINE", "1")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        proc = subprocess.run(
            [sys.executable, str(script), *entry.get("args", [])],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）"
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    lines = [line for line in output.splitlines() if line.strip()]
    return proc.returncode, (lines[-1] if lines else "")[:200]


def evaluate(root: Path, registry: Dict[str, Any], *,
             only: Optional[Set[str]], fast: bool, timeout: int) -> Dict[str, Any]:
    ledger_path = root / registry.get("ledger", "docs/known-red-register.md")
    known_k = ledger_ids(ledger_path)
    rows: List[Dict[str, Any]] = []
    counts: Counter = Counter()

    for entry in registry.get("guards", []):
        gid = entry.get("id") or entry.get("script", "?")
        if only is not None and gid not in only:
            continue
        mode = entry.get("mode", "run")
        row: Dict[str, Any] = {"id": gid, "script": entry.get("script", ""), "mode": mode}

        if mode == "manual":
            err = _check_review(entry.get("review_by"))
            if not str(entry.get("reason", "")).strip():
                err = "mode=manual 但没写 reason"
            row["status"] = "manual"
            row["detail"] = entry.get("reason", "")
            row["review_by"] = entry.get("review_by")
            if err:
                row["problem"] = err
                counts["未跑无期限"] += 1
            else:
                counts["未跑"] += 1
            rows.append(row)
            continue

        if fast and entry.get("slow"):
            row["status"] = "skipped-slow"
            row["detail"] = "fast 模式跳过 slow 项"
            row["review_by"] = (entry.get("exemption") or {}).get("review_by", "")
            counts["跳过"] += 1
            rows.append(row)
            continue

        rc, tail = _run_guard(root, entry, timeout)
        row["rc"] = rc
        row["detail"] = tail
        if rc == 0:
            row["status"] = "green"
            counts["绿"] += 1
            rows.append(row)
            continue

        counts["红"] += 1
        k_list = entry.get("k") or []
        missing = [k for k in k_list if k not in known_k]
        if k_list and not missing:
            row["status"] = "accounted-k"
            row["account"] = ",".join(k_list)
            counts["已入账"] += 1
        elif missing:
            row["status"] = "dangling-k"
            row["problem"] = f"K 编号不在账里：{','.join(missing)}"
            counts["账目悬空"] += 1
        elif entry.get("exemption"):
            exemption = entry["exemption"]
            err = _check_review(exemption.get("review_by"))
            if not str(exemption.get("reason", "")).strip():
                err = "豁免没写 reason"
            if not str(exemption.get("owner", "")).strip():
                err = "豁免没写 owner"
            row["review_by"] = exemption.get("review_by")
            row["owner"] = exemption.get("owner", "")
            if err:
                row["status"] = "expired-exemption"
                row["problem"] = err
                counts["豁免过期"] += 1
            else:
                row["status"] = "accounted-exemption"
                row["account"] = f"豁免至 {exemption['review_by']}"
                counts["豁免"] += 1
        else:
            row["status"] = "unaccounted"
            row["problem"] = "没有 K 条目、也没有带期限的豁免"
            counts["无账红"] += 1
        rows.append(row)

    bad = counts["无账红"] + counts["账目悬空"] + counts["豁免过期"] + counts["未跑无期限"]
    return {"rows": rows, "counts": dict(counts), "fail": bad, "known_k": sorted(known_k)}


def _print_report(result: Dict[str, Any]) -> None:
    for row in result["rows"]:
        gid = row["id"]
        if row["status"] == "green":
            print(f"GUARD {gid}: 绿 rc=0")
        elif row["status"] == "accounted-k":
            print(f"GUARD {gid}: 红 rc={row.get('rc')} -> {row['account']}")
        elif row["status"] == "accounted-exemption":
            print(f"GUARD {gid}: 红 rc={row.get('rc')} -> {row['account']}（owner={row['owner']}）")
        elif row["status"] == "unaccounted":
            print(f"无账红 {gid}: rc={row.get('rc')} —— {row['problem']}；输出尾行：{row.get('detail', '')}")
        elif row["status"] == "dangling-k":
            print(f"账目悬空 {gid}: rc={row.get('rc')} —— {row['problem']}")
        elif row["status"] == "expired-exemption":
            print(f"豁免失效 {gid}: rc={row.get('rc')} —— {row['problem']}")
        elif row["status"] == "manual":
            if row.get("problem"):
                print(f"未跑无期限 {gid}: {row['problem']}")
            else:
                print(f"未跑 {gid}: {row['detail']}（复核 {row['review_by']}）")
        elif row["status"] == "skipped-slow":
            print(f"跳过 {gid}: {row['detail']}")

    counts = result["counts"]
    print(
        "汇总：跑绿 {绿} / 红 {红}（已入账 {已入账} / 豁免 {豁免} / 无账 {无账红}）"
        " / 未跑 {未跑}（无期限 {未跑无期限}） / 跳过 {跳过}".format(
            **Counter({key: counts.get(key, 0) for key in
                       ("绿", "红", "已入账", "豁免", "无账红", "未跑", "未跑无期限", "跳过")})
        )
    )
    if result["fail"]:
        print(f"结论：FAIL —— 无账红/悬空/过期合计 {result['fail']} 条")
    else:
        print("结论：PASS —— 所有非 0 守卫都有 K 条目或带期限的豁免")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(ROOT), help="仓库根（测试用）")
    parser.add_argument("--registry", default=None, help=f"默认 <root>/{DEFAULT_REGISTRY}")
    parser.add_argument("--only", default=None, help="逗号分隔的守卫 id")
    parser.add_argument("--fast", action="store_true", help="跳过 slow 守卫")
    parser.add_argument("--timeout", type=int, default=300, help="单条守卫超时秒数")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    registry_path = Path(args.registry) if args.registry else root / DEFAULT_REGISTRY
    if not registry_path.is_absolute():
        registry_path = root / registry_path
    if not registry_path.exists():
        print(f"配置错误：找不到守卫清单 {registry_path}", file=sys.stderr)
        return 2
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"配置错误：{registry_path} 不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    only = {part.strip() for part in args.only.split(",") if part.strip()} if args.only else None
    result = evaluate(root, registry, only=only, fast=args.fast, timeout=args.timeout)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_report(result)
    return 1 if result["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
