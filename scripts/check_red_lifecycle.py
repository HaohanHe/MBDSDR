#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""红项生命周期机检：把「已修」这类状态当作需举证的断言来查。

规则：
  R1 FAIL  出现「已修 / 已修复 / 已落地 / 已解决」但同一行没有 commit hash。
  R2 FAIL  写了 hash，但 `git cat-file -e <sha>^{commit}` 查不到（hash 打错/不是提交）。
  R3 WARN  状态为已修且 hash 有效，但本行没有可复跑命令（`ctest -R ...` / `pytest ...`）。
           有些修复只能靠反证，所以只告警不判红。
  R4 FAIL  标了 `not_verified` / 「未复核」，但没写「为什么没验 / 缺什么才能验」。
  R5 FAIL  同一红项（按测试名归一）在 ≥2 个文档里出现且状态不一致，列出各文件:行。

局限（明说）：
  * 「红项」的身份只能从反引号里的测试名推断（`Foo::test_bar` → `test_bar`）；
    没有测试名的散句无法做跨文档一致性比对。
  * 关键字豁免：代码块、`「…」`/`“…”` 引号内文本不算断言。
  * `docs/claim-waivers.md` 是引述历史违规的台账；其第三列是行内容 hash，不是提交 hash，
    因此该文件会按 R2 报大量红——这是如实结果，不静默豁免（可用 --exclude 排除）。

用法：
  python scripts/check_red_lifecycle.py                 # 扫 docs/**/*.md，按文件汇总
  python scripts/check_red_lifecycle.py --json
  python scripts/check_red_lifecycle.py --root <仓库>   # 测试用，默认本仓库
  python scripts/check_red_lifecycle.py --exclude docs/claim-waivers.md
  python scripts/check_red_lifecycle.py --max-detail 5

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
DOCS_DIR = ROOT / "docs"
# 测试可用 --root 指到临时仓库；运行期由 main() 覆盖。
SCAN_ROOT = ROOT

