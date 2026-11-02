# -*- coding: utf-8 -*-
"""typing 注解防漂移守卫（2026-11-02 CI 红灯教训固化）。

事故：desktop/spectrum_widget.py 第 614 行模块级注解 `Optional[List[Dict[...]]]`
用了 `Dict` 却没从 typing 导入。本机 Python 3.14（PEP 649 注解惰性求值）永远
发现不了——收集、运行全绿；CI 是 Python 3.12，模块级/类级注解立即求值，
import 即 NameError，tests/(A)(B) 两组收集期被打断（exit 2），全部测试陪葬。

教训：本机绿 ≠ CI 绿，注解求值语义 3.12↔3.14 不一致。静态扫描钉死这一类：
任何 .py 的注解里引用 typing 名（Dict/List/Optional/…），必须显式导入
（含 TYPE_CHECKING 导入）或在本模块内定义，否则红。

扫描范围：desktop/ mbdsdr_ai/ tests/ tools/ experiments/ scripts/
跳过：.git、build、build_cpp、node_modules、__pycache__、reference、scratch、
      output、_phase60_backup、.workbuddy。
"""

import ast
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCAN_DIRS = ["desktop", "mbdsdr_ai", "tests", "tools", "experiments", "scripts"]

SKIP_DIRS = {
    ".git", "build", "build_cpp", "node_modules", "__pycache__",
    "reference", "scratch", "output", "_phase60_backup", ".workbuddy",
    "env", ".venv", "venv",
}

TYPING_NAMES = {
    "Dict", "List", "Optional", "Tuple", "Set", "FrozenSet",
    "Union", "Callable", "Iterable", "Iterator", "Mapping",
    "Sequence", "MutableMapping", "MutableSequence", "Any",
    "Type", "ClassVar", "Final", "Literal", "Annotated",
    "DefaultDict", "OrderedDict", "Deque", "Counter", "ChainMap",
    "Awaitable", "Coroutine", "Generator", "AsyncIterator",
    "AsyncGenerator", "TypeVar", "Generic", "Protocol", "TypedDict",
    "NamedTuple", "cast", "overload", "NoReturn",
    "Self", "ParamSpec", "TypeAlias", "Unpack", "Required", "NotRequired",
}


def _iter_py_files():
    for d in SCAN_DIRS:
        base = os.path.join(REPO_ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _check_file(path):
    """返回 (relpath, missing_names)。可解析且无缺失时不返回。"""
    rel = os.path.relpath(path, REPO_ROOT).replace("\\", "/")
    try:
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
    except (SyntaxError, UnicodeDecodeError):
        return None  # 语法问题由收集/编译期其它护栏兜住，不在此判

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.asname or a.name)

    used = set()

    class V(ast.NodeVisitor):
        def _grab(self, node):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name):
                    used.add(sub.id)

        def visit_FunctionDef(self, n):
            args = list(n.args.args) + list(n.args.kwonlyargs) + list(n.args.posonlyargs)
            if getattr(n.args, "posonlyargs", None):
                pass
            if n.args.vararg:
                args.append(n.args.vararg)
            if n.args.kwarg:
                args.append(n.args.kwarg)
            for a in args:
                if a is not None and a.annotation is not None:
                    self._grab(a.annotation)
            if n.returns is not None:
                self._grab(n.returns)
            self.generic_visit(n)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_AnnAssign(self, n):
            if n.annotation is not None:
                self._grab(n.annotation)
            self.generic_visit(n)

    V().visit(tree)

    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)

    missing = (used & TYPING_NAMES) - imported - defined
    if missing:
        return rel, sorted(missing)
    return None


def test_no_undefined_typing_names_in_annotations():
    """注解里的 typing 名必须显式导入或本地定义——防 3.12/3.14 求值语义差复发。"""
    problems = []
    for path in _iter_py_files():
        got = _check_file(path)
        if got:
            problems.append(got)
    assert not problems, (
        "以下文件的注解使用了未导入的 typing 名（CI 3.12 会 NameError，"
        "本机 3.14 因 PEP 649 惰性注解发现不了）：\n" +
        "\n".join(f"  {rel}: {names}" for rel, names in sorted(problems))
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
