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
  - EMPTY / SILENT_CONTROL / SILENT_FALLBACK 未进白名单 → 存量（defect）
  - 有记录（RECORDED）→ 只计数，不入缺陷
  - `except Exception: raise`（单条裸 raise 的假重抛）→ FAKE_RERAISE，须修
  - `contextlib.suppress(Exception)` / `suppress(Exception)` → 禁令，须修
    （禁止用 suppress(Exception) 或假重抛绕过本扫描器）

白名单 `scripts/swallow_whitelist.json` 每条必须回答三问 + 复核期限，缺一即不合格：
  1. reason      为什么允许降级（必须是外部边界：第三方库/网络/硬件/平台）
  2. user_visible 降级后用户看到什么（不能是“什么都没有”）
  3. test        哪条测试覆盖这条降级路径（文件必须存在）
  4. review_by   `YYYY-MM`，到期必须复核，否则按不合格处理

棘轮策略：存量不是逐处修完，而是「只许降不许升」。
  - `docs/code-guards-baseline.json` 按文件记录 ERROR/WARN 基线；
  - 任何文件超过基线、或基线里没有的文件出现静默 catch-all → 新红线；
  - 总量超过基线 → 红。
  - 生成基线是人工动作：`--write-baseline docs/code-guards-baseline.json`

用法：
  python scripts/lint_swallow_all.py            # 汇总 + 棘轮判定（超基线退出 1）
  python scripts/lint_swallow_all.py --list     # 打印每一处（含已记录型）
  python scripts/lint_swallow_all.py --json out.json
  python scripts/lint_swallow_all.py --write-baseline docs/code-guards-baseline.json
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
from datetime import date
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
    body: str                      # EMPTY / SILENT_CONTROL / SILENT_FALLBACK / RECORDED / RERAISE / FAKE_RERAISE / FORBIDDEN_SUPPRESS
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
    forbidden: int = 0
    whitelisted: int = 0
    whitelist_invalid: int = 0
    whitelist_overdue: int = 0
    whitelist_stale: int = 0
    ratchet_violations: List[str] = field(default_factory=list)
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
    # 假重抛：except Exception: raise —— 什么都没做，只是骗扫描器“我重抛了”。
    if len(stmts) == 1 and isinstance(stmts[0], ast.Raise) and stmts[0].exc is None:
        return "FAKE_RERAISE"
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

    def visit_Call(self, node: ast.Call) -> None:
        # 明令禁止 contextlib.suppress(Exception)：它是 `except Exception: pass`
        # 的等价写法，扫描器必须拦住，否则门禁形同虚设。
        name = _call_name(node.func)
        if name in {"suppress", "contextlib.suppress"} or name.endswith(".suppress"):
            if any(_catches_all(arg) for arg in node.args):
                self.findings.append(Finding(
                    path=self.path,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    symbol=".".join(self.stack) or "<module>",
                    exception="suppress(Exception)",
                    bare=False,
                    body="FORBIDDEN_SUPPRESS",
                    severity="ERROR",
                    snippet=_first_code_line([node], self.src) or "suppress(Exception)",
                ))
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        for i, handler in enumerate(node.handlers):
            if not _catches_all(handler.type):
                continue
            body_kind = _classify(handler.body)
            if body_kind == "RERAISE":
                severity = "INFO"
            elif body_kind == "RECORDED":
                severity = "INFO"
            elif body_kind in {"EMPTY", "FAKE_RERAISE"}:
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


#: 白名单 trigger 支持的外部边界失效方式 → 测试里必须能找到同构屏蔽。
TRIGGER_KINDS = {
    "import_fail": "第三方模块导入失败",
    "env_absent": "必需的环境/命令缺失",
}


def _load_test_ast(path: Path):
    """读取测试源码并解析；失败返回 (None, "")，由调用方判不合格。"""
    try:
        src = _read_source(path)
        return ast.parse(src, filename=str(path)), src
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None, ""


def _iter_test_functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _test_raises_import_error_for(path: Path, module: str) -> bool:
    """测试函数里必须同时出现模块名与 `raise ImportError`。

    只声明 test 路径不够——改个路径就能过；要求真实的导入失败屏蔽语句。
    """
    tree, src = _load_test_ast(path)
    if tree is None:
        return False
    for fn in _iter_test_functions(tree):
        seg = ast.get_source_segment(src, fn) or ""
        if module not in seg:
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(exc, ast.Name) and exc.id == "ImportError":
                return True
    return False


