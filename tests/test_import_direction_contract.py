# SPDX-License-Identifier: MIT
"""D1 分层守卫：mbdsdr_ai 是纯逻辑层，不得依赖 Qt，也不得反向依赖 desktop。

背景（D1 收口，2026-11-13）：收口前 ``mbdsdr_ai`` 里有 3 处直接 import Qt
（``optional_deps.py`` / ``device_watchdog.py`` / ``desktop_sdr_session.py``）和
8 条 ``mbdsdr_ai -> desktop`` 反向边。修法是把界面层的东西整块搬到 desktop，
而不是把 import 挪进函数体——后者只是让静态检查器看不见，依赖关系没变。
本文件就是把这条约定钉死的守卫：

1. ``mbdsdr_ai/**/*.py`` 里出现任何 ``PySide6`` / ``PyQt5`` / ``PyQt6`` /
   ``qtpy`` / ``shiboken6`` 的 import（**包括函数体内、TYPE_CHECKING 块内**）
   → 失败，并点名 文件:行号；
2. 出现任何 ``desktop`` / ``desktop.*`` 的 import（同上，含函数体内）
   → 失败；
3. 通过 ``importlib.import_module("desktop.…")`` / ``__import__("desktop.…")``
   这类常量字符串动态导入绕过的，同样失败。

已知局限（不粉饰）：只覆盖 import 语句与上述两种常量动态导入；用变量拼出的
模块名无法在静态层识别——那种写法必须在 code review 里挡住。

为什么要允许 ``tests/ -> desktop``：tests 是验收层，允许驱动真实界面对象；
但 ``mbdsdr_ai`` 的测试也在这个包里，所以本守卫对 ``mbdsdr_ai/tests`` 同样生效。
"""

import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 纯逻辑层不得出现的第三方界面框架。
_QT_PREFIXES = ("PySide6", "PySide2", "PyQt5", "PyQt6", "qtpy", "shiboken6")

#: 纯逻辑层不得反向依赖的上层包。
_UPSTREAM_PREFIXES = ("desktop",)


def _iter_units():
    """遍历 mbdsdr_ai 下所有 .py（含 tests 子包）。"""
    root = os.path.join(_ROOT, "mbdsdr_ai")
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _module_names(node):
    """取一条 import 语句引入的顶层模块名（相对导入返回空）。"""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:            # 相对导入不可能指向 desktop
            return []
        return [node.module or ""]
    return []


def _hits_prefix(module, prefixes):
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _dynamic_import_targets(node):
    """``importlib.import_module("x")`` / ``__import__("x")`` 的常量目标。"""
    if not isinstance(node, ast.Call) or not node.args:
        return []
    func = node.func
    is_import_module = (isinstance(func, ast.Attribute)
                        and func.attr == "import_module")
    is_dunder_import = isinstance(func, ast.Name) and func.id == "__import__"
    if not (is_import_module or is_dunder_import):
        return []
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return [first.value]
    return []


def _scan(path, source):
    """返回该文件的违规描述列表（文件:行号 + 命中的模块名）。"""
    tree = ast.parse(source, filename=path)
    found = []
    for node in ast.walk(tree):
        for module in _module_names(node):
            if _hits_prefix(module, _QT_PREFIXES + _UPSTREAM_PREFIXES):
                found.append((node.lineno, module))
        for module in _dynamic_import_targets(node):
            if _hits_prefix(module, _QT_PREFIXES + _UPSTREAM_PREFIXES):
                found.append((node.lineno, "importlib:" + module))
    return found


def _violations():
    result = []
    for path in sorted(_iter_units()):
        with open(path, "r", encoding="utf-8-sig") as f:
            source = f.read()
        for lineno, module in _scan(path, source):
            rel = os.path.relpath(path, _ROOT).replace("\\", "/")
            result.append("%s:%d -> %s" % (rel, lineno, module))
    return result


def test_no_qt_import_in_core_layer():
    """纯逻辑层不得 import 任何 Qt 绑定（含函数体里的延迟 import）。"""
    offenders = [v for v in _violations() if "-> importlib:" not in v]
    offenders = [v for v in offenders
                 if v.split(" -> ", 1)[1].startswith(_QT_PREFIXES)]
    assert not offenders, (
        "mbdsdr_ai 属于纯逻辑层，不得依赖 Qt。若某模块真的需要 Qt 类型贯穿，"
        "把它移到 desktop/ 或做显式适配模块；把 import 挪进函数体不算修好。\n"
        + "\n".join(offenders))


def test_no_reverse_dependency_on_desktop():
    """纯逻辑层不得反向 import 上层 desktop（含动态 import_module）。"""
    offenders = [v for v in _violations()
                 if _hits_prefix(v.split(" -> ", 1)[1].replace("importlib:", ""),
                                 _UPSTREAM_PREFIXES)]
    assert not offenders, (
        "mbdsdr_ai 不得反向依赖 desktop：需要界面层事实时，由 desktop 侧取数，"
        "或在 mbdsdr_ai 里放纯函数供 desktop 调用。\n"
        + "\n".join(offenders))


def test_guard_actually_scanned_the_package():
    """守卫自身的空转保护：扫描集合必须非空且包含已知模块。"""
    units = [os.path.relpath(p, _ROOT).replace("\\", "/") for p in _iter_units()]
    assert len(units) >= 50, "扫描到的 mbdsdr_ai 文件过少，遍历逻辑可能已失效"
    assert "mbdsdr_ai/diagnostics.py" in units
