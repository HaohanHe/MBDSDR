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

from .checks import build_axioms
from .model import (
    Axiom,
    Finding,
    Repo,
    Unit,
    _read,
    line_of,
    parse,
)

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
    "A10": "D5", "A11": "D5",
}

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
    mod_parts = _rel_module(unit.path).split(".")
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

    for (src, dst), n in sorted(edges.items()):
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
    for path, lineno, module in qt_sites[:40]:
        dim.findings.append(Finding(
            "D1-qt-in-core", path, lineno, f"import {module}",
            "纯 Python 层（mbdsdr_ai）直接 import 了 Qt；这层一旦被 CLI/MCP 复用就会拖入 GUI 依赖。",
            "把 Qt 相关实现移到 desktop/，mbdsdr_ai 只保留可注入的接口。",
        ))

    # 模块级环
    graph: Dict[str, Set[str]] = {}
    for rel, tree in modules.items():
        if not rel.startswith(("desktop/", "mbdsdr_ai/")):
            continue
        mod = _rel_module(rel)
        graph.setdefault(mod, set())
        for module, _ in _imports(Unit(rel, "")):
            if module.startswith(("desktop.", "mbdsdr_ai.")) and module in graph:
                graph[mod].add(module)
    # 先补全所有节点再判环
    for rel in modules:
        if rel.startswith(("desktop/", "mbdsdr_ai/")):
            graph.setdefault(_rel_module(rel), set())
    sccs = _tarjan(graph)
    cycles = [c for c in sccs if len(c) > 1]
    dim.facts.append(f"模块级强连通分量：{len(cycles)} 个")
    for comp in cycles[:5]:
        dim.findings.append(Finding(
            "D1-cycle", comp[0].replace(".", "/") + ".py", 0,
            f"{len(comp)} 个模块互相依赖",
            "存在 import 环，任何一侧的初始化顺序都会影响另一侧；单独改一处无法预测影响面。",
            "按依赖方向拆出接口模块，环内至少一条边改为依赖注入或延迟 import。",
            "review",
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
        for w in graph.get(v, ()):  # type: ignore[arg-type]
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

    for node in list(graph):
        if node not in index:
            strong(node)
    return result


# ────────────────────────────── D2 ──────────────────────────────


CONST_ASSIGN = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*=\s*(.+)$")


def collect_d2(repo: Repo, base: Sequence[Finding]) -> Dimension:
    dim = Dimension("D2", "真值源清单", DIMENSIONS[1][2])
    dim.findings.extend(f for f in base if RULE_DIMENSION.get(f.rule) == "D2")

    units = repo.py_units(("desktop", "mbdsdr_ai"))
    producers: Dict[str, Set[str]] = {}
    for unit in units:
        if unit.path in ("desktop/readouts.py", "desktop/constants.py"):
            continue
        tree = parse(unit)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                literals = "".join(
                    v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
                for unit_name in re.findall(r"GHz|MHz|kHz|Hz|dBFS|dBm|dB", literals):
                    producers.setdefault(unit_name, set()).add(unit.path)
    for unit_name, paths in sorted(producers.items(), key=lambda kv: -len(kv[1])):
        dim.facts.append(f"读数单位 {unit_name}：{len(paths)} 个文件各自拼装")
    multi = {k: v for k, v in producers.items() if len(v) >= 2}
    if multi:
        top = max(multi.items(), key=lambda kv: len(kv[1]))
        dim.findings.append(Finding(
            "D2-multi-producer", sorted(top[1])[0], 0,
            f"{top[0]} 读数由 {len(top[1])} 个文件产生",
            "同一事实存在多个产生点，尚未登记真值源；改一处口径不会传导到其他面板。",
            "在 docs/truth-sources.json 登记唯一产生函数，消费点只读该函数。",
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
    for name, values in list(dual.items())[:12]:
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
    for path, line in modules_no_contract[:10]:
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
    for name, items in role_conflicts[:15]:
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


def _gate_scripts(repo: Repo) -> List[str]:
    base = os.path.join(repo.root, "scripts")
    if not os.path.isdir(base):
        return []
    names = []
    for f in os.listdir(base):
        if not f.endswith(".py"):
            continue
        if re.match(r"(check_|lint_|audit_|guard|gate|verify_|report_)", f):
            names.append(f)
    return sorted(names)


def collect_d4(repo: Repo, base: Sequence[Finding]) -> Dimension:
    dim = Dimension("D4", "门禁与验收", DIMENSIONS[3][2])
    dim.findings.extend(f for f in base if RULE_DIMENSION.get(f.rule) == "D4")

    hooks = _hook_files(repo)
    gates = _gate_scripts(repo)
    hook_text = "\n".join(_read(h) for h in hooks)
    docs_text = "\n".join(_read(os.path.join(repo.root, p)) for p in (
        "docs/code-guards.md", "docs/evidence-standard.md", "AGENTS.md",
    ))
    wired = [g for g in gates if g in hook_text]
    documented = [g for g in gates if g in docs_text]
    silent = [g for g in gates if g not in hook_text and g not in docs_text]
    dim.facts.append(f"已安装 git 钩子：{len(hooks)} 个")
    dim.facts.append(f"门禁类脚本：{len(gates)} 个；钩子直接调用 {len(wired)} 个；文档登记 {len(documented)} 个")

    if not any("pre-commit" in os.path.basename(h) for h in hooks):
        dim.findings.append(Finding(
            "D4-no-hook", ".git/hooks", 0, "未安装 pre-commit 钩子",
            "门禁脚本没有挂在提交必经路径上，是否执行完全取决于人。",
            "运行 python scripts/install_git_hooks.py 并验证钩子可执行。",
        ))
    for g in silent:
        dim.findings.append(Finding(
            "D4-silent-gate", f"scripts/{g}", 0, "未接入钩子，也未在文档登记",
            "门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。",
            "接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。",
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
        dim.facts.append(f"协作记录：{len(re.findall(r'claim|CLAIM', text))} 处 claim 提及")
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
                referenced += 1
                if raw not in index:
                    missing += 1
                    if missing <= 30:
                        artifact = raw.endswith((".json", ".log", ".csv", ".bin", ".npz", ".wav", ".db"))
                        archive = bool(_ARCHIVE_DOC.search(unit.path))
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
            f"{len(no_expiry)} 条已知红没有到期/复核条件：{', '.join(no_expiry[:8])}",
            "债务没有到期就不会被重新审视，只增不减。",
            "为每条补「复核日期」或「转绿条件」；转绿条件已有时可视为到期判据。",
            "review",
        ))
    return dim


# ────────────────────────────── 汇总 ──────────────────────────────


def collect_all(repo: Repo) -> Tuple[List[Axiom], List[Dimension]]:
    axioms = build_axioms(repo)
    units = repo.py_units(("desktop", "mbdsdr_ai", "tests", "scripts", "tools"))
    base: List[Finding] = []
    docs = repo.doc_units()
    for axiom in axioms:
        try:
            # A10/A11 检查的是文档里的事实，其余检查源码/测试。
            target = docs if axiom.id in {"A10", "A11"} else units
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
    return axioms, dims
