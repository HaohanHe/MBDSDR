#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""按提交信息里的 ``claim-line:`` 归线，并按规则生效时点**分段**核对台账。

背景：659 笔提交共用一个 git 身份，``git log`` 的 author/committer 区分不了
「哪条线」。2026-11-14 起，``safe_commit.py`` 会把提交者**此刻真实持有**的
claim 线名追加到说明末尾（每线一行）：

    claim-line: 收尸线

本脚本只做**事后核对**，不改历史、不判断归属。

为什么必须分段（2026-11-14）
----------------------------

``claim-line`` 是今天才上线的规则，规则生效之前的历史提交不可能有这一行。
如果全史共用一个分母，「规则前无行」会被算成缺口；反过来把两段相加，也会
让「旧区段恒为 0」看起来像清白——那不是清白，是空白。

因此输出固定分两段，**每段自己的起止、自己的分母、自己的缺口率**：

* **生效前**：有 claim 却无行属正常，**不计缺口**；只核对「写了行但与台账不符」。
* **生效后**：有 claim 却无行是**缺口**，必须单独计数。

两段**不相加**，输出里不出现合并总计。

生效时点的判据来源
------------------

默认取 ``RULE_EVIDENCE_SHA`` 这笔提交的 committer 时间——它是 ``safe_commit``
首次自动生成 ``claim-line`` 的提交，即规则上线的证据。当前仓库取不到该提交
（比如浅克隆）时退回常量 ``RULE_EVIDENCE_AT``，并在输出里明说。也可用
``--rule-since`` 显式覆盖；无论哪种，输出都会打印「分段依据」一行。

分桶：

* 相符：写了行，且行名在台账期望里；
* **有 claim 但提交信息没这行**：台账说当时有人占着，说明却没写（仅生效后算缺口）；
* **这行与台账不符**：写了行，但行名不在期望里（含「台账里没有覆盖本笔的
  claim」这种无凭据签名）；
* 无 claim 也无行：无凭据，但不是缺口。

``claim-line:`` 是**复述**，不是授权：它只说明「提交者当时持有哪个 claim」，
不证明代码是谁写的。工具不补写、不纠正——写错就是写错，审计只负责报出来。

台账区间口径：优先用 ``history[].acquired_at`` / ``released_at``；未释放的
条目用 ``last_heartbeat_at + current.ttl_min``（缺 ttl 时取 180 分钟，与默认
claim TTL 一致）。没有台账记录的提交按「无 claim」处理。

用法::

    python scripts/audit_claim_lines.py            # HEAD 全历史，按规则时点分段
    python scripts/audit_claim_lines.py --all      # 所有 ref 可达提交
    python scripts/audit_claim_lines.py --quiet    # 只打印两段统计
    python scripts/audit_claim_lines.py --rule-since "2026-11-14 11:13:51"
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
from typing import Any, Dict, List, Tuple

