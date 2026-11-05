#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""扫描测试里「钉源码字面量」的脆断言（只读，不改测试）。

判据（用 AST，不用正则扫源码）：
  1. 先找「读源码文本」的变量：赋值右值里出现 ``.read_text()`` / ``open(...)`` /
     ``inspect.getsource(...)`` / 参数含 ``*.py`` 字面量的调用；对这类变量做
     传递闭包（``SRC.split(...)[1]``、``open(...) as f`` 后 ``f.read()`` 等）。
  2. 再找断言：``assert "字面量" in <源码变量>``、``assert re.search(...)``、
     ``<源码变量>.count("...")`` 等。命中的每一处就是一条「源码文本 pin」。
  3. 判定提示：pin 只证明「某段文字还在」，不证明行为；注释/换行/局部变量重命名
     都会让它误红（本仓已发生两次：test_u03_decoder_rule_copy、
     test_settings_convergence::test_satellite_tune_updates_control_panel）。

用法：
  python scripts/audit_source_text_pins.py            # 文本报表
  python scripts/audit_source_text_pins.py --json     # 机器可读
  python scripts/audit_source_text_pins.py --paths tests/foo.py tests/bar.py

退出码：0 扫描完成（与命中数量无关）；2 用法/解析错误。

限制（明说）：只看 `tests/` 下的 .py；动态拼装的源码文本（f-string 路径、
子进程读文件）可能漏检；本工具只标候选，最终判定需人读断言上下文。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_DIR = ROOT / "tests"

_READ_ATTRS = {"read_text", "getsource", "read", "read_bytes", "open"}
_REGEX_FUNCS = {"search", "match", "findall", "finditer", "fullmatch"}


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _looks_like_source_load(call: ast.Call) -> bool:
    """只认「读文本」的调用；不再用「参数里有 .py 字面量」猜（会误抓数据表）。"""
    name = _call_name(call).lower()
    if name in _READ_ATTRS:
        return True
    return name in {"_read", "_read_text", "read_source", "load_source"}


def _names_in(node: ast.AST) -> Set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _load_target(call: ast.Call) -> str:
    """从调用参数里取被读文件的字面量路径（取不到返回 ""）。"""
    for node in ast.walk(call):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.endswith((".py", ".md", ".json", ".cpp", ".h", ".js")):
                return value
    return ""


def _collect_source_vars(tree: ast.AST) -> Dict[str, str]:
    """反复迭代直到不动点：读文本的变量 + 由它们派生的变量 → 被读文件（可空）。"""
    tracked: Dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets: Iterable[ast.AST]
                if isinstance(node, ast.Assign):
                    targets = node.targets
                    value = node.value
                else:
                    targets = [node.target]
                    value = node.value
                if value is None:
                    continue
                if any(isinstance(t, ast.Name) and t.id in tracked for t in targets):
                    continue
                call = next(
                    (
                        n
                        for n in ast.walk(value)
                        if isinstance(n, ast.Call) and _looks_like_source_load(n)
                    ),
                    None,
                )
                if call is not None:
                    target = _load_target(call)
                elif _names_in(value) & set(tracked):
                    target = ""
                else:
                    continue
                for t in targets:
                    if isinstance(t, ast.Name) and t.id not in tracked:
                        tracked[t.id] = target
                        changed = True
            elif isinstance(node, ast.With):
                for item in node.items:
                    if item.optional_vars is None:
                        continue
                    call = next(
                        (
                            n
                            for n in ast.walk(item.context_expr)
                            if isinstance(n, ast.Call) and _looks_like_source_load(n)
                        ),
                        None,
                    )
                    if call is None:
                        continue
                    target = _load_target(call)
                    for n in ast.walk(item.optional_vars):
                        if isinstance(n, ast.Name) and n.id not in tracked:
                            tracked[n.id] = target
                            changed = True
    return tracked


def _snippet(node: ast.AST) -> str:
    try:
        text = ast.get_source_segment(_SRC_OF[node], node) or ""
    except Exception:  # noqa: BLE001 - 只用于展示
        return ""
    text = " ".join(text.split())
    return text[:160]


