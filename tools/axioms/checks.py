"""13 条公理的可执行检查。

每条检查只读仓库内容：源码 AST、测试里的 skip/xfail 元数据、文档里引用的
提交号。不 import 项目模块、不启动 Qt、不连硬件。
"""

from __future__ import annotations

import ast
import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .model import (
    Axiom,
    Finding,
    Repo,
    Unit,
    const_eval,
    enclosing_functions,
    line_of,
    parse,
    walk_with_parents,
)

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
IMPOSSIBLE_MESSAGE = re.compile(
    r"永不可能|不可能(?:通过|成立|绿|变绿)|才可能结束|"
    r"永远不(?:会|可能)(?:通过|结束|退出|返回)"
)


def _add(findings: List[Finding], rule: str, unit: Unit, line: int, why: str, fix: str,
         severity: str = "must-fix") -> None:
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


_PERSIST_READ = {"load", "loads", "read_text", "read_bytes", "safe_load"}


def _has_persist_read(nodes: Iterable[ast.AST]) -> bool:
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = _call_name(child)
                if name in _PERSIST_READ:
                    return True
    return False


def scan_a03(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if _is_test_path(unit.path) or unit.path.startswith("scripts/"):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            if not _has_persist_read(node.body):
                continue
            for handler in node.handlers:
                if not _broad_except(handler):
                    continue
                if not _silent_handler(handler):
                    continue
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


def scan_a04(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not (unit.path.startswith("desktop/") or unit.path.startswith("mbdsdr_ai/")):
            continue
        # 只认 json.dump( 这个「整文件覆写」，json.dumps( 只是序列化，不会写盘。
        if not re.search(r"json\.dump\(", unit.text):
            continue
        if not _USER_DATA_HINT.search(unit.text):
            continue
        if re.search(r"corrupt|backup|write_reason|\.corrupt-", unit.text, re.I):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        silent_lines: List[int] = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Try) and _has_persist_read(node.body)):
                continue
            for handler in node.handlers:
                if _broad_except(handler) and _silent_handler(handler):
                    silent_lines.append(handler.lineno)
        if not silent_lines:
            continue
        dump_line = next(
            (i for i, line in enumerate(unit.text.splitlines(), 1) if re.search(r"json\.dump\(", line)),
            0,
        )
        for line in sorted(set(silent_lines)):
            _add(
                findings, "A04", unit, line,
                f"读损坏文件时静默回默认值，但同一模块第 {dump_line} 行会用 json.dump 整文件覆写；"
                "用户下一次操作就会永久丢掉原内容，且没有备份。",
                "读失败时先备份为 *.corrupt-<时间>、记录原因，并拒绝用默认值覆写原件。",
            )
    return findings


# ────────────────────────────── A05 ──────────────────────────────

_BLOCKING_CALLS = {"recv", "read", "get", "poll"}


def _thread_target_blocks(tree: ast.Module, target: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target:
            has_loop = any(isinstance(n, ast.While) for n in ast.walk(node))
            if not has_loop:
                return False
            for n in ast.walk(node):
                if isinstance(n, ast.Call) and _call_name(n) in _BLOCKING_CALLS:
                    if _kw(n, "timeout") is None and len(n.args) == 0:
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


def _guards_call_arg(fn: ast.AST, call: ast.Call) -> bool:
    """守卫必须作用在这条调用传进去的那个值上，才算「这条路径加了约束」。"""
    arg_names = {a.id for a in call.args if isinstance(a, ast.Name)}
    if not arg_names:
        return False
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        tested = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if not (tested & arg_names):
            continue
        if any(isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) for n in ast.walk(node.test)):
            return True
    return False


#: 同名但不同职：显示器/控件也有 set_frequency()，那不是「真正执行调谐的路径」。
WIDGET_RECEIVER = re.compile(
    r"display|label|widget|spin|slider|combo|button|panel|indicator|readout|_ui\b|\bview\b",
    re.I,
)


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


