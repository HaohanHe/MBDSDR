#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""提交后符号存在性检查：调用目标在 HEAD 的对应模块里是否真的存在。

为什么要有这一层
----------------
``head_build_smoke`` 跑的是既有用例，判据是「这些路径跑得通」。它天然抓不到
「调用侧先落地、被调侧后落地」：新写的调用点还没有用例覆盖，用例全绿，HEAD
却已经在 ``AttributeError`` 上。2026-11-14 11:13–11:14 两笔提交（``2b69e3df``
的 ``self.status_panel.set_reassembly_quality(...)``、``ec3a964b`` 的
``parser.stats()``）就是这样：两个定义直到 11:25 的 ``87b3a36d`` 才入库，
中间 12 分钟 HEAD 在这两条路径上不可用，烟雾检查判绿。

本检查不跑用例，只做静态解析：取本笔的**新增行**，解析其中的属性调用链，
按文件里真实的 import 把接收者解析到仓库内的模块/类，再看被调的成员在
**同一个提交的树**里是否存在。解析不了就说「无法判定」，不算通过。

结论分四类，互不合并（也不与「跑不过用例」合并，那是另一个入口）：

* ``missing``：接收者解析成功，成员确实不存在 → 红（退出码 3）；
* ``unresolved``：接收者解析不了（未声明类型的局部变量、参数、外部对象）→
  「无法判定」：逐条列出、写进台账，**不计入「存在」数**，也不据此判红
  （否则每次提交都在猜）；判红只认「确实不存在」；
* ``external``：接收者是仓库外的模块（``np`` / ``PySide6`` 等），静态不可核查
  ——单列数量，不算绿也不冒充核查过；
* ``ok``：解析成功且成员存在。

白名单
------
``scripts/symbol_check_allowlist.json``，每条必须写 ``reason``；条数上限写死
（``MAX_ALLOWLIST_ENTRIES``），超出或理由为空直接按错误退出——白名单是逐条
签过字的例外，不是「先放行再说」。命中白名单的条目在报告里原样列出理由。

用法::

    python scripts/head_symbol_check.py --commit HEAD
    python scripts/head_symbol_check.py --commit 2b69e3df --json
