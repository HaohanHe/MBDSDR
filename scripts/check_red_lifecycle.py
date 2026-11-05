#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""红项生命周期机检：把「已修」这类状态当作需举证的断言来查。

规则：
  R1 FAIL  出现「已修 / 已修复 / 已落地 / 已解决」但同一行没有 commit hash。
  R2 FAIL  写了 hash，但 `git cat-file -e <sha>^{commit}` 查不到（hash 打错/不是提交）。
  R3 WARN  状态为已修且 hash 有效，但相邻行没有可复跑命令（`ctest -R ...` / `pytest ...`）。
  R4 FAIL  标了 `not_verified` / 「未复核」，但没写「为什么没验 / 缺什么才能验」。
  R5 FAIL  同一红项（测试名 / 槽名 / 条目编号 / §节号 / `§7.3.1 A` 组键）在 ≥2 处
          （跨文档或同文档不同节）状态不一致。

豁免（显式，不静默）：
  * 默认读 `docs/red-lifecycle-exemptions.md`；只对文件里逐条列明、且机制可验证的路径生效。
  * 目前唯一允许的机制是 `content-hash-ledger`（该文件的 hash 列是行内容 hash，不是提交 hash）。
    声明了该机制但文件里没有 12 位行内容 hash 列 → 豁免无效，照常扫描并报 FAIL。
  * 每次运行打印 `SKIP n（豁免：<文件>，理由见 …）`。

历史快照横幅：
  * 只有 `docs/learn/phase*/` 下的文件允许写「历史快照，不作现状依据」横幅。
  * 带横幅的历史文件，R1/R2 降为 WARN 并计入单独的 HIST-WARN。
  * 现代文档带横幅 → X1 FAIL（横幅只能用于历史目录）。

用法：
  python scripts/check_red_lifecycle.py
  python scripts/check_red_lifecycle.py --json
  python scripts/check_red_lifecycle.py --root <仓库>       # 测试用
  python scripts/check_red_lifecycle.py --exemptions <清单>
  python scripts/check_red_lifecycle.py --exclude <md...>   # 临时排除，仍会打印 SKIP

退出码：0 无 FAIL（WARN 允许）；1 有 FAIL；2 用法/git 错误。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
# 测试可用 --root 指到临时仓库；运行期由 main() 覆盖。
SCAN_ROOT = ROOT

EXEMPTION_FILE = "docs/red-lifecycle-exemptions.md"
BANNER_TEXT = "历史快照，不作现状依据"
HISTORICAL_PATH_RE = re.compile(r"^docs/learn/phase\d+/")
ALLOWED_MECHANISMS = {"content-hash-ledger"}

FIXED_RE = re.compile(r"已修复|已修|已落地|已解决")
# 「未复核」只有落在红项语境里才算状态；「未复核 claim」是协作时序，不是红项未验。
RED_CONTEXT_RE = re.compile(r"槽|红项|测试|用例|状态|ctest|pytest|test_")
OPEN_RE = re.compile(r"待修|未修|在途修")
# 纯数字串（如采样率 3200000）不是提交 hash；要求至少含一个 a-f 字母。
HASH_RE = re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
CMD_RE = re.compile(
    r"`?(?:python\s+-m\s+)?pytest\b|`?ctest\s+-R|`?python\s+scripts/|`?\./scripts/"
)
REASON_RE = re.compile(
    r"缺|阻塞|原因|因为|无法|尚未|待|才能|未具备|不可复现|无环境|没有|不满足"
)
ITEM_RE = re.compile(r"`([^`]*(?:::|test_[A-Za-z0-9_]+)[^`]*)`")
SLOT_KEY_RE = re.compile(r"`([A-Z]\d+)`")
SECTION_KEY_RE = re.compile(r"§\s*(\d+(?:\.\d+)*)\s+([A-Z])(?![0-9A-Za-z])")
GROUP_REF_RE = re.compile(r"(?<![0-9A-Za-z])([A-C])\s*的\s*\d+\s*(?:条|槽|个)")
QUOTE_PAIRS = (("「", "」"), ("“", "”"), ("‘", "’"), ('"', '"'))
STATUS_LABELS = {
    "fixed": "已修",
    "not_verified": "not_verified",
    "open": "open",
}


@dataclass
class Finding:
    rule: str
    level: str
    path: str
    line: int
    message: str
    text: str
    historical: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "rule": self.rule,
            "level": self.level,
            "historical": self.historical,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "text": self.text,
        }


@dataclass
class Hit:
    path: str
    line: int
    status: str
    key: str = field(default="")


