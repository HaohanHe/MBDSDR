#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""catch-all 静默吞噬扫描器（`except: pass` 守卫）。

背景：`desktop/rf_sky_view.py::_find_body_at` 里 `float(None)` 抛 TypeError，
被 `try/except Exception: pass` 吞掉，导致所有深空天体既不可见也不可点，
且无日志无报错。这类"静默丢功能"比崩溃更危险，因为没有任何证据链。

本脚本用 AST（不是正则）找出所有「catch-all 且静默」的 except 处理器：

* catch-all = `except:` / `except Exception:` / `except BaseException:`，
  或元组里含 Exception/BaseException；
* 静默 = 处理器体里没有任何“记录原因”的调用（log/print/warn/record…），
  且没有重新抛出；具体分三档：
  - EMPTY：只有 `pass`、`...` 或注释（AST 里注释不可见，等同 Pass）
  - SILENT_CONTROL：只 `return None` / `return <常量>` / `continue` / `break`
  - SILENT_FALLBACK：有语句但没有记录（例如给变量塞默认值后继续）

判定：
  - EMPTY / SILENT_CONTROL / SILENT_FALLBACK 未进白名单 → 须修（defect）
  - 有记录（RECORDED）或重新抛（RERAISE）→ 只计数，不入缺陷

白名单 `scripts/swallow_whitelist.json` 每条必须回答三问，缺一即判不合格：
  1. reason      为什么允许降级（必须是外部边界：第三方库/网络/硬件/平台）
  2. user_visible 降级后用户看到什么（不能是“什么都没有”）
  3. test        哪条测试覆盖这条降级路径（文件必须存在）

用法：
  python scripts/lint_swallow_all.py            # 汇总 + 缺陷清单
  python scripts/lint_swallow_all.py --list     # 打印每一处（含已记录型）
  python scripts/lint_swallow_all.py --json out.json
  python scripts/lint_swallow_all.py --no-fail  # 只报告，不改退出码
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tokenize
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
WHITELIST_PATH = ROOT / "scripts" / "swallow_whitelist.json"

#: 不扫的目录：第三方/构建产物/虚拟环境，不是本仓源码。
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", ".tox", "build", "dist",
    "node_modules", ".mypy_cache", ".pytest_cache", "site-packages", "output",
}

_CATCH_ALL_NAMES = {"Exception", "BaseException"}

#: “记录了原因”的调用名特征。必须是事件/日志/上报类，不能是数据 append。
_RECORD_CALL_RE = re.compile(
    r"(^|[._])(log|logger|logging|print|warn|warning|error|exception|critical|"
    r"debug|info|trace|record|report|notify|emit|audit|capture|message)",
    re.IGNORECASE,
)

#: 把“写入错误/状态字段”也算记录原因（用户能在状态栏/日志面板看到）。
_RECORD_TARGET_RE = re.compile(
    r"(exc|err|error|status|message|reason|warn|log|detail|last_err)", re.IGNORECASE
)

#: 把错误/问题追加进记录容器也算记录（如 records.append({... "detail": ...})）。
_RECORD_CONTAINER_RE = re.compile(
    r"(record|report|error|issue|log|journal|errs|failures)", re.IGNORECASE
)


@dataclass
class Finding:
    path: str                      # 相对仓库根，正斜杠
    line: int                      # except 处理器起始行
    end_line: int
    symbol: str                    # 所在类/函数，点分
    exception: str                 # 捕获类型原文
    bare: bool                     # 是否裸 except
    body: str                      # EMPTY / SILENT_CONTROL / SILENT_FALLBACK / RECORDED / RERAISE
    severity: str                  # ERROR / WARN / INFO
    snippet: str
    whitelisted: bool = False
    whitelist_issue: str = ""

    @property
    def key(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass
class ScanReport:
    total_catch_all: int = 0
    silent: int = 0
    recorded: int = 0
    reraised: int = 0
    whitelisted: int = 0
    whitelist_invalid: int = 0
    whitelist_stale: int = 0
    defects: List[Finding] = field(default_factory=list)
    all_findings: List[Finding] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def defect_count(self) -> int:
        return len(self.defects)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _assign_target_name(target: ast.AST) -> str:
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Subscript):
        key = ""
        if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
            key = target.slice.value
        base = _assign_target_name(target.value)
        return f"{base}.{key}" if key else base
    return ""


def _recorded(body: List[ast.stmt]) -> bool:
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                if _RECORD_CALL_RE.search(_call_name(node.func)):
                    return True
                if (isinstance(node.func, ast.Attribute) and node.func.attr == "append"
                        and _RECORD_CONTAINER_RE.search(_call_name(node.func.value))):
                    return True
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if _RECORD_TARGET_RE.search(_assign_target_name(target)):
                        return True
    return False


