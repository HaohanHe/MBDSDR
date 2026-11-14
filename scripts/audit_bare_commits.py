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

另有一项**提示**（不是判据）：**提交时刻他线是否正持有同一文件**。按台账时间
窗查每笔路径，命中就点名 sha/路径/持有者/时间差，措辞是「疑似带入他线在途
内容」。它与 ① 分开列、分开算分母：① 查「本笔有没有被自己的 claim 覆盖」，
它查「本笔有没有踩在别人脚上」，两者互不替代。命中**不等于**被带入——也可能
双方都在改同一文件、各自只提交自己那段，所以只用来触发人工复核，不计违规、
不进 ① 的比率。判「本线」只认 ``claim-line``；没有这一行的提交不猜，计入
「无法判断」并交给 ④ 报缺行。

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
    python scripts/audit_bare_commits.py --trend-report   # 按判据版本看比率

趋势（不是每次都给一个新的快照）
--------------------------------

每次审计往 ``<台账目录>/bare_commit_trend.jsonl`` 追加**一行**（逐行 JSON，与
``coord_check_trend.jsonl`` 同一放置与格式）：区间起止、提交笔数、①/③/④ 的
违规数与各自分母、涉及线名去重计数、判据版本。

**键 = 区间（首末提交 sha）+ 判据版本**：同一区间重复跑只留一行；判据版本变了
（新增判据或改了行为）另起一行，两段不混。

**限制**：违规笔数下降不等于实践变好——可能只是裸提交变少、或提交总量变少。
所以每行都存分母，比率只按本项自己的分母算，不给「总违规率」；跨判据版本
不可直接比较。

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

#: 判据版本：**改动任一判据的行为都要升 ``CRITERIA_BASE_VERSION``**。
#: 不升版本的话，趋势文件里「同一版本下违规变少」会被误读成实践变好——实际可能
#: 只是判据变松。``CRITERIA_ITEMS`` 进了版本串，新增/删除判据会自动改串。
#: v2：新增「提交时刻他线持有同一文件」提示项（不影响 ① 的判定）。
CRITERIA_BASE_VERSION = "v2"
CRITERIA_ITEMS = ("claim-coverage", "append-only", "claim-line", "foreign-hold")

#: 趋势文件（与 ``coord_check_trend.jsonl`` 同一位置、同一逐行 JSON 格式）。
TREND_FILE = "bare_commit_trend.jsonl"


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
                bad.append({"path": display.get(path, path), "owners": [],
                            "reason": "提交时刻没有 claim 覆盖"})
                continue
            if trailer:
                own_lines = {line_name(it["owner"]) for it in covering}
                if not (own_lines & set(trailer)):
                    owners = sorted({it["owner"] for it in covering})
                    bad.append({
                        "path": display.get(path, path),
                        "owners": owners,
                        "reason": f"覆盖该路径的是他线 claim（{'、'.join(owners)}），"
                                  f"消息写的线名是「{'、'.join(trailer)}」",
                    })
        if bad:
            violations.append({"sha": c["sha"], "at": _iso(c["ct"]), "paths": bad})
    return {"status": STATUS_CHECKED, "checked": checked, "exempted": exempted,
            "violations": violations, "violating": len(violations)}


# ---------------------------------------------------------------------------
# ①后半：提交时刻他线正持有同一文件（**提示**，不是违规）
# ---------------------------------------------------------------------------