"""
from __future__ import annotations

import argparse
import ast
import builtins
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

MAX_ALLOWLIST_ENTRIES = 12
DEFAULT_ALLOWLIST = Path(__file__).with_name("symbol_check_allowlist.json")

EXIT_OK = 0
EXIT_MISSING = 3
EXIT_ERROR = 4

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败：{proc.stderr.decode('utf-8', 'replace')[:300]}"
        )
    return proc.stdout


def _resolve_sha(repo: Path, rev: str) -> str:
    return _git(repo, "rev-parse", f"{rev}^{{commit}}").decode().strip()


def _locks_dir(repo: Path) -> Path:
    env = os.environ.get("MBDSDR_LOCKS_DIR")
    if env:
        return Path(env)
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    common = Path(proc.stdout.decode().strip())
    if not common.is_absolute():
        common = (repo / common).resolve()
    return common.parent / ".locks"


def _ledger_path(repo: Path) -> Path:
    env = os.environ.get("MBDSDR_SYMBOL_LOG")
    if env:
        return Path(env)
    return _locks_dir(repo) / "head_symbol_check.log"


# ---------------------------------------------------------------------------
# 提交树 → 模块索引 / 变动行
# ---------------------------------------------------------------------------
def _module_index(repo: Path, sha: str) -> dict[str, str]:
    """模块名 → 仓库内路径（``desktop/main_window.py`` → ``desktop.main_window``）。"""
    index: dict[str, str] = {}
    for path in _git(repo, "ls-tree", "-r", "--name-only", sha).decode().splitlines():
        if not path.endswith(".py"):
            continue
        module = path[:-3].replace("\\", "/").replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        index.setdefault(module, path)
    return index


def _changed_py_lines(repo: Path, sha: str) -> dict[str, set[int]]:
    """本笔在各 .py 文件里**新增**的行号（新侧）。"""
    text = _git(
        repo, "diff-tree", "--root", "-r", "-p", "--no-color", "--unified=0",
        "--format=", sha, "--", "*.py",
    ).decode("utf-8", "replace")
    out: dict[str, set[int]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = _FILE_RE.match(line)
        if match:
            current = match.group(1)
            out.setdefault(current, set())
            continue
        hunk = _HUNK_RE.match(line)
        if hunk and current:
            start = int(hunk.group(1))
            count = int(hunk.group(2) or "1")
            out[current].update(range(start, start + max(count, 0)))
    return {path: lines for path, lines in out.items() if lines}


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------
class Ref:
    """解析结果：仓库内模块 / 仓库内类（实例与类同等对待）/ 仓库外模块。"""

    __slots__ = ("kind", "module", "name")

    def __init__(self, kind: str, module: str = "", name: str = ""):
        self.kind = kind          # module | class | external | unknown
        self.module = module
        self.name = name

    def __repr__(self) -> str:
        return f"<{self.kind} {self.module}{'.' if self.name else ''}{self.name}>"


UNKNOWN = Ref("unknown")


class _BlobReader:
    """一个 ``git cat-file --batch`` 进程读所有文件内容。

    逐文件 ``git show`` 会为每个模块起一个进程：改一笔跨模块提交要解析几十个
    模块，光起进程就吃掉数秒。批量读把进程数压到常数，实测从 2.4 s 降到 0.2 s。
    """

    def __init__(self, repo: Path):
        self.proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )

    def read(self, spec: str) -> bytes | None:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(spec.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if len(header) < 3 or header[1] == b"missing":
            return None
        size = int(header[2])
        data = self.proc.stdout.read(size)
        self.proc.stdout.read(1)          # 结尾换行
        return data

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 - 清理失败不影响结论
            self.proc.kill()


class Checker:
    def __init__(self, repo: Path, sha: str):
        self.repo = repo
        self.sha = sha
        self.modules = _module_index(repo, sha)
        self._reader = _BlobReader(repo)
        self._trees: dict[str, ast.Module | None] = {}
        self._attrs: dict[str, dict[str, str] | None] = {}
        self._members: dict[tuple[str, str], dict[str, str] | None] = {}
        self._imports: dict[str, tuple[dict[str, Ref], dict[str, Ref]]] = {}
        self._external_base: dict[tuple[str, str], bool] = {}
        self._self_attrs: dict[tuple[str, str], dict[str, Ref]] = {}
        self._builtins = set(dir(builtins))

    # -- 读树 ---------------------------------------------------------------
    def tree(self, module: str) -> ast.Module | None:
        if module in self._trees:
            return self._trees[module]
        path = self.modules.get(module)
        tree = None
        if path is not None:
            blob = self._reader.read(f"{self.sha}:{path}")
            if blob is not None:
                try:
                    tree = ast.parse(blob.decode("utf-8", "replace"), filename=path)
                except SyntaxError:
                    tree = None
        self._trees[module] = tree
        return tree

    # -- 模块级名字 ---------------------------------------------------------
    def module_attrs(self, module: str) -> dict[str, str] | None:
        """模块顶层名字 → 种类（``func`` / ``class`` / ``value``）。"""
        if module in self._attrs:
            return self._attrs[module]
        tree = self.tree(module)
        if tree is None:
            self._attrs[module] = None
            return None
        attrs: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                attrs[node.name] = "func"
            elif isinstance(node, ast.ClassDef):
                attrs[node.name] = "class"
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        attrs[target.id] = "value"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    attrs[(alias.asname or alias.name).split(".")[0]] = "value"
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    attrs[alias.asname or alias.name] = "value"
        self._attrs[module] = attrs
        return attrs

    def class_members(self, module: str, cls_name: str,
                      _seen: set[tuple[str, str]] | None = None) -> dict[str, str] | None:
        """类成员（含仓库内基类）；有基类解析不了则返回 None（无法判定）。"""
        key = (module, cls_name)
        if key in self._members:
            return self._members[key]
        _seen = _seen or set()
        if key in _seen:
            return {}
        _seen.add(key)
        tree = self.tree(module)
        cls = None
        if tree is not None:
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == cls_name:
                    cls = node
                    break
        if cls is None:
            self._members[key] = None
            return None
        members: dict[str, str] = {}
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                members[node.name] = "func"
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        members[target.id] = "value"
                    elif (isinstance(target, ast.Attribute)
                          and isinstance(target.value, ast.Name)
                          and target.value.id == "self"):
                        members[target.attr] = "value"
        external_base = False
        for base in cls.bases:
            ref = self.base_ref(base, module)
            if ref.kind == "external":
                # 外部基类（Qt 等）：其成员静态不可核查，只查本类与仓库内基类
                external_base = True
                continue
            if ref.kind != "class":
                self._members[key] = None
                return None
            inherited = self.class_members(ref.module, ref.name, _seen)
            if inherited is None:
                self._members[key] = None
                return None
            if self.has_external_base(ref.module, ref.name):
                external_base = True
            for name, kind in inherited.items():
                members.setdefault(name, kind)
        self._external_base[key] = external_base
        self._members[key] = members
        return members

    def has_external_base(self, module: str, cls_name: str) -> bool:
        if (module, cls_name) not in self._external_base:
            self.class_members(module, cls_name)
        return self._external_base.get((module, cls_name), True)

    def self_attrs_of(self, module: str, cls_name: str) -> dict[str, Ref]:
        """类内 ``self.x`` 的类型表（每类只算一次）。"""
        key = (module, cls_name)
        if key in self._self_attrs:
            return self._self_attrs[key]
        self._self_attrs[key] = {}          # 防递归
        cls = _class_node(self.tree(module), cls_name)
        if cls is None:
            return {}
        ctx = _FileCtx(self, self.modules.get(module, ""))
        ctx.enter(cls)
        self._self_attrs[key] = _self_attr_types(self, ctx, cls)
        return self._self_attrs[key]

    def file_imports(self, module: str) -> tuple[dict[str, Ref], dict[str, Ref]]:
        if module not in self._imports:
            tree = self.tree(module)
            path = self.modules.get(module, "")
            self._imports[module] = self.import_map(tree, path) if tree else ({}, {})
        return self._imports[module]

    def base_ref(self, expr: ast.expr, module: str) -> Ref:
        """基类表达式 → Ref（不走局部上下文；类体里的基类只可能是模块级名字）。"""
        if isinstance(expr, ast.Name):
            if _class_node(self.tree(module), expr.id) is not None:
                return Ref("class", module, expr.id)
            aliases, symbols = self.file_imports(module)
            return (symbols.get(expr.id) or aliases.get(expr.id)
                    or (Ref("external") if expr.id in self._builtins else UNKNOWN))
        if isinstance(expr, ast.Attribute):
            return self.member_ref(self.base_ref(expr.value, module), expr.attr)
        return UNKNOWN

    # -- import 表 ----------------------------------------------------------
    def import_map(self, tree: ast.Module, path: str) -> tuple[dict[str, Ref], dict[str, Ref]]:
        """返回 (模块别名 → 模块 Ref, from-import 名字 → Ref)。"""
        package = path.rsplit("/", 1)[0].replace("/", ".") if "/" in path else ""
        module_aliases: dict[str, Ref] = {}
        symbols: dict[str, Ref] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in self.modules:
                        module_aliases[alias.asname or alias.name.split(".")[0]] = Ref(
                            "module", alias.name)
                    else:
                        module_aliases[alias.asname or alias.name.split(".")[0]] = Ref("external")
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".") if package else []
                    if node.level > 1:
                        parts = parts[: -(node.level - 1)] if len(parts) >= node.level - 1 else []
                    base = ".".join([p for p in parts + ([base] if base else []) if p])
                for alias in node.names:
                    name = alias.asname or alias.name
                    sub = f"{base}.{alias.name}" if base else alias.name
                    if sub in self.modules:
                        symbols[name] = Ref("module", sub)
                    elif base in self.modules:
                        attrs = self.module_attrs(base) or {}
                        symbols[name] = (
                            Ref("class", base, alias.name)
                            if attrs.get(alias.name) == "class" else UNKNOWN
                        )
                    else:
                        symbols[name] = Ref("external")
        return module_aliases, symbols

    # -- 表达式 → 接收者 ----------------------------------------------------
    def resolve(self, expr: ast.expr, ctx: "_FileCtx | None") -> Ref:
        if isinstance(expr, ast.Name):
            if ctx is not None:
                if expr.id in ctx.locals:
                    return ctx.locals[expr.id]
                if expr.id in ctx.symbols:
                    return ctx.symbols[expr.id]
                if expr.id in ctx.module_aliases:
                    return ctx.module_aliases[expr.id]
                if expr.id in ctx.classes:
                    return Ref("class", ctx.module, expr.id)
                if expr.id == "self" and ctx.self_class:
                    return Ref("class", ctx.module, ctx.self_class)
            if expr.id in self._builtins:
                return Ref("external")
            return UNKNOWN
        if isinstance(expr, ast.Attribute):
            if (ctx is not None and isinstance(expr.value, ast.Name)
                    and expr.value.id == "self" and expr.attr in ctx.self_attrs):
                return ctx.self_attrs[expr.attr]
            owner = self.resolve(expr.value, ctx)
            return self.member_ref(owner, expr.attr)
        if isinstance(expr, ast.Call):
            panel = self._panel_from_create(expr, ctx)
            if panel is not None:
                return panel
            owner = self.resolve(expr.func, ctx)
            if owner.kind == "class":
                return owner
            if owner.kind == "external":
                return Ref("external")
            return UNKNOWN
        return UNKNOWN

    def step(self, owner: Ref, attr: str, ctx: "_FileCtx | None") -> Ref:
        """链式访问的一步：``self.x`` 优先用本类的赋值类型，其余按成员类型。"""
        if (ctx is not None and owner.kind == "class"
                and owner.module == ctx.module and owner.name == ctx.self_class
                and attr in ctx.self_attrs):
            return ctx.self_attrs[attr]
        return self.member_ref(owner, attr)

    def member_ref(self, owner: Ref, attr: str) -> Ref:
        if owner.kind == "external":
            return Ref("external")
        if owner.kind == "module":
            attrs = self.module_attrs(owner.module) or {}
            return Ref("class", owner.module, attr) if attrs.get(attr) == "class" else UNKNOWN
        return UNKNOWN

    # -- 面板注册表（``self.x = self._create_panel("status")``）-------------
    def _panel_from_create(self, expr: ast.Call, ctx: "_FileCtx | None") -> Ref | None:
        func = expr.func
        if not isinstance(func, ast.Attribute) or func.attr != "_create_panel":
            return None
        if not expr.args or not isinstance(expr.args[0], ast.Constant):
            return None
        panel_id = expr.args[0].value
        return self.panel_class(panel_id)

    def panel_class(self, panel_id: str) -> Ref | None:
        registry = "desktop.panel_registry"
        tree = self.tree(registry)
        if tree is None:
            return None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else "")
            if name != "PanelSpec" or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or first.value != panel_id:
                continue
            for keyword in node.keywords:
                if keyword.arg != "factory":
                    continue
                factory = keyword.value
                if (isinstance(factory, ast.Call)
                        and len(factory.args) >= 2
                        and all(isinstance(a, ast.Constant) for a in factory.args[:2])):
                    module_name = str(factory.args[0].value)
                    class_name = str(factory.args[1].value)
                    for candidate in (f"desktop.{module_name}", module_name):
                        if candidate in self.modules:
                            return Ref("class", candidate, class_name)
        return None

    # -- 单条调用链 ---------------------------------------------------------
    def check_call(self, call: ast.Call, ctx: "_FileCtx") -> tuple[str, str, str]:
        """返回 (结论, 调用文本, 说明)。

        结论：ok / missing / unresolved / external / skip。
        """
        func = call.func
        if not isinstance(func, ast.Attribute):
            return "skip", "", ""      # 裸名字调用：本地函数或内建，交给用例
        parts: list[str] = []
        node: ast.expr = func
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        parts.reverse()
        symbol = ast.unparse(func)
        base = node
        owner = self.resolve(base, ctx)

        if owner.kind == "external":
            return "external", symbol, "仓库外模块，静态不可核查"
        if owner.kind == "unknown":
            return "unresolved", symbol, "接收者类型无法解析"
        for attr in parts[:-1]:
            owner = self.step(owner, attr, ctx)
            if owner.kind == "external":
                return "external", symbol, "仓库外模块，静态不可核查"
            if owner.kind != "class":
                return "unresolved", symbol, f"接收者链在 .{attr} 处无法解析"
        last = parts[-1]
        if owner.kind == "module":
            attrs = self.module_attrs(owner.module)
            if attrs is None:
                return "unresolved", symbol, f"模块 {owner.module} 解析失败"
            if last in attrs:
                return "ok", symbol, f"{owner.module} 有 {last}"
            return "missing", symbol, f"{self.modules.get(owner.module, owner.module)} 里没有 {last}"
        if owner.kind == "class":
            members = self.class_members(owner.module, owner.name)
            where = f"{owner.module}.{owner.name}"
            if members is None:
                return "unresolved", symbol, f"{where} 的基类无法解析"
            if last in members:
                return "ok", symbol, f"{where} 有 {last}"
            if self.has_external_base(owner.module, owner.name):
                if _project_style(last):
                    return "missing", symbol, (
                        f"{where} 及其仓库内基类都没有 {last}"
                        f"（带下划线的项目式命名；外部基类不会提供这种符号）")
                return "unresolved", symbol, f"{where} 有外部基类，{last} 可能来自那里"
            return "missing", symbol, f"{where} 及其仓库内基类都没有 {last}"
        return "unresolved", symbol, "接收者类型无法解析"


class _FileCtx:
    """单个被改文件在**该提交树**里的解析上下文。"""

    def __init__(self, checker: Checker, path: str):
        self.path = path
        self.module = path[:-3].replace("/", ".")
        tree = checker.tree(self.module)
        assert tree is not None
        self.module_aliases, self.symbols = checker.import_map(tree, path)
        self.classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
        self.self_class: str | None = None
        self.locals: dict[str, Ref] = {}
        self.self_attrs: dict[str, Ref] = {}

    def enter(self, node: ast.AST) -> None:
        """进入函数/类：重建局部类型表（``x = Cls()`` / ``self.x = ...``）。"""
        self.locals = {}
        self.self_attrs = {}
        if isinstance(node, ast.ClassDef):
            self.self_class = node.name
        else:
            self.self_class = getattr(node, "_enclosing_class", None)
        if self.self_class:
            self.self_attrs = _CHECKER.self_attrs_of(self.module, self.self_class)
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            targets: list[ast.expr] = []
            if isinstance(child, ast.Assign):
                targets = list(child.targets)
                value = child.value
            elif isinstance(child, ast.AnnAssign) and child.value is not None:
                targets = [child.target]
                value = child.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    ref = _CHECKER.resolve(value, self)
                    if ref.kind != "unknown":
                        self.locals[target.id] = ref


_CHECKER: Checker  # 由 run() 赋值；_FileCtx.enter 里用到


def _class_node(tree: ast.Module | None, name: str) -> ast.ClassDef | None:
    if tree is None:
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _self_attr_types(checker: Checker, ctx: "_FileCtx", cls: ast.ClassDef) -> dict[str, Ref]:
    """类内 ``self.x = <可解析表达式>`` → ``x`` 的类型（含面板注册表）。"""
    out: dict[str, Ref] = {}
    for node in ast.walk(cls):
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        ref = checker.resolve(value, ctx)
        if ref.kind == "unknown":
            continue
        for target in targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                out[target.attr] = ref
    return out


def _walk_with_class(tree: ast.Module):
    """按 (节点, 所在类名) 展开，供 _FileCtx.enter 定位 self 类型。"""
    def walk(node, cls):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from walk(child, child.name)
            else:
                child._enclosing_class = cls  # type: ignore[attr-defined]
                yield child, cls
                yield from walk(child, cls)
    yield from walk(tree, None)


def _allowlist_load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"{path} 缺少 entries 列表")
    cap = int(data.get("max_entries", MAX_ALLOWLIST_ENTRIES))
    if cap > MAX_ALLOWLIST_ENTRIES:
        raise ValueError(f"{path} 的 max_entries={cap} 超过硬上限 {MAX_ALLOWLIST_ENTRIES}")
    if len(entries) > cap:
        raise ValueError(f"{path} 条目 {len(entries)} 条超过上限 {cap}")
    for item in entries:
        for key in ("path", "symbol", "reason"):
            if not str(item.get(key) or "").strip():
                raise ValueError(f"{path} 有条目缺 {key}：{item!r}")
    return entries


def _allowlisted(entries: list[dict], path: str, symbol: str, line: int) -> dict | None:
    for item in entries:
        if item["path"] not in ("*", path):
            continue
        pattern = item["symbol"]
        if pattern in ("*", symbol) or pattern == symbol.rsplit(".", 1)[-1]:
            if item.get("line") in (None, line):
                return item
    return None


def _project_style(name: str) -> bool:
    """项目自有符号的形态：带下划线、非 dunder。

    Qt/第三方基类提供的是 camelCase 或单词名（``statusBar`` / ``close``），
    带下划线的名字只可能来自本项目代码。这是有意的启发式：它让「新写的
    项目符号还没定义」判红，同时不把外部基类的成员误判成缺失；边界写进
    文档，反例进白名单。
    """
    return "_" in name.strip("_") and not (name.startswith("__") and name.endswith("__"))


def run(repo: Path, rev: str) -> tuple[int, dict]:
    global _CHECKER
    sha = _resolve_sha(repo, rev)
    checker = _CHECKER = Checker(repo, sha)
    try:
        return _run_with(checker, repo, sha)
    finally:
        checker._reader.close()


def _run_with(checker: Checker, repo: Path, sha: str) -> tuple[int, dict]:
    allow = _allowlist_load(DEFAULT_ALLOWLIST)
    changed = _changed_py_lines(repo, sha)

    missing: list[dict] = []
    unresolved: list[dict] = []
    external = 0
    ok = 0
    allowed: list[dict] = []
    checked = 0

    for path, lines in changed.items():
        module = path[:-3].replace("/", ".")
        if checker.tree(module) is None:
            continue
        ctx = _FileCtx(checker, path)
        tree = checker.tree(ctx.module)
        if tree is None:  # pragma: no cover - 上面已判
            continue
        list(_walk_with_class(tree))            # 给节点打上所在类标记
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        groups: dict[int, tuple[ast.AST, list[ast.Call]]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or node.lineno not in lines:
                continue
            inner = _enclosing_function(funcs, node)
            key = id(inner) if inner is not None else 0
            groups.setdefault(key, (inner or tree, []))[1].append(node)
        for owner, calls in groups.values():
            ctx.enter(owner)
            for node in calls:
                checked += 1
                verdict, symbol, note = checker.check_call(node, ctx)
                record = {"path": path, "line": node.lineno, "symbol": symbol, "note": note}
                if verdict == "missing":
                    hit = _allowlisted(allow, path, symbol, node.lineno)
                    if hit:
                        allowed.append({**record, "reason": hit["reason"]})
                    else:
                        missing.append(record)
                elif verdict == "unresolved":
                    unresolved.append(record)
                elif verdict == "external":
                    external += 1
                elif verdict == "ok":
                    ok += 1

    status = "red" if missing else "green"
    report = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sha": sha,
        "status": status,
        "checked": checked,
        "ok": ok,
        "external": external,
        "missing": missing,
        "unresolved": unresolved,
        "allowlisted": allowed,
    }
    code = EXIT_MISSING if missing else EXIT_OK
    return code, report


def _enclosing_function(funcs: list[ast.AST], node: ast.AST) -> ast.AST | None:
    """调用点所在的最内层函数（用于还原局部变量类型）。"""
    best = None
    best_line = -1
    for candidate in funcs:
        end = getattr(candidate, "end_lineno", candidate.lineno)
        if candidate.lineno <= node.lineno <= end and candidate.lineno > best_line:
            best, best_line = candidate, candidate.lineno
    return best


def _append_ledger(repo: Path, report: dict) -> None:
    path = _ledger_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False) + "\n")


def _print(report: dict, ledger: Path) -> None:
    sha = report["sha"][:8]
    missing, unresolved = report["missing"], report["unresolved"]
    if report["status"] == "green":
        line = (
            f"[符号] {sha} 绿：核查 {report['checked']} 处调用，"
            f"存在 {report['ok']} 处，外部 {report['external']} 处未核查"
        )
        if unresolved:
            line += f"，无法判定 {len(unresolved)} 处（未计入存在性结论，明细见台账）"
        if report["allowlisted"]:
            line += f"，白名单 {len(report['allowlisted'])} 条"
        print(line + f"；台账 {ledger}")
        return
    print("=" * 68)
    print(f"[符号] {sha} 红：缺失 {len(missing)} 处，无法判定 {len(unresolved)} 处，"
          f"核查 {report['checked']} 处（存在 {report['ok']}，外部 {report['external']} 未核查）")
    for item in missing:
        print(f"  ✗ 缺失 {item['path']}:{item['line']}  {item['symbol']}")
        print(f"      {item['note']}")
    for item in unresolved[:5]:
        print(f"  ? 无法判定 {item['path']}:{item['line']}  {item['symbol']}  ({item['note']})")
    if len(unresolved) > 5:
        print(f"  ? ……另有 {len(unresolved) - 5} 处无法判定")
    for item in report["allowlisted"]:
        print(f"  · 白名单 {item['path']}:{item['line']}  {item['symbol']}  理由：{item['reason']}")
    print(f"  台账：{ledger}")
    print("=" * 68)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="提交后符号存在性检查（静态）")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 结果")
    parser.add_argument("--no-ledger", action="store_true")
    parser.add_argument("--max-ms", type=int, default=0,
                        help="超过该耗时按错误退出（0 = 不设限）")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    started = time.monotonic()
    try:
        code, report = run(repo, args.commit)
    except Exception as exc:  # noqa: BLE001 - 检查自身失败必须响亮
        elapsed = int((time.monotonic() - started) * 1000)
        print(f"[符号] 检查本身失败：{exc.__class__.__name__}: {exc}", file=sys.stderr)
        print(f"[符号] 这不是通过：耗时 {elapsed} ms，提交 {args.commit}", file=sys.stderr)
        return EXIT_ERROR
    elapsed = int((time.monotonic() - started) * 1000)
    report["elapsed_ms"] = elapsed
    if not args.no_ledger:
        _append_ledger(repo, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print(report, _ledger_path(repo))
        print(f"[符号] 耗时 {elapsed} ms")
    if args.max_ms and elapsed > args.max_ms:
        print(f"[符号] 超出预算 {args.max_ms} ms（实际 {elapsed} ms）", file=sys.stderr)
        return EXIT_ERROR
    return code


if __name__ == "__main__":
    sys.exit(main())