_SRC_OF: Dict[ast.AST, str] = {}


def _pipe_hits(test: ast.AST, tracked: Dict[str, str]) -> List[str]:
    """断言里出现对源码变量的文本比较 → 返回「钉的是什么」的片段。"""
    found: List[str] = []
    if isinstance(test, ast.UnaryOp):  # assert not (...)
        test = test.operand
    stack: List[ast.AST] = [test]
    while stack:
        node = stack.pop()
        stack.extend(ast.iter_child_nodes(node))
        if isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(comparator, ast.Name):
                    if comparator.id in tracked:
                        found.append(_snippet(node.left))
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _REGEX_FUNCS and node.args:
                if _names_in(node.args[0]) & set(tracked):
                    found.append(_snippet(node.args[0]))
            if isinstance(node.func, ast.Attribute) and node.func.attr == "count":
                if _names_in(node.func.value) & set(tracked):
                    found.append(_snippet(node))
    return found


_CODE_SUFFIX = (".py", ".cpp", ".h", ".js")
_DOC_SUFFIX = (".md", ".json", ".html")
_SOURCE_HINTS = ("src", "source", "body", "code", "helper", "text")


def _kind(target: str, matched: List[str]) -> str:
    """code = 钉源码文本；doc_or_data = 钉文档/数据；unknown = 无法判定。"""
    if target.endswith(_CODE_SUFFIX):
        return "code"
    if target.endswith(_DOC_SUFFIX):
        return "doc_or_data"
    joined = " ".join(matched).lower()
    if any(hint in joined for hint in _SOURCE_HINTS):
        return "code"
    return "unknown"


def scan_file(path: Path) -> List[dict]:
    try:
        # utf-8-sig：本仓部分测试文件带 BOM，ast.parse 会直接 SyntaxError
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError) as exc:
        return [{"path": str(path), "error": f"{exc.__class__.__name__}: {exc}"}]
    _SRC_OF.clear()
    for node in ast.walk(tree):
        _SRC_OF[node] = source

    tracked = _collect_source_vars(tree)
    if not tracked:
        return []

    funcs = [
        (n.name, n.lineno, getattr(n, "end_lineno", n.lineno))
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    def _func_at(line: int) -> str:
        best = ("", -1)
        for name, start, end in funcs:
            if start <= line <= end and start > best[1]:
                best = (name, start)
        return best[0]

    hits: List[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assert, ast.Expr)):
            continue
        if isinstance(node, ast.Assert):
            test = node.test
        else:
            test = node.value
            if not isinstance(test, ast.Call) or _call_name(test) not in {"ok", "fail"}:
                continue
        matched = _names_in(test) & set(tracked)
        if not matched:
            continue
        targets = {t for name in matched for t in [tracked[name]] if t}
        for snippet in _pipe_hits(test, tracked):
            hits.append(
                {
                    "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "line": getattr(node, "lineno", 0),
                    "func": _func_at(getattr(node, "lineno", 0)),
                    "source_vars": sorted(matched),
                    "target_file": sorted(targets)[0] if targets else "",
                    "kind": _kind(sorted(targets)[0] if targets else "", sorted(matched)),
                    "pinned": snippet,
                }
            )
    return hits


MANUAL_BEGIN = "<!-- manual:begin -->"
MANUAL_END = "<!-- manual:end -->"


