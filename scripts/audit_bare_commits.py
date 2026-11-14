#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""§11.3 裸 git 提交四条前提的**只读机检**入口（不是 pre-commit）。

起因：``docs/evidence-standard.md`` §11 把「什么条件下可以不走
``safe_commit``」写成了四条前提，但裸提交没有工具位置——文档里的「允许」
很容易变成「没人查」。pre-commit 也补不了这一刀：裸提交跑的就是 pre-commit，
钩子里没有「这笔是裸提交」的标记。所以做成事后审计：给一段区间，逐项报。

覆盖范围（别把能力说过头）
--------------------------

**本工具不判断某笔是不是裸提交。** 没有可机读的标记区分裸提交与
``safe_commit``（私有 index、声明快照都不落库），所以「裸提交」这件事本身在
本工具覆盖范围之外；能机检的只有下列三项，其余逐项打印
「本项机检不覆盖，需人工复核」，不许把「查过了」当成「全过了」。

机检三项
--------

① **文件集是否被本线活跃 claim 全覆盖**：按 ``claims.json`` 的时间窗
   （``acquired_at``/``released_at``，未释放用 ``heartbeat_at + ttl``）逐路径判断，
   **不看 §6.1 行**（行是台账投影，§9 已说明它当不了第二份凭据）。消息里写了
   ``claim-line`` 时，「本线」就是那个线名；写了他线的 claim 也算违规。
② **消息末尾是否手写 ``claim-line``，且线名与台账一致**：分桶复用
   ``scripts/audit_claim_lines.py`` 的 ``classify``，不另写一套判等；规则生效前
   的「有 claim 无行」是正常空白，不计缺口（与 ``audit_claim_lines`` 同口径）。
③ **只许追加的文件是否被删行**：对协调文档用
   ``check_coordination_append_only.removed_rows``（§6.1 表格行，剥离行末释放
   标记后比较）。删行/改写行/整档删除都是违规，**不管有没有 claim**；表格外的
   正文不在本项范围（与 pre-commit 的 ④ 同口径，不另立更严/更松的判据）。

明确不覆盖（人工复核）
----------------------

* §11.3-① 后半：他线在途 hunk 是否被带入（要 ``--declare`` 快照，历史提交没留）；
* §11.3-②：本笔要用的 §6.1 行是否已在 HEAD（哪几行「属于本笔」无法机读）；
* §11.3-③ 后半：``--allow-foreign-changes`` / 有意删除非只增文件这类留痕例外。

用法::

    python scripts/audit_bare_commits.py                  # 规则生效以来
    python scripts/audit_bare_commits.py --range 11588844..HEAD
    python scripts/audit_bare_commits.py --since "2026-11-14 11:13" --until "2026-11-14 12:00"
    python scripts/audit_bare_commits.py --json --quiet   # 机器可读，只给计数