def scan_a07(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    guarded: Dict[str, Set[str]] = {m: set() for m in ACTION_METHODS}
    unguarded: Dict[str, List[Tuple[Unit, ast.Call]]] = {m: [] for m in ACTION_METHODS}
    for unit in units:
        if not (unit.path.startswith("desktop/") or unit.path.startswith("mbdsdr_ai/")):
            continue
        if _is_test_path(unit.path):
            continue
        tree = parse(unit)
        if tree is None:
            continue
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
            if _guards_call_arg(fn, node):
                guarded[name].add(unit.path)
            elif not _result_used(node, parents):
                unguarded[name].append((unit, node))
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
            if WIDGET_RECEIVER.search(_receiver_text(unit, node)):
                continue
            seen_files.add(unit.path)
            _add(
                findings, "A07", unit, node.lineno,
                f"{method} 的数值前置条件只出现在 {example} 等入口，{unit.path} 这条调用路径没有同一约束；"
                "真正执行动作的地方也没有，所以这条路径能绕过去。",
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


def scan_a09(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not _is_test_path(unit.path):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name not in {"skip", "skipif", "SkipTest"}:
                continue
            reason = _skip_reason(unit, node)
            if not reason:
                _add(findings, "A09", unit, node.lineno, "skip 没有说明原因。", "写出具体的环境缺失条件。", "review")
                continue
            if VAGUE_SKIP.search(reason) and not CONCRETE_ANCHOR.search(reason):
                _add(
                    findings, "A09", unit, node.lineno,
                    f"skip 理由只写了「{reason[:50]}」，没有可核对的缺失条件；"
                    "本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。",
                    "写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。",
                )
    return findings


# ────────────────────────────── A10 ──────────────────────────────

HASH_IN_BACKTICKS = re.compile(r"`([0-9a-f]{7,40})`")
PLACEHOLDERS = {"abc1234", "0123456789", "deadbeef", "0000000", "1234567"}
#: 同一段反引号里的 16 进制串可能根本不是提交：会话 id、blob、字节串、二进制字面量、
#: 上游仓库版本号。这些不该算「引用了不存在的提交」。
A10_NON_COMMIT = re.compile(
    r"会话|session|线名|交接方|收件方|来源|提出|维护者|owner|blob|字节|byte|打包|"
    r"多项式|系数|二进制|0b[01]+|第三方|不存在|幻影|幽灵|phantom|未落地|已作废",
    re.I,
)


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
        for lineno, line in enumerate(unit.text.splitlines(), 1):
            for m in HASH_IN_BACKTICKS.finditer(line):
                token = m.group(1)
                if not _looks_like_commit(token):
                    continue
                if A10_NON_COMMIT.search(line):
                    continue
                if line[: m.start()].rstrip().endswith("@"):
                    # `repo @ <hash>` 指向上游版本，不是本仓提交。
                    continue
                occurrences.setdefault(token, []).append((unit, lineno, line))
    types = repo.batch_object_types(list(occurrences))
    for token, items in occurrences.items():
        typ = types.get(token, "missing")
        for unit, lineno, line in items:
            if re.search(r"例如|示例|示意|占位|placeholder", line):
                continue
            if typ == "missing":
                _add(
                    findings, "A10", unit, lineno,
                    f"文档引用的提交 `{token}` 在本仓不存在；基于它的后续结论无法复核。",
                    "改为真实提交号；未落地的改动标注「未落地」，不要用提交口吻引用。",
                )
            elif typ != "commit" and re.search(r"提交|commit|HEAD", line, re.I):
                _add(
                    findings, "A10", unit, lineno,
                    f"`{token}` 在本仓是 {typ}，不是提交，但文中按提交引用。",
                    "引用提交号，或明确标注这是 blob/tree 标识。",
                )
    return findings


# ────────────────────────────── A11 ──────────────────────────────

CURRENT_DOC = re.compile(r"code-guards|evidence|known-red|ci-notes|redundancy|hardcoded|packaging|network-layer|copy-audit")
#: 这些目录是「某一时刻的快照」，本身不承担当前树的结论；其中的绿色声明
#: 只要不是绑到不存在/过期的提交就不算违规（后者仍会报）。
ARCHIVE_DOC = re.compile(r"^docs/(?:learn|research|audit[^/]*|pending-patches|incidents)/")


def scan_a11(repo: Repo, units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    for unit in units:
        if not unit.path.startswith("docs/"):
            continue
        if unit.path.startswith("docs/learn/") and not CURRENT_DOC.search(unit.path):
            continue
        lines = unit.text.splitlines()
        no_hash_reported = False
        for i, line in enumerate(lines):
            if not GREEN_CLAIM.search(line):
                continue
            window = "\n".join(lines[max(0, i - 2): min(len(lines), i + 3)])
            if HISTORY_MARKER.search(window):
                continue
            match = HASH_IN_BACKTICKS.search(window)
            if not match:
                if unit.path.startswith("docs/learn/") or ARCHIVE_DOC.search(unit.path):
                    continue
                if not no_hash_reported:
                    no_hash_reported = True
                    _add(
                        findings, "A11", unit, i + 1,
                        "「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。",
                        "补上实跑所在的提交号，或标注为历史快照。",
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

#: 只比较「同一语义、用户可见口径」的同名实现（fmt_xxx / format_xxx / render_xxx）。
#: `parse()` 在各协议解码器里是不同对象的不同方法，属于 D3「同名不同职」，不该算 A12。
DUP_NAME = re.compile(
    r"^_?(?:fmt|format|render)(?:_?(?:freq|frequency|hz|mhz|khz|bw|bandwidth|gain|rate|"
    r"dbfs|dbm|db|power|unit|level|snr|ppm|offset))",
    re.I,
)


def _normalized(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def scan_a12(units: List[Unit]) -> List[Finding]:
    findings: List[Finding] = []
    groups: Dict[str, List[Tuple[Unit, ast.AST, str]]] = {}
    for unit in units:
        if not (unit.path.startswith("desktop/") or unit.path.startswith("mbdsdr_ai/")):
            continue
        if unit.path.endswith("readouts.py") or unit.path.endswith("constants.py"):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and DUP_NAME.search(node.name):
                if len(node.body) < 4:
                    continue
                groups.setdefault(node.name, []).append((unit, node, _normalized(node)))
    for name, items in groups.items():
        modules = {u.path for u, _, _ in items}
        if len(modules) < 2:
            continue
        bodies = {b for _, _, b in items}
        if len(bodies) < 2:
            continue
        first = items[0]
        for unit, node, body in items[1:]:
            if body == first[2]:
                continue
            _add(
                findings, "A12", unit, node.lineno,
                f"与 {first[0].path}:{first[1].lineno} 同名（{name}）但实现不同；"
                "同一语义在两处各写一遍，迟早会对同一个值给出不同结论。",
                "合并到唯一实现（读数类统一收进 desktop/readouts.py），删除另一份。",
            )
    return findings


# ────────────────────────────── A13 ──────────────────────────────

IDENTITY_NAME = re.compile(r"(^|_)(owner|holder|serial|device_key|device_id|identity)($|_)", re.I)
INDEX_NAMES = {"idx", "index", "i", "n"}


def _has_index_name(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id in INDEX_NAMES for n in ast.walk(node))


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
        fn_nodes = enclosing_functions(tree)
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
                    elif isinstance(op, ast.Call) and isinstance(op.func, ast.Attribute):
                        names.append(op.func.attr)
                # 必须两边都是 owner/holder —— 一边是 owner、另一边是 epoch/常量时，
                # 判的不是「是不是同一个持有者」。
                owner_like = [n for n in names if re.search(r"owner|holder", n, re.I)]
                if len(owner_like) < 2:
                    continue
                fn = fn_nodes.get(node.lineno)
                fn_src = ""
                if fn is not None:
                    try:
                        fn_src = ast.get_source_segment(unit.text, fn) or ""
                    except Exception:
                        pass
                if re.search(r"\bpid\b", fn_src):
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
                    if not IDENTITY_NAME.search(target.id):
                        continue
                    if _index_derived(node.value):
                        _add(
                            findings, "A13", unit, node.lineno,
                            f"{target.id} 的身份值由枚举序号/循环下标拼成；插拔顺序一变，同一台设备就会变成另一台。",
                            "身份用序列号（缺失就明确拒绝并提示），不要用枚举序号或可改的标签兜底。",
                        )
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
    ]