def _strip_quoted(text: str) -> str:
    for left, right in QUOTE_PAIRS:
        text = re.sub(left + r"[^" + right + r"]*" + right, "", text)
    return text


def _item_keys(text: str) -> List[str]:
    keys: List[str] = []
    for raw in ITEM_RE.findall(text):
        token = raw.strip()
        key = token.split("::")[-1].strip() if "::" in token else token.strip()
        key = re.sub(r"\(.*$", "", key).strip()
        if key and (key.startswith("test_") or "::" in raw):
            keys.append(key)
    for slot in SLOT_KEY_RE.findall(text):
        keys.append(slot)
    for section, letter in SECTION_KEY_RE.findall(text):
        keys.append(f"§{section} {letter}")
    return keys


def _iter_lines(path: Path) -> Iterable[Tuple[int, str]]:
    """跳过代码块；返回 (行号, 原文)。"""
    in_fence = False
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        yield number, raw


def _commit_exists(sha: str, cache: Dict[str, bool]) -> bool:
    if sha in cache:
        return cache[sha]
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=str(SCAN_ROOT),
        capture_output=True,
        text=True,
    )
    cache[sha] = proc.returncode == 0
    return cache[sha]


def _detect_statuses(text: str, context: str = "") -> List[str]:
    found: List[str] = []
    if FIXED_RE.search(text):
        found.append("fixed")
    if "`not_verified`" in text or (
        "未复核" in text and RED_CONTEXT_RE.search(context or text)
    ):
        found.append("not_verified")
    if OPEN_RE.search(text):
        found.append("open")
    return found


def _banner_line(lines: List[Tuple[int, str]]) -> Optional[int]:
    for number, raw in lines[:20]:
        if BANNER_TEXT in raw:
            return number
    return None


def scan_file(
    path: Path,
    display: str,
    hash_cache: Dict[str, bool],
) -> Tuple[List[Finding], List[Hit]]:
    findings: List[Finding] = []
    hits: List[Hit] = []
    lines = list(_iter_lines(path))
    historical = bool(HISTORICAL_PATH_RE.match(display))
    banner = _banner_line(lines)
    if banner is not None and not historical:
        findings.append(
            Finding(
                "X1", "FAIL", display, banner,
                "「历史快照，不作现状依据」横幅只能用于 docs/learn/phase*/ 历史目录",
                lines[banner - 1][1].strip() if banner - 1 < len(lines) else "",
            )
        )
    in_731 = False
    group: Optional[str] = None

    for index, (number, raw) in enumerate(lines):
        stripped = raw.strip()
        if re.match(r"^####\s+7\.3\.1", stripped):
            in_731, group = True, None
        elif in_731 and re.match(r"^(?:##|###)\s+([A-Z]\d?)\s*\.", stripped):
            # `## A.` / `### A2.` 都是账本里的分组标题，A2 归入 A。
            group = re.match(r"^(?:##|###)\s+([A-Z]\d?)\s*\.", stripped).group(1)[0]  # type: ignore[union-attr]
        elif re.match(r"^#{1,4}\s", stripped):
            in_731, group = False, None

        text = _strip_quoted(raw)
        # 原因/复跑命令允许落在相邻两行内（表格单元格会折行）。
        window = "\n".join(item for _n, item in lines[max(0, index - 2): index + 3])
        window_text = _strip_quoted(window)
        statuses = _detect_statuses(text, window_text)
        if not statuses:
            continue

        keys = _item_keys(text)
        if in_731 and group:
            keys.append(f"§7.3.1 {group}")
        for letter in GROUP_REF_RE.findall(text):
            keys.append(f"§7.3.1 {letter}")
        if in_731:
            # §7.3.1 的说明常折行：状态词与「B 的 8 个槽」可能落在相邻行。
            for letter in GROUP_REF_RE.findall(window_text):
                keys.append(f"§7.3.1 {letter}")
        keys = list(dict.fromkeys(keys))

        for status in statuses:
            hits.append(Hit(display, number, status))
            for key in keys:
                hits.append(Hit(display, number, status, key))

            if status == "fixed":
                hashes = HASH_RE.findall(text)
                if not hashes:
                    findings.append(
                        Finding(
                            "R1", "WARN" if (banner is not None and historical) else "FAIL",
                            display, number,
                            "出现「已修/已修复/已落地/已解决」但同一行没有 commit hash",
                            raw.strip(), historical,
                        )
                    )
                    continue
                bad = [h for h in hashes if not _commit_exists(h, hash_cache)]
                for sha in bad:
                    findings.append(
                        Finding(
                            "R2", "WARN" if (banner is not None and historical) else "FAIL",
                            display, number,
                            f"hash `{sha}` 不是可解析的提交（git cat-file -e {sha}^{{commit}} 失败）",
                            raw.strip(), historical,
                        )
                    )
                if not bad and not CMD_RE.search(window_text):
                    findings.append(
                        Finding(
                            "R3", "WARN", display, number,
                            "已修 + hash 有效，但相邻行内没有可复跑命令（ctest -R / pytest）",
                            raw.strip(), historical,
                        )
                    )
            elif status == "not_verified":
                if not REASON_RE.search(window_text):
                    findings.append(
                        Finding(
                            "R4", "FAIL", display, number,
                            "标了 `not_verified`/未复核，但相邻行内没写为什么没验或缺什么才能验",
                            raw.strip(), historical,
                        )
                    )
    return findings, hits


