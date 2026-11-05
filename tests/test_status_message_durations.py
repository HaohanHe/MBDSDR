# SPDX-License-Identifier: MIT
"""
状态栏提示时长档位契约
======================

`desktop/main_window.py` 里 `statusBar().showMessage(...)` 曾有 8 种字面时长
（2~15 s）+ 16 处单参调用（Qt 默认 timeout=0 → 常驻）。后果：
  - 同类「已保存」在 A 处留 4 s、B 处留 8 s；
  - 2 s 的错误提示用户抬头前就消失；
  - 15 s 那条压住随后 1~3 s 的新提示，用户只看到旧消息。

契约（AST 扫描，不靠正则）：
  1. 模块级必须有登记档位常量 MSG_QUICK/MSG_NORMAL/MSG_IMPORTANT；
  2. 每个 showMessage 调用都必须显式给时长：第二位置参数或 timeout=；
     不许单参调用落进 Qt 默认常驻；
  3. 字面量时长只许出现在登记档位集合里；确需常驻必须显式 timeout=0
     且同一调用内带注释说明原因。
"""
import ast
import io
import os
import sys
import tokenize

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_PATH = os.path.join(_ROOT, "desktop", "main_window.py")

#: 登记档位：新增档位必须先说明场景，再改这里（测试与服务端唯一口径）
_TIERS = {"MSG_QUICK": 2000, "MSG_NORMAL": 4000, "MSG_IMPORTANT": 8000}


def _source() -> str:
    with open(_SRC_PATH, encoding="utf-8") as f:
        return f.read()


def _tree(src: str) -> ast.Module:
    return ast.parse(src)


def _show_calls(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "showMessage"):
            yield node


def _timeout_expr(call):
    """返回时长表达式；None 表示调用没给时长（落进 Qt 默认常驻）。"""
    if len(call.args) >= 2:
        return call.args[1]
    for kw in call.keywords:
        if kw.arg == "timeout":
            return kw.value
    return None


def _comments_in_lines(src: str, start: int, end: int) -> bool:
    """调用覆盖的行范围内是否有注释（tokenize 判定，注释不进 AST）。"""
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
    except tokenize.TokenError:  # pragma: no cover - 源文件必须可 tokenize
        return False
    for tok in toks:
        if tok.type == tokenize.COMMENT and start <= tok.start[0] <= end:
            return True
    return False


def test_tier_constants_registered():
    tree = _tree(_source())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in _TIERS:
                    found[tgt.id] = ast.literal_eval(node.value)
    missing = set(_TIERS) - set(found)
    assert not missing, f"缺状态栏时长档位常量: {sorted(missing)}"
    assert found == _TIERS, f"档位值被改写: {found} != {_TIERS}"


def test_every_call_passes_explicit_duration():
    src = _source()
    bad = []
    for call in _show_calls(_tree(src)):
        if _timeout_expr(call) is None:
            bad.append(call.lineno)
    assert not bad, (
        "以下 showMessage 是单参调用，会落进 Qt 默认 timeout=0（常驻，"
        f"旧提示压新提示）：行 {bad}. 需要常驻的必须显式 timeout=0 并注释原因。")


def test_literal_durations_are_registered_tiers():
    src = _source()
    names = {n.id for n in ast.walk(_tree(src)) if isinstance(n, ast.Name)}
    bad = []
    for call in _show_calls(_tree(src)):
        expr = _timeout_expr(call)
        if isinstance(expr, ast.Constant) and isinstance(expr.value, int):
            if expr.value == 0:
                # 常驻必须显式且带原因注释
                if not _comments_in_lines(src, call.lineno, call.end_lineno):
                    bad.append((call.lineno, expr.value))
            elif expr.value not in _TIERS.values():
                bad.append((call.lineno, expr.value))
        elif isinstance(expr, ast.Name):
            # 变量时长（如 _show_error_status 的 timeout 参数）允许透传
            if expr.id.startswith("MSG_") and expr.id not in _TIERS:
                bad.append((call.lineno, expr.id))
    assert not bad, (
        f"以下时长不是登记档位 {sorted(_TIERS.values())}，或是无注释的常驻：{bad}")
    # 反向钉扎：档位常量必须真的被用上，不许只声明不用
    for name in _TIERS:
        assert name in names, f"档位常量 {name} 声明了却没用上"