def _noop(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return isinstance(stmt.value.value, (str, type(Ellipsis))
                          ) or stmt.value.value is Ellipsis
    return False


def _classify(body: List[ast.stmt]) -> str:
    stmts = [s for s in body if not _noop(s)]
    if not stmts:
        return "EMPTY"
    if any(isinstance(s, ast.Raise) for s in stmts):
        return "RERAISE"
    if _recorded(stmts):
        return "RECORDED"
    last = stmts[-1]
    if isinstance(last, (ast.Continue, ast.Break)):
        return "SILENT_CONTROL"
    if isinstance(last, ast.Return):
        # return 带明确非 None 常量仍是静默兜底，但至少调用方拿得到值；
        # 统一归 SILENT_CONTROL，交给白名单/线归属人判断。
        return "SILENT_CONTROL"
    return "SILENT_FALLBACK"


def _catches_all(tp: Optional[ast.expr]) -> bool:
    if tp is None:
        return True
    if isinstance(tp, ast.Name):
        return tp.id in _CATCH_ALL_NAMES
    if isinstance(tp, ast.Attribute):
        return tp.attr in _CATCH_ALL_NAMES
    if isinstance(tp, ast.Tuple):
        return any(_catches_all(e) for e in tp.elts)
    return False


def _exception_src(src: str, tp: Optional[ast.expr]) -> str:
    if tp is None:
        return "bare except:"
    try:
        return ast.get_source_segment(src, tp) or ast.unparse(tp)
    except (AttributeError, ValueError, TypeError):
        # 源码片段还原失败只影响展示，不影响判定；收窄类型避免掩盖编程错误。
        return "<unknown>"


class _Walker(ast.NodeVisitor):
    def __init__(self, path: str, src: str):
        self.path = path
        self.src = src
        self.stack: List[str] = []
        self.findings: List[Finding] = []

    def _visit_named(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_named
    visit_AsyncFunctionDef = _visit_named
    visit_ClassDef = _visit_named

    def visit_Try(self, node: ast.Try) -> None:
        for i, handler in enumerate(node.handlers):
            if not _catches_all(handler.type):
                continue
            body_kind = _classify(handler.body)
            if body_kind == "RERAISE":
                severity = "INFO"
            elif body_kind == "RECORDED":
                severity = "INFO"
            elif body_kind == "EMPTY":
                severity = "ERROR"
            else:
                severity = "WARN"
            snippet = _first_code_line(handler.body, self.src)
            self.findings.append(Finding(
                path=self.path,
                line=handler.lineno,
                end_line=getattr(handler, "end_lineno", handler.lineno) or handler.lineno,
                symbol=".".join(self.stack) or "<module>",
                exception=_exception_src(self.src, handler.type),
                bare=handler.type is None,
                body=body_kind,
                severity=severity,
                snippet=snippet,
            ))
        self.generic_visit(node)


def _first_code_line(body: List[ast.stmt], src: str) -> str:
    for stmt in body:
        try:
            seg = ast.get_source_segment(src, stmt)
        except (AttributeError, ValueError, TypeError):
            # 纯展示用途：取不到片段就留空，不影响判定结果。
            seg = None
        if seg:
            line = seg.strip().splitlines()[0].strip()
            return line[:120]
    return ""


def _iter_py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        yield p


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_source(path: Path) -> str:
    with tokenize.open(str(path)) as fh:
        return fh.read()


def _load_whitelist(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("entries", []) if isinstance(data, dict) else data
    return [e for e in entries if isinstance(e, dict)]


def _entry_issue(entry: Dict[str, Any], root: Path) -> str:
    for field_name in ("reason", "user_visible", "test"):
        value = (entry.get(field_name) or "").strip() if isinstance(entry.get(field_name), str) else ""
        if not value:
            return f"缺少 {field_name}"
    test = entry.get("test", "")
    candidates = [root / test, ROOT / test, Path(test)]
    if not any(p.exists() for p in candidates):
        return f"测试文件不存在: {test}"
    return ""


def _entry_matches(entry: Dict[str, Any], finding: Finding) -> bool:
    if entry.get("path") != finding.path:
        return False
    symbol = entry.get("symbol")
    if symbol and symbol != finding.symbol:
        return False
    line = entry.get("line")
    if line is not None:
        return int(line) == finding.line
    return True


def scan_repo(root: Path = ROOT, whitelist_path: Optional[Path] = None) -> ScanReport:
    root = Path(root)
    wl_path = Path(whitelist_path) if whitelist_path else (root / "scripts" / "swallow_whitelist.json")
    entries = _load_whitelist(wl_path)
    report = ScanReport()
    matched_idx: set[int] = set()

    for path in _iter_py_files(root):
        rel = _relative(path, root)
        try:
            src = _read_source(path)
            tree = ast.parse(src, filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            report.parse_errors.append(f"{rel}: {exc.__class__.__name__}: {exc}")
            continue
        report.files_scanned += 1
        walker = _Walker(rel, src)
        walker.visit(tree)
        for finding in walker.findings:
            report.total_catch_all += 1
            if finding.body == "RECORDED":
                report.recorded += 1
            elif finding.body == "RERAISE":
                report.reraised += 1
            else:
                report.silent += 1
            entry_hit = None
            for idx, entry in enumerate(entries):
                if _entry_matches(entry, finding):
                    entry_hit = (idx, entry)
                    break
            if entry_hit is not None:
                idx, entry = entry_hit
                issue = _entry_issue(entry, root)
                if issue:
                    finding.whitelist_issue = issue
                    report.whitelist_invalid += 1
                else:
                    finding.whitelisted = True
                    report.whitelisted += 1
                    matched_idx.add(idx)
            if finding.severity in {"ERROR", "WARN"} and not finding.whitelisted:
                report.defects.append(finding)
            report.all_findings.append(finding)

    report.whitelist_stale = len(entries) - len(matched_idx)
    report.all_findings.sort(key=lambda f: (f.path, f.line, f.end_line))
    report.defects.sort(key=lambda f: (f.path, f.line, f.end_line))
    return report


def _group_by_file(findings: List[Finding]) -> Dict[str, List[Finding]]:
    out: Dict[str, List[Finding]] = {}
    for f in findings:
        out.setdefault(f.path, []).append(f)
    return out


def print_report(report: ScanReport, list_all: bool = False) -> None:
    print(f"扫描文件：{report.files_scanned}"
          + (f"（解析失败 {len(report.parse_errors)}）" if report.parse_errors else ""))
    print(f"全仓 catch-all 处理器：{report.total_catch_all}")
    print(f"  静默吞噬（pass/空块/静默 return-continue/无记录降级）：{report.silent}")
    print(f"  有记录或重新抛（非静默）：{report.recorded + report.reraised}")
    print(f"白名单：{report.whitelisted} 处有效"
          + (f" / {report.whitelist_invalid} 处不合格" if report.whitelist_invalid else "")
          + (f" / {report.whitelist_stale} 处失效（代码已变）" if report.whitelist_stale else ""))
    print(f"须修：{report.defect_count} 处")

    if report.parse_errors:
        print("\n[解析失败]")
        for item in report.parse_errors:
            print(f"  {item}")

    if report.defect_count:
        print("\n[须修清单]")
        for path, items in sorted(_group_by_file(report.defects).items()):
            print(f"\n{path}  ({len(items)} 处)")
            for f in items:
                print(f"  {f.line}:{f.end_line} [{f.severity}/{f.body}] "
                      f"{f.symbol}  {f.exception}  {f.snippet}")
                if f.whitelist_issue:
                    print(f"      白名单不合格：{f.whitelist_issue}")

    if list_all:
        print("\n[全部 catch-all]")
        for path, items in sorted(_group_by_file(report.all_findings).items()):
            print(f"\n{path}")
            for f in items:
                flag = "W" if f.whitelisted else ("!" if f.severity != "INFO" else "-")
                print(f"  {flag} {f.line}:{f.end_line} [{f.severity}/{f.body}] "
                      f"{f.symbol}  {f.exception}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="catch-all 静默吞噬扫描")
    parser.add_argument("--root", default=str(ROOT), help="仓库根目录")
    parser.add_argument("--whitelist", default=None, help="白名单 JSON 路径")
    parser.add_argument("--json", default=None, help="把完整报告写成 JSON")
    parser.add_argument("--list", action="store_true", help="打印每一处 catch-all")
    parser.add_argument("--no-fail", action="store_true", help="有缺陷也返回 0")
    args = parser.parse_args(argv)

    report = scan_repo(Path(args.root), Path(args.whitelist) if args.whitelist else None)
    print_report(report, list_all=args.list)

    if args.json:
        payload = {
            "files_scanned": report.files_scanned,
            "total_catch_all": report.total_catch_all,
            "silent": report.silent,
            "recorded_or_reraised": report.recorded + report.reraised,
            "whitelisted": report.whitelisted,
            "whitelist_invalid": report.whitelist_invalid,
            "whitelist_stale": report.whitelist_stale,
            "defects": [asdict(f) for f in report.defects],
            "all_findings": [asdict(f) for f in report.all_findings],
            "parse_errors": report.parse_errors,
        }
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {out}")

    if report.defect_count and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