def _cross_doc_conflicts(hits: List[Hit]) -> List[Finding]:
    """同一红项键在两处出现（可跨文档，也可同文档不同节），且状态不一致 → R5。"""
    by_key: Dict[str, List[Hit]] = defaultdict(list)
    for hit in hits:
        if hit.key and hit.status != "open":
            by_key[hit.key].append(hit)
    findings: List[Finding] = []
    for key, items in sorted(by_key.items()):
        locations = {(item.path, item.line) for item in items}
        statuses = {item.status for item in items}
        if len(locations) >= 2 and len(statuses) >= 2:
            files = {item.path for item in items}
            where = "；".join(
                f"{item.path}:{item.line}={STATUS_LABELS.get(item.status, item.status)}"
                for item in items
            )
            findings.append(
                Finding(
                    "R5", "FAIL", sorted(files)[0], items[0].line,
                    f"同一红项 `{key}` 在多处状态不一致：{where}",
                    "",
                )
            )
    return findings


def _load_exemptions() -> Dict[str, str]:
    path = SCAN_ROOT / EXEMPTION_FILE
    if not path.exists():
        return {}
    result: Dict[str, str] = {}
    for _number, raw in _iter_lines(path):
        match = re.match(r"\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|", raw.strip())
        if match:
            result[os_norm(match.group(1))] = match.group(2)
    return result


def _validate_exemption(path: Path, mechanism: str) -> Tuple[bool, str]:
    if mechanism not in ALLOWED_MECHANISMS:
        return False, f"未知豁免机制 `{mechanism}`"
    text = path.read_text(encoding="utf-8", errors="replace")
    if mechanism == "content-hash-ledger":
        if re.search(r"\|[^\n]*\|\s*[0-9a-f]{12}\s*\|", text):
            return True, ""
        return False, "声明 content-hash-ledger，但文件里没有 12 位行内容 hash 列"
    return False, "机制不可验证"


def _collect_docs(paths: Optional[List[str]]) -> List[Tuple[Path, str]]:
    if paths:
        candidates = [Path(p) for p in paths]
    else:
        candidates = sorted((SCAN_ROOT / "docs").rglob("*.md"))
    result: List[Tuple[Path, str]] = []
    for path in candidates:
        if not path.is_absolute():
            path = (SCAN_ROOT / path).resolve()
        if not path.exists() or path.suffix.lower() != ".md":
            continue
        try:
            display = path.resolve().relative_to(SCAN_ROOT).as_posix()
        except ValueError:
            display = path.as_posix()
        result.append((path, display))
    return result


