"""工程公理体系：模型、仓库扫描、输出与优先级排序。

设计约束（见 docs/axioms.md）：
- 所有检查的输入只来自仓库自身内容（源码 / 测试 / 文档 / git 提交），
  不依赖任何一次对话里的判断。
- 所有检查都是只读静态分析：不 import 项目模块、不启动 Qt、不连硬件，
  因此可以在没有硬件的机器上一条命令跑完。
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ────────────────────────────── 数据结构 ──────────────────────────────


@dataclass
class Unit:
    """一份被检查的文本单元。

    path 是相对仓库根目录的路径；对于反证用的内联片段，path 形如
    ``<inline:A09-web-bridge>``，不会与真实文件混淆。
    """

    path: str
    text: str

    @property
    def is_real_file(self) -> bool:
        return not self.path.startswith("<inline:")


@dataclass
class Finding:
    rule: str
    path: str
    line: int
    snippet: str
    why: str
    fix: str
    severity: str = "must-fix"  # must-fix | review
    waived: bool = False
    waiver_reason: str = ""
    waiver_expired: bool = False

    def __post_init__(self) -> None:
        """判据对象的字段类型就是判据的一部分，构造时立即校验。

        历史事故：D1-cycle 构造时多传一个位置参数，`waived` 被赋成字符串
        "review"（真值），于是所有模块级 import 环一直被当成「已豁免」，
        同时从报告和棘轮里消失——判据看上去在跑，实际什么都没判。
        这里不再依赖调用点自觉：`waived` 只接受 bool，`severity` 只接受两个
        合法值，任何字符串/数字等非布尔真值一律报错退出。
        """
        if not isinstance(self.waived, bool):
            raise TypeError(
                f"Finding.waived 只接受 bool，收到 {type(self.waived).__name__}="
                f"{self.waived!r}（rule={self.rule}, path={self.path}, line={self.line}）。"
                "位置参数错位会把非布尔真值当成「已豁免」，让判据静默失效。"
            )
        if self.severity not in ("must-fix", "review"):
            raise ValueError(
                f"Finding.severity 只接受 'must-fix' / 'review'，收到 "
                f"{self.severity!r}（rule={self.rule}, path={self.path}）。"
            )

    def key(self) -> Tuple[str, str, int]:
        return (self.rule, self.path, self.line)


@dataclass
class Axiom:
    id: str
    title: str
    statement: str
    decidable: str
    cases: List[str]
    check_name: str
    counterexample: str
    loss: int          # 1..3 用户可感知损失（3 = 数据丢失/显示错数）
    freq: int          # 1..3 触发频率（3 = 常走路径）
    evidence: int      # 1..2 证据可得性（2 = 无硬件可验证）
    scan: Callable[[List[Unit]], List[Finding]]

    def meta(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "statement": self.statement,
            "decidable": self.decidable,
            "cases": self.cases,
            "check": self.check_name,
            "counterexample": self.counterexample,
            "loss": self.loss,
            "freq": self.freq,
            "evidence": self.evidence,
        }


# ────────────────────────────── 仓库与工具 ──────────────────────────────

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "build", "build_cpp", "build_cpp_tests",
    "third_party", ".pytest_cache", ".kilo-tmp", ".audit_tmp", ".tmp-tool-results",
    ".mypy_cache", ".ruff_cache", "dist", "release",
}

SOURCE_ROOTS = ("desktop", "mbdsdr_ai", "scripts", "tools")

#: 本工具自己生成的产物。扫描它们等于把自己的输出当成本仓事实。
GENERATED_ARTIFACTS = {
    "docs/axioms-first-run.md",
    "docs/axioms-first-run.json",
}


class Repo:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self._units: Optional[Dict[str, Unit]] = None
        self._git_cache: Dict[Tuple[str, ...], str] = {}

    # -- 文件 --
    def rel(self, path: str) -> str:
        return os.path.relpath(path, self.root).replace("\\", "/")

    def py_units(self, roots: Sequence[str] = SOURCE_ROOTS) -> List[Unit]:
        out: List[Unit] = []
        for rel_root in roots:
            base = os.path.join(self.root, rel_root)
            if not os.path.isdir(base):
                continue
            for dp, dn, fn in os.walk(base):
                dn[:] = [d for d in dn if d not in SKIP_DIRS]
                for f in fn:
                    if not f.endswith(".py"):
                        continue
                    p = os.path.join(dp, f)
                    out.append(Unit(self.rel(p), _read(p)))
        return out

    def doc_units(self) -> List[Unit]:
        out: List[Unit] = []
        base = os.path.join(self.root, "docs")
        if not os.path.isdir(base):
            return out
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for f in fn:
                if not f.endswith(".md"):
                    continue
                p = os.path.join(dp, f)
                rel = self.rel(p)
                if rel in GENERATED_ARTIFACTS:
                    # 本工具自己的产物：扫它会把自己的输出当成本仓事实，形成自指噪声。
                    continue
                out.append(Unit(rel, _read(p)))
        return out

    # -- git --
    def git(self, *args: str, input_text: Optional[str] = None) -> str:
        key = args
        if input_text is None and key in self._git_cache:
            return self._git_cache[key]
        proc = subprocess.run(
            ["git", "-C", self.root, *args],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if input_text is None:
            self._git_cache[key] = proc.stdout
        return proc.stdout

    def git_show(self, rev: str, path: str) -> Optional[str]:
        proc = subprocess.run(
            ["git", "-C", self.root, "show", f"{rev}:{path}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            return None
        return proc.stdout

    def describe_commit(self, abbr: str) -> Tuple[str, str]:
        """返回 (类型, 完整名)；类型为 commit/tree/blob/missing。"""
        proc = subprocess.run(
            ["git", "-C", self.root, "cat-file", "-t", abbr],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            return "missing", ""
        return proc.stdout.strip(), ""

    def batch_object_types(self, names: Sequence[str]) -> Dict[str, str]:
        if not names:
            return {}
        payload = "\n".join(names) + "\n"
        proc = subprocess.run(
            ["git", "-C", self.root, "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            input=payload, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        result: Dict[str, str] = {}
        lines = proc.stdout.strip().splitlines()
        for name, line in zip(names, lines):
            parts = line.split()
            result[name] = parts[1] if len(parts) == 2 else "missing"
        return result

    def commits_behind(self, rev: str) -> Optional[int]:
        proc = subprocess.run(
            ["git", "-C", self.root, "rev-list", "--count", f"{rev}..HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            return None
        try:
            return int(proc.stdout.strip())
        except ValueError:
            return None

    def head(self) -> str:
        proc = subprocess.run(
            ["git", "-C", self.root, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def parse(unit: Unit) -> Optional[ast.Module]:
    try:
        return ast.parse(unit.text)
    except (SyntaxError, ValueError):
        return None


def line_of(unit: Unit, lineno: int) -> str:
    lines = unit.text.splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()[:200]
    return ""


def func_name_of(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    return ""


def enclosing_functions(tree: ast.Module) -> Dict[int, ast.AST]:
    """行号 -> 最内层函数节点。"""
    mapping: Dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            for ln in range(start, end + 1):
                mapping[ln] = node
    return mapping


def walk_with_parents(tree: ast.AST) -> Iterable[Tuple[ast.AST, Optional[ast.AST]]]:
    stack: List[Tuple[ast.AST, Optional[ast.AST]]] = [(tree, None)]
    while stack:
        node, parent = stack.pop()
        yield node, parent
        for child in ast.iter_child_nodes(node):
            stack.append((child, node))


def const_eval(node: ast.AST, env: Dict[str, object]) -> Tuple[bool, object]:
    """保守常量求值：只处理字面量、简单一元/比较/布尔运算与已知变量。"""
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, ast.Name) and node.id in env:
        return True, env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
        ok, val = const_eval(node.operand, env)
        if not ok:
            return False, None
        try:
            if isinstance(node.op, ast.Not):
                return True, not val
            if isinstance(node.op, ast.USub):
                return True, -val
            return True, +val
        except Exception:
            return False, None
    if isinstance(node, ast.BoolOp):
        vals = []
        for v in node.values:
            ok, val = const_eval(v, env)
            if not ok:
                return False, None
            vals.append(bool(val))
        return True, (all(vals) if isinstance(node.op, ast.And) else any(vals))
    if isinstance(node, ast.Compare):
        ok, left = const_eval(node.left, env)
        if not ok:
            return False, None
        for op, comp in zip(node.ops, node.comparators):
            ok2, right = const_eval(comp, env)
            if not ok2:
                return False, None
            try:
                if isinstance(op, ast.Eq):
                    res = left == right
                elif isinstance(op, ast.NotEq):
                    res = left != right
                elif isinstance(op, ast.Lt):
                    res = left < right
                elif isinstance(op, ast.LtE):
                    res = left <= right
                elif isinstance(op, ast.Gt):
                    res = left > right
                elif isinstance(op, ast.GtE):
                    res = left >= right
                elif isinstance(op, ast.Is):
                    res = left is right
                elif isinstance(op, ast.IsNot):
                    res = left is not right
                elif isinstance(op, ast.In):
                    res = left in right
                elif isinstance(op, ast.NotIn):
                    res = left not in right
                else:
                    return False, None
            except Exception:
                return False, None
            if not res:
                return True, False
            left = right
        return True, True
    return False, None


# ────────────────────────────── 豁免 ──────────────────────────────


DEFAULT_WAIVER_FILE = "docs/axioms-waivers.json"


@dataclass
class Waiver:
    rule: str
    pattern: str
    reason: str
    review_by: str = ""
    note: str = ""


def load_waivers(repo: Repo, path: str = DEFAULT_WAIVER_FILE) -> List[Waiver]:
    full = os.path.join(repo.root, path)
    if not os.path.isfile(full):
        return []
    try:
        data = json.loads(_read(full))
    except json.JSONDecodeError:
        return []
    out: List[Waiver] = []
    for item in data.get("waivers", []):
        rule = str(item.get("rule", "")).upper()
        pattern = str(item.get("path", ""))
        reason = str(item.get("reason", "")).strip()
        if not rule or not pattern or not reason:
            continue
        out.append(Waiver(rule, pattern, reason, str(item.get("review_by", "")), str(item.get("note", ""))))
    return out


def apply_waivers(findings: Sequence[Finding], waivers: Sequence[Waiver], today: Optional[date] = None) -> None:
    today = today or date.today()
    for f in findings:
        for w in waivers:
            if w.rule not in (f.rule, "*"):
                continue
            if not fnmatch.fnmatch(f.path, w.pattern) and not fnmatch.fnmatch(os.path.basename(f.path), w.pattern):
                continue
            expired = False
            if w.review_by:
                try:
                    expired = date.fromisoformat(w.review_by) < today
                except ValueError:
                    expired = False
            f.waived = not expired
            f.waiver_expired = expired
            f.waiver_reason = w.reason + (f"（豁免已过期，复核日 {w.review_by}）" if expired else "")
            if expired:
                f.severity = "must-fix"
            break


# ────────────────────────────── 优先级 ──────────────────────────────

#: 冻结文件：当前不可修改（见 docs/axioms.md 的派活规则）。
FROZEN_FILES = {
    "desktop/main_window.py": "main_window 冻结，改动需先上报排期",
}


def priority_score(f: Finding, axiom: Axiom) -> int:
    """派活优先级分数；分数越高越先做。

    规则（docs/axioms.md）：
      损失权重 100 / 频率权重 10 / 证据可得性权重 3；
      复核级（不是已证实的缺陷）-50；冻结文件 -60；已豁免 -1000。
    用户可感知损失永远排在内部整洁之前，因此 loss 是最高位。
    """
    score = axiom.loss * 100 + axiom.freq * 10 + axiom.evidence * 3
    if f.severity != "must-fix" and not f.waived:
        score -= 50
    if f.path in FROZEN_FILES:
        score -= 60
    if f.waived:
        score -= 1000
    return score


# ────────────────────────────── 渲染 ──────────────────────────────


class Report:
    def __init__(self, axioms: Sequence[Axiom], findings: Sequence[Finding], self_test: Sequence[dict]):
        self.axioms = list(axioms)
        self.findings = sorted(
            findings,
            key=lambda f: (f.rule, 0 if f.severity == "must-fix" else 1, f.path, f.line),
        )
        self.self_test = list(self_test)

    def by_rule(self) -> Dict[str, List[Finding]]:
        groups: Dict[str, List[Finding]] = {a.id: [] for a in self.axioms}
        for f in self.findings:
            groups.setdefault(f.rule, []).append(f)
        return groups

    def summary(self) -> Dict[str, object]:
        groups = self.by_rule()
        active = [f for f in self.findings if not f.waived]
        return {
            "total": len(self.findings),
            "active": len(active),
            "waived": len(self.findings) - len(active),
            "must_fix": sum(1 for f in active if f.severity == "must-fix"),
            "review": sum(1 for f in active if f.severity != "must-fix"),
            "by_rule": {
                a.id: {
                    "title": a.title,
                    "total": len(groups.get(a.id, [])),
                    "active": sum(1 for f in groups.get(a.id, []) if not f.waived),
                    "waived": sum(1 for f in groups.get(a.id, []) if f.waived),
                }
                for a in self.axioms
            },
            "self_test_failed": sum(1 for t in self.self_test if not t.get("ok")),
        }

    def to_dict(self) -> Dict[str, object]:
        return {
            "axioms": [a.meta() for a in self.axioms],
            "summary": self.summary(),
            "self_test": self.self_test,
            "findings": [
                {
                    "rule": f.rule,
                    "path": f.path,
                    "line": f.line,
                    "snippet": f.snippet,
                    "why": f.why,
                    "fix": f.fix,
                    "severity": f.severity,
                    "waived": f.waived,
                    "waiver_reason": f.waiver_reason,
                    "waiver_expired": f.waiver_expired,
                    "priority": priority_score(f, next(a for a in self.axioms if a.id == f.rule)),
                }
                for f in self.findings
            ],
        }

    # -- 文本 --
    def render_text(self, max_per_check: int = 200, prioritized: bool = False) -> str:
        out: List[str] = []
        s = self.summary()
        out.append("=" * 78)
        out.append("工程公理检查（只读静态，全部输入来自本仓库）")
        out.append("=" * 78)
        out.append(
            f"违规 {s['total']} 条：应修 {s['must_fix']}，需复核 {s['review']}，"
            f"豁免 {s['waived']}；公理 {len(self.axioms)} 条；"
            f"反证自检 {'通过' if not s['self_test_failed'] else '失败 %d 条' % s['self_test_failed']}"
        )
        if prioritized:
            out.append("")
            out.append("── 按派活规则排序（先做上面的）──")
            for i, f in enumerate(self.prioritized(), 1):
                a = next(x for x in self.axioms if x.id == f.rule)
                blocked = FROZEN_FILES.get(f.path, "")
                out.append(
                    f"{i:>3}. [{priority_score(f, a):>4}] {f.rule} {f.path}:{f.line}"
                    f"{'（冻结）' if blocked else ''}{'（豁免）' if f.waived else ''}"
                )
            out.append("")

        groups = self.by_rule()
        for a in self.axioms:
            items = groups.get(a.id, [])
            active = [f for f in items if not f.waived]
            waived = [f for f in items if f.waived]
            out.append("")
            out.append(f"── {a.id} {a.title} ── 检查={a.check_name} 违规={len(active)} 豁免={len(waived)}")
            out.append(f"   公理：{a.statement}")
            if not active:
                out.append("   （无现行违规）")
            shown = 0
            for f in active:
                if shown >= max_per_check:
                    out.append(f"   … 其余 {len(active) - shown} 条省略（--max-per-check 调整）")
                    break
                tag = "应修" if f.severity == "must-fix" else "复核"
                out.append(f"   [{tag}] {f.path}:{f.line}  {f.snippet}")
                out.append(f"        为什么违反：{f.why}")
                out.append(f"        怎么修：{f.fix}")
                shown += 1
            for f in waived:
                out.append(f"   [豁免] {f.path}:{f.line}  {f.waiver_reason}")

        out.append("")
        out.append("── 反证自检（真实违规样例必须红，修复后的真实样例必须绿）──")
        for t in self.self_test:
            out.append(
                f"   {'PASS' if t.get('ok') else 'FAIL'} {t['rule']} {t['name']}："
                f"正例 {t.get('positive_hits')} 条，负例 {t.get('negative_hits')} 条"
            )
            if not t.get("ok"):
                out.append(f"         {t.get('detail', '')}")
        out.append("")
        return "\n".join(out)

    def prioritized(self) -> List[Finding]:
        axioms = {a.id: a for a in self.axioms}
        return sorted(self.findings, key=lambda f: (-priority_score(f, axioms[f.rule]), f.path, f.line))

    # -- markdown --
    def render_markdown(self, max_per_check: int = 200) -> str:
        s = self.summary()
        out: List[str] = []
        out.append("# 工程公理检查：首次全量运行结果")
        out.append("")
        out.append(f"- 生成时间：{date.today().isoformat()}")
        out.append(f"- 违规总数：{s['total']}（应修 {s['must_fix']}，需复核 {s['review']}，豁免 {s['waived']}）")
        out.append(f"- 公理条数：{len(self.axioms)}")
        out.append(f"- 反证自检：{'全部通过' if not s['self_test_failed'] else '失败 %d 条' % s['self_test_failed']}")
        out.append("")
        out.append("## 按公理分布")
        out.append("")
        out.append("| 公理 | 标题 | 现行违规 | 豁免 |")
        out.append("|---|---|---:|---:|")
        for a in self.axioms:
            row = s["by_rule"][a.id]
            out.append(f"| {a.id} | {a.title} | {row['active']} | {row['waived']} |")
        out.append("")
        out.append("## 反证自检")
        out.append("")
        out.append("| 公理 | 样例 | 结果 | 正例命中 | 负例命中 |")
        out.append("|---|---|---|---:|---:|")
        for t in self.self_test:
            out.append(
                f"| {t['rule']} | {t['name']} | {'PASS' if t.get('ok') else 'FAIL'} | "
                f"{t.get('positive_hits')} | {t.get('negative_hits')} |"
            )
        out.append("")
        groups = self.by_rule()
        for a in self.axioms:
            items = groups.get(a.id, [])
            active = [f for f in items if not f.waived]
            waived = [f for f in items if f.waived]
            out.append(f"## {a.id} {a.title}（违规 {len(active)}，豁免 {len(waived)}）")
            out.append("")
            out.append(f"> {a.statement}")
            out.append("")
            if not active:
                out.append("无现行违规。")
                out.append("")
            for f in active[:max_per_check]:
                tag = "应修" if f.severity == "must-fix" else "复核"
                out.append(f"- **[{tag}]** `{f.path}:{f.line}` — {f.snippet}")
                out.append(f"  - 为什么：{f.why}")
                out.append(f"  - 怎么修：{f.fix}")
            if len(active) > max_per_check:
                out.append(f"- … 其余 {len(active) - max_per_check} 条省略")
            for f in waived:
                out.append(f"- **豁免** `{f.path}:{f.line}` — {f.waiver_reason}")
            out.append("")
        return "\n".join(out)
