"""14 条公理的可执行检查。

每条检查只读仓库内容：源码 AST、测试里的 skip/xfail 元数据、文档里引用的
提交号。不 import 项目模块、不启动 Qt、不连硬件。
"""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .model import (
    Axiom,
    Finding,
    Repo,
    Unit,
    _read,
    const_eval,
    enclosing_functions,
    line_of,
    parse,
    walk_with_parents,
)

# ────────────────────────────── 跳过账本 ──────────────────────────────
# 「没报」可能是真的没有违规，也可能是检查器主动没看。两者必须分开计数，
# 否则「应修 0」无法区分「没问题」与「没看见」。
_SKIPS: Dict[str, Dict[str, int]] = {}


def note_skip(rule: str, reason: str) -> None:
    bucket = _SKIPS.setdefault(rule, {})
    bucket[reason] = bucket.get(reason, 0) + 1


def reset_skips() -> None:
    _SKIPS.clear()


def skip_report() -> Dict[str, Dict[str, int]]:
    return {rule: dict(reasons) for rule, reasons in sorted(_SKIPS.items())}

# ────────────────────────────── 公共工具 ──────────────────────────────

UNIT_RE = re.compile(r"(?:GHz|MHz|kHz|Hz|dBFS|dBµV|dBm|dB|MS/s)")
#: A01 只盯 readouts.py 已经提供唯一入口的几类事实，避免把普通提示文案也算进来。
FACT_FREQ = re.compile(r"freq|frequency|center_hz|tune_hz", re.I)
FACT_BW = re.compile(r"\bbw\b|bandwidth|band_width", re.I)
FACT_GAIN = re.compile(r"\bgain\b|gain_db", re.I)
FACT_RATE = re.compile(r"sample_?rate|rate_hz|\bsr\b|ms/s", re.I)
FACT_DBFS = re.compile(r"dbfs", re.I)
USER_VISIBLE = re.compile(
    r"setText|setToolTip|setWindowTitle|setStatusTip|setPlaceholderText|showMessage|"
    r"set_status|statusBar|QLabel|label|text\(",
)
NON_READOUT_CONTEXT = re.compile(r"logger\.|logging\.|_log\b|Noqa|type:\s*ignore")
SPEC_CONTEXT = re.compile(
    r"范围|上限|下限|可调|门限|阈值|步进|间隔|容差|默认|建议|至少|不超过|"
    r"偏移|offset|ppm|带宽|bw|bandwidth|BANDWIDTH|宽"
)
HISTORY_MARKER = re.compile(r"历史|当时|首次|截至|快照|此后|不再|未重跑|已修|已销账|previous|at the time")
GREEN_CLAIM = re.compile(r"全绿|全部通过|零失败|all green")
#: 「转绿条件：…全部通过」是在写出口判据，不是声称当前树已经绿。
EXIT_CRITERION = re.compile(r"转绿条件|销账条件|复核条件|验收条件|判据|通过条件|acceptance criteria", re.I)
IMPOSSIBLE_MESSAGE = re.compile(
    r"永不可能|不可能(?:通过|成立|绿|变绿)|才可能结束|"
    r"永远不(?:会|可能)(?:通过|结束|退出|返回)"
)


def _add(findings: List[Finding], rule: str, unit: Unit, line: int, why: str, fix: str,
         severity: str = "must-fix") -> None:
    # 同一个 AST 位置可能被多条路径命中（嵌套比较、同语句多个子表达式）：
    # 完全相同的结论只报一次，否则棘轮会把它当成两条独立违规。
    if any(f.rule == rule and f.path == unit.path and f.line == line and f.why == why
           for f in findings):
        return
    findings.append(Finding(rule, unit.path, line, line_of(unit, line), why, fix, severity))


def _is_test_path(path: str) -> bool:
    return path.startswith("tests/") or "/tests/" in path


def _broad_except(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in {"Exception", "BaseException"}
    if isinstance(handler.type, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in {"Exception", "BaseException"} for e in handler.type.elts)
    return False


def _silent_handler(handler: ast.ExceptHandler) -> bool:
    for stmt in handler.body:
        if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
            continue
        if isinstance(stmt, ast.Return):
            value = stmt.value
            if value is None:
                continue
            if isinstance(value, ast.Constant) and value.value in (None, "", 0, 0.0, False):
                continue
            if isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set)) and not list(ast.iter_child_nodes(value)):
                continue
            return False
        return False
    return True


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _kw(node: ast.Call, name: str) -> Optional[ast.AST]:
    for k in node.keywords:
        if k.arg == name:
            return k.value
    return None