def _write_report(path: Path, payload: dict, targets: List[Path]) -> None:
    """写 Markdown 清单；`manual:begin/end` 之间的判定段落原样保留。"""
    previous_manual = ""
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        if MANUAL_BEGIN in text and MANUAL_END in text:
            previous_manual = text.split(MANUAL_BEGIN, 1)[1].split(MANUAL_END, 1)[0]

    by_file: Dict[str, List[dict]] = {}
    for hit in payload["pins"]:
        by_file.setdefault(hit["path"], []).append(hit)

    lines: List[str] = [
        "# 测试里的「源码文本 pin」扫描清单",
        "",
        "> 自动段由 `python scripts/audit_source_text_pins.py --report "
        f"{path.as_posix().replace(str(ROOT) + '/', '')}` 生成；",
        "> manual 段（文件头部的判定）不会随重生成丢失。",
        "",
        MANUAL_BEGIN,
        previous_manual.strip() or "（人工判定段为空。判定规则见本次审计报告。）",
        MANUAL_END,
        "",
        "## 统计",
        "",
        f"- 扫描测试文件：{payload['scanned_files']}",
        f"- 命中：{payload['total_pins']} 处 / {payload['files_with_pins']} 个文件",
        f"- 类别：code {payload['kind_counts']['code']}、"
        f"doc_or_data {payload['kind_counts']['doc_or_data']}、"
        f"unknown {payload['kind_counts']['unknown']}",
        "",
        "## 按文件",
        "",
        "| 测试文件 | 处数 | 测试函数数 | 目标文件（Top3） |",
        "|---|---:|---:|---|",
    ]
    for file, hits in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
        funcs = {h["func"] for h in hits}
        targets_top = sorted(
            {h["target_file"] for h in hits if h["target_file"]},
            key=lambda t: -sum(1 for h in hits if h["target_file"] == t),
        )[:3]
        lines.append(
            f"| `{file}` | {len(hits)} | {len(funcs)} | "
            f"{', '.join('`' + t + '`' for t in targets_top) or '（派生变量）'} |"
        )

    lines += [
        "",
        "## 逐条候选",
        "",
        "| # | 位置 | 测试函数 | 类别 | 目标 | 钉的片段 |",
        "|---:|---|---|---|---|---|",
    ]
    for index, hit in enumerate(payload["pins"], 1):
        snippet = hit["pinned"].replace("|", "\\|")
        lines.append(
            f"| {index} | `{hit['path']}:{hit['line']}` | `{hit['func']}` | "
            f"{hit['kind']} | `{hit['target_file'] or '-'}` | `{snippet}` |"
        )
    if payload["parse_errors"]:
        lines += ["", "## 解析失败（已跳过）", ""]
        for err in payload["parse_errors"]:
            lines.append(f"- `{err['path']}`：{err['error']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描测试里的源码文本 pin")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--report", metavar="PATH", default=None,
                        help="写出 Markdown 清单（逐条候选 + 按文件统计）")
    parser.add_argument("--paths", nargs="*", default=None, help="只扫指定文件")
    args = parser.parse_args(argv)

    targets = (
        [Path(p) for p in args.paths]
        if args.paths
        else sorted(DEFAULT_TEST_DIR.rglob("*.py"))
    )
    all_hits: List[dict] = []
    errors: List[dict] = []
    for path in targets:
        for hit in scan_file(path):
            if "error" in hit:
                errors.append(hit)
            else:
                all_hits.append(hit)

    files = sorted({h["path"] for h in all_hits})
    kind_counts = {"code": 0, "doc_or_data": 0, "unknown": 0}
    for hit in all_hits:
        kind_counts[hit["kind"]] = kind_counts.get(hit["kind"], 0) + 1
    payload = {
        "root": str(ROOT),
        "scanned_files": len(targets),
        "files_with_pins": len(files),
        "total_pins": len(all_hits),
        "kind_counts": kind_counts,
        "files": files,
        "pins": all_hits,
        "parse_errors": errors,
        "limitations": [
            "只看 tests/ 下的 .py；动态拼装路径可能漏检",
            "命中=候选，是否保留需人读断言上下文",
        ],
    }
    if args.report:
        _write_report(Path(args.report), payload, targets)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"扫描 {len(targets)} 个测试文件；命中 {len(all_hits)} 处 / {len(files)} 个文件")
    for hit in all_hits:
        print(f"  {hit['path']}:{hit['line']}  钉：{hit['pinned']}")
    if errors:
        print(f"\n解析失败 {len(errors)} 个文件（跳过，不猜测）：")
        for err in errors:
            print(f"  {err['path']}: {err['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
