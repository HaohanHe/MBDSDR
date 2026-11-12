#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""死符号检测器：把「零引用」拆成 真死 / 动态可达 / 保留 三类，并挂棘轮。

背景：2026-11-13 的测绘按「符号引用 0 次」报出 526 条，那个数**不可信**——
`getattr` 拼名、Qt 的 `connectSlotsByName` 按名连槽、命令表/插件注册表、
`eval/exec`、文档与配置里的名字引用全都落在符号表之外，删了就是运行时才炸。

三类判据（全部结构性、可复跑，不看名字像不像）：

**动态可达**（不可删，任一命中）
  D1 名字作为字符串常量出现过（`getattr(o, "x")`、`hasattr`、`eval`、命令表、
     插件注册表、序列化字段名、`__all__`…；f-string 的常量片段也算）；
  D2 名字以任何一个 `getattr/setattr/hasattr/delattr/eval/exec/import_module`
     的字符串参数为前缀（`getattr(self, "_preset_" + n)` → `_preset_*` 全部可达）；
  D3 Qt 自动连接槽：`on_<对象>_<信号>` 命名；
  D4 装饰器是 `pyqtSlot` / `Slot`（含带参数的调用形态）；
  D5a 运行时按名解析：名字出现在**代码真按路径读过**的配置文件里（命令表、
      插件注册表、序列化字段名、配置键）。判据是结构性的——配置文件的相对
      路径或文件名必须出现在某个代码字符串常量里，才算「运行时会被取到」；
      没有任何代码引用的 JSON（死真值源）整份不收。
  D5b 仅文档提及：只出现在 `docs/**/*.md` 的文字里，**不算可达**。
      这类名字落回「真死候选」，reason 记 ``D5b`` 以便和「从未被提及」区分。

**保留**（不删，理由必须落库）
  R1 `__*__`、`main`、`test_*`；
  R2 出现在任一模块的 `__all__` 里（公共 API）；
  R3 类的某个基类是 Qt 基类或 `Protocol` / `ABC`（协议实现、外部回调面）；
  R4 `docs/dead-symbols-waivers.json` 里逐条登记（必须写 reason + review_by）。

**真死**：以下条件全不满足——无跨文件引用、无同文件引用（声明本身不算）、
不在 D1–D4 / D5a、不在 R1–R7。**只有这一类允许删。**

覆盖缺口：解析失败的文件不进入引用源，会静默压低真死数。`--ratchet` 必须
显式打印「N 个文件未纳入引用源」，缺口不许静默。

棘轮：`docs/dead-symbols-baseline.json` 记真死总数与按文件分布，只许降不许升。

用法::

    python scripts/lint_dead_symbols.py                 # 统计 + 逐条真死
    python scripts/lint_dead_symbols.py --json out.json
    python scripts/lint_dead_symbols.py --ratchet       # 对比基线，升了就退出 1
    python scripts/lint_dead_symbols.py --root <dir>    # 指向测试夹具

退出码：0 正常；1 棘轮红了（真死条数超过基线）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

SRC_ROOTS = ("desktop", "mbdsdr_ai", "scripts", "tools")
SKIP_DIRS = {"__pycache__", ".git", "build", "build_cpp", "third_party",
             "node_modules", ".pytest_cache", ".venv", "dist", "release"}
DYNAMIC_CALLS = {"getattr", "setattr", "hasattr", "delattr", "eval", "exec",
                 "compile", "__import__", "import_module"}
QT_BASES = {"QObject", "QWidget", "QMainWindow", "QDialog", "QFrame", "QAbstractItemModel",
            "QAbstractTableModel", "QAbstractListModel", "QGraphicsItem", "QQuickItem",
            "QStyledItemDelegate", "QSortFilterProxyModel"}
PROTO_BASES = {"Protocol", "ABC", "ABCMeta"}
VISITOR_BASES = {"NodeVisitor", "NodeTransformer", "ASTVisitor"}
HTTP_BASES = {"BaseHTTPRequestHandler", "SimpleHTTPRequestHandler", "HTTPRequestHandler"}
PROPERTY_DECOS = {"property", "cached_property", "lru_cache"}
DOC_GLOB_DIRS = ("docs",)


def _is_test(rel: str) -> bool:
    return rel.startswith("tests/") or "/tests/" in rel or os.path.basename(rel).startswith("test_")