def _test_simulates_env_absent(path: Path, target: str) -> bool:
    """测试函数里必须出现目标名，并 monkeypatch 环境/命令查找。"""
    tree, src = _load_test_ast(path)
    if tree is None:
        return False
    markers = ("monkeypatch.setenv", "monkeypatch.delenv", "patch.dict",
               "environ.pop", "environ.__delitem__", "shutil.which")
    for fn in _iter_test_functions(tree):
        seg = ast.get_source_segment(src, fn) or ""
        if target in seg and any(m in seg for m in markers):
            return True
    return False


def _trigger_issue(entry: Dict[str, Any], test_path: Path) -> str:
    """校验 trigger 声明，并要求对应测试真的执行了这种屏蔽。

    形如 ``{"import_fail": "sounddevice"}`` 或 ``{"env_absent": "AUDIO_DEVICE"}``。
    """
    trigger = entry.get("trigger")
    declared = entry.get("test") or test_path.name
    if not isinstance(trigger, dict) or len(trigger) != 1:
        return ('缺少或非法 trigger（应为单键对象，如 {"import_fail": "sounddevice"}）')
    kind, target = next(iter(trigger.items()))
    if kind not in TRIGGER_KINDS:
        return f"未知 trigger 类型 {kind!r}；支持 {sorted(TRIGGER_KINDS)}"
    target = str(target).strip()
    if not target:
        return f"trigger[{kind}] 的目标为空"
    if kind == "import_fail":
        if not _test_raises_import_error_for(test_path, target):
            return (f"trigger 未被测试真实触发：{declared} 里没有"
                    f"「引用 {target} 且 raise ImportError」的测试函数")
        return ""
    if not _test_simulates_env_absent(test_path, target):
        return (f"trigger 未被测试真实触发：{declared} 里没有"
                f"「操作 {target} 且 monkeypatch 环境」的测试函数")
    return ""


def _entry_issue(entry: Dict[str, Any], root: Path) -> str:
    for field_name in ("reason", "user_visible", "test"):
        value = (entry.get(field_name) or "").strip() if isinstance(entry.get(field_name), str) else ""
        if not value:
            return f"缺少 {field_name}"
    test = entry.get("test", "")
    candidates = [root / test, ROOT / test, Path(test)]
    test_path = next((p for p in candidates if p.exists()), None)
    if test_path is None:
        return f"测试文件不存在: {test}"
    review = entry.get("review_by")
    if not isinstance(review, str) or not re.fullmatch(r"\d{4}-\d{2}", review.strip()):
        return "缺少或非法 review_by（应为 YYYY-MM）"
    try:
        year, month = (int(x) for x in review.strip().split("-"))
        today = date.today()
        if (year, month) < (today.year, today.month):
            return f"白名单已过期，需复核: review_by={review}"
    except ValueError:
        return "缺少或非法 review_by（应为 YYYY-MM）"
    return _trigger_issue(entry, test_path)


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
            if finding.body == "FORBIDDEN_SUPPRESS":
                report.forbidden += 1
            else:
                report.total_catch_all += 1
                if finding.body == "RECORDED":
                    report.recorded += 1
                elif finding.body == "RERAISE":
                    report.reraised += 1
                else:
                    report.silent += 1
            # 禁令构造（suppress(Exception) / 假重抛）不允许白名单豁免。
            if finding.body in {"FORBIDDEN_SUPPRESS", "FAKE_RERAISE"}:
                report.defects.append(finding)
                report.all_findings.append(finding)
                continue
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
                    if issue.startswith("白名单已过期"):
                        report.whitelist_overdue += 1
                    else:
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


DEFAULT_BASELINE = "docs/code-guards-baseline.json"


def build_baseline(report: ScanReport) -> Dict[str, Any]:
    """按文件记录 ERROR/WARN 计数，作为棘轮上限（只许降不许升）。"""
    files: Dict[str, Dict[str, int]] = {}
    for f in report.defects:
        row = files.setdefault(f.path, {"ERROR": 0, "WARN": 0})
        row[f.severity] = row.get(f.severity, 0) + 1
    return {
        "version": 1,
        "snapshot": date.today().isoformat(),
        "policy": "棘轮基线：任何文件的 ERROR/WARN 不得超过此处计数；新文件出现静默 catch-all 即红。只许降不许升。",
        "total": len(report.defects),
        "whitelisted": report.whitelisted,
        "forbidden": report.forbidden,
        "files": dict(sorted(files.items())),
    }