def os_norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./").lower()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="红项生命周期机检（已修/not_verified 必须举证）")
    parser.add_argument("--paths", nargs="*", default=None, help="只扫指定 md 文件")
    parser.add_argument("--root", default=None, help="仓库根目录（默认本仓库，测试用）")
    parser.add_argument("--exemptions", default=None, help="豁免清单路径（默认 docs/red-lifecycle-exemptions.md）")
    parser.add_argument("--exclude", nargs="*", default=[], help="临时排除的路径（仍打印 SKIP）")
    parser.add_argument("--max-detail", type=int, default=3, help="每个文件最多打印几条明细")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    global SCAN_ROOT, EXEMPTION_FILE
    SCAN_ROOT = Path(args.root).resolve() if args.root else ROOT
    if args.exemptions:
        EXEMPTION_FILE = args.exemptions

    docs = _collect_docs(args.paths)
    exemptions = _load_exemptions()
    excluded = {os_norm(p) for p in args.exclude}
    hash_cache: Dict[str, bool] = {}
    all_findings: List[Finding] = []
    all_hits: List[Hit] = []
    skip_entries: List[Dict[str, object]] = []
    errors: List[str] = []

    for path, display in docs:
        try:
            findings, hits = scan_file(path, display, hash_cache)
        except OSError as exc:
            errors.append(f"{display}: {exc}")
            continue

        if display in excluded:
            skip_entries.append({"path": display, "count": len(findings), "mechanism": "--exclude"})
            continue

        if display in exemptions:
            mechanism = exemptions[display]
            valid, why = _validate_exemption(path, mechanism)
            if valid:
                skip_entries.append({"path": display, "count": len(findings), "mechanism": mechanism})
                continue
            all_findings.append(
                Finding("X0", "FAIL", display, 0, f"豁免无效：{why}（{EXEMPTION_FILE}）", "")
            )

        all_findings.extend(findings)
        all_hits.extend(hits)

    all_findings.extend(_cross_doc_conflicts(all_hits))

    fails = [f for f in all_findings if f.level == "FAIL"]
    warns = [f for f in all_findings if f.level == "WARN" and not f.historical]
    hist_warns = [f for f in all_findings if f.level == "WARN" and f.historical]
    skip_total = sum(int(entry["count"]) for entry in skip_entries)
    skip_label = "、".join(f"{entry['path']}" for entry in skip_entries) or "-"

    if args.json:
        print(
            json.dumps(
                {
                    "scanned": len(docs),
                    "fail_count": len(fails),
                    "warn_count": len(warns),
                    "hist_warn_count": len(hist_warns),
                    "skip_count": skip_total,
                    "skipped": skip_entries,
                    "by_file": _group_counts(fails, warns, hist_warns),
                    "findings": [f.as_dict() for f in all_findings],
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if fails else 0

    print(
        f"SCAN {len(docs)} 个文档；FAIL {len(fails)}，WARN {len(warns)}，"
        f"HIST-WARN {len(hist_warns)}（历史快照降级），SKIP {skip_total}"
    )
    if skip_total:
        for entry in skip_entries:
            print(
                f"  SKIP {entry['count']}（豁免：{entry['path']}，"
                f"机制={entry['mechanism']}，理由见 {EXEMPTION_FILE}）"
            )
    for err in errors:
        print(f"  ERROR {err}")
    print()

    groups = _group_counts(fails, warns, hist_warns)
    if groups:
        print("按文件汇总（FAIL / WARN / HIST-WARN）：")
        for row in groups:
            print(f"  {row['path']}  FAIL {row['fail']} / WARN {row['warn']} / HIST {row['hist']}")
        print()

    by_file: Dict[str, List[Finding]] = defaultdict(list)
    for finding in fails + warns + hist_warns:
        by_file[finding.path].append(finding)
    print(f"明细（每文件最多 {args.max_detail} 条）：")
    if not by_file:
        print("  无")
    for path in sorted(by_file):
        print(f"  {path}")
        for finding in by_file[path][: args.max_detail]:
            if finding.historical:
                level = "HIST-WARN"
            else:
                level = "FAIL" if finding.level == "FAIL" else "WARN"
            print(f"    {level} {path}:{finding.line} [{finding.rule}] {finding.message}")
        remaining = len(by_file[path]) - args.max_detail
        if remaining > 0:
            print(f"    …… 另有 {remaining} 条同类，用 --max-detail 展开或 --json 取全量")
    print()
    if fails:
        print("结论：FAIL —— 先把清单交各 owner，不要在扫描时顺带改别人的段落。")
        return 1
    print("结论：PASS（WARN / HIST-WARN 不阻断）")
    return 0


def _group_counts(
    fails: List[Finding], warns: List[Finding], hist_warns: List[Finding]
) -> List[Dict[str, object]]:
    fail_counts: Dict[str, int] = defaultdict(int)
    warn_counts: Dict[str, int] = defaultdict(int)
    hist_counts: Dict[str, int] = defaultdict(int)
    for finding in fails:
        fail_counts[finding.path] += 1
    for finding in warns:
        warn_counts[finding.path] += 1
    for finding in hist_warns:
        hist_counts[finding.path] += 1
    paths = sorted(set(fail_counts) | set(warn_counts) | set(hist_counts))
    return [
        {
            "path": path,
            "fail": fail_counts.get(path, 0),
            "warn": warn_counts.get(path, 0),
            "hist": hist_counts.get(path, 0),
        }
        for path in paths
    ]


if __name__ == "__main__":
    sys.exit(main())