ROOT = Path(os.environ.get("MBDSDR_REPO_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
from claim_window import line_name, parse_claim_lines  # noqa: E402  （解析口径唯一实现）

#: 未释放的 history 条目缺 ttl_min 时的兜底（与 claim 默认 TTL 一致）。
DEFAULT_TTL_MIN = 180.0

#: 规则生效证据：7598f21d 是 safe_commit 首次自动写 claim-line 的提交。
RULE_EVIDENCE_SHA = "7598f21dc12c519111b9c4e819a95ebf2bcf6400"
#: 该提交的 committer 时间（2026-11-14 11:13:51 +0800）。
#: sha 在当前仓库可取时以提交时间为准；取不到时用这个常量并明确提示。
RULE_EVIDENCE_AT = 1794626031.0

BUCKET_MATCHED = "matched"
BUCKET_CLAIM_NO_LINE = "claim_without_trailer"
BUCKET_MISMATCH = "mismatch"
BUCKET_NO_CLAIM_NO_LINE = "no_claim_no_trailer"

LABELS = {
    BUCKET_MATCHED: "行与台账相符",
    BUCKET_CLAIM_NO_LINE: "有 claim 但提交信息没这行",
    BUCKET_MISMATCH: "这行与台账不符",
    BUCKET_NO_CLAIM_NO_LINE: "无 claim 也无行",
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
    written_raw = parse_claim_lines(commit["msg"])
    # 规则约束的是「线」，不是「会话」：有人写成 `线名/会话短号` 时按斜杠前归一，
    # 否则会把「同一条线、写全了 owner」误报成「与台账不符」。
    written: List[str] = []
    for item in written_raw:
        name = line_name(item)
        if name and name not in written:
            written.append(name)
    row = {
        "sha": commit["sha"], "ct": commit["ct"], "paths": commit["paths"],
        "owners": owners, "expected": expected, "written": written,
        "written_raw": written_raw,
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


def _parse_when(raw: str) -> float:
    text = str(raw).strip()
    try:
        return float(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(text, fmt))
        except ValueError:
            continue
    raise SystemExit(f"--rule-since 无法解析：{raw!r}（可用 epoch 秒或 YYYY-MM-DD HH:MM:SS）")


def resolve_rule(raw: str) -> Tuple[float, str]:
    """返回（生效时点, 判据来源）。来源会原样打印，避免「时点怎么来的」说不清。"""
    if raw:
        return _parse_when(raw), f"命令行 --rule-since {raw!r}（显式覆盖，仅用于对照/测试）"
    probe = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{RULE_EVIDENCE_SHA}^{{commit}}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if probe.returncode == 0:
        stamp = _git("show", "-s", "--format=%ct", RULE_EVIDENCE_SHA).strip()
        try:
            return float(stamp), (
                f"提交 {RULE_EVIDENCE_SHA[:12]} 的 committer 时间"
                "（该笔首次由 safe_commit 自动写 claim-line，即规则上线点）"
            )
        except ValueError:
            pass
    return RULE_EVIDENCE_AT, (
        f"常量 RULE_EVIDENCE_AT={int(RULE_EVIDENCE_AT)}"
        f"（对应提交 {RULE_EVIDENCE_SHA[:12]}；当前仓库取不到该提交，只能退回常量）"
    )


def _segment(name: str, rows: List[Dict[str, Any]], lo: str, hi: str,
             is_post: bool) -> None:
    """打印一段的统计。分母只在本段内算，不与另一段相加。"""
    counts = Counter(r["bucket"] for r in rows)
    with_claim = sum(1 for r in rows if r["expected"])
    print()
    print(f"【{name}】{lo} .. {hi}（{len(rows)} 笔）")
    if not rows:
        print("  本段无提交。")
        return
    print(f"  本段分母：本段有 claim 的提交 {with_claim} 笔"
          "（只在本段内计算，不与另一段相加）")
    print(f"  {LABELS[BUCKET_MATCHED]:<22}{counts.get(BUCKET_MATCHED, 0)} 笔")
    print(f"  {LABELS[BUCKET_MISMATCH]:<22}{counts.get(BUCKET_MISMATCH, 0)} 笔")
    no_line = counts.get(BUCKET_CLAIM_NO_LINE, 0)
    if is_post:
        print(f"  {LABELS[BUCKET_CLAIM_NO_LINE]:<22}{no_line} 笔  ← 缺口")
        print(f"  缺口率：{no_line}/{with_claim}" if with_claim else "  缺口率：0/0")
    print(f"  {LABELS[BUCKET_NO_CLAIM_NO_LINE]:<22}"
          f"{counts.get(BUCKET_NO_CLAIM_NO_LINE, 0)} 笔")
    if not is_post:
        print(f"  本段另有 {no_line} 笔有 claim 但无行：规则生效前属正常，未计入缺口。")
        print("  缺口率：不适用（规则未生效）")
    suffix = sum(1 for r in rows if r.get("written_raw") != r["written"])
    if suffix:
        print(f"  另有 {suffix} 笔写的值带会话后缀，已按线名归一后再比对。")

    per_line = Counter()
    for r in rows:
        if r["written"]:
            for line in r["written"]:
                per_line[line] += 1
        else:
            per_line["<无 claim-line>"] += 1
    print("  按 claim-line 归线（本段）：")
    for line, n in per_line.most_common():
        print(f"    {line:<26} {n} 笔")


def _print_problems(title: str, rows: List[Dict[str, Any]]) -> None:
    print()
    print(f"逐条（{title}，新→旧）：")
    for r in sorted(rows, key=lambda x: x["ct"], reverse=True):
        print(f"{r['sha'][:12]}  {_iso(r['ct'])}  [{LABELS[r['bucket']]}]")
        if r["written"]:
            raw = r.get("written_raw") or []
            note = ""
            if raw != r["written"]:
                note = f"（按线名归一看：{'、'.join(r['written'])}）"
            print(f"  写：{'、'.join(raw)}{note}；"
                  f"台账期望：{'、'.join(r['expected']) or '<无覆盖 claim>'}")
        else:
            print("  写：<无>；台账期望："
                  f"{'、'.join(r['expected']) or '<无覆盖 claim>'}")
        print(f"  台账 owner：{'、'.join(r['owners']) or '<无>'}；"
              f"路径 {len(r['paths'])} 个：{', '.join(r['paths'][:5])}"
              + (" …" if len(r["paths"]) > 5 else ""))
        print(f"  原因：{r['reason']}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按 claim-line 归线并按规则时点分段核对台账")
    ap.add_argument("--rev", default="HEAD", help="git log 范围（默认 HEAD）")
    ap.add_argument("--all", action="store_true", help="改为所有 ref 可达提交")
    ap.add_argument("--registry", default="", help="台账路径（默认 MBDSDR_LOCKS_DIR/claims.json）")
    ap.add_argument("--rule-since", default="",
                    help="显式指定规则生效时点（epoch 秒或 YYYY-MM-DD HH:MM:SS）")
    ap.add_argument("--quiet", action="store_true", help="只打印两段统计")
    args = ap.parse_args(argv)

    import file_claim as fc  # noqa: E402  （与 claim 工具同一处台账路径）
    registry = Path(args.registry) if args.registry else fc.REGISTRY_PATH
    rev = "--all" if args.all else args.rev
    commits = load_commits(rev)
    intervals = load_intervals(registry)
    rows = [classify(c, intervals) for c in commits]
    rule_at, rule_source = resolve_rule(args.rule_since)

    pre = [r for r in rows if r["ct"] < rule_at]
    post = [r for r in rows if r["ct"] >= rule_at]
    first_ct = min((r["ct"] for r in rows), default=rule_at)
    last_ct = max((r["ct"] for r in rows), default=rule_at)

    print(f"审计范围：{rev}，{len(rows)} 笔提交；台账：{registry}")
    print(f"分段依据：{rule_source}")
    print(f"规则时点：{_iso(rule_at)}（前段不含该时点，后段含该时点）")
    print("口径：两段各自给分母与缺口率，**不相加、不提供合并总计**。")

    _segment("生效前", pre, _iso(first_ct) if pre else "（无提交）", _iso(rule_at), False)
    _segment("生效后", post, _iso(rule_at), _iso(last_ct) if post else "（无提交）", True)

    if args.quiet:
        return 0

    pre_problems = [r for r in pre if r["bucket"] == BUCKET_MISMATCH]
    post_problems = [r for r in post
                     if r["bucket"] in (BUCKET_CLAIM_NO_LINE, BUCKET_MISMATCH)]
    if pre_problems:
        _print_problems("生效前，只列写了行但与台账不符", pre_problems)
    else:
        print()
        print("逐条（生效前）：没有写了行但与台账不符的提交。")
    if post_problems:
        _print_problems("生效后，缺口或与台账不符", post_problems)
    else:
        print()
        print("逐条（生效后）：没有缺口或与台账不符的提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