FIXED_RE = re.compile(r"已修复|已修|已落地|已解决")
NOT_VERIFIED_RE = re.compile(r"`not_verified`|未复核")
OPEN_RE = re.compile(r"待修|未修|在途修")
HASH_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
CMD_RE = re.compile(
    r"`?(?:python\s+-m\s+)?pytest\b|`?ctest\s+-R|`?python\s+scripts/|`?\./scripts/"
)
REASON_RE = re.compile(
    r"缺|阻塞|原因|因为|无法|尚未|待|才能|未具备|不可复现|无环境|没有|不满足"
)
ITEM_RE = re.compile(r"`([^`]*(?:::|test_[A-Za-z0-9_]+)[^`]*)`")
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

    def as_dict(self) -> Dict[str, object]:
        return {
            "rule": self.rule,
            "level": self.level,
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
        if "::" in token:
            key = token.split("::")[-1].strip()
        else:
            key = token.strip()
        key = re.sub(r"\(.*$", "", key).strip()
        if key and (key.startswith("test_") or "::" in raw):
            keys.append(key)
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


def _detect_statuses(text: str) -> List[str]:
    found: List[str] = []
    if FIXED_RE.search(text):
        found.append("fixed")
    if NOT_VERIFIED_RE.search(text):
        found.append("not_verified")
    if OPEN_RE.search(text):
        found.append("open")
    return found


def scan_file(path: Path, display: str, hash_cache: Dict[str, bool]) -> Tuple[List[Finding], List[Hit]]:
    findings: List[Finding] = []
    hits: List[Hit] = []
    lines = list(_iter_lines(path))
    for index, (number, raw) in enumerate(lines):
        text = _strip_quoted(raw)
        statuses = _detect_statuses(text)
        if not statuses:
            continue
        # 原因/复跑命令允许落在相邻两行内（表格单元格会折行）。
        window = "\n".join(item for _n, item in lines[max(0, index - 2): index + 3])
        window_text = _strip_quoted(window)
        keys = _item_keys(text)
        for status in statuses:
            hits.append(Hit(display, number, status))
            for key in keys:
                hits.append(Hit(display, number, status, key))

            if status == "fixed":
                hashes = HASH_RE.findall(text)
                if not hashes:
                    findings.append(
                        Finding(
                            "R1", "FAIL", display, number,
                            "出现「已修/已修复/已落地/已解决」但同一行没有 commit hash",
                            raw.strip(),
                        )
                    )
                    continue
                bad = [h for h in hashes if not _commit_exists(h, hash_cache)]
                for sha in bad:
                    findings.append(
                        Finding(
                            "R2", "FAIL", display, number,
                            f"hash `{sha}` 不是可解析的提交（git cat-file -e {sha}^{{commit}} 失败）",
                            raw.strip(),
                        )
                    )
                if not bad and not CMD_RE.search(window_text):
                    findings.append(
                        Finding(
                            "R3", "WARN", display, number,
                            "已修 + hash 有效，但相邻行内没有可复跑命令（ctest -R / pytest）",
                            raw.strip(),
                        )
                    )
            elif status == "not_verified":
                if not REASON_RE.search(window_text):
                    findings.append(
                        Finding(
                            "R4", "FAIL", display, number,
                            "标了 `not_verified`/未复核，但相邻行内没写为什么没验或缺什么才能验",
                            raw.strip(),
                        )
                    )
    return findings, hits


def _cross_doc_conflicts(hits: List[Hit]) -> List[Finding]:
    """同一测试名在多文档出现，且状态不一致 → R5。"""
    by_key: Dict[str, List[Hit]] = defaultdict(list)
    for hit in hits:
        if hit.key and hit.status != "open":
            by_key[hit.key].append(hit)
    findings: List[Finding] = []
    for key, items in sorted(by_key.items()):
        files = {item.path for item in items}
        statuses = {item.status for item in items}
        if len(files) >= 2 and len(statuses) >= 2:
            where = "；".join(
                f"{item.path}:{item.line}={STATUS_LABELS.get(item.status, item.status)}"
                for item in items
            )
            findings.append(
                Finding(
                    "R5", "FAIL", sorted(files)[0], items[0].line,
                    f"同一红项 `{key}` 在多文档状态不一致：{where}",
                    "",
                )
            )
    return findings


def _collect_docs(paths: Optional[List[str]], excludes: List[str]) -> List[Tuple[Path, str]]:
    excluded = {os_norm(p) for p in excludes}
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
        if os_norm(display) in excluded:
            continue
        result.append((path, display))
    return result


def os_norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./").lower()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="红项生命周期机检（已修/not_verified 必须举证）")
    parser.add_argument("--paths", nargs="*", default=None, help="只扫指定 md 文件")
    parser.add_argument("--root", default=None, help="仓库根目录（默认本仓库，测试用）")
    parser.add_argument("--exclude", nargs="*", default=[], help="排除的路径（可多次）")
    parser.add_argument("--max-detail", type=int, default=3, help="每个文件最多打印几条明细")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    global SCAN_ROOT
    SCAN_ROOT = Path(args.root).resolve() if args.root else ROOT

    docs = _collect_docs(args.paths, args.exclude)
    hash_cache: Dict[str, bool] = {}
    all_findings: List[Finding] = []
    all_hits: List[Hit] = []
    errors: List[str] = []
    for path, display in docs:
        try:
            findings, hits = scan_file(path, display, hash_cache)
        except OSError as exc:
            errors.append(f"{display}: {exc}")
            continue
        all_findings.extend(findings)
        all_hits.extend(hits)
    all_findings.extend(_cross_doc_conflicts(all_hits))

    fails = [f for f in all_findings if f.level == "FAIL"]
    warns = [f for f in all_findings if f.level == "WARN"]

    if args.json:
        print(
            json.dumps(
                {
                    "scanned": len(docs),
                    "fail_count": len(fails),
                    "warn_count": len(warns),
                    "by_file": _group_counts(fails, warns),
                    "findings": [f.as_dict() for f in all_findings],
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if fails else 0

    print(f"SCAN {len(docs)} 个文档；FAIL {len(fails)} 条，WARN {len(warns)} 条")
    for err in errors:
        print(f"  ERROR {err}")
    print()

    groups = _group_counts(fails, warns)
    if groups:
        print("按文件汇总（FAIL+WARN）：")
        for row in groups:
            print(f"  {row['path']}  FAIL {row['fail']} / WARN {row['warn']}")
        print()

    by_file: Dict[str, List[Finding]] = defaultdict(list)
    for finding in fails + warns:
        by_file[finding.path].append(finding)
    print(f"明细（每文件最多 {args.max_detail} 条）：")
    if not by_file:
        print("  无")
    for path in sorted(by_file):
        print(f"  {path}")
        for finding in by_file[path][: args.max_detail]:
            level = "FAIL" if finding.level == "FAIL" else "WARN"
            print(f"    {level} {path}:{finding.line} [{finding.rule}] {finding.message}")
        remaining = len(by_file[path]) - args.max_detail
        if remaining > 0:
            print(f"    …… 另有 {remaining} 条同类，用 --max-detail 展开或 --json 取全量")
    print()
    if fails:
        print("结论：FAIL —— 先把清单交各 owner，不要在扫描时顺带改别人的段落。")
        return 1
    print("结论：PASS（WARN 不阻断）")
    return 0


def _group_counts(fails: List[Finding], warns: List[Finding]) -> List[Dict[str, object]]:
    fail_counts: Dict[str, int] = defaultdict(int)
    warn_counts: Dict[str, int] = defaultdict(int)
    for finding in fails:
        fail_counts[finding.path] += 1
    for finding in warns:
        warn_counts[finding.path] += 1
    paths = sorted(set(fail_counts) | set(warn_counts))
    return [
        {"path": path, "fail": fail_counts.get(path, 0), "warn": warn_counts.get(path, 0)}
        for path in paths
    ]


if __name__ == "__main__":
    sys.exit(main())