退出码恒 0：只报告、不拦截。发现违规也返回 0——它的用途是「让人看见」，
不是「把提交挡在门外」；拦提交是 pre-commit 的事。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(os.environ.get("MBDSDR_REPO_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))

import audit_claim_lines as acl  # noqa: E402  （范围解析/分桶/台账区间唯一实现）
import check_coordination_append_only as cao  # noqa: E402  （只增判据唯一实现）
from claim_window import line_name, parse_claim_lines  # noqa: E402  （owner→线名/行解析唯一口径）

STATUS_CHECKED = "checked"
STATUS_NOT_CHECKED = "not_checked"

#: 只许追加的文件。默认与 pre-commit 的 ④ 同一份文档（``MBDSDR_COORD_DOC`` 可覆盖）。
APPEND_ONLY_DOC = cao.DEFAULT_DOC


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _parent_of(sha: str) -> Optional[str]:
    proc = _git("rev-list", "--parents", "-n", "1", sha)
    if proc.returncode != 0:
        return None
    parts = proc.stdout.split()
    return parts[1] if len(parts) > 1 else None


def _file_at(rev: str, path: str) -> Optional[str]:
    proc = _git("show", f"{rev}:{path}")
    if proc.returncode != 0:
        return None
    return proc.stdout


def _display_paths() -> Dict[str, str]:
    """比较键 → 仓库里的原始拼写（git 路径区分大小写，报告里别把 README 写成 readme）。"""
    proc = _git("ls-files")
    if proc.returncode != 0:
        return {}
    return {acl.norm_path(p): p for p in proc.stdout.splitlines() if p.strip()}


def _trailer_names(message: str) -> List[str]:
    """提交信息里的 ``claim-line`` → 线名（去重保序，口径同审计脚本）。"""
    out: List[str] = []
    for raw in parse_claim_lines(message):
        name = line_name(raw)
        if name and name not in out:
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# ① 文件集 ← 活跃 claim（按台账时间窗）
# ---------------------------------------------------------------------------


def check_claim_coverage(commits: List[Dict[str, Any]],
                         intervals: Dict[str, List[Dict[str, Any]]],
                         exempt: set[str],
                         display: Dict[str, str]) -> Dict[str, Any]:
    violations: List[Dict[str, Any]] = []
    checked = 0
    exempted = 0
    for c in commits:
        if not c["paths"]:
            continue
        checked += 1
        trailer = _trailer_names(c["msg"])
        bad: List[Dict[str, str]] = []
        for path in c["paths"]:
            if path in exempt:
                exempted += 1
                continue
            covering = [it for it in intervals.get(path, ())
                        if it["start"] <= c["ct"] <= it["end"]]
            if not covering:
                bad.append({"path": display.get(path, path),
                            "reason": "提交时刻没有 claim 覆盖"})
                continue
            if trailer:
                own_lines = {line_name(it["owner"]) for it in covering}
                if not (own_lines & set(trailer)):
                    owners = "、".join(sorted({it["owner"] for it in covering}))
                    bad.append({
                        "path": display.get(path, path),
                        "reason": f"覆盖该路径的是他线 claim（{owners}），"
                                  f"消息写的线名是「{'、'.join(trailer)}」",
                    })
        if bad:
            violations.append({"sha": c["sha"], "at": _iso(c["ct"]), "paths": bad})
    return {"status": STATUS_CHECKED, "checked": checked, "exempted": exempted,
            "violations": violations, "violating": len(violations)}


# ---------------------------------------------------------------------------
# ③ 只许追加的文件 ← 删行
# ---------------------------------------------------------------------------


def check_append_only(commits: List[Dict[str, Any]], doc: str) -> Dict[str, Any]:
    doc_key = acl.norm_path(doc)
    violations: List[Dict[str, Any]] = []
    checked = 0
    for c in commits:
        if doc_key not in c["paths"]:
            continue
        checked += 1
        target = _file_at(c["sha"], doc)
        if target is None:
            violations.append({"sha": c["sha"], "at": _iso(c["ct"]), "path": doc,
                               "reason": "只许追加的文件被整档删除", "rows": []})
            continue
        parent = _parent_of(c["sha"])
        if parent is None:
            continue  # 本笔新增该文件：没有基线可删
        base = _file_at(parent, doc)
        if base is None:
            continue  # 基线里没有该文件
        try:
            missing = cao.removed_rows(base, target)
        except ValueError as exc:
            violations.append({"sha": c["sha"], "at": _iso(c["ct"]), "path": doc,
                               "reason": f"§6.1 小节无法解析（可能连标题一起被删）：{exc}",
                               "rows": []})
            continue
        if missing:
            violations.append({"sha": c["sha"], "at": _iso(c["ct"]), "path": doc,
                               "reason": "删除了只许追加的表格行", "rows": missing[:3],
                               "removed": len(missing)})
    return {"status": STATUS_CHECKED, "checked": checked,
            "violations": violations, "violating": len(violations)}


# ---------------------------------------------------------------------------
# ④ 消息末尾的 claim-line（按规则时点分段，与 audit_claim_lines 同口径）
# ---------------------------------------------------------------------------


def check_claim_line(commits: List[Dict[str, Any]],
                     intervals: Dict[str, List[Dict[str, Any]]],
                     rule_at: float) -> Dict[str, Any]:
    post: List[Dict[str, Any]] = []
    pre_mismatch: List[Dict[str, Any]] = []
    pre_no_line = 0
    pre_checked = 0
    post_checked = 0
    for c in commits:
        row = acl.classify(c, intervals)
        if c["ct"] < rule_at:
            pre_checked += 1
            if row["bucket"] == acl.BUCKET_MISMATCH:
                pre_mismatch.append(_line_violation(row, "mismatch"))
            elif row["bucket"] == acl.BUCKET_CLAIM_NO_LINE:
                pre_no_line += 1
            continue
        post_checked += 1
        if row["bucket"] == acl.BUCKET_CLAIM_NO_LINE:
            post.append(_line_violation(row, "missing_trailer"))
        elif row["bucket"] == acl.BUCKET_MISMATCH:
            post.append(_line_violation(row, "mismatch"))
    return {
        "status": STATUS_CHECKED,
        "post": {"checked": post_checked, "violations": post,
                 "violating": len(post)},
        "pre": {"checked": pre_checked, "claim_without_trailer": pre_no_line,
                "mismatch": pre_mismatch},
    }


def _line_violation(row: Dict[str, Any], kind: str) -> Dict[str, Any]:
    return {"sha": row["sha"], "at": _iso(row["ct"]), "kind": kind,
            "written": row["written"], "expected": row["expected"],
            "owners": row["owners"], "reason": row["reason"]}


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def _print_item_cover(result: Dict[str, Any], doc: str, quiet: bool) -> None:
    print()
    print("【§11.3-① 文件集被本线活跃 claim 覆盖（机检，按台账时间窗，不看 §6.1 行）】")
    print(f"  豁免：{doc}（登记面自身，走 §6.1 窄锁写行，不占整档 claim；"
          "与 pre-commit 的 self_key 同一例外）")
    if result["status"] != STATUS_CHECKED:
        print(f"  未审计：{result.get('reason', '台账不可读')}")
        return
    print(f"  机检结论：已审计 {result['checked']} 笔；违规 {result['violating']} 笔；"
          f"豁免路径出现 {result['exempted']} 次")
    if quiet:
        return
    for v in result["violations"]:
        print(f"    {v['sha'][:12]}  {v['at']}")
        for item in v["paths"]:
            print(f"      - {item['path']}：{item['reason']}")


def _print_item_hunks() -> None:
    print()
    print("【§11.3-①后半 他线在途 hunk 是否被带入】")
    print("  本项机检不覆盖，需人工复核（要 --declare 快照，历史提交没留）。")


def _print_item_coord_row() -> None:
    print()
    print("【§11.3-② 本笔要用的 §6.1 行是否已在 HEAD】")
    print("  本项机检不覆盖，需人工复核（哪几行「属于本笔」无法机读判定）。")


def _print_item_append(result: Dict[str, Any], doc: str, quiet: bool) -> None:
    print()
    print("【§11.3-③ 只许追加的文件是否被删行（机检，不管有没有 claim）】")
    print(f"  检查范围：{doc}（§6.1 表格行；剥离线末释放标记后比较）")
    print("  留痕例外（--allow-foreign-changes / 有意删除非只增文件）："
          "本项机检不覆盖，需人工复核。")
    if result["status"] != STATUS_CHECKED:
        print(f"  未审计：{result.get('reason', '台账不可读')}")
        return
    print(f"  机检结论：已审计 {result['checked']} 笔；违规 {result['violating']} 笔")
    if quiet:
        return
    for v in result["violations"]:
        print(f"    {v['sha'][:12]}  {v['at']}  {v['path']}：{v['reason']}")
        for row in v.get("rows") or []:
            shown = row if len(row) <= 100 else row[:97] + "..."
            print(f"      - {shown}")


def _print_item_line(result: Dict[str, Any], rule_at: float, quiet: bool) -> None:
    print()
    print("【§11.3-④ 消息末尾手写 claim-line，且线名与台账一致（机检）】")
    if result["status"] != STATUS_CHECKED:
        print(f"  未审计：{result.get('reason', '台账不可读')}")
        return
    post = result["post"]
    pre = result["pre"]
    print(f"  规则时点之后：已审计 {post['checked']} 笔；"
          f"缺行/不符 {post['violating']} 笔")
    print(f"  规则时点之前：{pre['checked']} 笔（无行 {pre['claim_without_trailer']} 笔"
          "属正常空白，不计缺口）")
    if quiet:
        return
    for v in post["violations"]:
        if v["kind"] == "missing_trailer":
            print(f"    {v['sha'][:12]}  {v['at']}：有 claim"
                  f"（{'、'.join(v['owners'])}）但消息里没有 claim-line")
        else:
            print(f"    {v['sha'][:12]}  {v['at']}：写了"
                  f"「{'、'.join(v['written'])}」，台账期望"
                  f"「{'、'.join(v['expected']) or '<无覆盖 claim>'}」")
    for v in pre["mismatch"]:
        print(f"    {v['sha'][:12]}  {v['at']}（规则前）：写了"
              f"「{'、'.join(v['written'])}」，台账期望"
              f"「{'、'.join(v['expected']) or '<无覆盖 claim>'}」→ 仍算不符")


def _pick_revs(args: argparse.Namespace) -> Tuple[List[str], str]:
    if args.range:
        return [args.range], args.range
    if args.since or args.until:
        revs = ["HEAD"]
        if args.since:
            revs.append(f"--since={args.since}")
        if args.until:
            revs.append(f"--until={args.until}")
        return revs, " ".join(revs)
    probe = _git("cat-file", "-e", f"{acl.RULE_EVIDENCE_SHA}^{{commit}}")
    if probe.returncode == 0:
        rng = f"{acl.RULE_EVIDENCE_SHA}^..HEAD"
        return [rng], rng
    return ["HEAD"], "HEAD（取不到规则生效提交，退回全历史）"


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§11.3 裸提交前提的只读机检（退出码恒 0）")
    ap.add_argument("--range", default="", help="git 范围（如 A..B）；默认规则生效以来")
    ap.add_argument("--since", default="", help="git log --since（与 --range 二选一）")
    ap.add_argument("--until", default="", help="git log --until")
    ap.add_argument("--registry", default="",
                    help="台账路径（默认 MBDSDR_LOCKS_DIR/claims.json）")
    ap.add_argument("--rule-since", default="",
                    help="显式指定 claim-line 规则生效时点（仅用于对照/测试）")
    ap.add_argument("--doc", default=APPEND_ONLY_DOC, help="只许追加的协调文档")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--quiet", action="store_true", help="只打印逐项计数")
    args = ap.parse_args(argv)

    import file_claim as fc  # noqa: E402  （与 claim 工具同一处台账路径）
    registry = Path(args.registry) if args.registry else fc.REGISTRY_PATH
    revs, range_label = _pick_revs(args)
    rule_at, rule_source = acl.resolve_rule(args.rule_since)

    try:
        commits = acl.load_commits(revs)
    except SystemExit as exc:
        print(f"git log 失败，未能审计：{exc}", file=sys.stderr)
        return 0

    commits.sort(key=lambda c: c["ct"], reverse=True)
    display = _display_paths()
    doc_key = acl.norm_path(args.doc)
    ledger_ok = registry.exists()
    intervals = acl.load_intervals(registry) if ledger_ok else {}
    ledger_gap = None if ledger_ok else f"台账不存在：{registry}"

    cover = (check_claim_coverage(commits, intervals, {doc_key}, display)
             if ledger_ok else {"status": STATUS_NOT_CHECKED, "reason": ledger_gap})
    line = (check_claim_line(commits, intervals, rule_at) if ledger_ok
            else {"status": STATUS_NOT_CHECKED, "reason": ledger_gap})
    append = check_append_only(commits, args.doc)

    if args.json:
        print(json.dumps({
            "range": range_label,
            "registry": str(registry),
            "commits": len(commits),
            "rule_at": _iso(rule_at),
            "rule_source": rule_source,
            "checks": {"claim_coverage": cover, "claim_line": line,
                       "append_only": append},
            "not_covered": [
                "§11.3-①后半 他线在途 hunk 是否被带入",
                "§11.3-② 本笔要用的 §6.1 行是否已在 HEAD",
                "§11.3-③ 留痕例外（--allow-foreign-changes / 有意删除）",
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    print("§11 裸提交审计：只报告、不拦截（退出码恒 0）")
    print(f"审计范围：{range_label}；提交 {len(commits)} 笔；台账：{registry}")
    print(f"规则时点：{_iso(rule_at)}（{rule_source}）")
    print("覆盖范围：本工具不判断某笔是不是裸提交——没有可机读标记区分裸提交与"
          " safe_commit，")
    print("          所以「裸提交」本身不在覆盖范围内。能机检的只有 ①/③/④ 三项，")
    print("          其余逐条标「本项机检不覆盖，需人工复核」。")

    _print_item_cover(cover, args.doc, args.quiet)
    _print_item_hunks()
    _print_item_coord_row()
    _print_item_append(append, args.doc, args.quiet)
    _print_item_line(line, rule_at, args.quiet)

    print()
    print("小结：只报告，未拦截任何提交。第 ② 项与两处后半条不在机检范围，"
          "不得据此声称「四条的查过了」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