def load_baseline(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def check_ratchet(report: ScanReport, baseline: Dict[str, Any]) -> List[str]:
    """返回所有超基线项；空列表 = 只降没升。"""
    violations: List[str] = []
    files = baseline.get("files") or {}
    current: Dict[str, Dict[str, int]] = {}
    for f in report.defects:
        row = current.setdefault(f.path, {"ERROR": 0, "WARN": 0})
        row[f.severity] = row.get(f.severity, 0) + 1

    for path in sorted(current):
        cur = current[path]
        base = files.get(path)
        if base is None:
            violations.append(
                f"{path}: 基线中没有该文件，但出现了静默 catch-all "
                f"({cur['ERROR']} ERROR / {cur['WARN']} WARN)")
            continue
        for sev in ("ERROR", "WARN"):
            allow = int(base.get(sev, 0))
            if cur[sev] > allow:
                violations.append(
                    f"{path}: {sev} {cur[sev]} > 基线 {allow}（新增 {cur[sev] - allow}）")

    total_allow = int(baseline.get("total", 0))
    if len(report.defects) > total_allow:
        violations.append(
            f"总量 {len(report.defects)} > 基线 {total_allow}"
            f"（新增 {len(report.defects) - total_allow}）")
    return violations


def write_baseline(report: ScanReport, path: Path) -> None:
    data = build_baseline(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    if report.forbidden:
        print(f"  禁令构造（suppress(Exception)/假重抛）：{report.forbidden}")
    print(f"白名单：{report.whitelisted} 处有效"
          + (f" / {report.whitelist_invalid} 处不合格" if report.whitelist_invalid else "")
          + (f" / {report.whitelist_overdue} 处到期" if report.whitelist_overdue else "")
          + (f" / {report.whitelist_stale} 处失效（代码已变）" if report.whitelist_stale else ""))
    print(f"存量须修：{report.defect_count} 处")

    if report.ratchet_violations:
        print(f"\n[棘轮越线] {len(report.ratchet_violations)} 项")
        for item in report.ratchet_violations[:30]:
            print(f"  {item}")
        if len(report.ratchet_violations) > 30:
            print(f"  ……其余 {len(report.ratchet_violations) - 30} 项见 --json")
    else:
        print("棘轮：未越基线（只降没升）")

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
    parser.add_argument("--baseline", default=None, help="棘轮基线 JSON 路径")
    parser.add_argument("--write-baseline", default=None,
                        help="把当前存量写成棘轮基线（人工动作）")
    parser.add_argument("--no-fail", action="store_true", help="只报告，不因越线返回 1")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = scan_repo(root, Path(args.whitelist) if args.whitelist else None)

    if args.write_baseline:
        out = Path(args.write_baseline)
        write_baseline(report, out)
        print(f"基线已写入：{out}（total={report.defect_count}）")
        return 0

    baseline_path = Path(args.baseline) if args.baseline else root / DEFAULT_BASELINE
    baseline = load_baseline(baseline_path)
    if baseline is not None:
        report.ratchet_violations = check_ratchet(report, baseline)

    print_report(report, list_all=args.list)

    if args.json:
        payload = {
            "files_scanned": report.files_scanned,
            "total_catch_all": report.total_catch_all,
            "silent": report.silent,
            "recorded_or_reraised": report.recorded + report.reraised,
            "forbidden": report.forbidden,
            "whitelisted": report.whitelisted,
            "whitelist_invalid": report.whitelist_invalid,
            "whitelist_overdue": report.whitelist_overdue,
            "whitelist_stale": report.whitelist_stale,
            "ratchet_violations": report.ratchet_violations,
            "defects": [asdict(f) for f in report.defects],
            "all_findings": [asdict(f) for f in report.all_findings],
            "parse_errors": report.parse_errors,
        }
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {out}")

    if args.no_fail:
        return 0
    # 有基线时只看“越线”（存量不阻塞 CI）；无基线时退回全量缺陷判定，
    # 避免误删基线后门禁静默变绿。
    if baseline is not None:
        gate = (report.ratchet_violations or report.whitelist_invalid
                or report.whitelist_overdue or report.whitelist_stale)
        return 1 if gate else 0
    return 1 if report.defect_count else 0


if __name__ == "__main__":
    sys.exit(main())
