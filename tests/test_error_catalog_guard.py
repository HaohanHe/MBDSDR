# SPDX-License-Identifier: MIT
"""错误码目录守卫。

三条硬约束（对应 docs/error-catalog.md 第 4 节）：

1. 表内每个码至少被一处生产代码引用 —— 无死条目；
2. 生产代码里出现的码都在表里 —— 无野码；
3. 面向用户的字符串不出现「未知错误」类空话 —— 白名单必须写理由。

扫描范围：``mbdsdr_ai/`` 下的生产代码（排除 ``tests/``、``__pycache__``
与目录模块自身）。``desktop/`` 不整体扫描，只对
``_CROSS_LAYER_CONSUMERS`` 里显式登记的跨层码回查其消费文件。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from mbdsdr_ai.error_catalog import (
    ERRORS,
    FAMILY_PREFIXES,
    exception_code,
    exception_message,
    spec,
    user_message,
)

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "mbdsdr_ai"
CATALOG_MODULE = PKG / "error_catalog.py"
DOC = ROOT / "docs" / "error-catalog.md"

#: 只扫描「码形状」的取值：小写标识符。含空格/点/冒号的是事件名或消息，
#: 大写常量（如 NEED_HW）单独由死条目检查覆盖。
_CODE_SHAPE = re.compile(r"^[a-z][a-z0-9_]+$")

#: 空话黑名单。整串等于「失败」这类也拦。
_EMPTY_PHRASES = ("未知错误", "未知失败", "未知异常", "出错了", "发生错误")
_BARE_FAILURES = {"失败", "操作失败", "执行失败"}

#: 误报白名单：(相对路径, 取值) -> 理由。新增条目必须写清为什么不是错误码。
_WILD_CODE_ALLOW = {
    # agent.py 的经验类型别名表：键 "error" 只是别名入口，值 "error_recovery"
    # 是经验类型枚举，不是错误码。
    ("agent.py", "error_recovery"):
        "经验类型别名表（error → error_recovery），非错误码",
    # session_log.py 的 EV_ERROR = "error" 是 append-only JSONL 的**事件类型**
    # 常量（与 turn_start / tool_call / compaction 同族），不是 ToolResult.error
    # 错误码；模型可见事件都要有常量，故不能改写成 catalog 里的码。
    ("session_log.py", "error"):
        "会话日志事件类型 EV_ERROR，非错误码（Sprint 3 状态面新增）",
}

#: 跨层码：由桌面接入层消费、``mbdsdr_ai`` 生产代码不再产生。逐码显式
#: 声明消费文件，检查时回查该文件里确有这个字面量；消费方删掉后判据照样红，
#: 不是无条件豁免。``usb_permission`` 是 dd0a47f8 从 device_busy 拆出的
#: 接入层码，AI 层仍把权限错误归为 device_busy（行为未改）。
_CROSS_LAYER_CONSUMERS = {
    "usb_permission": ROOT / "desktop" / "troubleshooting.py",
}

_EMPTY_PHRASE_ALLOW = {
    # (相对路径, 空话) -> 理由
}


def _production_files() -> list[Path]:
    files = []
    for p in PKG.rglob("*.py"):
        rel = p.relative_to(PKG)
        if "tests" in rel.parts or "__pycache__" in rel.parts:
            continue
        if p == CATALOG_MODULE:
            continue
        files.append(p)
    return sorted(files)


def _rel(path: Path) -> str:
    return path.relative_to(PKG).as_posix()


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            ids.add(id(body[0].value))
    return ids


def _is_docstring(node: ast.Constant, docs: set[int]) -> bool:
    return id(node) in docs


def _fstring_fragments(tree: ast.AST) -> set[int]:
    """f-string 子树内的常量片段：单独看是「失败」，拼起来是完整句子。

    含 FormattedValue 里的条件表达式常量（如 ``{'成功' if ok else '失败'}``）。
    """
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant):
                    ids.add(id(sub))
    return ids


def _code_collected(tree: ast.AST) -> list[tuple[str, int]]:
    """收集所有「被当作错误码使用」的字面量。"""
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                v = kw.value
                if (kw.arg in ("error", "error_code", "reason_code")
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str) and v.value):
                    out.append((v.value, node.lineno))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                is_code_slot = (
                    isinstance(target, ast.Attribute)
                    and target.attr in ("error", "error_code",
                                        "last_error_kind")
                ) or (
                    isinstance(target, ast.Name)
                    and target.id.endswith(("_ERROR", "_ERR", "ERROR_CODE"))
                    and not target.id.endswith("ERRORS")
                )
                if (is_code_slot and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                        and node.value.value):
                    out.append((node.value.value, node.lineno))
                # 接入层异常类的 class 属性 code = "xxx"
                if (isinstance(target, ast.Name) and target.id == "code"
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                        and node.value.value):
                    out.append((node.value.value, node.lineno))
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant)
                        and k.value in ("error", "error_code",
                                        "reason_code", "code")
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str) and v.value):
                    out.append((v.value, node.lineno))
    return out


def test_every_catalog_code_is_used() -> None:
    """无死条目：每个码都在生产代码里真实出现过。"""
    blobs = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                      for p in _production_files())
    dead = []
    for code in ERRORS:
        prefix = FAMILY_PREFIXES.get(code)
        quoted = (f'"{code}"' in blobs) or (f"'{code}'" in blobs)
        used = quoted or (prefix is not None and prefix in blobs)
        consumer = _CROSS_LAYER_CONSUMERS.get(code)
        if not used and consumer is not None:
            consumer_text = consumer.read_text(
                encoding="utf-8", errors="replace")
            used = (f'"{code}"' in consumer_text) or (
                f"'{code}'" in consumer_text)
        if not used:
            dead.append(code)
    assert not dead, f"目录里有未被任何代码引用的死条目: {dead}"


def test_no_wild_codes() -> None:
    """无野码：代码里当错误码用的标识符必须已登记。"""
    bad: list[str] = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for value, line in _code_collected(tree):
            if not _CODE_SHAPE.match(value):
                continue
            ok = value in ERRORS or any(
                value.startswith(prefix)
                for prefix in FAMILY_PREFIXES.values())
            if ok:
                continue
            if (_rel(path), value) in _WILD_CODE_ALLOW:
                continue
            bad.append(f"{_rel(path)}:{line} -> {value!r}")
    assert not bad, "发现未登记的错误码（野码）:\n" + "\n".join(bad)


def test_no_empty_user_facing_phrases() -> None:
    """面向用户的字符串不得只说「未知错误」这类空话。"""
    bad: list[str] = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        docs = _docstrings(tree)
        fragments = _fstring_fragments(tree)
        rel = _rel(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if _is_docstring(node, docs):
                continue
            text = node.value.strip()
            phrase = None
            for p in _EMPTY_PHRASES:
                if p in text:
                    phrase = p
                    break
            if phrase is None and text in _BARE_FAILURES:
                # f-string 片段会与相邻片段拼成完整句子（如「步骤 3 执行失败」），
                # 不算独立空话；独立字符串仍然拦截。
                if id(node) in fragments:
                    continue
                phrase = text
            if phrase is None:
                continue
            if (rel, phrase) in _EMPTY_PHRASE_ALLOW:
                continue
            bad.append(f"{rel}:{node.lineno} -> {phrase!r} in {text[:60]!r}")
    assert not bad, "发现无信息兜底话术:\n" + "\n".join(bad)


def test_whitelist_entries_have_reasons() -> None:
    """白名单不是垃圾桶：每条都必须有理由，且文件真实存在。"""
    for rel, value in _WILD_CODE_ALLOW:
        assert (PKG / rel).exists(), f"白名单文件不存在: {rel}"
        assert _WILD_CODE_ALLOW[(rel, value)].strip(), \
            f"白名单 {rel}:{value} 缺少理由"
    for rel, phrase in _EMPTY_PHRASE_ALLOW:
        assert (PKG / rel).exists(), f"白名单文件不存在: {rel}"
        assert _EMPTY_PHRASE_ALLOW[(rel, phrase)].strip(), \
            f"白名单 {rel}:{phrase} 缺少理由"


def test_catalog_doc_matches_module() -> None:
    """docs/error-catalog.md 是模块的镜像，禁止两边漂移。"""
    assert DOC.exists(), "缺少 docs/error-catalog.md"
    text = DOC.read_text(encoding="utf-8")
    start = text.index("## 3. 错误码表")
    end = text.index("## 4. 守卫测试")
    table = text[start:end]
    row_codes = set(re.findall(
        r"^\| `([A-Za-z][A-Za-z0-9_]*)`", table, re.M))
    assert row_codes == set(ERRORS), (
        f"文档与模块不一致；仅文档有: {row_codes - set(ERRORS)}；"
        f"仅模块有: {set(ERRORS) - row_codes}")


def test_exception_code_is_traceable() -> None:
    """exception 家族必须带原始异常类型 + 上下文。"""
    code = exception_code(ValueError("竖线"))
    assert code == "exception:ValueError"
    msg = exception_message("sdr_tune", ValueError("竖线"), context="预设 100 MHz")
    assert "ValueError" in msg and "预设 100 MHz" in msg


def test_unknown_code_fallback_is_honest() -> None:
    """未登记码的兜底要带码与上下文，不能只说「失败」。"""
    msg = user_message("not_registered_code", context="some_call")
    assert "not_registered_code" in msg
    assert "some_call" in msg
    assert spec("not_registered_code").code == "not_registered_code"
    assert not spec("not_registered_code").retryable