def check_foreign_hold(commits: List[Dict[str, Any]],
                       intervals: Dict[str, List[Dict[str, Any]]],
                       display: Dict[str, str]) -> Dict[str, Any]:
    """本笔路径在提交时刻是否正被他线持有。

    与 ① 的分工：① 问「本笔有没有被自己的 claim 覆盖」，本项问「本笔有没有踩在
    别人脚上」。两者互不替代——一笔可以既没有自己的 claim，又正好压在他线的
    在途文件上；也可以各自单独成立。

    判「本线」只认消息里的 ``claim-line``：没有这一行时无法判断归属，**不猜**，
    计入 ``skipped_no_trailer`` 并交给 ④ 去报缺行。

    命中**不是**违规：台账只能证明「那个文件当时有人在改」，不能证明「带进来的是
    他那部分」。这是提示，用来触发人工复核。
    """
    suspected: List[Dict[str, Any]] = []
    checked = 0
    skipped = 0
    for c in commits:
        if not c["paths"]:
            continue
        own = _trailer_names(c["msg"])
        if not own:
            skipped += 1
            continue
        checked += 1
        hits: List[Dict[str, Any]] = []
        seen: set[Tuple[str, str]] = set()
        for path in c["paths"]:
            for item in intervals.get(path, ()):
                if not (item["start"] <= c["ct"] <= item["end"]):
                    continue
                name = line_name(item["owner"])
                if not name or name in own or (path, item["owner"]) in seen:
                    continue
                seen.add((path, item["owner"]))
                hits.append({
                    "path": display.get(path, path),
                    "line": name,
                    "owner": item["owner"],
                    "held_min": round((c["ct"] - item["start"]) / 60.0, 1),
                    "left_min": round((item["end"] - c["ct"]) / 60.0, 1),
                })
        if hits:
            suspected.append({"sha": c["sha"], "at": _iso(c["ct"]),
                              "own": own, "hits": hits})
    return {"status": STATUS_CHECKED, "checked": checked,
            "skipped_no_trailer": skipped,
            "suspected": suspected, "count": len(suspected)}


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
        lines = _trailer_names(c["msg"])
        target = _file_at(c["sha"], doc)
        if target is None:
            violations.append({"sha": c["sha"], "at": _iso(c["ct"]), "path": doc,
                               "lines": lines,
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
                               "lines": lines,
                               "reason": f"§6.1 小节无法解析（可能连标题一起被删）：{exc}",
                               "rows": []})
            continue
        if missing:
            violations.append({"sha": c["sha"], "at": _iso(c["ct"]), "path": doc,
                               "lines": lines,
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
# 趋势记录：键 = 区间 + 判据版本；同一键重复跑不重复计
# ---------------------------------------------------------------------------


def criteria_version(override: str = "") -> str:
    return override or f"{CRITERIA_BASE_VERSION}:{'+'.join(CRITERIA_ITEMS)}"


def read_trend(path: Path) -> List[Dict[str, Any]]:
    """逐行 JSON；坏行跳过，不让一行脏数据把整份趋势读废。"""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def trend_row(commits: List[Dict[str, Any]], range_label: str,
              cover: Dict[str, Any], line: Dict[str, Any],
              append: Dict[str, Any], foreign: Dict[str, Any],
              criteria: str) -> Dict[str, Any]:
    """一次审计 → 一行趋势。比例只带本项自己的分母，不给「总违规率」。"""
    oldest = commits[-1] if commits else None
    newest = commits[0] if commits else None
    range_key = (f"{oldest['sha']}..{newest['sha']}" if oldest and newest
                 else f"empty:{range_label}")

    def checked(result: Dict[str, Any], key: str = "checked") -> int:
        if result.get("status") != STATUS_CHECKED:
            return 0
        return int(result.get(key) or 0)

    def violated(result: Dict[str, Any]) -> Optional[int]:
        """未审计 → None（不是 0：0 会被读成「清白」）。"""
        if result.get("status") != STATUS_CHECKED:
            return None
        return int(result.get("violating") or 0)

    post = line.get("post") if isinstance(line.get("post"), dict) else {}
    denominators = {
        "claim_coverage": checked(cover),
        "append_only": checked(append),
        "claim_line": int(post.get("checked") or 0),
    }
    violations = {
        "claim_coverage": violated(cover),
        "append_only": violated(append),
        "claim_line": (int(post.get("violating") or 0)
                       if line.get("status") == STATUS_CHECKED else None),
    }
    rates = {name: (round(violations[name] / denominators[name], 4)
                    if violations[name] is not None and denominators[name] else None)
             for name in denominators}

    names: set[str] = set()
    for item in cover.get("violations") or []:
        for path_hit in item.get("paths") or []:
            names.update(path_hit.get("owners") or [])
    for item in (line.get("post") or {}).get("violations") or []:
        names.update(item.get("written") or [])
        names.update(item.get("expected") or [])
    for item in append.get("violations") or []:
        names.update(item.get("lines") or [])

    def stamp(commit: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return ({"sha": commit["sha"], "at": _iso(commit["ct"])}
                if commit else {"sha": None, "at": None})

    return {
        "at": time.time(),
        "criteria": criteria,
        "range_key": range_key,
        "range": {"from": stamp(oldest), "to": stamp(newest), "label": range_label},
        "commits": len(commits),
        "violations": violations,
        "denominators": denominators,
        "rates": rates,
        # 提示项单列：分母是自己的「有 claim-line 的提交数」，不混进 ① 的比率。
        "suspected_foreign_hold": {
            "count": foreign.get("count", 0),
            "checked": foreign.get("checked", 0),
            "skipped_no_trailer": foreign.get("skipped_no_trailer", 0),
            "rate": (round(int(foreign.get("count") or 0)
                           / int(foreign.get("checked") or 0), 4)
                     if foreign.get("checked") else None),
        },
        "lines": {"distinct": len(names), "names": sorted(names)},
    }


def append_trend(path: Path, row: Dict[str, Any]) -> Tuple[bool, str]:
    for old in read_trend(path):
        if (old.get("range_key") == row["range_key"]
                and old.get("criteria") == row["criteria"]):
            return False, (f"同一区间 + 同一判据版本已记过"
                           f"（at={old.get('at')}），本笔不重复计")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        return False, f"写入失败（{exc}），本次不留痕"
    return True, "已追加一行"


def _fmt_rate(value: Optional[int], denom: int) -> str:
    return "未审计" if value is None else f"{value}/{denom}"


def print_trend_report(path: Path) -> None:
    rows = read_trend(path)
    print(f"裸提交审计趋势：{path}（{len(rows)} 行）")
    if not rows:
        print("  还没有记录。跑一次审计即追加一行。")
        return
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("criteria") or "<未知>"), []).append(row)
    for version, items in sorted(groups.items()):
        items.sort(key=lambda r: float(r.get("at") or 0))
        print()
        print(f"判据版本 {version}：{len(items)} 个区间")
        total_commits = 0
        total_den = {k: 0 for k in ("claim_coverage", "append_only", "claim_line")}
        total_vio = {k: 0 for k in total_den}
        unchecked: set[str] = set()
        fh_count = 0
        fh_checked = 0
        all_lines: set[str] = set()
        for row in items:
            den = row.get("denominators") or {}
            vio = row.get("violations") or {}
            fh = row.get("suspected_foreign_hold") or {}
            rng = row.get("range") or {}
            frm = (rng.get("from") or {}).get("sha") or "?"
            to = (rng.get("to") or {}).get("sha") or "?"
            print(f"  {_iso(float(row.get('at') or 0))} | {frm[:7]}..{to[:7]}"
                  f" | 提交 {row.get('commits', 0)} 笔"
                  f" | ① {_fmt_rate(vio.get('claim_coverage'), den.get('claim_coverage', 0))}"
                  f" | ③ {_fmt_rate(vio.get('append_only'), den.get('append_only', 0))}"
                  f" | ④ {_fmt_rate(vio.get('claim_line'), den.get('claim_line', 0))}"
                  f" | 他线在途（提示）{fh.get('count', 0)}/{fh.get('checked', 0)}"
                  f" | 涉及线 {(row.get('lines') or {}).get('distinct', 0)} 条")
            total_commits += int(row.get("commits") or 0)
            fh_count += int(fh.get("count") or 0)
            fh_checked += int(fh.get("checked") or 0)
            for key in total_den:
                total_den[key] += int(den.get(key) or 0)
                if vio.get(key) is None:
                    unchecked.add(key)
                else:
                    total_vio[key] += int(vio.get(key) or 0)
            all_lines.update((row.get("lines") or {}).get("names") or [])

        def total(key: str) -> str:
            text = f"{total_vio[key]}/{total_den[key]}"
            return text + ("（含未审计行）" if key in unchecked else "")

        print(f"  合计：提交 {total_commits} 笔；① {total('claim_coverage')}；"
              f"③ {total('append_only')}；④ {total('claim_line')}；"
              f"他线在途（提示，非违规）{fh_count}/{fh_checked}；"
              f"涉及线 {len(all_lines)} 条")
        print("  汇总分母是各行分母之和；区间重叠时同一提交会重复计入，"
              "只看方向，别当总体比例。")
    print()
    print("限制：违规笔数下降不等于实践变好——裸提交变少、提交总量变少都会让分子变小；")
    print("      比率必须带分母（分母本身也会变）；判据版本不同时两段不可直接比较。")
    print("      「他线在途」是提示：命中不等于被带入，也可能双方各提交自己那段，"
          "只能触发复核。")


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


def _print_item_hunks(result: Dict[str, Any], doc: str, quiet: bool) -> None:
    print()
    print("【§11.3-①后半 提交时刻他线正持有同一文件（提示，非违规）】")
    print("  口径：与 ① 分开算。① 问「本笔有没有被自己的 claim 覆盖」，本项问"
          "「本笔有没有踩在别人脚上」。")
    print("  命中不等于被带入：也可能双方都在改同一文件、各自只提交自己那段。"
          "本项只触发人工复核，\n  不改 ① 的结论，也不进违规率。")
    if result["status"] != STATUS_CHECKED:
        print(f"  未审计：{result.get('reason', '台账不可读')}")
        print("  仍不覆盖：他线在途 hunk 是否真被带入本笔——本项机检不覆盖，需人工复核。")
        return
    print(f"  疑似 {result['count']} 笔 / 已审计 {result['checked']} 笔"
          f"（另有 {result['skipped_no_trailer']} 笔无 claim-line，无法判断本线，"
          "未计入，见 §11.3-④）")
    doc_key = acl.norm_path(doc)
    if any(acl.norm_path(h["path"]) == doc_key
           for v in result["suspected"] for h in v["hits"]):
        print(f"  说明：命中 {doc} 的多半是 §6.1 窄锁形态（双方都在往登记表写行、"
              "各自只提交自己那行），")
        print("        正属于「命中不等于被带入」；别据此认定串了内容。")
    if not quiet:
        for v in result["suspected"]:
            print(f"    {v['sha'][:12]}  {v['at']}  本线={'、'.join(v['own'])}")
            for hit in v["hits"]:
                print(f"      - {hit['path']}：{hit['owner']} 已持有 {hit['held_min']} 分钟"
                      f"（其窗口还剩 {hit['left_min']} 分钟）")
    print("  仍不覆盖：他线在途 hunk 是否真被带入本笔（要 --declare 快照）——"
          "本项机检不覆盖，需人工复核。")


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
    ap.add_argument("--trend", default="",
                    help=f"趋势文件（默认 <台账目录>/{TREND_FILE}）")
    ap.add_argument("--trend-report", action="store_true",
                    help="只读趋势文件，按判据版本分组看比率，不跑审计")
    ap.add_argument("--criteria-version", default="",
                    help="显式指定判据版本（仅用于对照/回填；改了判据却没升版会串段）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--quiet", action="store_true", help="只打印逐项计数")
    args = ap.parse_args(argv)

    import file_claim as fc  # noqa: E402  （与 claim 工具同一处台账路径）
    registry = Path(args.registry) if args.registry else fc.REGISTRY_PATH
    trend_path = Path(args.trend) if args.trend else registry.parent / TREND_FILE

    if args.trend_report:
        print_trend_report(trend_path)
        return 0

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
    foreign = (check_foreign_hold(commits, intervals, display) if ledger_ok
               else {"status": STATUS_NOT_CHECKED, "reason": ledger_gap})
    criteria = criteria_version(args.criteria_version)
    trend = trend_row(commits, range_label, cover, line, append, foreign, criteria)
    appended, trend_note = append_trend(trend_path, trend)

    if args.json:
        print(json.dumps({
            "range": range_label,
            "registry": str(registry),
            "commits": len(commits),
            "rule_at": _iso(rule_at),
            "rule_source": rule_source,
            "checks": {"claim_coverage": cover, "claim_line": line,
                       "append_only": append, "foreign_hold": foreign},
            "trend": {"path": str(trend_path), "appended": appended,
                      "note": trend_note, "criteria": criteria,
                      "row": trend},
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
    print("          所以「裸提交」本身不在覆盖范围内。能机检的只有 ①/③/④ 三项判据"
          "与 ①后半的「他线在途」提示，")
    print("          其余逐条标「本项机检不覆盖，需人工复核」。")

    _print_item_cover(cover, args.doc, args.quiet)
    _print_item_hunks(foreign, args.doc, args.quiet)
    _print_item_coord_row()
    _print_item_append(append, args.doc, args.quiet)
    _print_item_line(line, rule_at, args.quiet)

    print()
    print(f"趋势：{trend_path}（{trend_note}；判据版本 {criteria}）")
    print("小结：只报告，未拦截任何提交。第 ② 项与两处后半条不在机检范围，"
          "不得据此声称「四条的查过了」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
