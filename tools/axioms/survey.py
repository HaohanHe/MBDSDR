"""八维治理测绘：把仓库当前「实际是什么样」跑出来。

所有采集器只读仓库内容：源码 AST、git hooks、文档、已知红台账。
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .checks import build_axioms, note_skip, reset_skips
from .model import (
    GENERATED_ARTIFACTS,
    Axiom,
    Finding,
    Repo,
    Unit,
    _read,
    line_of,
    parse,
)

#: 本检查器自己的产物。它们「是否存在于工作区」取决于有没有跑过，
#: 不能当作「文档指向不存在的路径」来计数，否则同一棵树在跑基线前后各出一个数。
TOOL_ARTIFACTS = set(GENERATED_ARTIFACTS) | {
    "docs/axioms-baseline.json",
    "docs/axioms-accepted.json",
}

# ────────────────────────────── 维度 ──────────────────────────────


@dataclass
class Dimension:
    id: str
    title: str
    rule: str
    facts: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)

    def active(self) -> List[Finding]:
        return [f for f in self.findings if not f.waived]

    def waived(self) -> List[Finding]:
        return [f for f in self.findings if f.waived]


RULE_DIMENSION = {
    "A01": "D2", "A02": "D2", "A03": "D2", "A04": "D2", "A12": "D2", "A13": "D2",
    "A05": "D4", "A06": "D4", "A07": "D4", "A08": "D4", "A09": "D4",
    "A10": "D5", "A11": "D5", "A14": "D5",
}


def _dimension_of(rule: str) -> Optional[str]:
    """finding 的规则名归一：`A13-undecidable` 与 `A13` 属于同一维。

    维度由公理前缀决定；子规则名（判据无法判定这类）不能掉出维度，
    否则它会被静默丢弃、报告里看不到。
    """
    return RULE_DIMENSION.get(rule) or RULE_DIMENSION.get(rule.split("-")[0])


DIMENSIONS = [
    ("D1", "分层与依赖方向", "哪一层可以 import 哪一层；纯 Python 层不得依赖 Qt。"),
    ("D2", "真值源清单", "每个事实只能有一个产生点；同一事实两份实现即为违规。"),
    ("D3", "接口契约与命名", "模块对外只暴露登记过的东西；同名不同职、同概念两名都算违规。"),
    ("D4", "门禁与验收", "门禁必须真的挂在必经路径上；守卫必须可成立、可反证。"),
    ("D5", "流程与协作", "引用必须可复核；已知红必须带转绿条件与复跑命令。"),
    ("D6", "目录与文档结构", "文档里的事实陈述必须与代码一致；引用的路径必须存在。"),
    ("D7", "任务分派与优先级", "下一笔做什么由规则算出来，不靠最后回消息的人。"),
    ("D8", "豁免与债务登记", "所有「暂时不做」必须有理由、到期、批准人。"),
]

PKG_ROOTS = ("desktop", "mbdsdr_ai", "scripts", "tools", "tests")
QT_ROOTS = {"PySide6", "PySide2", "PyQt5", "PyQt6", "qtpy", "shiboken6"}


def _rel_module(rel: str) -> str:
    return rel[:-3].replace("/", ".") if rel.endswith(".py") else rel


def _imports(unit: Unit) -> List[Tuple[str, int]]:
    tree = parse(unit)
    if tree is None:
        return []
    return _imports_of(unit.path, tree)


def _imports_of(path: str, tree: ast.Module) -> List[Tuple[str, int]]:
    """从已解析的语法树取 import 边（避免再读一次空文本的 ``Unit``）。"""
    mod_parts = _rel_module(path).split(".")
    out: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            base = ""
            if node.level:
                keep = mod_parts[: max(1, len(mod_parts) - node.level)]
                base = ".".join(keep)
                if node.module:
                    base += "." + node.module
            else:
                base = node.module or ""
            if base:
                out.append((base, node.lineno))
            for a in node.names:
                if a.name != "*" and base:
                    out.append((base + "." + a.name, node.lineno))
    return out


def _root_of(module: str) -> str:
    return module.split(".")[0] if module else ""


# ────────────────────────────── D1 ──────────────────────────────


def collect_d1(repo: Repo) -> Dimension:
    dim = Dimension("D1", "分层与依赖方向", DIMENSIONS[0][2])
    units = repo.py_units(("desktop", "mbdsdr_ai", "scripts", "tools", "tests"))
    edges: Dict[Tuple[str, str], int] = {}
    modules: Dict[str, ast.Module] = {}
    qt_sites: List[Tuple[str, int, str]] = []

    qt_seen: Set[Tuple[str, int]] = set()
    for unit in units:
        pkg = unit.path.split("/")[0]
        for module, lineno in _imports(unit):
            root = _root_of(module)
            if root in PKG_ROOTS:
                edges[(pkg, root)] = edges.get((pkg, root), 0) + 1
            if root in QT_ROOTS or root.startswith("PySide") or root.startswith("PyQt"):
                if pkg == "mbdsdr_ai" and (unit.path, lineno) not in qt_seen:
                    qt_seen.add((unit.path, lineno))
                    qt_sites.append((unit.path, lineno, module))
        tree = parse(unit)
        if tree is not None:
            modules[unit.path] = tree

    dim.facts.append(f"模块 {len(units)} 个；包间依赖边 {sum(edges.values())} 条")
    for (src, dst), n in sorted(edges.items(), key=lambda kv: -kv[1])[:12]:
        dim.facts.append(f"依赖 {src} -> {dst}：{n} 条")
    # 判据（避免再出现「规则不管的灰区」）：
    #   tests/scripts -> tools 允许（用例与治理脚本本来就用开发工具）；
    #   desktop -> mbdsdr_ai 允许（界面层单向依赖核心）；
    #   mbdsdr_ai -> tools 一律应修——pip 安装 mbdsdr_ai 的人拿不到 tools，
    #   核心库会因此不可导入。测试放错包时把用例迁到 tests/，不许延迟 import。
    dim.facts.append("允许方向：tests/scripts -> tools、desktop -> mbdsdr_ai；"
                     "mbdsdr_ai -> tools 一律应修。")

    for (src, dst), n in sorted(edges.items()):
        if src == "mbdsdr_ai" and dst == "tools":
            dim.findings.append(Finding(
                "D1-core-depends-tools", "mbdsdr_ai/", 0, f"{n} 处 import",
                "依赖方向反了：核心库反向依赖开发工具层；pip 安装 mbdsdr_ai 的人"
                "拿不到 tools/，核心包会直接不可导入。",
                "把被依赖的常量/函数下沉到 mbdsdr_ai；端到端用例迁到 tests/"
                "（tests -> tools 是允许方向）；不得用函数体内延迟 import 掩盖。",
            ))
        if src == "mbdsdr_ai" and dst == "desktop":
            dim.findings.append(Finding(
                "D1-reverse", "mbdsdr_ai/", 0, f"{n} 处 import",
                "依赖方向反了：纯 Python 后端依赖了 Qt 桌面层，后端无法脱离桌面单独测试/复用。",
                "把被依赖的部分下沉到 mbdsdr_ai（或独立纯模块），desktop 只允许单向依赖 mbdsdr_ai。",
            ))
        if src in {"tools", "tests"} and dst == "desktop":
            dim.findings.append(Finding(
                "D1-test-reverse", f"{src}/", 0, f"{n} 处 import",
                f"{src} 反向依赖 desktop，测试/工具会被 UI 生命周期绑住。",
                "只依赖 mbdsdr_ai 的纯接口；确需 UI 的用例放在 tests/ui 并显式标注。",
                "review",
            ))
    for path, lineno, module in qt_sites:
        dim.findings.append(Finding(
            "D1-qt-in-core", path, lineno, f"import {module}",
            "纯 Python 层（mbdsdr_ai）直接 import 了 Qt；这层一旦被 CLI/MCP 复用就会拖入 GUI 依赖。",
            "把 Qt 相关实现移到 desktop/，mbdsdr_ai 只保留可注入的接口。",
        ))

    # 模块级环。三个口径缺一不可，否则环会被系统性漏报：
    #   * desktop 内部大量用裸模块名（``from tokens import tokens``），由
    #     desktop/__init__.py 的 sys.path 垫片支持；只认 ``desktop.`` 前缀
    #     等于漏掉大半内部边（实测 62 条 vs 158 条）。
    #   * 先建齐所有节点再加边：原实现边加边建图，指向「尚未入图节点」的边
    #     会被静默丢弃，环检测随文件遍历顺序漂移。
    #   * 函数体内的延迟 import 同样算边：依赖关系没有变，只是检查器看不见。
    graph: Dict[str, Set[str]] = {}
    desktop_bare: Dict[str, str] = {}
    for rel in modules:
        if not rel.startswith(("desktop/", "mbdsdr_ai/")):
            continue
        mod = _rel_module(rel)
        graph.setdefault(mod, set())
        if mod.startswith("desktop.") and mod.count(".") == 1:
            desktop_bare[mod.split(".", 1)[1]] = mod

    def _target(module: str) -> str:
        """裸模块名（垫片可见的 desktop 顶层模块）归一成 ``desktop.*``。"""
        root, _, rest = module.partition(".")
        if root in desktop_bare:
            return desktop_bare[root] + ("." + rest if rest else "")
        return module

    for rel in modules:
        if not rel.startswith(("desktop/", "mbdsdr_ai/")):
            continue
        mod = _rel_module(rel)
        for module, _ in _imports_of(rel, modules[rel]):
            target = _target(module)
            if target.startswith(("desktop.", "mbdsdr_ai.")) and target in graph:
                graph[mod].add(target)
    sccs = _tarjan(graph)
    # 环的「代表模块」必须稳定：集合/字典迭代顺序受字符串哈希随机化影响，
    # 同一棵树两次跑会选到不同成员，棘轮就会把一个环报成「新增 + 消失」。
    cycles = [sorted(c) for c in sccs if len(c) > 1]
    cycles.sort(key=lambda c: c[0])
    dim.facts.append(f"模块级强连通分量：{len(cycles)} 个")
    for comp in cycles:
        dim.findings.append(Finding(
            "D1-cycle", comp[0].replace(".", "/") + ".py", 0,
            # 指纹片段必须稳定：成员数量会随「修掉一个模块」变化，把它写进
            # snippet 会让同一个环在缩小一成员时被记成「消失 + 新增」。
            # 环的身份 = 代表模块（path）；成员数只写进 why 供人读。
            "模块级 import 环",
            f"与另外 {len(comp) - 1} 个模块互相依赖（代表模块取字典序最小者）："
            + "、".join(comp[1:]) + "。任何一侧的初始化顺序都会影响另一侧；"
            "单独改一处无法预测影响面。",
            "把常量/状态下沉到下层，或改依赖注入；不得用函数体内延迟 import "
            "掩盖——依赖没有变，只是检查器看不见。",
            severity="review",
        ))
    return dim


def _tarjan(graph: Dict[str, Set[str]]) -> List[List[str]]:
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    stack: List[str] = []
    on_stack: Set[str] = set()
    result: List[List[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(graph.get(v, ())):  # type: ignore[arg-type]
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp: List[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            result.append(comp)

    for node in sorted(graph):
        if node not in index:
            strong(node)
    return result


# ────────────────────────────── D2 ──────────────────────────────


CONST_ASSIGN = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*=\s*(.+)$")

#: 真值源登记表。登记意味着「这个量的合法产生点就是这些文件」，其余产生点
#: 各算一条违规；登记了但文件不存在/不产生这个量，算登记失真。
DEFAULT_TRUTH_SOURCES = "docs/truth-sources.json"
#: 登记表允许把产生点指到这两个文件：它们是唯一读数口径的出口。
CANONICAL_SOURCES = {"desktop/readouts.py", "desktop/constants.py"}


def _declared_produces(repo: Repo, path: str, fact: str) -> bool:
    """登记的文件到底有没有产生这个量。

    不去信任登记表自己的声明：看文件里有没有一个与事实同名的产生函数
    （`format_frequency_hz` 之于频率）。这样「把登记指到 readouts.py，
    但它根本没有采样率口径」会被抓出来，而不是被 CANONICAL 名单放行。
    """
    full = os.path.join(repo.root, path)
    if not os.path.exists(full):
        return False
    try:
        with open(full, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    return any(_fact_of(m.group(1), "") == fact
               for m in re.finditer(r"^def\s+(\w+)", text, re.M))

#: 读数的「量」按事实类别分，不按单位分——只按 Hz 分组会把全仓 244 个文件
#: 算成同一个量，登记表就失去意义；按 (事实,单位) 分又会把同一个量碎成
#: 「频率（MHz）/频率（kHz）」两组，同样没法登记。
D2_FACTS = (
    ("频率", re.compile(r"freq|frequency|center_hz|tune_hz", re.I)),
    ("带宽", re.compile(r"\bbw\b|bandwidth|band_width", re.I)),
    ("增益", re.compile(r"(?<![a-z])gain(?![a-z])|gain_db", re.I)),
    ("采样率", re.compile(r"sample_?rate|rate_hz|\bsr\b|ms/s", re.I)),
    ("电平", re.compile(r"dbfs|dbm|dbµv", re.I)),
)
D2_UNITS = ("GHz", "MHz", "kHz", "Hz", "dBFS", "dBm", "dBµV", "dB")
D2_VISIBLE = re.compile(
    r"setText|setToolTip|setWindowTitle|setStatusTip|setPlaceholderText|showMessage|"
    r"set_status|statusBar|QLabel|label|text\(",
)


def _load_truth_sources(repo: Repo) -> Dict[str, Set[str]]:
    full = os.path.join(repo.root, DEFAULT_TRUTH_SOURCES)
    if not os.path.exists(full):
        return {}
    try:
        with open(full, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    sources = data.get("sources")
    if not isinstance(sources, dict):
        return {}
    out: Dict[str, Set[str]] = {}
    for key, value in sources.items():
        if isinstance(value, dict):
            prods = value.get("producers", [])
        else:
            prods = value
        if isinstance(prods, str):
            prods = [prods]
        out[str(key)] = {str(p) for p in prods if p}
    return out


def _fact_of(expr: str, context: str) -> str:
    text = f"{expr} {context}"
    for name, pattern in D2_FACTS:
        if pattern.search(text):
            return name
    return ""


def collect_d2(repo: Repo, base: Sequence[Finding]) -> Dimension:
    dim = Dimension("D2", "真值源清单", DIMENSIONS[1][2])
    dim.findings.extend(f for f in base if _dimension_of(f.rule) == "D2")

    units = repo.py_units(("desktop", "mbdsdr_ai"))
    # key = 事实（频率/带宽/…）；value = 产生该读数的文件集合。
    # 只认「用户可见读数」：一段 f-string 出现在 setText/日志/配置说明里，
    # 才是在产生别人会看到的字符串；纯内部提示不算第二个真值源。
    producers: Dict[str, Set[str]] = {}
    for unit in units:
        if unit.path in CANONICAL_SOURCES:
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literals = "".join(
                v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if not any(u in literals for u in D2_UNITS):
                continue
            # 与 A01 同口径：看整行源码，而不是只看 f-string 片段——
            # 多行调用时 `setText(` 在上一行，只看片段会把用户可见读数漏掉。
            line_text = line_of(unit, node.lineno)
            if not D2_VISIBLE.search(line_text):
                continue
            expr = " ".join(
                (ast.get_source_segment(unit.text, v.value) or "")
                for v in node.values if isinstance(v, ast.FormattedValue)
            )
            fact = _fact_of(expr, line_text)
            if not fact:
                continue
            producers.setdefault(fact, set()).add(unit.path)

    registered = _load_truth_sources(repo)
    dim.facts.append(
        f"真值源登记表：{len(registered)} 个量（{DEFAULT_TRUTH_SOURCES}）"
        if registered else f"真值源登记表：未建立（{DEFAULT_TRUTH_SOURCES}）"
    )

    # 1) 登记失真：登记的路径不存在，或该文件并不产生这个量。
    for key, declared in sorted(registered.items()):
        for path in sorted(declared):
            if not os.path.exists(os.path.join(repo.root, path)):
                dim.findings.append(Finding(
                    "D2-truth-source-stale", DEFAULT_TRUTH_SOURCES, 0,
                    f"{key} 登记失真：{path} 不存在",
                    "登记表指向了一个不存在的路径；按登记表去读唯一产生点会直接失败。",
                    "把登记改成真实存在的产生文件，或删掉这条登记。",
                ))
            elif not _declared_produces(repo, path, key):
                dim.findings.append(Finding(
                    "D2-truth-source-stale", DEFAULT_TRUTH_SOURCES, 0,
                    f"{key} 登记失真：{path} 里没有产生这个量的函数",
                    "登记表把「不产生这个量」的文件标成了唯一产生点，检查器会因此放过真正的多产生点。",
                    "把登记改成实际产生这个量的文件，或先在该文件补一个与事实同名的产生函数。",
                ))

    # 2) 未登记：该量的全部产生者各算一条；已登记：登记文件之外的产生者各算一条。
    for key, paths in sorted(producers.items()):
        legal = {p for p in registered.get(key, set())
                 if p in paths or _declared_produces(repo, p, key)}
        offenders = sorted(p for p in paths if p not in legal)
        if not offenders:
            continue
        fact = key
        declared = sorted(registered.get(key, set()))
        # 未登记且只有一个产生者：还不构成「同一事实两份实现」，不报。
        if not declared and len(paths) < 2:
            continue
        dim.facts.append(
            f"{fact}：{len(paths)} 个文件各自拼装"
            + (f"，登记 {len(legal)} 个合法产生点" if legal else "，未登记真值源")
        )
        for path in offenders:
            others = [p for p in sorted(paths) if p != path]
            if declared:
                why = (f"登记的真值源是 {', '.join(declared)}；其余 {len(offenders)} 处仍自行拼装："
                       f"{', '.join(others)}。")
            else:
                why = (f"同一事实共 {len(paths)} 个文件各自产生：{', '.join(sorted(paths))}；"
                       "尚未登记真值源。")
            dim.findings.append(Finding(
                "D2-multi-producer", path, 0,
                f"{fact}读数在本文件自行拼装",
                why,
                "改为调用登记的唯一产生函数（desktop/readouts.py），或把本文件的产生点登记进 "
                f"{DEFAULT_TRUTH_SOURCES}。",
            ))

    # 同名常量不同值
    consts: Dict[str, Dict[str, List[str]]] = {}
    for unit in units:
        for lineno, line in enumerate(unit.text.splitlines(), 1):
            m = CONST_ASSIGN.match(line.strip())
            if not m:
                continue
            name, value = m.group(1), m.group(2).strip().rstrip(",")
            if value.startswith(('"', "'", "f'", 'f"')):
                continue
            consts.setdefault(name, {}).setdefault(value, []).append(f"{unit.path}:{lineno}")
    dual = {k: v for k, v in consts.items() if len(v) > 1 and len(k) >= 4}
    dim.facts.append(f"同名常量存在多值：{len(dual)} 组")
    for name, values in dual.items():
        locs = [loc for v in values.values() for loc in v]
        dim.findings.append(Finding(
            "D2-dual-constant", locs[0].split(":")[0], int(locs[0].split(":")[1]),
            f"{name} 有 {len(values)} 个不同取值（{', '.join(str(v)[:20] for v in list(values)[:4])}）",
            "同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。",
            "先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。",
            "review",
        ))
    return dim


# ────────────────────────────── D3 ──────────────────────────────

ALIAS_PAIRS = [
    ("bw", "bandwidth"), ("freq", "frequency"), ("sr", "sample_rate"),
    ("gain", "gain_db"), ("dev", "device"), ("cfg", "config"),
    ("amp", "amplitude"), ("lvl", "level"),
]
COMMON_NAMES = {
    "__init__", "run", "start", "stop", "close", "read", "write", "get", "set",
    "setup", "update", "reset", "apply", "connect", "disconnect", "open", "save",
    "load", "handle", "process", "main", "test", "describe", "to_dict", "from_dict",
}


def collect_d3(repo: Repo) -> Dimension:
    dim = Dimension("D3", "接口契约与命名", DIMENSIONS[2][2])
    units = repo.py_units(("desktop", "mbdsdr_ai", "scripts", "tools"))
    modules_no_contract: List[Tuple[str, int]] = []
    funcs: Dict[str, List[Tuple[str, int, str]]] = {}
    alias_sites: Dict[str, Dict[str, Set[str]]] = {a: {"a": set(), "b": set()} for a in ALIAS_PAIRS}

    for unit in units:
        tree = parse(unit)
        if tree is None:
            continue
        if unit.path.startswith(("desktop/", "mbdsdr_ai/")):
            public = [
                n for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.name.startswith("_")
            ]
            has_all = any(isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "__all__" for t in n.targets) for n in tree.body)
            if len(public) >= 8 and not has_all:
                modules_no_contract.append((unit.path, public[0].lineno))
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and not n.name.startswith("_") and n.name not in COMMON_NAMES:
                    if len(n.body) >= 5:
                        funcs.setdefault(n.name, []).append((unit.path, n.lineno, ast.dump(n, include_attributes=False)))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                names.add(n.id)
            elif isinstance(n, ast.arg):
                names.add(n.arg)
            elif isinstance(n, ast.Attribute):
                names.add(n.attr)
        for a, b in ALIAS_PAIRS:
            if a in names:
                alias_sites[(a, b)]["a"].add(unit.path)
            if b in names:
                alias_sites[(a, b)]["b"].add(unit.path)

    dim.facts.append(f"公开函数/类 >=8 但未声明 __all__ 的模块：{len(modules_no_contract)} 个")
    for path, line in modules_no_contract:
        dim.findings.append(Finding(
            "D3-no-contract", path, line, "模块未声明 __all__",
            "模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。",
            "补 __all__，只列真正对外承诺的名字。",
            "review",
        ))

    role_conflicts = []
    for name, items in funcs.items():
        paths = {p for p, _, _ in items}
        bodies = {b for _, _, b in items}
        if len(paths) >= 2 and len(bodies) >= 2:
            role_conflicts.append((name, items))
    dim.facts.append(f"同名不同职的公开函数：{len(role_conflicts)} 组")
    for name, items in role_conflicts:
        first = items[0]
        second = items[1]
        dim.findings.append(Finding(
            "D3-name-role", second[0], second[1],
            f"{name} 与 {first[0]}:{first[1]} 同名但实现不同",
            "同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。",
            "保留职责更明确的一方，另一方改名（名字里带上领域限定）。",
            "review",
        ))

    for (a, b), groups in alias_sites.items():
        if len(groups["a"]) >= 2 and len(groups["b"]) >= 2:
            sample = sorted(groups["a"])[0]
            dim.facts.append(f"同概念两名 {a}/{b}：各在 {len(groups['a'])}/{len(groups['b'])} 个文件出现")
            dim.findings.append(Finding(
                "D3-concept-alias", sample, 0, f"{a} 与 {b} 同时使用",
                "同一概念两个名字，跨模块搜索/替换必然漏掉另一半。",
                "选一个名字写进术语表，另一个在公共接口中移除。",
                "review",
            ))
    return dim


# ────────────────────────────── D4 ──────────────────────────────


def _git_path(repo: Repo, name: str) -> str:
    out = repo.git("rev-parse", "--git-path", name).strip()
    return out if os.path.isabs(out) else os.path.join(repo.root, out)


def _hook_files(repo: Repo) -> List[str]:
    hooks_dir = _git_path(repo, "hooks")
    if not os.path.isdir(hooks_dir):
        return []
    return [os.path.join(hooks_dir, f) for f in os.listdir(hooks_dir) if not f.endswith(".sample")]


def _script_names(repo: Repo) -> List[str]:
    base = os.path.join(repo.root, "scripts")
    if not os.path.isdir(base):
        return []
    return sorted(
        f for f in os.listdir(base)
        if f.endswith(".py")
        and re.match(r"(check_|lint_|audit_|guard|gate|verify_|report_)", f)
    )


def _has_main(repo: Repo, name: str) -> bool:
    return bool(re.search(r"^def main\s*\(", _read(os.path.join(repo.root, "scripts", name)), re.M))


def _gate_scripts(repo: Repo) -> Tuple[List[str], List[str]]:
    """scripts/ 下按命名收进来的脚本，再按「有没有 main()」分成门禁与共享库。

    `guard_common.py` 只有被复用的函数、没有自己的入口，是**共享库不是门禁**：
    对它要求「谁跑它」只能逼人编一行假登记。门禁才需要入口；库只需要被某个
    已登记脚本引用（见 collect_d4 的 D4-unreferenced-lib）。
    """
    gates: List[str] = []
    libs: List[str] = []
    for f in _script_names(repo):
        (gates if _has_main(repo, f) else libs).append(f)
    return gates, libs


def _executed_by_test(gate: str, test_text: str) -> bool:
    """测试里真的出现了这个脚本的可执行引用，而不是只提到名字。

    认三种形态：`check_x.py` 文件名、`scripts.check_x` 模块路径、
    `from scripts import check_x`。只在注释里写一句名字不算入口。
    """
    stem = gate[:-3]
    return (
        gate in test_text
        or f"scripts.{stem}" in test_text
        or f"from scripts import {stem}" in test_text
    )


def _tests_text(repo: Repo) -> str:
    """tests/ 下的源码文本：门禁被测试实际执行（subprocess 或 import）算真入口。"""
    parts: List[str] = []
    for rel_root in ("tests", "mbdsdr_ai/tests"):
        base = os.path.join(repo.root, rel_root)
        if not os.path.isdir(base):
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for f in fn:
                if f.endswith(".py"):
                    parts.append(_read(os.path.join(dp, f)))
    return "\n".join(parts)


#: 文档里的可执行入口：脚本路径 / release_check 步名 / 钩子名。
_ENTRY_SCRIPT = re.compile(r"`?(?:python[0-9.]*\s+)?(scripts/[A-Za-z0-9_./-]+\.py)")
_ENTRY_HOOK = re.compile(r"(?:\.git/hooks/|钩子[：:\s]+)(pre-commit|pre-push|commit-msg|post-commit)")
_ENTRY_STEP = re.compile(r"--steps?[= ]`?([A-Za-z0-9_,-]+)")


def _gate_entries(docs_text: str, gate: str) -> List[str]:
    """提到这个门禁的行里给出的可执行入口。

    只在「同一行提到门禁名」的地方找，避免文档任意角落的一句 `scripts/x.py`
    被算成所有门禁的登记。
    """
    out: List[str] = []
    for line in docs_text.splitlines():
        if gate not in line:
            continue
        out += [m.group(1) for m in _ENTRY_SCRIPT.finditer(line)]
        out += ["hook:" + m.group(1) for m in _ENTRY_HOOK.finditer(line)]
        for m in _ENTRY_STEP.finditer(line):
            out += ["step:" + s.strip() for s in m.group(1).split(",") if s.strip()]
    return out


def _entry_resolves(repo: Repo, entry: str, gate: str, hooks: Sequence[str]) -> bool:
    """入口真实存在，并且真的是这个门禁的入口。

    只查「文件存在」不够：登记到别的脚本、或写一个不存在的路径，都会让
    「文档里有名字」重新变成装饰品。钩子/步骤要能看出它确实调用了本门禁。
    """
    stem = gate[:-3] if gate.endswith(".py") else gate
    if entry.startswith("hook:"):
        name = entry[5:]
        return any(os.path.basename(h) == name and stem in _read(h) for h in hooks)
    if entry.startswith("step:"):
        path = os.path.join(repo.root, "scripts", "release_check.py")
        if not os.path.isfile(path):
            return False
        text = _read(path)
        m = re.search(rf"def\s+_step_{re.escape(entry[5:])}\s*\(.*?(?=\ndef |\Z)", text, re.S)
        return bool(m) and stem in m.group(0)
    full = os.path.join(repo.root, entry)
    if not os.path.isfile(full):
        return False
    return os.path.basename(entry) == gate or stem in _read(full)


def collect_d4(repo: Repo, base: Sequence[Finding]) -> Dimension:
    dim = Dimension("D4", "门禁与验收", DIMENSIONS[3][2])
    dim.findings.extend(f for f in base if _dimension_of(f.rule) == "D4")

    hooks = _hook_files(repo)
    gates, libs = _gate_scripts(repo)
    hook_text = "\n".join(_read(h) for h in hooks)
    docs_text = "\n".join(_read(os.path.join(repo.root, p)) for p in (
        "docs/code-guards.md", "docs/evidence-standard.md", "AGENTS.md",
    ))
    test_text = _tests_text(repo)
    wired = [g for g in gates if g in hook_text]
    # 被测试实际执行（subprocess 跑或 import 调用）也是真入口：它挂在
    # pytest 的必经路径上，不是一句文档里的承诺。
    tested = [g for g in gates if _executed_by_test(g, test_text)]
    # 「门禁名出现在文档任意位置」曾经就是登记的全部条件：写一行
    # 「这个门禁不能用」也能绿。登记必须指名可执行入口，且入口要能解析到真实存在。
    registered: List[str] = []
    unresolved: List[Tuple[str, str]] = []
    silent: List[str] = []
    for g in gates:
        if g in wired or g in tested:
            continue
        if g not in docs_text:
            silent.append(g)
            continue
        entries = _gate_entries(docs_text, g)
        if not entries:
            unresolved.append((g, ""))
            continue
        # 登记成立的口径是「至少有一个入口能解析」：一行里常同时写脚本路径和
        # release_check 步名，只要脚本路径真实存在且确实调用本门禁，这条登记就
        # 成立；另一个入口写错不该把已成立的登记否掉。
        if any(_entry_resolves(repo, e, g, hooks) for e in entries):
            registered.append(g)
        else:
            unresolved.append((g, entries[0]))

    # 共享库没有自己的入口，不能要求「谁跑它」——那只能逼人编一行假登记。
    # 它只要求被某个「已经真的有入口」的脚本引用，否则就是死代码或漏接线。
    active: Set[str] = set(wired) | set(registered) | set(tested)
    for m in _ENTRY_SCRIPT.finditer(docs_text):
        active.add(os.path.basename(m.group(1)))
    for h in hooks:
        active.add(os.path.basename(h))
    unreferenced: List[str] = []
    for lib in libs:
        stem = lib[:-3]
        users = [
            s for s in sorted(active)
            if s != lib and stem in _read(os.path.join(repo.root, "scripts", s))
        ]
        if not users:
            unreferenced.append(lib)

    dim.facts.append(f"已安装 git 钩子：{len(hooks)} 个")
    dim.facts.append(
        f"门禁类脚本：{len(gates)} 个（另有共享库 {len(libs)} 个）；"
        f"钩子直接调用 {len(wired)} 个；文档登记且入口可解析 {len(registered)} 个；"
        f"被测试实际执行 {len(tested)} 个；登记不可解析 {len(unresolved)} 个；"
        f"无任何入口 {len(silent)} 个；库无人引用 {len(unreferenced)} 个"
    )

    if not any("pre-commit" in os.path.basename(h) for h in hooks):
        dim.findings.append(Finding(
            "D4-no-hook", ".git/hooks", 0, "未安装 pre-commit 钩子",
            "门禁脚本没有挂在提交必经路径上，是否执行完全取决于人。",
            "运行 python scripts/install_git_hooks.py 并验证钩子可执行。",
        ))
    for g, entry in unresolved:
        detail = (f"文档里点名的入口 `{entry}` 在本仓不存在，或它并没有调用 {g}" if entry
                  else "文档只提到了这个名字，没有给出任何可执行入口")
        dim.findings.append(Finding(
            "D4-gate-unresolvable", f"scripts/{g}", 0,
            f"登记入口 `{entry}` 不可解析" if entry else "登记只有名字，没有可执行入口",
            f"{detail}；写一行文字不等于登记，这个门禁仍然没有入口。",
            "在 docs/code-guards.md 写明可执行入口（`python scripts/xxx.py` / "
            "`--steps xxx` / `.git/hooks/pre-commit`），并确保该入口真实存在且调用了本脚本。",
        ))
    for g in silent:
        dim.findings.append(Finding(
            "D4-silent-gate", f"scripts/{g}", 0, "未接入钩子，也未在文档登记",
            "门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。",
            "接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。",
            "review",
        ))
    for lib in unreferenced:
        dim.findings.append(Finding(
            "D4-unreferenced-lib", f"scripts/{lib}", 0, "没有任何已登记脚本引用",
            "共享库没有自己的入口，也没有被任何已登记/已接线/被测试执行的脚本引用；"
            "等于死代码或漏接线。",
            "把它接到某个已登记门禁，或删除；不要给它编一行「谁跑」的登记。",
            "review",
        ))

    # 测试元数据规模
    py = repo.py_units(("tests",))
    skips = 0
    xfails = 0
    for unit in py:
        skips += len(re.findall(r"pytest\.skip\s*\(", unit.text))
        skips += len(re.findall(r"skipif", unit.text))
        xfails += len(re.findall(r"xfail", unit.text))
    dim.facts.append(f"测试侧：pytest.skip/skipif {skips} 处，xfail {xfails} 处")
    return dim


# ────────────────────────────── D5 ──────────────────────────────


def _known_red_entries(repo: Repo) -> List[Tuple[str, str, int]]:
    path = os.path.join(repo.root, "docs/known-red-register.md")
    if not os.path.isfile(path):
        return []
    text = _read(path)
    lines = text.splitlines()
    entries: List[Tuple[str, str, int]] = []
    current: Optional[Tuple[str, int]] = None
    buf: List[str] = []
    for i, line in enumerate(lines, 1):
        m = re.match(r"^##\s+(K\d+)", line)
        if m:
            if current:
                entries.append((current[0], "\n".join(buf), current[1]))
            current = (m.group(1), i)
            buf = []
        elif current:
            buf.append(line)
    if current:
        entries.append((current[0], "\n".join(buf), current[1]))
    return entries


def collect_d5(repo: Repo, base: Sequence[Finding]) -> Dimension:
    dim = Dimension("D5", "流程与协作", DIMENSIONS[4][2])
    dim.findings.extend(f for f in base if RULE_DIMENSION.get(f.rule) == "D5")

    entries = _known_red_entries(repo)
    dim.facts.append(f"已知红条目：{len(entries)} 条")
    for kid, body, line in entries:
        missing = []
        if "转绿条件" not in body:
            missing.append("转绿条件")
        if "复跑" not in body and "pytest" not in body:
            missing.append("复跑命令")
        if not re.search(r"20\d\d-\d\d-\d\d", body):
            missing.append("首次记录日期")
        if missing:
            dim.findings.append(Finding(
                "D5-known-red-incomplete", "docs/known-red-register.md", line,
                f"{kid} 缺：{'、'.join(missing)}",
                "已知红缺少可执行字段，无法判断它是否还红、何时能销账。",
                "补齐 nodeid、转绿条件、复跑命令与首次记录日期。",
            ))
    if not entries:
        dim.findings.append(Finding(
            "D5-no-ledger", "docs/known-red-register.md", 0, "未找到已知红台账",
            "「还没修」没有可见载体，只能靠命令行 --deselect 隐藏。",
            "建立 known-red-register.md 并按条目登记。",
        ))

    coord = os.path.join(repo.root, "docs/COORDINATION.md")
    if os.path.isfile(coord):
        text = _read(coord)
        # 只做计数，不做语义判定。这里刻意不产出 finding：
        # 「出现过 claim 字样」既不能证明协作有效，也不能证明无效；
        # 把它当白名单用是误读，所以结论里必须写明白。
        dim.facts.append(
            f"协作记录：docs/COORDINATION.md 有 {len(re.findall(r'claim|CLAIM', text))} 处 "
            "claim 提及（只统计提及次数，不做语义判定；不是白名单）"
        )
    return dim


# ────────────────────────────── D6 ──────────────────────────────

DOC_PATH = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./\\-]*\.(?:py|md|json|yaml|yml|toml|cpp|h|ui|qss|txt|jsonl))(?::(\d+))?`")
_ARCHIVE_DOC = re.compile(r"^docs/(?:learn|research|audit[^/]*|pending-patches|incidents)/")
COLOR_CLAIM = re.compile(r"(颜色|配色|themes\.py|tokens\.py).{0,12}(不改|禁止|冻结|不动)|(不改|禁止).{0,12}(颜色|配色)")
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b")


def _file_index(repo: Repo) -> Set[str]:
    out: Set[str] = set()
    for dp, dn, fn in os.walk(repo.root):
        dn[:] = [d for d in dn if d not in {".git", "__pycache__", "node_modules", "build", "build_cpp"}]
        for f in fn:
            out.add(os.path.relpath(os.path.join(dp, f), repo.root).replace("\\", "/"))
    return out


_MOVED_HINT = re.compile(r"迁|搬|移至|移到|改名|重构到|落到|拆到")


def _migration_target(unit: Unit, raw: str, index: Set[str]) -> Tuple[str, int]:
    """同一个文档里有没有「这个模块已迁移」的记录，返回 (新路径, 行号)。

    只认同时满足三件事的行：点名了同一个模块名、出现了迁移词、给出了一个
    仓内存在的 .py 路径。三条缺一不算迁移记录，避免把「只是又提了一次」
    当成已经改过名。
    """
    stem = os.path.basename(raw).rsplit(".", 1)[0]
    if len(stem) < 6:
        return "", 0
    for lineno, line in enumerate(unit.text.splitlines(), 1):
        if stem not in line or not _MOVED_HINT.search(line):
            continue
        for m in DOC_PATH.finditer(line):
            cand = m.group(1).replace("\\", "/")
            if cand != raw and cand.endswith(".py") and cand in index:
                return cand, lineno
    return "", 0


def collect_d6(repo: Repo) -> Dimension:
    dim = Dimension("D6", "目录与文档结构", DIMENSIONS[5][2])
    docs = repo.doc_units()
    index = _file_index(repo)
    referenced = 0
    missing = 0
    for unit in docs:
        if unit.path.startswith("docs/learn/"):
            continue
        lines = unit.text.splitlines()
        for lineno, line in enumerate(lines, 1):
            for m in DOC_PATH.finditer(line):
                raw = m.group(1).replace("\\", "/")
                if raw.startswith(("http", "/", "~")) or "*" in raw or " " in raw:
                    continue
                if raw.startswith(("foo.", "example.", "path/to")):
                    continue
                if re.search(r"/(?:X|Y|Z|foo|bar|baz)\.(?:md|py|json|txt)$", raw):
                    # 文档里的占位路径（如 docs/X.md），不是对真实文件的陈述。
                    continue
                if not raw.startswith(("desktop/", "mbdsdr_ai/", "scripts/", "tools/", "tests/", "docs/")):
                    continue
                if raw in TOOL_ARTIFACTS:
                    # 本工具自己的产物：存在与否取决于「有没有跑过 --accept-current」，
                    # 把它计入「文档指向不存在的路径」会让同一棵树在跑基线前后各出一个数。
                    note_skip("D6", "引用的是本检查器自己的产物（按需生成）")
                    continue
                referenced += 1
                if raw not in index:
                    missing += 1
                    artifact = raw.endswith((".json", ".log", ".csv", ".bin", ".npz", ".wav", ".db"))
                    archive = bool(_ARCHIVE_DOC.search(unit.path))
                    moved_to, moved_at = _migration_target(unit, raw, index)
                    if moved_to:
                        # 路径过期 ≠ 声明是假的：同一文档记着它迁到了哪。
                        # 仍然报，但降级成 review 并直接给出建议路径，避免
                        # 把「改一行文档」估成「重写一个模块」。
                        dim.findings.append(Finding(
                            "D6-path-moved", unit.path, lineno,
                            f"引用的 {raw} 已迁移",
                            f"路径过期：同文档第 {moved_at} 行记着该模块已迁到 {moved_to}；"
                            "文件确实存在，只是这份文档的路径没跟上。",
                            f"把引用改为 {moved_to}；不需要重建模块。",
                            "review",
                        ))
                        continue
                    dim.findings.append(Finding(
                        "D6-missing-path", unit.path, lineno, f"引用的 {raw} 不存在",
                        "文档事实指向了仓库里不存在的路径，按文档操作会直接失败。"
                        + ("（该路径像运行时产物，需注明生成命令或移除引用。）" if artifact else "")
                        + ("（历史快照文档，当时可能确实存在。）" if archive else ""),
                        "改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。",
                        "review" if (artifact or archive) else "must-fix",
                    ))
                    continue
                if m.group(2):
                    full = os.path.join(repo.root, raw)
                    try:
                        total = len(_read(full).splitlines())
                    except OSError:
                        total = 0
                    if total and int(m.group(2)) > total:
                        dim.findings.append(Finding(
                            "D6-stale-line", unit.path, lineno,
                            f"{raw}:{m.group(2)} 超出文件行数 {total}",
                            "文档里的行号已经失效，读者按行号跳转会落到无关位置。",
                            "改为当前行号或改用符号名定位。",
                            "review",
                        ))
    dim.facts.append(f"文档引用仓内路径：{referenced} 处；不存在：{missing} 处")

    hex_sites = 0
    hex_files = 0
    for unit in repo.py_units(("desktop",)):
        if unit.path.endswith(("themes.py", "tokens.py")):
            continue
        n = len(HEX_COLOR.findall(unit.text))
        if n:
            hex_sites += n
            hex_files += 1
    dim.facts.append(f"desktop 非主题文件的硬编码颜色：{hex_sites} 处 / {hex_files} 个文件")
    for unit in docs:
        if unit.path.startswith("docs/learn/"):
            continue
        for lineno, line in enumerate(unit.text.splitlines(), 1):
            if COLOR_CLAIM.search(line):
                dim.findings.append(Finding(
                    "D6-color-claim", unit.path, lineno, line.strip()[:120],
                    f"文档声称颜色不改，但 desktop 非主题文件里仍有 {hex_sites} 处硬编码颜色；"
                    "陈述与代码不一致。",
                    "要么把颜色迁移到 themes/tokens，要么在文档标注实际例外清单。",
                    "review",
                ))
    return dim


# ────────────────────────────── D7 ──────────────────────────────


def collect_d7(axioms: Sequence[Axiom], findings: Sequence[Finding]) -> Dimension:
    dim = Dimension("D7", "任务分派与优先级", DIMENSIONS[6][2])
    from .model import FROZEN_FILES, priority_score

    by_id = {a.id: a for a in axioms}
    rows = []
    for f in findings:
        a = by_id.get(f.rule)
        if a is None:
            continue
        rows.append((priority_score(f, a), f))
    rows.sort(key=lambda x: (-x[0], x[1].path, x[1].line))
    dim.facts.append("排序规则：loss×100 + freq×10 + evidence×3 − 冻结60 − 豁免1000")
    for score, f in rows[:10]:
        dim.facts.append(f"优先级 {score:>4} {f.rule} {f.path}:{f.line}")
    return dim


# ────────────────────────────── D8 ──────────────────────────────


def audit_waiver_items(items: Sequence[dict]) -> List[Finding]:
    out: List[Finding] = []
    for item in items:
        rule = str(item.get("rule", "") or "?")
        path = str(item.get("path", "") or "?")
        missing = []
        if not str(item.get("reason", "")).strip():
            missing.append("理由")
        if not str(item.get("review_by", "")).strip():
            missing.append("到期")
        if not str(item.get("approver", "")).strip():
            missing.append("批准人")
        if missing:
            out.append(Finding(
                "D8-waiver-incomplete", path, 0, f"{rule} 豁免缺：{'、'.join(missing)}",
                "「暂时不做」没有完整三要素，等于永久豁免。",
                "补齐理由、到期日、批准人；到期后自动重新计入违规。",
            ))
    return out


def collect_d8(repo: Repo) -> Dimension:
    dim = Dimension("D8", "豁免与债务登记", DIMENSIONS[7][2])
    path = os.path.join(repo.root, "docs/axioms-waivers.json")
    items: List[dict] = []
    if os.path.isfile(path):
        try:
            items = json.loads(_read(path)).get("waivers", [])
        except json.JSONDecodeError:
            items = []
    dim.facts.append(f"公理豁免登记：{len(items)} 条")
    dim.findings.extend(audit_waiver_items(items))

    entries = _known_red_entries(repo)
    no_expiry = [k for k, body, _ in entries if not re.search(r"到期|复核|销账期限", body)]
    dim.facts.append(f"已知红 {len(entries)} 条，其中 {len(no_expiry)} 条没写到期/复核条件")
    if no_expiry:
        dim.findings.append(Finding(
            "D8-debt-no-expiry", "docs/known-red-register.md", 0,
            f"{len(no_expiry)} 条已知红没有到期/复核条件：{', '.join(no_expiry)}",
            "债务没有到期就不会被重新审视，只增不减。",
            "为每条补「复核日期」或「转绿条件」；转绿条件已有时可视为到期判据。",
            "review",
        ))
    return dim


# ────────────────────────────── 汇总 ──────────────────────────────


def _location_problem(raw_path: str, line: int, root: str) -> str:
    """finding 指向的位置是否属于**被分析的这棵树**。

    返回 ``""`` / ``unknown-path`` / ``line-out-of-range``。
    这类错误比漏报更伤：报了一个不存在的文件，读报告的人会以为看见了证据。
    """
    if not raw_path or raw_path.startswith("<inline:") or raw_path.startswith(".git/"):
        # 内联片段与 git 目录（钩子）不在工作树里，不算「被分析树」的路径。
        return ""
    full = os.path.join(root, raw_path)
    if not os.path.exists(full):
        return "unknown-path"
    if line <= 0 or os.path.isdir(full):
        return ""
    text = _read(full)
    if line > len(text.splitlines()):
        return "line-out-of-range"
    return ""


def validate_finding_locations(repo: Repo, dims: Sequence[Dimension]) -> Dict[str, int]:
    """在报告出闸前核对每条 finding 的 path/line 确实落在被分析树里。

    历史事故：报告里出现过本仓、全部 worktree、`git log --all` 都不存在的
    路径（`mbdsdr_ai/skyfield_compat.py`、`docs/learn/phase9/_PHASE9_FINAL_SUMMARY.md`）。
    判据自己不知道自己看的是哪棵树，是这类错误的根；所以这里不删条目，
    而是把规则名改写成 `-unknown-path` / `-line-out-of-range` 并升级为应修，
    让「报了个不存在的东西」本身成为可见的违规。
    """
    counts = {"unknown-path": 0, "line-out-of-range": 0, "checked": 0}
    for dim in dims:
        for f in dim.findings:
            counts["checked"] += 1
            bad = _location_problem(f.path, f.line, repo.root)
            if not bad:
                continue
            counts[bad] += 1
            f.rule = f"{f.rule}-{bad}"
            f.severity = "must-fix"
            what = "不存在" if bad == "unknown-path" else "行号越界"
            f.why = (
                f"判据指向的位置在被分析树里{what}（{f.path}:{f.line}）；"
                "先修判据取到的路径/行号，这条结论不可信。" + f.why
            )
            f.fix = "路径必须来自被分析的那棵树；" + f.fix
    for dim in dims:
        if dim.id == "D6":
            dim.facts.append(
                f"位置校验：核对 {counts['checked']} 条 finding 的 path/line；"
                f"unknown-path {counts['unknown-path']} 条；"
                f"line-out-of-range {counts['line-out-of-range']} 条"
            )
    return counts


def collect_all(repo: Repo) -> Tuple[List[Axiom], List[Dimension]]:
    axioms = build_axioms(repo)
    reset_skips()
    units = repo.py_units(("desktop", "mbdsdr_ai", "tests", "scripts", "tools"))
    base: List[Finding] = []
    docs = repo.doc_units()
    for axiom in axioms:
        try:
            # A10/A11/A14 检查的是文档与台账里的事实，其余检查源码/测试。
            target = docs if axiom.id in {"A10", "A11", "A14"} else units
            base.extend(axiom.scan(target))
        except Exception as exc:  # 检查器自身故障必须显式暴露
            base.append(Finding(
                axiom.id, "<scanner>", 0, f"{type(exc).__name__}: {exc}",
                f"检查 {axiom.check_name} 自身崩溃，结果不可信。",
                "修复检查器；崩溃不能当作通过。",
            ))
    dims = [
        collect_d1(repo),
        collect_d2(repo, base),
        collect_d3(repo),
        collect_d4(repo, base),
        collect_d5(repo, base),
        collect_d6(repo),
        collect_d7(axioms, base),
        collect_d8(repo),
    ]
    validate_finding_locations(repo, dims)
    return axioms, dims