def _iter_py(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _string_parts(node: ast.AST) -> list[str]:
    """取出节点里的字符串常量（f-string 取常量片段，`+` 拼接逐段取）。"""
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _decorator_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


class Scanner:
    def __init__(self, root: Path):
        self.root = root
        self.defs: list[dict] = []                 # 候选符号
        self.all_names: dict[str, set[str]] = defaultdict(set)
        self.string_consts: set[str] = set()
        self.code_strings: set[str] = set()        # 全部代码字符串常量（判「配置真被读过」）
        self.dynamic_prefixes: set[str] = set()
        self.doc_tokens: set[str] = set()          # D5b 仅 .md 提及
        self.config_tokens: set[str] = set()       # D5a 运行时按名解析
        self.exported: set[str] = set()
        self.scanned = 0
        self.parse_errors: list[str] = []

    # ---------- 扫描 ----------
    def scan(self) -> None:
        for path in _iter_py(self.root):
            rel = path.relative_to(self.root).as_posix()
            try:
                # utf-8-sig：2026-11-13 实测 10 个文件带 BOM（U+FEFF），用 utf-8
                # 解码后 ast.parse 直接报 invalid non-printable character，
                # 这 10 个文件就静默退出了引用源。BOM 对解释器合法，所以不是
                # 文件坏，是读法错——在这里兜住，别让缺口变成不可见。
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            except (SyntaxError, UnicodeDecodeError) as exc:
                self.parse_errors.append(f"{rel}: {exc}")
                continue
            self.scanned += 1
            self._collect_refs(tree, rel)
            # 测试模块只当引用源，不当候选：测试里的辅助函数没人调是常态，
            # 计入棘轮只会稀释生产侧的真死数（测绘口径：生产代码）。
            if rel.split("/")[0] in SRC_ROOTS and not _is_test(rel):
                self._collect_defs(tree, rel)

    def _collect_refs(self, tree: ast.Module, rel: str) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                self.all_names[node.id].add(rel)
            elif isinstance(node, ast.Attribute):
                self.all_names[node.attr].add(rel)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.all_names[alias.name].add(rel)
            elif isinstance(node, ast.Call):
                name = _call_name(node)
                if name in DYNAMIC_CALLS:
                    for arg in node.args:
                        for text in _string_parts(arg):
                            self._add_prefix(text)
                for text in _string_parts(node):
                    if len(text) < 80:
                        self.string_consts.add(text)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        for text in _string_parts(node.value):
                            self.exported.add(text)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # 全部字符串常量（不限调用参数）：判断「某个配置文件是否真被
                # 代码按路径读过」要靠它，路径常量常写在模块级赋值里。
                if len(node.value) <= 300:
                    self.code_strings.add(node.value)

    def _add_prefix(self, text: str) -> None:
        """`_preset_` / `_preset_{n}` / `_preset_%s` 都归一成前缀 `_preset_`。"""
        cut = len(text)
        for marker in ("{", "%"):
            idx = text.find(marker)
            if idx != -1:
                cut = min(cut, idx)
        prefix = text[:cut]
        if len(prefix) >= 3:
            self.dynamic_prefixes.add(prefix)

    def _collect_defs(self, tree: ast.Module, rel: str) -> None:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_def(rel, node, node.name, ())
            elif isinstance(node, ast.ClassDef):
                bases = {_call_name(b) if isinstance(b, ast.Call) else
                         (b.id if isinstance(b, ast.Name) else getattr(b, "attr", ""))
                         for b in node.bases}
                self._add_def(rel, node, node.name, (), bases=bases)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._add_def(rel, child, child.name, (node.name,), bases=bases)

    def _add_def(self, rel: str, node: ast.AST, name: str, parents: tuple[str, ...],
                 bases: set[str] | None = None) -> None:
        self.defs.append({
            "path": rel, "line": node.lineno, "name": name,
            "qualname": ".".join((*parents, name)),
            "kind": "class" if isinstance(node, ast.ClassDef) else "func",
            "decorators": _decorator_names(node),
            "bases": bases or set(),
        })

    # ---------- 分类 ----------
    def classify(self) -> list[dict]:
        # R6：同名方法在别处（别的类/模块）也有定义 → 多实现/接口对齐，删除会破坏
        # 「同名同义」的契约（例如各 SDR 后端的 list_antennas）。按 distinct 限定名计。
        qualnames_by_name: dict[str, set[str]] = defaultdict(set)
        for d in self.defs:
            if d["kind"] == "func" and "." in d["qualname"]:
                qualnames_by_name[d["name"]].add(d["qualname"])
        override_names = {n for n, qs in qualnames_by_name.items() if len(qs) >= 2}
        rows = []
        for d in self.defs:
            name, rel = d["name"], d["path"]
            sites = self.all_names.get(name, set())
            # 同名符号的名字级判定：别处出现过（跨文件或同文件的使用点）即算已引用。
            # `def foo` 语句本身不产生 Name 节点，所以同文件命中一定是真实使用。
            # 代价是同名不同符号会互相「救活」——偏保守，宁可漏报不误删。
            referenced = bool(sites)
            reason = "ref" if referenced else self._dynamic_reason(d)
            if reason is None:
                reason = self._reserved_reason(d)
            if reason is None and d["kind"] == "func" and name in override_names:
                reason = "R6 同名方法在别处也有定义（接口/多实现对齐）"
            if reason is None and d["kind"] == "func" and "." in d["qualname"] \
                    and not name.startswith("_") \
                    and not d["qualname"].split(".")[0].startswith("_"):
                reason = "R7 公开类的公开方法（外部调用面，删了会破 API）"
            if reason is None:
                # D5b：只被 .md 文字提过，不算可达。仍按真死处理，但把原因
                # 写进 reason，避免和「从未被任何地方提过」混成同一类。
                reason = ("D5b 仅文档提及（不计可达）" if name in self.doc_tokens
                          else None)
                verdict = "dead"
            elif reason == "ref":
                verdict = "used"
            elif reason.startswith("R"):
                verdict = "reserved"
            else:
                verdict = "dynamic"
            rows.append({**d, "verdict": verdict, "reason": reason})
        return rows

    def _dynamic_reason(self, d: dict) -> str | None:
        name = d["name"]
        if name in self.string_consts:
            return "D1 字符串常量（getattr/注册表/序列化字段名）"
        for prefix in self.dynamic_prefixes:
            if name.startswith(prefix):
                return f"D2 动态前缀 {prefix}*"
        if name.startswith("on_"):
            return "D3 Qt connectSlotsByName 自动连接槽（on_*）"
        if d["bases"] & VISITOR_BASES and (name.startswith("visit_") or name == "generic_visit"):
            return "D6 ast.NodeVisitor 按名分发（visit_*）"
        if d["bases"] & HTTP_BASES and name.startswith(("do_", "handle_", "log_")):
            return "D7 HTTP 请求处理器按名回调（do_*/handle_*）"
        if d["decorators"] & {"pyqtSlot", "Slot", "slot"}:
            return "D4 pyqtSlot 装饰"
        if name in self.config_tokens:
            return "D5a 运行时按名解析（代码按路径读取的配置/命令表）"
        return None

    def _reserved_reason(self, d: dict) -> str | None:
        name = d["name"]
        if name.startswith("__") and name.endswith("__"):
            return "R1 双下划线协议"
        if name == "main" or name.startswith("test_"):
            return "R1 入口/测试命名"
        if name in self.exported:
            return "R2 __all__ 公共 API"
        if d["bases"] & QT_BASES:
            return "R3 Qt 基类实现（外部按名回调）"
        if d["bases"] & PROTO_BASES:
            return "R3 Protocol/ABC 实现"
        if d["decorators"] & PROPERTY_DECOS:
            return "R5 属性/缓存访问面（读取点可能不在符号表里）"
        return None


def _load_doc_tokens(root: Path) -> set[str]:
    """D5b：**只**扫 .md 文字里提到的标识符（反引号包着，或名字后紧跟 `(`）。

    这些名字不算可达：文档写了名字不等于运行时按名取。收进来只是为了让
    真死报告能区分「只被文档提过」与「从未被提过」。
    """
    tokens: set[str] = set()
    ident = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")
    call = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\(")
    for dirname in DOC_GLOB_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for m in ident.finditer(text):
                tokens.add(m.group(1).split(".")[-1])
            for m in call.finditer(text):
                tokens.add(m.group(1))
    return tokens


def _iter_json(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".json"):
                yield Path(dirpath) / name


def _load_config_tokens(root: Path, code_strings: set[str]) -> set[str]:
    """D5a：只收「代码真按路径读过」的配置文件里的名字。

    结构性判据：配置文件的相对路径或文件名必须出现在某个代码字符串常量
    里，才认定运行时会被读到。没有任何代码引用的 JSON（死真值源）整份
    不收——否则「文档/数据里写了名字」就会伪装成「运行时按名取」。
    """
    blob = "\n".join(code_strings)
    tokens: set[str] = set()
    for path in _iter_json(root):
        rel = path.relative_to(root).as_posix()
        if rel not in blob and path.name not in blob:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in re.finditer(r'"([A-Za-z_][A-Za-z0-9_]{2,})"', text):
            tokens.add(m.group(1))
    return tokens


def _load_waivers(root: Path) -> dict[str, dict]:
    path = root / "docs" / "dead-symbols-waivers.json"
    if not path.is_file():
        return {}
    # utf-8-sig：豁免表/基线被编辑器存成带 BOM 时，utf-8 解码会把 U+FEFF
    # 留在 JSON 文本里直接报错（同 .py 的 BOM 问题）。
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return {f"{k}": v for k, v in (data.get("waivers") or {}).items()}


def run(root: Path) -> dict:
    scanner = Scanner(root)
    scanner.scan()
    scanner.doc_tokens = _load_doc_tokens(root)
    scanner.config_tokens = _load_config_tokens(root, scanner.code_strings)
    rows = scanner.classify()
    waivers = _load_waivers(root)
    for row in rows:
        key = f"{row['path']}::{row['qualname']}"
        if row["verdict"] == "dead" and key in waivers:
            row["verdict"] = "reserved"
            row["reason"] = f"R4 登记豁免：{waivers[key].get('reason', '')}"
    dead = [r for r in rows if r["verdict"] == "dead"]
    dynamic = [r for r in rows if r["verdict"] == "dynamic"]
    reserved = [r for r in rows if r["verdict"] == "reserved"]
    used = [r for r in rows if r["verdict"] == "used"]
    return {
        "root": str(root),
        "defs": len(rows),
        "dead": dead, "dynamic": dynamic, "reserved": reserved, "used": used,
        "d5a": [r for r in rows if (r["reason"] or "").startswith("D5a")],
        "d5b": [r for r in rows if (r["reason"] or "").startswith("D5b")],
        "scanned": scanner.scanned,
        "parse_errors": scanner.parse_errors,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="死符号检测：真死 / 动态可达 / 保留")
    ap.add_argument("--root", default=None, help="仓库根（测试夹具用）")
    ap.add_argument("--json", default=None, help="把结果写成 JSON")
    ap.add_argument("--ratchet", action="store_true", help="对比基线，真死条数上升退出 1")
    ap.add_argument("--show", default="dead", choices=["dead", "dynamic", "reserved", "none"])
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    result = run(root)
    counts = {k: len(result[k]) for k in ("dead", "dynamic", "reserved", "used")}
    print(f"符号定义 {result['defs']}；真死 {counts['dead']}；动态可达 {counts['dynamic']}；"
          f"保留 {counts['reserved']}；已引用 {counts['used']}")
    print(f"D5a 运行时按名解析 {len(result['d5a'])} 条（不可删）；"
          f"D5b 仅文档提及 {len(result['d5b'])} 条（计入真死候选）")
    total_py = result["scanned"] + len(result["parse_errors"])
    print(f"覆盖：共 {total_py} 个 .py，已解析 {result['scanned']}，"
          f"{len(result['parse_errors'])} 个文件未纳入引用源")
    for err in result["parse_errors"]:
        print(f"[warn] 未纳入引用源：{err}")

    if args.show != "none":
        for row in result[args.show]:
            print(f"  {row['path']}:{row['line']}\t{row['qualname']}\t{row['reason']}")

    payload = {k: result[k] for k in ("dead", "dynamic", "reserved", "used")}
    payload["defs"] = result["defs"]
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=list),
            encoding="utf-8")

    if args.ratchet:
        # 缺口不许静默：真死数只在「已纳入引用源」的文件上成立。有解析失败
        # 就必须在棘轮输出里点名，否则「真死 0」会被读成「扫干净了」。
        missing = len(result["parse_errors"])
        print(f"[ratchet] 覆盖：{missing} 个文件未纳入引用源"
              f"（已解析 {result['scanned']} / 共 {total_py} 个 .py）")
        if missing:
            for err in result["parse_errors"][:5]:
                print(f"[ratchet]   缺口：{err}")
            if missing > 5:
                print(f"[ratchet]   缺口：…另有 {missing - 5} 个（见 --show 前的 [warn] 行）")
        base_path = root / "docs" / "dead-symbols-baseline.json"
        if not base_path.is_file():
            print(f"[ratchet] 基线缺失：{base_path}")
            return 0
        baseline = json.loads(base_path.read_text(encoding="utf-8-sig"))
        limit = int(baseline.get("dead_total", 0))
        if counts["dead"] > limit:
            print(f"[ratchet] 红：真死 {counts['dead']} > 基线 {limit}"
                  f"（新出现 {counts['dead'] - limit} 条，只许降不许升）")
            return 1
        print(f"[ratchet] 绿：真死 {counts['dead']} <= 基线 {limit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