def _literal_text(unit: Unit, node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    try:
        return ast.get_source_segment(unit.text, node) or ""
    except Exception:
        return ""


# ────────────────────────────── A01 ──────────────────────────────


def scan_a01(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not unit.path.startswith("desktop/") or unit.path.endswith("readouts.py") or unit.path.endswith("constants.py"):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literals = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            literal_text = "".join(literals)
            if not UNIT_RE.search(literal_text):
                continue
            if not any(isinstance(v, ast.FormattedValue) for v in node.values):
                continue
            src = line_of(unit, node.lineno)
            if NON_READOUT_CONTEXT.search(src) or not USER_VISIBLE.search(src):
                continue
            expr_parts = []
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    try:
                        expr_parts.append(ast.get_source_segment(unit.text, v.value) or "")
                    except Exception:
                        pass
            context = " ".join(expr_parts) + " " + src
            covered = (
                (FACT_FREQ.search(context) and re.search(r"GHz|MHz|kHz|Hz", literal_text) is not None)
                or (FACT_BW.search(context) and re.search(r"MHz|kHz|Hz", literal_text) is not None)
                or (FACT_GAIN.search(context) and re.search(r"dBFS|dBm|dB", literal_text) is not None)
                or (FACT_RATE.search(context) and re.search(r"MS/s|GHz|MHz|kHz|Hz", literal_text) is not None)
                or (FACT_DBFS.search(context) and "dBFS" in literal_text)
            )
            if not covered:
                continue
            _add(
                findings, "A01", unit, node.lineno,
                "用户可见读数在本文件内用 f-string 就地拼装，绕过了 desktop/readouts.py；"
                "同一个值换个面板就会拼出另一种字符串。",
                "改为调用 desktop/readouts.py 里对应的唯一产生函数；新口径只在该文件新增。",
            )
    return findings


# ────────────────────────────── A02 ──────────────────────────────


def _decimals(spec: Optional[str]) -> Optional[int]:
    if not spec:
        return None
    m = re.fullmatch(r"\.(\d+)f", spec)
    return int(m.group(1)) if m else None


def scan_a02(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not unit.path.startswith("desktop/") or unit.path.endswith("readouts.py"):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literals = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if SPEC_CONTEXT.search(literals):
                continue
            for val in node.values:
                if not isinstance(val, ast.FormattedValue):
                    continue
                spec = None
                if isinstance(val.format_spec, ast.JoinedStr):
                    spec = "".join(
                        v.value for v in val.format_spec.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    )
                dec = _decimals(spec)
                if dec is None:
                    continue
                expr = ""
                try:
                    expr = ast.get_source_segment(unit.text, val.value) or ""
                except Exception:
                    pass
                if "MHz" in literals and dec < 6 and re.search(r"freq|hz|MHz", expr, re.I):
                    _add(
                        findings, "A02", unit, node.lineno,
                        f"频率读数保留 {dec} 位小数（MHz）会把 1 Hz 调谐分辨率量化成 "
                        f"{10 ** (6 - dec)} Hz，读者看到的不是实际调谐值。",
                        "频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。",
                    )
                if "kHz" in literals and dec == 0 and re.search(r"bw|bandwidth|带宽|width", expr + literals, re.I):
                    _add(
                        findings, "A02", unit, node.lineno,
                        "带宽用 0 位小数 kHz 会丢掉亚 kHz 分量（例如 2400 Hz 显示成 2 kHz）。",
                        "带宽用至少 1 位小数 kHz（readouts.format_bandwidth_hz）。",
                    )
    return findings


# ────────────────────────────── A03 ──────────────────────────────

#: 名字只是线索，不是门槛。真正的判据是「这段代码有没有碰持久化」：
#: 被 open(...) / Path(...) 绑定的对象、模块内做过同样事情的函数，都算。
#: 之前白名单里没有裸 `read`，于是 `ConfigStore.read()` 换个方法名就合规——
#: 列举法的病不是漏了哪个名字，而是它可以被换名字绕过。
_PERSIST_HINT = {
    "read", "readline", "readlines", "read_text", "read_bytes", "readinto",
    "load", "loads", "safe_load", "parse",
}
_OPENERS = {"open", "Path", "PurePath", "PureWindowsPath", "PurePosixPath"}


def _attr_text(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _attr_text(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node)
    if isinstance(node, ast.Subscript):
        return _attr_text(node.value)
    return ""


def _is_opener(call: ast.Call) -> bool:
    name = _call_name(call)
    return name in _OPENERS or name.split(".")[-1] == "open"


def _persistent_names(tree: ast.Module) -> Set[str]:
    """被 open(...) / Path(...) 绑定出来的名字：之后调什么方法都视为持久化 I/O。"""
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if not isinstance(item.context_expr, ast.Call):
                    continue
                if _is_opener(item.context_expr) and item.optional_vars is not None:
                    text = _attr_text(item.optional_vars)
                    if text:
                        names.add(text)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and _is_opener(node.value):
            for target in node.targets:
                text = _attr_text(target)
                if text:
                    names.add(text)
    return names


def _touches_persistence(nodes: Iterable[ast.AST], bound: Set[str],
                         module_fns: Set[str]) -> bool:
    for node in nodes:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = _call_name(child)
            short = name.split(".")[-1]
            if short in _OPENERS or short == "open":
                return True
            if short in _PERSIST_HINT:
                return True
            if isinstance(child.func, ast.Attribute) and _attr_text(child.func.value) in bound:
                return True
            if name in module_fns or short in module_fns:
                return True
    return False


def _persist_functions(tree: ast.Module, bound: Set[str]) -> Set[str]:
    """模块内碰持久化的函数（含「包一层」：调用这些函数的函数也算）。"""
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    out: Set[str] = set()
    for fn in funcs:
        if _touches_persistence(fn.body, bound, set()):
            out.add(fn.name)
    for _ in range(2):  # 允许两层包装
        for fn in funcs:
            if fn.name in out:
                continue
            if _touches_persistence(fn.body, bound, out):
                out.add(fn.name)
    return out


def scan_a03(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if _is_test_path(unit.path) or unit.path.startswith("scripts/"):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        bound = _persistent_names(tree)
        persist_fns = _persist_functions(tree, bound)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            broad_silent = [h for h in node.handlers
                            if _broad_except(h) and _silent_handler(h)]
            if not broad_silent:
                continue
            if not _touches_persistence(node.body, bound, persist_fns):
                for handler in broad_silent:
                    note_skip("A03", "宽泛静默 except，但这段代码不碰持久化（需人工复核）")
                continue
            for handler in broad_silent:
                _add(
                    findings, "A03", unit, handler.lineno,
                    "持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），"
                    "调用方拿到的「空」与「真的空」无法区分。",
                    "记录 reason（含异常类型与路径）并把降级状态暴露给调用方；"
                    "只有「文件不存在」可以按首次使用处理。",
                )
    return findings


# ────────────────────────────── A04 ──────────────────────────────

_USER_DATA_HINT = re.compile(
    r"expanduser|Path\.home|APPDATA|\.mbdsdr|config_dir|settings|bookmark|layout|"
    r"conversation|memory|history|profile|station|schedule|workflow|recording|journal",
    re.I,
)

#: 真正的「备份」是拷贝/改名原文件，而不是模块里出现了 backup 这个词。
#: 之前按整模块扫 `corrupt|backup` 决定跳过，等于给被测代码一个开关：
#: 写个空函数 `_backup_corrupt` 就能让这个模块永远不被检查。
_BACKUP_QUALIFIED = {
    "shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copyfileobj",
    "shutil.move", "os.rename", "os.replace", "os.link",
}
_BACKUP_SHORT = {"copy", "copy2", "copyfile", "copyfileobj", "move"}


def _same_path(unit: Unit, node: ast.AST, expected: str) -> bool:
    if not expected:
        return False
    seg = " ".join((ast.get_source_segment(unit.text, node) or "").split())
    return seg == expected


def _read_path(unit: Unit, nodes: Iterable[ast.AST], bound: Set[str],
               persist_fns: Set[str]) -> str:
    """读失败前那次持久化读取的路径表达式（用于判断备份是不是备份同一个文件）。"""
    for node in nodes:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = _call_name(child)
            short = name.split(".")[-1]
            persistent = (
                short in _OPENERS or short == "open" or short in _PERSIST_HINT
                or (isinstance(child.func, ast.Attribute) and _attr_text(child.func.value) in bound)
                or name in persist_fns or short in persist_fns
            )
            if not persistent:
                continue
            if child.args:
                return " ".join((ast.get_source_segment(unit.text, child.args[0]) or "").split())
            if isinstance(child.func, ast.Attribute):
                return _attr_text(child.func.value)
    return ""


def _backup_names(tree: ast.Module) -> Set[str]:
    """本模块里可用的「备份」调用名：shutil/os 的拷贝改名，或 from ... import 进来的同名函数。"""
    names = set(_BACKUP_QUALIFIED)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"shutil", "os"}:
            for a in node.names:
                if a.name in {"copy", "copy2", "copyfile", "copyfileobj", "move",
                              "rename", "replace", "link"}:
                    names.add(a.asname or a.name)
    return names


def _is_backup_call(unit: Unit, node: ast.Call, names: Set[str], read_path: str = "") -> bool:
    op = _call_name(node)
    if op in names:
        return True
    short = op.split(".")[-1]
    if short in {"rename", "replace"} and isinstance(node.func, ast.Attribute):
        # Path(p).replace(...) / p.rename(...)：接收者或参数里出现被读的那个路径。
        if not read_path:
            return True
        segments = [ast.get_source_segment(unit.text, n) or "" for n in node.args]
        receiver = ast.get_source_segment(unit.text, node.func.value) or ""
        return any(read_path in s for s in segments + [receiver])
    return False


def _backup_functions(tree: ast.Module, names: Set[str]) -> Set[str]:
    """函数体里真的做了拷贝/改名的模块内函数。

    只看结构（有没有 shutil.copy / os.replace 这类调用），不看名字：
    空壳 `def _backup_corrupt(p): pass` 不在集合里。
    """
    out: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and _call_name(child) in names:
                out.add(node.name)
                break
    return out


def _handler_backs_up(unit: Unit, handler: ast.ExceptHandler, read_path: str,
                      backup_fns: Set[str], names: Set[str]) -> bool:
    """读失败分支里是否有可达的备份调用（直接调用，或调用模块内真会拷贝的函数）。"""
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call):
            continue
        if _is_backup_call(unit, node, names, read_path):
            return True
        op = _call_name(node)
        if op in backup_fns or op.split(".")[-1] in backup_fns:
            return True
    return False


def scan_a04(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not (unit.path.startswith("desktop/") or unit.path.startswith("mbdsdr_ai/")):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        # 只认 json.dump( 这个「整文件覆写」，json.dumps( 只是序列化，不会写盘。
        dump_line = next(
            (i for i, line in enumerate(unit.text.splitlines(), 1) if re.search(r"json\.dump\(", line)),
            0,
        )
        names = _backup_names(tree)
        backup_fns = _backup_functions(tree, names)
        bound = _persistent_names(tree)
        persist_fns = _persist_functions(tree, bound)
        silent_lines: List[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            if not _touches_persistence(node.body, bound, persist_fns):
                continue
            read_path = _read_path(unit, node.body, bound, persist_fns)
            for handler in node.handlers:
                if not (_broad_except(handler) and _silent_handler(handler)):
                    continue
                if _handler_backs_up(unit, handler, read_path, backup_fns, names):
                    note_skip("A04", "读失败分支里有可达的备份调用")
                    continue
                if not dump_line:
                    note_skip("A04", "模块没有整文件写回路径")
                    continue
                if not _USER_DATA_HINT.search(unit.text):
                    note_skip("A04", "路径不像用户数据（无 expanduser/APPDATA 等）")
                    continue
                silent_lines.append(handler.lineno)
        for line in sorted(set(silent_lines)):
            _add(
                findings, "A04", unit, line,
                f"读损坏文件时静默回默认值，但同一模块第 {dump_line} 行会用 json.dump 整文件覆写；"
                "用户下一次操作就会永久丢掉原内容，且没有备份。",
                "读失败时先备份为 *.corrupt-<时间>、记录原因，并拒绝用默认值覆写原件。",
            )
    return findings


# ────────────────────────────── A05 ──────────────────────────────

#: socket/serial 这类**外部** API 的调用名。本仓改不动它们，所以按名字认是稳定的；
#: 以前这里还收了 `get`/`poll`（本仓自己的方法），换个名字就绕过去了。
_BLOCKING_CALLS = {"recv", "recv_into", "recvfrom", "accept", "read", "readinto", "readline"}
_EXTERNAL_IO_CTOR = re.compile(r"^(?:socket\.socket|socket\.create_connection|serial\.Serial|Serial|socket)$")


def _external_io_names(tree: ast.Module) -> Set[str]:
    """被绑定成 socket/serial 实例的名字；只有这些对象上的阻塞读才算无界等待。"""
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            if not _EXTERNAL_IO_CTOR.match(_attr_text(value.func)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                elif isinstance(t, ast.Attribute):
                    names.add(t.attr)
    return names


def _sets_timeout(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and _call_name(n) == "settimeout":
            return True
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Attribute) and t.attr in {"timeout", "read_timeout", "write_timeout"}
                   for t in targets):
                return True
    return False


def _thread_target_blocks(tree: ast.Module, target: str) -> bool:
    io_names = _external_io_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target:
            # 结构判据一：`while True` 里没有任何 break/return —— 没名字也能判。
            for loop in (n for n in ast.walk(node) if isinstance(n, ast.While)):
                ok, value = const_eval(loop.test, {})
                if not (ok and value):
                    continue
                has_exit = any(isinstance(n, (ast.Break, ast.Return, ast.Raise)) for n in ast.walk(loop)
                               if n is not loop)
                if not has_exit:
                    return True
            if _sets_timeout(node):
                return False
            # 结构判据二：socket/serial 实例上的阻塞读，且既没传 timeout 也没设过超时。
            for n in ast.walk(node):
                if not isinstance(n, ast.Call) or _call_name(n) not in _BLOCKING_CALLS:
                    continue
                if _kw(n, "timeout") is not None:
                    continue
                if len(n.args) > 0:
                    continue
                if isinstance(n.func, ast.Attribute):
                    recv = _attr_text(n.func.value)
                    if recv.split(".")[-1] in io_names or recv in io_names:
                        return True
            return False
    return False


def _has_early_return_before(fn: ast.AST, line: int) -> bool:
    for child in ast.walk(fn):
        if isinstance(child, ast.If) and child.lineno < line:
            if any(isinstance(s, ast.Return) for s in ast.walk(child)):
                return True
    return False


def scan_a05(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        tree = parse(unit)
        if tree is None:
            continue
        fn_nodes = enclosing_functions(tree)
        # `except ...: assert False, "必须报错"`、`if missing: ... assert False, 诊断`
        # 都是「走到这里才算失败」的正常写法：断言本身处于条件分支里，并非永远执行。
        # 只有函数体顶层（无条件执行）的 assert False 才是「物理上不可能通过」。
        conditional_asserts: Set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                 ast.With, ast.AsyncWith, ast.Try)):
                for child in ast.walk(node):
                    if isinstance(child, ast.Assert):
                        conditional_asserts.add(child.lineno)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            ok, value = const_eval(node.test, {})
            if ok and not value:
                if node.lineno in conditional_asserts:
                    continue
                fn = fn_nodes.get(node.lineno)
                if fn is not None and _has_early_return_before(fn, node.lineno):
                    # `if not missing: return` 之后再 `assert False, 诊断` 是
                    # 「只有在真的有问题时才失败」的正常写法，不是永远失败。
                    continue
                _add(
                    findings, "A05", unit, node.lineno,
                    "断言的测试条件是常量假值，这个断言永远不可能通过。",
                    "删除该死断言，或把它改成对真实行为的断言。",
                )
                continue
            msg = _literal_text(unit, node.msg) if node.msg is not None else ""
            if msg and IMPOSSIBLE_MESSAGE.search(msg):
                _add(
                    findings, "A05", unit, node.lineno,
                    f"断言消息自述通过条件不成立（{msg[:60]}），该断言在当前实现下不可能变绿。",
                    "降为显式前置条件并标注原因，或登记进 docs/known-red-register.md 并限制为可跟踪的 xfail(strict=True)。",
                )
                continue
            # assert not t.is_alive()：同函数里有 join(timeout=...)，且线程体卡在无超时阻塞读
            test_src = ""
            try:
                test_src = ast.get_source_segment(unit.text, node.test) or ""
            except Exception:
                pass
            if "is_alive" not in test_src:
                continue
            fn = fn_nodes.get(node.lineno)
            if fn is None:
                continue
            joined = False
            joins: List[ast.Call] = []
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and _call_name(n) == "join":
                    joins.append(n)
                    joined = True
            if not joined:
                continue
            target = ""
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and _call_name(n) == "Thread":
                    t = _kw(n, "target")
                    if isinstance(t, ast.Name):
                        target = t.id
                    elif isinstance(t, ast.Attribute):
                        # target=self._run：方法常以属性形式传入，不能只看裸名字。
                        target = t.attr
            if target and _thread_target_blocks(tree, target):
                _add(
                    findings, "A05", unit, node.lineno,
                    f"线程 {target} 会永久阻塞在无超时的读调用上，join 之后断言「已退出」永远不可能成立。",
                    "断言应针对可终止的条件（如超时返回/取消标志），或把测试登记为已知红。",
                )
    return findings


# ────────────────────────────── A06 ──────────────────────────────


def scan_a06(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not _is_test_path(unit.path):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            # xfail 不 strict = 变绿也不会有人知道
            if isinstance(node, ast.Call) and _call_name(node) == "xfail":
                strict = _kw(node, "strict")
                if strict is None or (isinstance(strict, ast.Constant) and not strict.value):
                    _add(
                        findings, "A06", unit, node.lineno,
                        "xfail 未声明 strict=True：用例一旦意外变绿会变成 XPASS 而不是失败，"
                        "「反证」实际上没人守。",
                        "改为 xfail(strict=True)，或登记进 known-red-register 并保留 strict。",
                    )
            # 吞掉断言异常
            if isinstance(node, ast.Try):
                has_assert = any(isinstance(n, ast.Assert) for stmt in node.body for n in ast.walk(stmt))
                for handler in node.handlers:
                    catches_assert = _broad_except(handler) or (
                        isinstance(handler.type, ast.Name) and handler.type.id == "AssertionError"
                    )
                    if has_assert and catches_assert and _silent_handler(handler):
                        _add(
                            findings, "A06", unit, handler.lineno,
                            "try 块内有 assert，异常被静默吞掉：断言失败不会让用例变红。",
                            "删除该 try/except，或只捕获与被测行为无关的具体异常类型。",
                        )
            # 用 skip 当逃生门
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    has_skip = any(
                        isinstance(n, ast.Call) and _call_name(n) == "skip"
                        for stmt in handler.body for n in ast.walk(stmt)
                    )
                    if has_skip and _broad_except(handler):
                        _add(
                            findings, "A06", unit, handler.lineno,
                            "在宽泛 except 里 pytest.skip：任何实现错误都会被伪装成「环境不凑巧」。",
                            "先断言具体异常/依赖探测结果，只有明确的外部缺失才允许 skip。",
                        )
            # 测试函数开头无条件 skip/return，后面还有断言 = 死断言
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                body = list(node.body)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    body = body[1:]
                if body and isinstance(body[0], ast.Return):
                    has_assert = any(isinstance(n, ast.Assert) for n in ast.walk(node))
                    if has_assert:
                        _add(
                            findings, "A06", unit, node.lineno,
                            "测试函数第一条语句就无条件 return，后面的断言永远不会执行。",
                            "删除死断言，或改成立即失败并说明为什么不能测。",
                        )
    return findings


# ────────────────────────────── A07 ──────────────────────────────

ACTION_METHODS = {
    "set_frequency", "set_center_freq", "set_gain", "set_sample_rate",
    "set_bandwidth", "set_agc", "set_antenna",
}


def _literal_number(node: Optional[ast.AST]) -> Optional[float]:
    """能从语法树直接读出的数值：字面量或带正负号的字面量。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _literal_number(node.operand)
        if inner is not None:
            return -inner if isinstance(node.op, ast.USub) else inner
    return None


def _module_constants(tree: ast.Module) -> Dict[str, Optional[float]]:
    """模块级赋值：名字 → 字面量值；运行时算出来的记 None（不可静态确认）。

    只认模块顶层的直接赋值。`FREQ_MAX = 6e9` 可确认；`FREQ_MAX = 6e9 * 1.0`
    在语法树里是 BinOp，无法确认——宁可报红并在理由里说明，也不默认放行。
    """
    out: Dict[str, Optional[float]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = _literal_number(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = _literal_number(node.value)
    return out


def _module_path_of(src: str, node: ast.ImportFrom) -> str:
    """把 `from ... import` 解析成仓内相对路径（解析不出就是空串）。"""
    if node.level:
        base = src.split("/")[:-1]
        parts = base[: max(len(base) - (node.level - 1), 0)]
        if node.module:
            parts = parts + node.module.split(".")
    else:
        parts = (node.module or "").split(".") if node.module else []
    return "/".join(p for p in parts if p) + ".py"


def _imported_constants(
    unit: Unit, tree: ast.Module, consts: Dict[str, Dict[str, Optional[float]]],
) -> Tuple[Dict[str, Optional[float]], Dict[str, str]]:
    """两个映射：`from x import FREQ_MAX` 的常量值；`import x as c` 的模块别名。

    模块别名用于解析 `constants.FREQ_MAX_HZ` 这种属性引用——那在 AST 里是
    Attribute 而不是 Name，只认 Name 会让「用常量」的写法反而判红。
    """
    values: Dict[str, Optional[float]] = {}
    modules: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            path = _module_path_of(unit.path, node)
            src = consts.get(path)
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                sub = path[:-3] + "/" + alias.name + ".py" if path.endswith(".py") else ""
                if sub in consts:
                    modules[name] = sub
                elif src is not None and alias.name in src:
                    values[name] = src[alias.name]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                path = alias.name.replace(".", "/") + ".py"
                if path in consts:
                    modules[alias.asname or alias.name] = path
    return values, modules


def _constant_state(
    node: ast.AST, arg_names: Set[str], values: Dict[str, Optional[float]],
    modules: Dict[str, str], consts: Dict[str, Dict[str, Optional[float]]],
) -> str:
    """判断条件里的引用能不能静态确认是数值常量：ok / unknown / ''（与常量无关）。"""
    if isinstance(node, ast.Name):
        if node.id in arg_names or node.id in {"True", "False", "None"}:
            return ""
        if node.id in values:
            return "ok" if values[node.id] is not None else "unknown"
        # 只对「看起来是常量」的名字报不可确认，避免把 self / 局部变量也写进理由。
        return "unknown" if re.match(r"^[A-Z][A-Z0-9_]{2,}$", node.id) else ""
    if isinstance(node, ast.Attribute):
        text = _attr_text(node)
        mod, _, attr = text.rpartition(".")
        path = modules.get(mod)
        if path:
            table = consts.get(path, {})
            if attr in table:
                return "ok" if table[attr] is not None else "unknown"
            return "unknown"
        return "unknown" if re.match(r"^[A-Z][A-Z0-9_]{2,}$", attr) else ""
    return ""


def _arg_names(call: ast.Call) -> Set[str]:
    """调用实参里被传进去的变量名。

    `set_frequency(float(center_hz))` 与 `set_frequency(center_hz)` 是同一个值，
    只认顶层 Name 会让前者永远找不到对应的守卫（scanner_panel.py:119 就是这种形态）。
    """
    out: Set[str] = set()
    for arg in call.args:
        node: ast.AST = arg
        while isinstance(node, ast.Call) and _call_name(node) in {"float", "int", "round", "abs"} \
                and node.args:
            node = node.args[0]
        if isinstance(node, ast.Name):
            out.add(node.id)
    return out


def _guards_call_arg(
    unit: Unit, fn: ast.AST, call: ast.Call,
    consts: Dict[str, Dict[str, Optional[float]]],
) -> Tuple[bool, str]:
    """守卫必须作用在这条调用传进去的那个值上，才算「这条路径加了约束」。

    返回 (是否加了约束, 无法静态确认的表达式)。上界写成数字字面量、
    同模块模块级常量、或 `from 模块 import 常量` / `模块.常量` 都算加了；
    解析不出来的（运行时计算、参数传进来的阈值）仍按未加约束处理，
    并把那个表达式写进理由，避免「写魔数才能变绿」把正解堵死。
    """
    tree = parse(unit)
    if tree is None:
        return False, ""
    values, modules = _imported_constants(unit, tree, consts)
    # 同模块的模块级常量与导入来的常量同等对待：`LIMIT_HZ = 6e9` 就在
    # 本文件顶部，解析不出它会让「用常量」这条正解永远红着。
    values.update(consts.get(unit.path, {}))
    arg_names = _arg_names(call)
    if not arg_names:
        return False, ""
    unknown = ""
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        tested = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if not (tested & arg_names):
            continue
        if any(isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
               and not isinstance(n.value, bool) for n in ast.walk(node.test)):
            return True, ""
        for n in ast.walk(node.test):
            state = _constant_state(n, arg_names, values, modules, consts)
            if state == "ok":
                return True, ""
            if state == "unknown" and not unknown:
                unknown = ast.get_source_segment(unit.text, n) or _attr_text(n) or "?"
    return False, unknown


#: 同名但不同职：控件也有 set_frequency()，那不是「真正执行调谐的路径」。
#: 判据不看接收者叫什么，而看它是不是在模块里被绑定成 Qt 控件构造出来的对象：
#: `self._x = QLabel(...)` 之后，`self._x` 叫什么名字都是展示；反过来，把
#: `self._vfo` 改名叫 `self._vfo_panel` 也不会变成展示。Qt 类名是外部 API，
#: 本仓改不动，因此可以作为稳定依据。
QT_CTOR = re.compile(r"^(?:QtWidgets\.)?Q[A-Z]\w*$")


def _widget_attrs(tree: ast.Module) -> Set[str]:
    """模块内被绑定成 Qt 控件构造结果的名字（属性名/局部名）。"""
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            func = value.func
            fname = _attr_text(func)
            if not QT_CTOR.match(fname):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute):
                    names.add(t.attr)
                elif isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _receiver_text(unit: Unit, call: ast.Call) -> str:
    if not isinstance(call.func, ast.Attribute):
        return ""
    try:
        return ast.get_source_segment(unit.text, call.func.value) or ""
    except Exception:
        return ""


def _parent_map(tree: ast.AST) -> Dict[int, ast.AST]:
    parents: Dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _result_used(call: ast.Call, parents: Dict[int, ast.AST]) -> bool:
    """调用结果是否被消费（赋值/返回/判断/比较/布尔转换）。

    被消费说明调用方至少看得到「后端夹值后是否生效」；只有当结果被丢弃时，
    「约束只在入口、动作处没有」才真正变成静默不一致。
    """
    parent = parents.get(id(call))
    if parent is None:
        return False
    if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return,
                           ast.If, ast.While, ast.Assert, ast.Compare, ast.BoolOp,
                           ast.IfExp, ast.UnaryOp)):
        return True
    if isinstance(parent, ast.Call) and _call_name(parent) in {"bool", "int", "float"}:
        return True
    return False


def _receiver_is_display(unit: Unit, call: ast.Call, widgets: Set[str]) -> bool:
    """接收者是不是控件实例：看它是否被绑定成 Qt 控件构造结果，不看它叫什么。"""
    if not isinstance(call.func, ast.Attribute):
        return False
    recv = call.func.value
    while isinstance(recv, ast.Subscript):
        recv = recv.value
    if isinstance(recv, ast.Attribute):
        return recv.attr in widgets
    if isinstance(recv, ast.Name):
        # `self` / 参数 / 局部名：只有被绑定成控件构造结果的才算展示。
        return recv.id in widgets
    return False


def scan_a07(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    guarded: Dict[str, Set[str]] = {m: set() for m in ACTION_METHODS}
    unguarded: Dict[str, List[Tuple[Unit, ast.Call]]] = {m: [] for m in ACTION_METHODS}
    widgets: Dict[str, Set[str]] = {}
    # 上界写成常量（constants.FREQ_MAX_HZ）是正解，不能因为「不是数字字面量」
    # 反而判红；这里先扫出所有模块的模块级数值常量，供守卫解析。
    consts: Dict[str, Dict[str, Optional[float]]] = {}
    unconfirmed: Dict[int, str] = {}
    for unit in units:
        if not (unit.path.startswith("desktop/") or unit.path.startswith("mbdsdr_ai/")):
            continue
        tree = parse(unit)
        if tree is not None:
            consts[unit.path] = _module_constants(tree)
    for unit in units:
        if not (unit.path.startswith("desktop/") or unit.path.startswith("mbdsdr_ai/")):
            continue
        if _is_test_path(unit.path):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        widgets[unit.path] = _widget_attrs(tree)
        fn_nodes = enclosing_functions(tree)
        parents = _parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name not in ACTION_METHODS:
                continue
            fn = fn_nodes.get(node.lineno)
            if fn is None or getattr(fn, "name", "") == name:
                continue
            guarded_ok, unresolved = _guards_call_arg(unit, fn, node, consts)
            if guarded_ok:
                guarded[name].add(unit.path)
            elif not _result_used(node, parents):
                unguarded[name].append((unit, node))
                if unresolved:
                    unconfirmed[id(node)] = unresolved
    for method, sites in unguarded.items():
        if not guarded[method]:
            continue
        example = sorted(guarded[method])[0]
        seen_files: Set[str] = set()
        for unit, node in sites:
            if unit.path in seen_files:
                continue
            # 约束的实现就在这个文件里：它自己调用自己不算「绕开约束」。
            if unit.path in guarded[method]:
                continue
            if _receiver_is_display(unit, node, widgets.get(unit.path, set())):
                note_skip("A07", "接收者是模块内 Qt 控件构造出来的对象，视为展示而非执行")
                continue
            seen_files.add(unit.path)
            # 同一文件同一方法的多处：只报第一条会让「修一处、浮一处」永远看不到
            # 全貌。计数口径仍是一文件一方法一条（棘轮基线不动），但把其余同形态
            # 的行号全部写进理由——读者不用再跑一遍才挤出下一个。
            siblings = sorted(
                n.lineno for u, n in sites
                if u.path == unit.path and n is not node
                and not _receiver_is_display(u, n, widgets.get(u.path, set()))
            )
            tail = ""
            if siblings:
                tail = (f"同一文件同一方法另有 {len(siblings)} 处同形态未展开："
                        f"L{', L'.join(str(n) for n in siblings)}；"
                        "修掉本处只会让它换一处冒出来，请一并看。")
            unresolved = unconfirmed.get(id(node), "")
            if unresolved:
                tail += (f"（守卫里的 {unresolved} 无法静态确认是数值常量，"
                         "按未加约束处理）")
            _add(
                findings, "A07", unit, node.lineno,
                f"{method} 的数值前置条件只出现在 {example} 等入口，{unit.path} 这条调用路径没有同一约束；"
                "真正执行动作的地方也没有，所以这条路径能绕过去。" + tail,
                "把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。",
            )
    return findings


# ────────────────────────────── A08 ──────────────────────────────


def _fold_literals(stmts: List[ast.stmt], unit: Unit, findings: List[Finding]) -> None:
    """只折叠「纯字面量条件」：`if False:`、`while 0:`、`if not True:`。

    不做局部变量传播——`freq = 0.0; try: freq = ...; if freq <= 0:` 这种写法里
    变量值受控制流影响，静态折叠必然误报。守卫是否可成立，只有在条件完全
    由字面量构成时才是可判定的。
    """
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            ok, value = const_eval(stmt.test, {})
            if ok:
                _add(
                    findings, "A08", unit, stmt.lineno,
                    f"分支条件只由字面量组成，折叠结果为 {bool(value)}；这条守卫要么永远执行、"
                    "要么永远不执行，它看起来在防一件事，实际没防。",
                    "确认判的是不是该判的对象；条件应来自运行时可得、可能变化的可用性状态。",
                    "review",
                )
            _fold_literals(stmt.body, unit, findings)
            _fold_literals(stmt.orelse, unit, findings)
        elif isinstance(stmt, ast.While):
            ok, value = const_eval(stmt.test, {})
            if ok and not value:
                _add(
                    findings, "A08", unit, stmt.lineno,
                    "while 条件只由字面量组成且恒假，循环体永远不会执行。",
                    "删除死循环或修正条件。",
                    "review",
                )
            _fold_literals(stmt.body, unit, findings)
            _fold_literals(stmt.orelse, unit, findings)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.Try)):
            for field in ("body", "orelse", "finalbody"):
                _fold_literals(getattr(stmt, field, []), unit, findings)


def scan_a08(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not (unit.path.startswith("desktop/") or unit.path.startswith("mbdsdr_ai/")):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _fold_literals(node.body, unit, findings)
    return findings


# ────────────────────────────── A09 ──────────────────────────────

CONCRETE_ANCHOR = re.compile(
    r"无网络|离线|未配置|环境变量|未插|无真实|无可用|0bda|udev|offscreen|屏幕|平台|"
    r"不存在|未随仓|缺失|fixture|样本|\.bsp|\.wav|\.json|\.csv|\.bin|SHA|哈希|目录|文件|"
    r"PortAudio|PulseAudio|ALSA|Windows|Linux|macOS|rtl_tcp|satdump|skyfield|de421|"
    r"Stellarium|HYG|TLE|GPU|驱动|权限|音频设备|输出设备|串口|硬件|standards/|外部",
    re.I,
)
VAGUE_SKIP = re.compile(r"不可构造|无法构造|构造失败|依赖缺失|导入失败|ImportError|ModuleNotFound|not available|不可用", re.I)


def _skip_reason(unit: Unit, node: ast.Call) -> str:
    for key in ("reason", "msg"):
        val = _kw(node, key)
        if val is not None:
            return _literal_text(unit, val).strip()
    if node.args:
        return _literal_text(unit, node.args[0]).strip()
    return ""


def _repo_symbols(units: List[Unit]) -> Set[str]:
    """仓内**生产代码**里的函数/类名（不含测试与 conftest）。

    只收生产代码：测试文件里叫 `f`、`helper` 的局部替身不是「环境缺失」的
    判据来源；拿它们去匹配理由文本只会制造噪声。
    """
    names: Set[str] = set()
    for unit in units:
        if not unit.path.endswith(".py"):
            continue
        if _is_test_path(unit.path) or unit.path.endswith("conftest.py"):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
    return names


def _covered_elsewhere(fn: ast.AST, tree: ast.Module, prod: Set[str]) -> bool:
    """跳过之后，同一模块里还有没有别的用例在断言同一个行为。

    判据看调用图：别的测试函数至少有一条 assert，且它调用的本仓函数/类
    与这个被跳过用例重叠。重叠则为「行为仍有覆盖」，不按删用例处理。
    """
    mine = {_call_name(n) for n in ast.walk(fn)
            if isinstance(n, ast.Call) and _call_name(n) in prod}
    if not mine:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node is fn:
            continue
        if not (node.name.startswith("test") or "_test" in node.name):
            continue
        if not any(isinstance(n, ast.Assert) for n in ast.walk(node)):
            continue
        theirs = {_call_name(n) for n in ast.walk(node)
                  if isinstance(n, ast.Call) and _call_name(n) in prod}
        if mine & theirs:
            return True
    return False


def scan_a09(units: List[Unit]) -> List[Finding]:
    """判据看 skip 的**形态**，不看理由里有没有某个词。

    三条形态判据：
    1. skip 在宽泛 except 里 —— 任何实现错误都能伪装成「环境不凑巧」；
    2. 无条件 skip 且跳过之前没有断言 —— 这个用例等于被删掉；
    3. 第 2 条只在同模块找不到覆盖同一行为的其他用例时才报。
    """
    findings: List[Finding] = []
    prod = _repo_symbols(units)
    for unit in units:
        if not _is_test_path(unit.path):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        fn_nodes = enclosing_functions(tree)
        parents: Dict[int, ast.AST] = {
            id(n): parent for n, parent in walk_with_parents(tree) if parent is not None
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name not in {"skip", "skipif", "SkipTest"}:
                continue
            reason = _skip_reason(unit, node)
            fn = fn_nodes.get(node.lineno)
            chain = []
            cur = parents.get(id(node))
            while cur is not None:
                chain.append(cur)
                cur = parents.get(id(cur))
            in_broad_except = any(
                isinstance(n, ast.ExceptHandler) and _broad_except(n) for n in chain
            )
            unconditional = not any(isinstance(n, (ast.If, ast.While, ast.Try, ast.For,
                                                   ast.AsyncFor, ast.With, ast.AsyncWith)) for n in chain)
            # 只看 skip 之前的断言：跳过之后的断言永远不会执行。
            has_assert = fn is not None and any(
                isinstance(n, ast.Assert) and n.lineno < node.lineno for n in ast.walk(fn))
            if in_broad_except:
                _add(
                    findings, "A09", unit, node.lineno,
                    "在宽泛 except 里 skip：任何实现错误都会被伪装成「环境不凑巧」。",
                    "先断言探测结果，只有明确的外部缺失才允许 skip；本仓缺陷一律改红。",
                )
                continue
            if unconditional and not has_assert:
                if fn is not None and _covered_elsewhere(fn, tree, prod):
                    note_skip("A09", "无条件 skip 但同模块另有带断言的用例覆盖同一行为")
                    continue
                _add(
                    findings, "A09", unit, node.lineno,
                    f"无条件 skip（理由「{reason[:40] or '空'}」）：函数体里没有任何断言，"
                    "这个用例等于被删掉，却仍显示为「跳过」。",
                    "改为按探测结果条件 skip，或在 skip 前保留至少一条断言。",
                )
                continue
            note_skip("A09", "skip 有理由且形态合法（条件/断言在前/只提仓外依赖），需人工复核")
    return findings


# ────────────────────────────── A10 ──────────────────────────────

HASH_IN_BACKTICKS = re.compile(r"`([0-9a-f]{7,40})`")
PLACEHOLDERS = {"abc1234", "0123456789", "deadbeef", "0000000", "1234567"}
#: 只有当 hash 前紧邻「提交 / commit / HEAD」时，才算「按提交引用」。
#: 同一行后文出现「提交」不算——那句可能说的是另一件事（例如「接管提交 diff」）。
A10_AS_COMMIT = re.compile(r"(?:提交|commit|HEAD)[\s:：]*$", re.I)
#: 「不是提交」的说明必须紧贴这个 hash（前后各一句内），不能靠整行任意位置的一个词来关掉检查。
#: 窗口从「整行」收到 ±24 字符后，这些词就不再是开关，而是就近的语境证据。
A10_NON_COMMIT_NEAR = re.compile(
    r"会话|session|blob|tree|字节|byte|打包|多项式|系数|二进制|0b[01]{6,}",
    re.I,
)


def _fenced_lines(text: str) -> Set[int]:
    """fenced code block 内的行号。示例只认「在代码块里」，不认正文里写「例如」。"""
    inside: Set[int] = set()
    fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            inside.add(i)
            continue
        if fence:
            inside.add(i)
    return inside


def _looks_like_commit(text: str) -> bool:
    if len(text) < 7 or len(text) > 40:
        return False
    if not re.fullmatch(r"[0-9a-f]+", text):
        return False
    if text.startswith(("0b", "0x")):
        return False
    if text in PLACEHOLDERS:
        return False
    if not re.search(r"[a-f]", text):
        return False
    if set(text) <= {"0", "1"}:
        return False
    return True


def scan_a10(repo: Repo, units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    occurrences: Dict[str, List[Tuple[Unit, int, str]]] = {}
    for unit in units:
        if not unit.path.startswith("docs/"):
            continue
        if unit.path.startswith("docs/learn/"):
            # learn/ 引用的是外部项目的历史提交，不属于本仓证据。
            continue
        fenced = _fenced_lines(unit.text)
        for lineno, line in enumerate(unit.text.splitlines(), 1):
            for m in HASH_IN_BACKTICKS.finditer(line):
                token = m.group(1)
                if not _looks_like_commit(token):
                    continue
                if lineno in fenced:
                    note_skip("A10", "hash 在代码块里，按示例处理")
                    continue
                near = line[max(0, m.start() - 24): m.end() + 24]
                if A10_NON_COMMIT_NEAR.search(near):
                    note_skip("A10", "hash 紧邻处标明不是提交（会话/blob/二进制等）")
                    continue
                if line[: m.start()].rstrip().endswith("@"):
                    # `repo @ <hash>` 指向上游版本，不是本仓提交。
                    note_skip("A10", "`repo @ hash` 指向上游版本")
                    continue
                occurrences.setdefault(token, []).append((unit, lineno, line))
    types = repo.batch_object_types(list(occurrences))
    for token, items in occurrences.items():
        typ = types.get(token, "missing")
        for unit, lineno, line in items:
            if typ == "missing":
                _add(
                    findings, "A10", unit, lineno,
                    f"文档引用的提交 `{token}` 在本仓不存在；基于它的后续结论无法复核。",
                    "改为真实提交号；未落地的改动标注「未落地」，不要用提交口吻引用。",
                )
            elif typ != "commit" and A10_AS_COMMIT.search(line[: m.start()]):
                _add(
                    findings, "A10", unit, lineno,
                    f"`{token}` 在本仓是 {typ}，不是提交，但文中按提交引用。",
                    "引用提交号，或明确标注这是 blob/tree 标识。",
                )
    return findings


# ────────────────────────────── A11 ──────────────────────────────

def scan_a11(repo: Repo, units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not unit.path.startswith("docs/"):
            continue
        # 目录不再是开关：docs/learn、docs/incidents 这些「历史快照目录」里的
        # 绿色声明同样要对当前树负责。确实不该计入的，按文件在
        # docs/axioms-waivers.json 显式登记（理由 + 到期 + 批准人），
        # 由 apply_waivers 统一判定；过期即重新计入违规。
        lines = unit.text.splitlines()
        no_hash_reported = False
        for i, line in enumerate(lines):
            if not GREEN_CLAIM.search(line):
                continue
            window = "\n".join(lines[max(0, i - 2): min(len(lines), i + 3)])
            if EXIT_CRITERION.search(line):
                note_skip("A11", "写的是转绿/验收判据，不是已绿的声明")
                continue
            # 判据看「有没有可复核的东西」：命令、提交号、日期至少要有一样。
            # 「历史 / 已修 / 快照」这类词本身不是证据，必须同时给出日期，
            # 否则读者不知道该重跑哪一版。
            has_date = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", line) is not None
            has_command = bool(re.search(r"`(?:python|pytest|git|pwsh|powershell|scripts/)[^`]*`", line, re.I))
            if HISTORY_MARKER.search(line):
                if has_date:
                    note_skip("A11", "标注为历史快照且带日期，可定位到某一版")
                    continue
                _add(
                    findings, "A11", unit, i + 1,
                    "标注为历史/已修，但没有日期，读者无法判断它说的是哪一版。",
                    "补上日期（YYYY-MM-DD）或提交号；否则按当前树的声明处理。",
                    "review",
                )
                continue
            match = HASH_IN_BACKTICKS.search(window)
            if not match:
                if has_command or has_date:
                    note_skip("A11", "绿声明带命令/日期，可复核（未绑定提交号）")
                    continue
                if not no_hash_reported:
                    no_hash_reported = True
                    _add(
                        findings, "A11", unit, i + 1,
                        "「全绿/全部通过」的声明没有命令、提交号、日期中的任何一项，读者无法复核。",
                        "补上实跑命令与所在提交号（或日期），或标注为历史快照。",
                        "review",
                    )
                continue
            token = match.group(1)
            if not _looks_like_commit(token):
                continue
            typ, _ = repo.describe_commit(token)
            if typ != "commit":
                _add(
                    findings, "A11", unit, i + 1,
                    f"绿色声明绑定的 `{token}` 不是本仓提交，声明无法归属到任何一棵树。",
                    "改用实跑所在的提交号。",
                )
                continue
            behind = repo.commits_behind(token)
            if behind is not None and behind > 50:
                _add(
                    findings, "A11", unit, i + 1,
                    f"绿色声明绑定的是 {behind} 个提交之前的树；它证明不了当前树的任何事。",
                    "在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。",
                    "review",
                )
    return findings


# ────────────────────────────── A12 ──────────────────────────────

def _normalized(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _shape_key(fn: ast.AST) -> str:
    """函数体的结构形状：抹掉标识符与常量值的差异，只留语句/表达式骨架。

    判据不再要求「函数名以 fmt/format/render 开头」——那只是给重命名留后门。
    两份实现只要骨架相同、产出单位/精度不同，就是同一个值有两种口径。
    """
    parts: List[str] = []
    for n in ast.walk(fn):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn:
            continue
        kind = type(n).__name__
        if isinstance(n, ast.Attribute):
            kind += ":" + n.attr
        elif isinstance(n, ast.Call):
            kind += ":" + (_call_name(n) or "-")
        elif isinstance(n, ast.Constant):
            kind += ":" + type(n.value).__name__
        parts.append(kind)
    return "|".join(parts)


def _unit_literals(fn: ast.AST) -> List[str]:
    """函数体里出现的单位/精度口径（`MHz`、`.6f` 这类）。"""
    out: List[str] = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and UNIT_RE.search(n.value):
            out.append(" ".join(n.value.split()))
        elif isinstance(n, ast.FormattedValue) and isinstance(n.format_spec, ast.JoinedStr):
            spec = "".join(v.value for v in n.format_spec.values
                           if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if spec:
                out.append("{" + spec + "}")
    return out


def scan_a12(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    groups: Dict[str, List[Tuple[Unit, ast.AST, List[str], str]]] = {}
    for unit in units:
        if not (unit.path.startswith("desktop/") or unit.path.startswith("mbdsdr_ai/")):
            continue
        if unit.path.endswith("readouts.py") or unit.path.endswith("constants.py"):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if len(node.body) < 4:
                continue
            units_seen = _unit_literals(node)
            if not units_seen:
                continue
            groups.setdefault(_shape_key(node), []).append(
                (unit, node, units_seen, _normalized(node)))
    for items in groups.values():
        if len(items) < 2:
            continue
        modules = {u.path for u, _, _, _ in items}
        if len(modules) < 2:
            continue
        literal_sets = {tuple(sorted(lits)) for _, _, lits, _ in items}
        if len(literal_sets) < 2:
            # 骨架相同、口径也相同：是重复实现，但不是「给出不同结论」，不归 A12。
            continue
        items.sort(key=lambda it: (it[0].path, it[1].lineno))
        first = items[0]
        for unit, node, lits, body in items[1:]:
            if tuple(sorted(lits)) == tuple(sorted(first[2])):
                continue
            _add(
                findings, "A12", unit, node.lineno,
                f"与 {first[0].path}:{first[1].lineno} 的 `{first[1].name}` 结构相同但口径不同"
                f"（{'/'.join(lits[:2])} vs {'/'.join(first[2][:2])}）；"
                "同一语义在两处各写一遍，迟早会对同一个值给出不同结论。",
                "合并到唯一实现（读数类统一收进 desktop/readouts.py），删除另一份。",
            )
    return findings


# ────────────────────────────── A13 ──────────────────────────────

IDENTITY_NAME = re.compile(r"(^|_)(owner|holder|serial|device_key|device_id|identity)($|_)", re.I)
INDEX_NAMES = {"idx", "index", "i", "n"}
#: 唯一性字段：一个记录里有这些字段，说明它知道自己「是谁的、哪个进程的」，
#: 那么用它旁边那个同名字符串字段判同一就是可复用的身份。
UNIQUE_FIELDS = {"pid", "start_time", "starttime", "serial", "serial_number", "udid", "epoch"}


def _record_schemas(tree: ast.Module) -> Set[str]:
    """模块里出现过的「带唯一性字段的记录」的字段集合（dict 字面量 / 类成员）。

    判据不要求字段叫 owner/holder：只要这个记录同时带 pid/serial 这类唯一字段，
    却拿另一个字段按名字比较，就是可复用的身份。
    """
    keys: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            names = {k.value for k in node.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if names & UNIQUE_FIELDS:
                keys |= names
        elif isinstance(node, ast.ClassDef):
            members: Set[str] = set()
            for child in node.body:
                if isinstance(child, (ast.Assign, ast.AnnAssign)):
                    targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                    for t in targets:
                        if isinstance(t, ast.Name):
                            members.add(t.id)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for a in list(child.args.args) + list(child.args.kwonlyargs):
                        if isinstance(a.arg, str):
                            members.add(a.arg)
            if members & UNIQUE_FIELDS:
                keys |= members
    return keys


def _has_index_name(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id in INDEX_NAMES for n in ast.walk(node))


def _module_fields(tree: ast.Module) -> List[str]:
    """模块里实际出现的字段名：dict 字符串键 + 赋值目标名 + 函数参数名。

    只在「判据无法判定」时用于把缺口写清楚：读者要能看到检查器到底拿到了
    哪些字段，才知道该补 pid/serial，还是该确认这个字段本来就合法。
    """
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            names |= {k.value for k in node.keys
                      if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                elif isinstance(t, ast.Attribute):
                    names.add(t.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                if isinstance(a.arg, str):
                    names.add(a.arg)
    return sorted(names)


def _index_derived(value: ast.AST) -> bool:
    """身份值是否「把下标拼进了字符串」。

    只看字符串拼接/格式化，不看普通函数调用：`get_serial(i)` 是用下标去取
    唯一序列号，`"rtl-%d" % idx` 才是拿序号当身份。
    """
    for node in ast.walk(value):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.FormattedValue) and _has_index_name(part.value):
                    return True
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and _has_index_name(node.right):
            return True
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "enumerate":
                return True
            if (isinstance(node.func, ast.Name) and node.func.id == "str"
                    and node.args and _has_index_name(node.args[0])):
                return True
    return False


def scan_a13(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not (unit.path.startswith("desktop/") or unit.path.startswith("mbdsdr_ai/") or unit.path.startswith("scripts/")):
            continue
        if unit.path.startswith("scripts/repro/"):
            # 复现脚本里的 owner 是数据字段，不承担互斥判定。
            continue
        tree = parse(unit)
        if tree is None:
            continue
        schema = _record_schemas(tree)
        for node in ast.walk(tree):
            # 身份比较只看名字，不看 pid
            if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                operands = [node.left, *node.comparators]
                names = []
                for op in operands:
                    if isinstance(op, ast.Name):
                        names.append(op.id)
                    elif isinstance(op, ast.Attribute):
                        names.append(op.attr)
                    elif isinstance(op, ast.Subscript) and isinstance(op.slice, ast.Constant) \
                            and isinstance(op.slice.value, str):
                        names.append(op.slice.value)
                    elif isinstance(op, ast.Call) and isinstance(op.func, ast.Attribute):
                        names.append(op.func.attr)
                # 两边比较的是同一个字段名，且这个字段属于「带 pid/serial 的记录」，
                # 而它本身不是唯一字段 —— 这就是拿可复用的名字当身份。
                field = ""
                if len(names) >= 2 and names[0] == names[1]:
                    field = names[0]
                if not field and len(names) >= 2:
                    same = {n for n in names[1:] if n == names[0]}
                    field = names[0] if same else ""
                if not field or field in UNIQUE_FIELDS:
                    continue
                # 有记录 schema 时：只有「schema 里的名字类字段」才算违规——
                # entry_id / fingerprint / size_bytes / source 本身就是唯一或
                # 内容标识，比较它们不是违规。
                # 没有 schema 时不允许退回「owner/holder/identity 字样」判断：
                # 字样既不能证明违规，也不能证明清白。判据无法判定时必须报出来，
                # 让缺口可见（否则换个字段名就静默通过）。
                name_like = re.search(r"owner|holder|claimant|identity", field, re.I)
                if schema:
                    if field not in schema:
                        continue
                    if not re.search(r"owner|holder|claimant|identity|label|^name$|user|author",
                                     field, re.I):
                        continue
                else:
                    if not name_like:
                        continue
                    fields = _module_fields(tree)
                    shown = "、".join(fields[:12]) + ("…" if len(fields) > 12 else "")
                    _add(
                        findings, "A13-undecidable", unit, node.lineno,
                        f"{unit.path} 里找不到带 pid/serial 等唯一字段的记录，"
                        f"无法判定 `{field}` 是不是可复用的身份；"
                        f"本模块可见字段：{shown or '（无）'}。",
                        "把持有者记录改成带 pid + 进程启动时间（或设备序列号）的结构，"
                        "让判据能判定；确认该比较本来就合法时，在 docs/axioms-waivers.json "
                        "按文件登记豁免（理由 + 到期 + 批准人）。",
                        "review",
                    )
                    continue
                # 只有比较式本身带了 pid/serial 才算「用了唯一事实」；
                # 同函数别处出现 `'pid'` 字面量（比如记录定义）不能当作豁免。
                cmp_src = ""
                try:
                    cmp_src = ast.get_source_segment(unit.text, node) or ""
                except Exception:
                    pass
                if any(re.search(rf"\b{u}\b", cmp_src) for u in UNIQUE_FIELDS):
                    continue
                _add(
                    findings, "A13", unit, node.lineno,
                    "用 owner/holder 字符串相等来判断「同一个持有者」；同名但不是同一进程时会误放行。",
                    "判同一必须用 pid + 进程启动时间（或设备序列号）这类唯一且不可复用的事实。",
                )
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    # 名字不再是唯一判据：属于「带 pid/serial 的记录」的字段同样算身份。
                    if not (IDENTITY_NAME.search(target.id) or target.id in schema):
                        continue
                    if _index_derived(node.value):
                        _add(
                            findings, "A13", unit, node.lineno,
                            f"{target.id} 的身份值由枚举序号/循环下标拼成；插拔顺序一变，同一台设备就会变成另一台。",
                            "身份用序列号（缺失就明确拒绝并提示），不要用枚举序号或可改的标签兜底。",
                        )
    return findings


# ────────────────────────────── A14 ──────────────────────────────

#: 机器会读的台账：这些位置一旦漂移，闸门会指向错误代码（而不是「过期记录」）。
#: 一次性审计报告里的行号属于历史快照，不在本规则范围内。
LEDGER_DOCS = ("docs/known-red-register.md",)
LEDGER_JSON = "docs/axioms-waivers.json"

_LOC_REF = re.compile(r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py):(?P<line>\d+)(?:-\d+)?")
_IDENT_HINT = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$")


def _file_index(repo: Repo) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for dp, dn, fn in os.walk(repo.root):
        dn[:] = [d for d in dn if d not in {".git", "__pycache__", ".venv", "build", "dist"}]
        for f in fn:
            index.setdefault(f, []).append(os.path.join(dp, f))
    return index


def _resolve_module(repo: Repo, rel: str, index: Dict[str, List[str]]) -> Tuple[Optional[str], int]:
    """把台账里的路径解析成仓内真实模块。

    台账常写裸文件名（`main_window.py:3679`），它本身不是仓内路径；
    「模块不存在」按裸文件名判会全线误报。返回 (唯一解析结果, 同名候选数)。
    """
    if "/" in rel:
        return (rel if os.path.isfile(os.path.join(repo.root, rel)) else None), 1
    hits = index.get(rel, [])
    if len(hits) == 1:
        return repo.rel(hits[0]), 1
    return None, len(hits)


def _module_symbols(repo: Repo, rel: str, cache: Dict[str, Optional[Set[str]]]) -> Optional[Set[str]]:
    """目标模块的符号表（函数/类/方法/模块级名字）；文件不存在返回 None。"""
    if rel in cache:
        return cache[rel]
    path = os.path.join(repo.root, rel)
    if not os.path.isfile(path):
        cache[rel] = None
        return None
    tree = parse(Unit(rel, _read(path)))
    names: Set[str] = set()
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.add(f"{node.name}.{child.name}")
    cache[rel] = names
    return names


def scan_a14(repo: Repo, docs: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    cache: Dict[str, Optional[Set[str]]] = {}
    index = _file_index(repo)
    for unit in docs:
        if unit.path not in LEDGER_DOCS:
            continue
        for lineno, line in enumerate(unit.text.splitlines(), 1):
            for m in _LOC_REF.finditer(line):
                raw_rel = m.group("path").lstrip("./")
                rel, candidates = _resolve_module(repo, raw_rel, index)
                hints: List[str] = []
                calls: List[str] = []
                for token in re.findall(r"`([^`]+)`", line):
                    text = token.strip()
                    if "/" in text or ":" in text or text.endswith(".py"):
                        continue
                    call = re.fullmatch(r"([A-Za-z_]\w*)\(\)", text)
                    if call:
                        hints.append(call.group(1))
                        calls.append(call.group(1))
                    elif _IDENT_HINT.match(text):
                        hints.append(text)
                if rel is None:
                    if candidates > 1:
                        _add(
                            findings, "A14", unit, lineno,
                            f"位置引用只写裸文件名 `{raw_rel}`，仓内有 {candidates} 个同名模块，无法确定指向哪一个。",
                            "写成仓内相对路径（如 desktop/xxx.py），并补上符号名。",
                            "review",
                        )
                    else:
                        _add(
                            findings, "A14", unit, lineno, f"位置引用指向不存在的模块 {raw_rel}",
                            "改为真实路径；文件迁移后同步更新台账。",
                        )
                    continue
                symbols = _module_symbols(repo, rel, cache)
                if symbols is None:
                    _add(
                        findings, "A14", unit, lineno, f"位置引用指向不存在的模块 {rel}",
                        "改为真实路径；文件迁移后同步更新台账。",
                    )
                    continue
                if not hints:
                    _add(
                        findings, "A14", unit, lineno,
                        f"位置引用只有 `{m.group(0)}`：行号会随无关改动漂移，"
                        "同一天内已发生多次「改十几行、豁免全部失效」。",
                        "写成 `模块` 的 `函数名`（或一句可搜到的判断描述）；行号可留作辅助，但不能是唯一定位。",
                        "review",
                    )
                elif calls and not any(name.split(".")[-1] in symbols for name in calls):
                    _add(
                        findings, "A14", unit, lineno,
                        f"位置引用里的符号 {', '.join(calls)} 在 {rel} 的 AST 符号表里不存在（已改名/删除）。",
                        "改成当前真实存在的函数/类名；符号不存在说明这条定位已经失效。",
                    )
    # 豁免文件：位置只允许「模块 + 符号/描述」，带行号即定位不稳定。
    wpath = os.path.join(repo.root, LEDGER_JSON)
    if os.path.isfile(wpath):
        try:
            data = json.loads(_read(wpath))
        except Exception:
            data = {}
        for i, item in enumerate((data.get("waivers") or []) if isinstance(data, dict) else []):
            rel = str(item.get("path", ""))
            line_field = item.get("line", item.get("line_start"))
            inline = bool(re.search(r":\d+$", rel))
            if line_field is not None or inline:
                findings.append(Finding(
                    "A14", LEDGER_JSON, 0, f"waivers[{i}] 用行号定位：{rel}:{line_field or ''}",
                    "豁免表是机器会读的台账；用行号定位时，目标文件一被改动，豁免就指向别的代码或直接失效。",
                    "改为模块 + 符号（函数名）或一句可搜到、稳定的判断描述；行号最多作为附加说明。",
                    "review",
                ))
            symbol = str(item.get("symbol", "") or "")
            if symbol:
                symbols = _module_symbols(repo, rel, cache)
                if symbols is None or symbol not in symbols:
                    findings.append(Finding(
                        "A14", LEDGER_JSON, 0, f"waivers[{i}] 符号 {symbol} 在 {rel} 中不存在",
                        "豁免指向的符号已不存在，这条豁免已经落到空处（既没保护代码，也没暴露问题）。",
                        "更新为当前符号；若问题已修，删除该豁免。",
                    ))
    return findings


# ────────────────────────────── 公理登记 ──────────────────────────────


def build_axioms(repo: Repo) -> List[Axiom]:
    py_units = repo.py_units()
    test_units = repo.py_units(("tests",))
    docs = repo.doc_units()

    def a01(units: List[Unit]) -> List[Finding]:
        return scan_a01(units)

    def a02(units: List[Unit]) -> List[Finding]:
        return scan_a02(units)

    def a03(units: List[Unit]) -> List[Finding]:
        return scan_a03(units)

    def a04(units: List[Unit]) -> List[Finding]:
        return scan_a04(units)

    def a05(units: List[Unit]) -> List[Finding]:
        return scan_a05(units)

    def a06(units: List[Unit]) -> List[Finding]:
        return scan_a06(units)

    def a07(units: List[Unit]) -> List[Finding]:
        return scan_a07(units)

    def a08(units: List[Unit]) -> List[Finding]:
        return scan_a08(units)

    def a09(units: List[Unit]) -> List[Finding]:
        return scan_a09(units)

    def a10(units: List[Unit]) -> List[Finding]:
        return scan_a10(repo, units)

    def a11(units: List[Unit]) -> List[Finding]:
        return scan_a11(repo, units)

    def a12(units: List[Unit]) -> List[Finding]:
        return scan_a12(units)

    def a13(units: List[Unit]) -> List[Finding]:
        return scan_a13(units)

    def a14(units: List[Unit]) -> List[Finding]:
        return scan_a14(repo, units)

    return [
        Axiom(
            "A01", "用户可见事实只有一个产生点",
            "同一个用户可见事实（频率/带宽/增益/采样率等读数）必须由唯一函数产生；"
            "任何面板都不得就地拼装另一套口径。",
            "扫描 desktop/ 下 readouts.py/constants.py 之外的 f-string：字面量含单位且含格式化值即违规。",
            ["desktop/main_window.py 多处就地拼 MHz/kHz（本轮读数口径分裂的根源）",
             "desktop/readouts.py 建立后 tests/test_copy_units_audit.py 才把关键读数钉住"],
            "readout_single_source", "desktop/main_window.py 任一单位 f-string",
            3, 3, 2, a01,
        ),
        Axiom(
            "A02", "读数保真：显示不得把值变成另一个值",
            "显示精度必须覆盖该量的实际分辨率；截断后读者看到的值不得与真实值相差超过一个末位。",
            "AST 检查数值 format_spec：MHz 少于 6 位小数（频率）或 kHz 0 位小数（带宽）即违规。",
            ["desktop/readouts.py 记录的 2400 Hz 被显示成 2 kHz（误差 400 Hz）",
             "desktop/spectrum_widget.py 与 bookmark_panel.py 用 3–4 位小数 MHz（1 kHz 量化）"],
            "lossy_readout", "desktop/bookmark_panel.py:342 的 `.4f} MHz`",
            3, 3, 2, a02,
        ),
        Axiom(
            "A03", "降级必须可见且可归因",
            "任何失败后的降级路径都必须记录原因并把降级状态暴露给调用方；"
            "「读不到」不得与「本来就是空」不可区分。",
            "AST 检查持久化读取外侧的宽泛 except：处理器只 pass/return None/default 且无日志即违规。",
            ["mbdsdr_ai/conversation.py 读损坏会话静默回空",
             "desktop/settings.py 等 9 处在 c792b416 才补上损坏原因记录"],
            "silent_degrade", "mbdsdr_ai/conversation.py 的 except Exception: pass",
            3, 3, 2, a03,
        ),
        Axiom(
            "A04", "不可读的输入不得被静默覆盖",
            "读失败时不得用默认值覆写原件；必须先留存证据（备份 + 原因），否则下次保存会永久丢数据。",
            "AST 检查同时具备「宽泛 except 静默读」与「json.dump 覆写」且无 corrupt/backup 机制的模块。",
            ["mbdsdr_ai/conversation.py 读失败后仍会覆写会话文件",
             "c792b416 之前的 settings/bookmark/layout 读损坏即回默认并覆盖"],
            "corrupt_no_clobber", "c792b416^:desktop/settings.py",
            3, 2, 2, a04,
        ),
        Axiom(
            "A05", "每个守卫必须有可成立的通过态",
            "断言/守卫的通过条件必须在当前实现下物理上可达成；不可能变绿的断言不得写成断言。",
            "AST 检查常量假断言、断言消息自述不可能、以及永久阻塞线程后断言已退出。",
            ["tests/test_network_failure_modes.py:407 慢滴握手断言（消息自述需要 307s）",
             "tests/test_demod_tap_thread_affinity.py 的线程退出断言"],
            "impossible_assertion", "tests/test_network_failure_modes.py 慢滴用例",
            3, 2, 2, a05,
        ),
        Axiom(
            "A06", "守卫必须可反证（不是空过）",
            "每条守卫/测试必须存在一条让它变红的具体路径；吞断言、非 strict xfail、死断言都使守卫失效。",
            "AST 检查非 strict xfail、try 吞 AssertionError、宽泛 except 内 skip、测试开头无条件 return 后的死断言。",
            ["docs/code-guards.md 要求「删除被测逻辑后必须变红」",
             "tests/test_swallow_guard.py 把空反证列为已知红 K06"],
            "vacuous_guard", "tests/ 中任意 xfail 去掉 strict=True",
            3, 2, 2, a06,
        ),
        Axiom(
            "A07", "约束必须放在必经的最窄处",
            "前置条件必须放在所有路径都会经过的实现里，不得只放在某一个 UI 入口。",
            "AST 收集 set_* 动作的调用函数：同一动作存在「有数值守卫的调用方」与「无守卫调用方」即违规。",
            ["desktop/device_panel.py 的采样率范围校验，其他 set_sample_rate 路径没有",
             "增益在 control_panel/gain_panel/main_window/AI 工具四条路径各写一套"],
            "constraint_at_chokepoint", "set_sample_rate 的 4 条调用路径",
            3, 2, 2, a07,
        ),
        Axiom(
            "A08", "守卫必须判对对象，且可被触发",
            "守卫条件必须来自运行时会变化的事实；被常量折叠成恒真/恒假的分支没有在防任何东西。",
            "纯字面量条件折叠：if/while 的条件完全由字面量构成即违规（不做变量传播，避免误报）。",
            ["mbdsdr_ai/decoders.py:435 `if False:`",
             "「无设备则降级为 None」判了错误对象，从未触发"],
            "guard_cannot_fire", "mbdsdr_ai/decoders.py:435",
            2, 2, 2, a08,
        ),
        Axiom(
            "A09", "skip 只能表示环境缺失",
            "skip 的理由必须是可核对的外部缺失；本仓依赖导不进来、对象构造失败不得登记成环境不凑巧。",
            "AST 扫描所有 skip/skipif 理由：含「不可构造/依赖缺失/not available」且无可核对锚点即违规。",
            ["tests/test_web_bridge_mcp.py 6 处「MCPToolSet 在此环境不可构造」",
             "本轮把「tomllib 缺失」类 skip 改为按 python_version 条件声明"],
            "skip_means_env_only", "tests/test_web_bridge_mcp.py:186 等 6 处",
            3, 2, 2, a09,
        ),
        Axiom(
            "A10", "证据必须可复核",
            "引用的提交号必须在本仓真实存在且类型为 commit；不存在的提交不得承载任何结论。",
            "扫描 docs/ 中反引号包裹的 7–40 位十六进制串，批量 `git cat-file -t` 校验。",
            ["docs/推进报告-本轮排错.md:141 引用了不存在的 `5d09bb2`",
             "docs/COORDINATION.md:3430 把不存在的 `0d7e433e` 当提交引用"],
            "evidence_commit_exists", "docs/推进报告-本轮排错.md:141",
            3, 2, 2, a10,
        ),
        Axiom(
            "A11", "绿必须绑定到被断言的那棵树",
            "「全绿/通过」的声明必须绑定实跑所在的提交，且该提交与当前树足够接近；否则必须标注历史快照。",
            "扫描 docs/ 的绿色声明行：无提交号，或提交距 HEAD > 50 且未标历史，即违规。",
            ["docs/learn/phase40/ci-notes.md 的绿色记录距 HEAD 752 个提交",
             "docs/known-red-register.md 的 796 提交前记录（已标首次记录，按历史豁免）"],
            "green_binds_tree", "docs/known-red-register.md 的 `51c01a57`",
            3, 2, 2, a11,
        ),
        Axiom(
            "A12", "同名实现必须行为一致",
            "同一语义的多份同名实现必须合并为一份；行为不一致的同名实现会直接产出矛盾结论。",
            "跨模块收集 fmt/format/render 开头的读数口径函数（freq/hz/bw/gain/rate/dbfs 等），规范化 AST 不同即违规。",
            ["main_window 的 _fmt_dbfs 与 readouts.fmt_dbfs 两套实现",
             "增益夹值后一块面板标「未生效」、另一块照常显示"],
            "duplicate_impl_divergence", "desktop/main_window.py 的 _fmt_dbfs",
            3, 3, 2, a12,
        ),
        Axiom(
            "A13", "身份必须用唯一且不可复用的事实",
            "判同与判活是两件事：身份不得用名字、枚举序号、可改路径字符串；"
            "必须用 pid+进程启动时间、设备序列号这类唯一且不可复用的事实。",
            "AST 检查 owner/holder 字符串比较（函数内无 pid）与 serial/device_id 的枚举序号兜底。",
            ["scripts/file_claim.py:540 `if holder != owner` release 只比名字",
             "mbdsdr_ai/device_manager.py:422/451 序列号缺失时用 `rtl-<枚举序号>` / `soapysdr-<序号>` 兜底"],
            "identity_not_reusable", "scripts/file_claim.py:540",
            3, 3, 2, a13,
        ),
        Axiom(
            "A14", "定位必须用符号，不能用行号",
            "台账/豁免引用代码位置时，必须用「模块 + 符号（函数/类/判断描述）」；"
            "行号可以留作辅助，但不能是唯一定位方式——行号会随无关改动漂移，"
            "目标文件一改，整批引用同时失效。",
            "扫描机器会读的台账（known-red 台账、豁免表）里的 `path.py:N` 引用："
            "无符号即「定位不稳定」；写了符号则用 AST 符号表校验它是否真实存在。",
            ["desktop/main_window.py 被另一条线改十几行后，三个豁免的 :8327/:12761/:14455 全部失效",
             "同一天另一份文档的部分行号引用同样失效"],
            "locator_is_symbolic", "docs/known-red-register.md 里的 `path.py:行号`",
            2, 3, 2, a14,
        ),
    ]
