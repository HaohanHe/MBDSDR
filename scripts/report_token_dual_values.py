# -*- coding: utf-8 -*-
"""令牌双值报告：同名键在「主题字典」与「全局字典」里各有一份值，谁在读。

只报不拦：
  * 不导入 PySide6、不导入项目模块，只用 ast 读源码；
  * 任何异常都打印说明后退出 0；
  * 不进 pre-commit / pre-push 钩子，不参与门禁。

用法：
    python scripts/report_token_dual_values.py

输出三段：
  A. 同名键（THEME_COLORS[*] ∩ COLORS）：两份值是否相同、各自被谁读；
  B. 只在主题字典里的键：QSS / 运行时 / 测试 三个通道的读取数（找死键）；
  C. 只在全局字典里的键：仅计数，供确认别名面。

读取点的判定是静态启发式（assumption 写在输出抬头），行号会随编辑漂移。
"""

from __future__ import annotations

import ast
import os
import re
import sys
from collections import OrderedDict, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKENS_PY = os.path.join(ROOT, "desktop", "tokens.py")
THEMES_PY = os.path.join(ROOT, "desktop", "themes.py")

SKIP_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "build", "dist",
             "node_modules", ".mypy_cache", ".pytest_cache", ".audit_tmp",
             ".trae", ".idea", ".vscode"}

#: QSS 的 palette(<role>) 角色名 → 主题令牌（Qt 角色名归一化后）
ROLE2TOKEN = {
    "window": "bg", "alternatebase": "bg_alt", "base": "card",
    "windowtext": "text", "placeholdertext": "text_secondary",
    "linkvisited": "text_disabled", "mid": "border", "midlight": "grid",
    "highlight": "primary", "link": "primary_text", "brighttext": "accent",
    "shadow": "danger", "button": "button", "light": "button_hover",
    "dark": "button_pressed", "accent": "button_disabled",
}

#: 以主题字典为第一个参数的内部函数（参数名不是 theme_colors 的返回值）
THEME_PARAM_FUNCS = {"_build_palette", "_on_primary", "_on_primary_ink"}


def _lit(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def parse_tokens():
    """返回 (colors, themes, alias_lines)。colors: {key: value}；themes: {主题: {键: 值}}。"""
    src = open(TOKENS_PY, encoding="utf-8").read()
    tree = ast.parse(src)
    colors, themes, alias_lines = OrderedDict(), OrderedDict(), []

    def dict_of(node):
        out = OrderedDict()
        if not isinstance(node, ast.Dict):
            return out
        for k, v in zip(node.keys, node.values):
            key, val = _lit(k), _lit(v)
            if key is not None and val is not None:
                out[key] = val
        return out

    light_alias = None  # _light = self.THEME_COLORS["default"]
    assigns = [n for n in ast.walk(tree)
               if isinstance(n, (ast.Assign, ast.AnnAssign))]
    assigns.sort(key=lambda n: (n.lineno, n.col_offset))
    for node in assigns:
        # self.COLORS = {...} / self.THEME_COLORS = {...}
        if isinstance(node, ast.Assign):
            target = node.targets[0] if node.targets else None
        else:
            target = node.target
        value = node.value
        if isinstance(target, ast.Attribute) and isinstance(value, ast.Dict):
            if target.attr == "COLORS":
                colors = dict_of(value)
            elif target.attr == "THEME_COLORS":
                for k, v in zip(value.keys, value.values):
                    name = _lit(k)
                    if name is not None:
                        themes[name] = dict_of(v)
        # _light = self.THEME_COLORS["default"]
        if (isinstance(target, ast.Name) and isinstance(value, ast.Subscript)
                and isinstance(value.value, ast.Attribute)
                and value.value.attr == "THEME_COLORS"):
            light_alias = target.id
        # COLORS["light_x"] = _light["y"] / self.COLORS["light_x"] = ...
        if (isinstance(target, ast.Subscript)
                and ((isinstance(target.value, ast.Attribute)
                      and target.value.attr == "COLORS")
                     or (isinstance(target.value, ast.Name)
                         and target.value.id == "COLORS"))):
            key = _lit(target.slice)
            if key is None:
                continue
            if (isinstance(value, ast.Subscript) and light_alias
                    and isinstance(value.value, ast.Name)
                    and value.value.id == light_alias):
                src_key = _lit(value.slice)
                val = themes.get("default", {}).get(src_key or "")
                if val:
                    colors[key] = val
                    alias_lines.append((key, node.lineno))
    return colors, themes, alias_lines


ROLE2TOKEN_NORM = ROLE2TOKEN


def parse_qss_reads():
    """扫 themes.py 的 palette(<role>) 文本出现，返回 [(token, line, role, prop)]。

    跳过 Python 注释行与 /* */ CSS 注释块，避免把文档示例计成读取点。
    """
    src = open(THEMES_PY, encoding="utf-8").read().splitlines()
    out = []
    in_comment = False
    for i, line in enumerate(src, 1):
        s = line.strip()
        if in_comment:
            if "*/" in s:
                in_comment = False
                s = s.split("*/", 1)[1].strip()
            else:
                continue
        if s.startswith("#"):
            continue
        if "/*" in s:
            if "*/" not in s:
                in_comment = True
                s = s.split("/*", 1)[0].strip()
            else:
                s = s.split("/*")[0].strip() + s.rsplit("*/", 1)[1].strip()
            if not s:
                continue
        for m in re.finditer(r"palette\(([^)]+)\)", s):
            role = m.group(1).strip().replace("-", "").lower()
            token = ROLE2TOKEN_NORM.get(role)
            if not token:
                continue
            # 属性名只用于「用途」提示
            prop = "取值"
            if "selection-background-color" in s or "background" in s:
                prop = "底"
            if "border" in s:
                prop = "边框"
            if re.search(r"(^|[;{\s])color\s*:", s) and "background" not in s:
                prop = "字"
            if "selection-color" in s:
                prop = "选中字"
            out.append((token, i, role, prop))
    return out


def _expr_is_theme(expr, theme_vars):
    if isinstance(expr, ast.Name) and expr.id in theme_vars:
        return True
    if isinstance(expr, ast.Attribute) and expr.attr == "colors":
        return True
    return False


def _expr_is_global(expr, global_vars=None):
    if isinstance(expr, ast.Name):
        return expr.id == "COLORS" or expr.id in (global_vars or set())
    if isinstance(expr, ast.Attribute) and expr.attr in ("COLORS", "_colors"):
        return True
    return False


def _purpose_of(line):
    if re.search(r"\b(QColor|QPen|QBrush|setPen|setBrush|PAL_|COLOR_)", line):
        return "图元"
    if "selection-background-color" in line:
        return "选中底"
    if "background" in line:
        return "底"
    if "border" in line:
        return "边框"
    if re.search(r"(^|[;{\"' ])color\s*:", line):
        return "字"
    return "取值"


def file_names(path):
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class ReadCollector(ast.NodeVisitor):
    """按函数作用域推断「哪些局部名持有主题字典」，再收集键读取。"""

    def __init__(self, relpath, lines, reads):
        self.relpath = relpath
        self.lines = lines
        self.reads = reads  # [(key, kind, relpath, line, purpose)]

    # ---- 作用域包装 ----
    def _visit_scope(self, node):
        theme_vars = set()
        global_vars = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in THEME_PARAM_FUNCS and node.args.args:
                theme_vars.add(node.args.args[0].arg)
        # 顺序预扫赋值：x = ...theme_colors(...) / x = theme.colors / x = c.copy()
        for stmt in ast.walk(node):
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                val = stmt.value
                tgt = stmt.targets[0] if isinstance(stmt, ast.Assign) else stmt.target
                name = None
                if isinstance(tgt, ast.Name):
                    name = tgt.id
                elif isinstance(tgt, ast.Attribute):
                    name = "__attr__" + tgt.attr
                if name is None:
                    continue
                if self._init_is_theme(val, theme_vars):
                    theme_vars.add(name)
                elif _expr_is_global(val, global_vars):
                    global_vars.add(name)
        old = getattr(self, "_theme_vars", None)
        oldg = getattr(self, "_global_vars", None)
        self._theme_vars = theme_vars
        self._global_vars = global_vars
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                self.visit(stmt)
        else:
            for stmt in node.body:
                self.visit(stmt)
        self._theme_vars = old or set()
        self._global_vars = oldg or set()

    def _init_is_theme(self, val, theme_vars):
        if isinstance(val, ast.Call):
            f = val.func
            if isinstance(f, ast.Attribute) and f.attr in ("theme_colors",):
                return True
            if isinstance(f, ast.Name) and f.id in ("get_theme",):
                return True
            if isinstance(f, ast.Attribute) and f.attr in ("copy",):
                return _expr_is_theme(f.value, theme_vars)
        if isinstance(val, ast.Attribute) and val.attr == "colors":
            return True
        if isinstance(val, ast.Name) and val.id in theme_vars:
            return True
        return False

    def visit_FunctionDef(self, node):
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_scope(node)

    def visit_Module(self, node):
        self._visit_scope(node)

    # ---- 读取点 ----
    def _add(self, key, kind, node):
        line = node.lineno
        snippet = self.lines[line - 1] if line - 1 < len(self.lines) else ""
        self.reads.append((key, kind, self.relpath, line, _purpose_of(snippet)))

    def visit_Subscript(self, node):
        key = _lit(node.slice)
        tv = getattr(self, "_theme_vars", set())
        gv = getattr(self, "_global_vars", set())
        if key:
            if _expr_is_theme(node.value, tv):
                self._add(key, "theme", node)
            elif _expr_is_global(node.value, gv):
                self._add(key, "global", node)
        self.generic_visit(node)

    def visit_Call(self, node):
        key = None
        kind = None
        f = node.func
        tv = getattr(self, "_theme_vars", set())
        gv = getattr(self, "_global_vars", set())
        if isinstance(f, ast.Attribute) and f.attr == "get" and node.args:
            key = _lit(node.args[0])
            if key:
                if _expr_is_theme(f.value, tv):
                    kind = "theme"
                elif _expr_is_global(f.value, gv):
                    kind = "global"
        elif isinstance(f, ast.Name) and f.id == "_tok" and node.args:
            key, kind = _lit(node.args[0]), "global"
        elif isinstance(f, ast.Attribute) and f.attr == "_tok" and node.args:
            key, kind = _lit(node.args[0]), "global"
        elif isinstance(f, ast.Attribute) and f.attr == "status_color" and node.args:
            # status_color("danger") -> 动态读 status_danger（函数体里 c[key]）
            raw = _lit(node.args[0])
            if raw:
                key = raw if raw.startswith("status_") else "status_" + raw
                kind = "theme"
        if key and kind:
            self._add(key, kind, node)
        self.generic_visit(node)


def collect_reads():
    reads = []
    for path in file_names(ROOT):
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        try:
            lines = open(path, encoding="utf-8").read().splitlines()
            tree = ast.parse("\n".join(lines))
        except (SyntaxError, UnicodeDecodeError):
            continue
        ReadCollector(rel, lines, reads).visit(tree)
    return reads


def kind_of(path):
    if path.startswith(("tests/", "tools/")) or path == "scripts/theme_contrast.py":
        return "测试"
    return "运行时"


def parse_gate_pairs():
    """解析 scripts/theme_contrast.py 的 PAIRS 表：那是唯一的「间接门禁读取」。

    表里的键是循环变量，ast 静态扫不到，但 audit() 会按表逐主题取色；
    漏掉它会把 success 误判成「可直接删」。
    """
    path = os.path.join(ROOT, "scripts", "theme_contrast.py")
    if not os.path.exists(path):
        return []
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and node.targets
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "PAIRS"
                and isinstance(node.value, (ast.Tuple, ast.List))):
            for elt in node.value.elts:
                if not isinstance(elt, ast.Tuple) or len(elt.elts) < 2:
                    continue
                fg, bgk = _lit(elt.elts[0]), _lit(elt.elts[1])
                for key in (fg, bgk):
                    if key:
                        out.append((key, "theme", "scripts/theme_contrast.py",
                                    elt.lineno, "门禁配对"))
    return out


def main():
    colors, themes, alias_lines = parse_tokens()
    qss = parse_qss_reads()
    reads = collect_reads()
    reads.extend(parse_gate_pairs())
    theme_keys = list(themes[next(iter(themes))].keys()) if themes else []
    print("令牌双值报告（只读 / 只报不拦）")
    print("=" * 78)
    print(f"主题字典：{len(themes)} 套（{'、'.join(themes)}），键 {len(theme_keys)} 个")
    print(f"全局字典：{len(colors)} 个键（含 {len(alias_lines)} 个别名）")
    print("读取点口径：ast 静态推断；QSS 只数 themes.py 里未注释的 palette()；")
    print("  tests/ 与 tools/ 记为「测试」，其余记为「运行时」；行号会随编辑漂移。")
    print()

    # ---- A. 同名键 ----
    both = [k for k in theme_keys if k in colors]
    print("A. 同名键（主题与全局各有一份值）")
    print("-" * 78)
    if not both:
        print("（无）")
    for key in both:
        vals = {t: themes[t].get(key, "—") for t in themes}
        differs = [t for t, v in vals.items() if v.lower() != colors[key].lower()]
        print(f"■ {key}：全局 {colors[key]}；主题 " +
              "、".join(f"{t}={v}" for t, v in vals.items()))
        print(f"  判定：{'两份值' if differs else '同名同值'}"
              f"（{len(differs)}/{len(themes)} 套主题与全局不同）")
        q = [x for x in qss if x[0] == key]
        if q:
            props = defaultdict(int)
            for _, _, _, p in q:
                props[p] += 1
            print(f"  主题读取 · QSS：{len(q)} 处（" +
                  "、".join(f"{p}{n}" for p, n in props.items()) + "）")
        tr = [r for r in reads if r[0] == key and r[1] == "theme"
              and kind_of(r[2]) == "运行时"]
        tt = [r for r in reads if r[0] == key and r[1] == "theme"
              and kind_of(r[2]) == "测试"]
        gr = [r for r in reads if r[0] == key and r[1] == "global"]
        for label, rows in (("主题读取 · 运行时", tr),
                            ("主题读取 · 测试", tt),
                            ("全局读取", gr)):
            if not rows:
                print(f"  {label}：0 处")
                continue
            print(f"  {label}：{len(rows)} 处")
            for _, _, path, line, purpose in rows:
                print(f"    {path}:{line}  [{kind_of(path)}/{purpose}]")
        print()

    # ---- B. 主题每个键的三通道读取数（含与全局同名的键）----
    print("B. 主题字典每个键的三通道读取数（零读取 = 死键，可考虑删）")
    print("-" * 78)
    print(f"{'键':<18}{'QSS':>5}{'运行时':>7}{'测试':>5}  状态")
    dead = []
    for key in theme_keys:
        q = sum(1 for x in qss if x[0] == key)
        rt = sum(1 for r in reads if r[0] == key and r[1] == "theme"
                 and kind_of(r[2]) == "运行时")
        te = sum(1 for r in reads if r[0] == key and r[1] == "theme"
                 and kind_of(r[2]) == "测试")
        state = ""
        if rt == 0 and q == 0 and te == 0:
            state = "全零（可直接删）"
            dead.append((key, "全零", []))
        elif rt == 0 and q == 0:
            state = "仅门禁（删键需同步改门禁）"
            ev = [r for r in reads if r[0] == key and r[1] == "theme"]
            dead.append((key, "仅门禁", ev))
        elif rt == 0:
            state = "运行时零读（QSS+门禁）" if te else "仅 QSS"
        print(f"{key:<18}{q:>5}{rt:>7}{te:>5}  {state}")
    print()
    for key, kind, ev in dead:
        print(f"[死键/{kind}] {key}")
        for _, _, path, line, purpose in ev:
            print(f"    证据：{path}:{line}  [{purpose}]")
    if not dead:
        print("零运行时读取的键：无")
    print()

    # ---- C. 只在全局里的键 ----
    only_global = [k for k in colors if k not in theme_keys]
    print("C. 只在全局字典里的键")
    print("-" * 78)
    print(f"共 {len(only_global)} 个：" + "、".join(only_global))
    print()
    return 0


if __name__ == "__main__":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        main()
    except Exception as exc:  # 只报不拦：内部错误也不返回非 0
        print(f"[report] 扫描失败（不影响任何门禁）：{type(exc).__name__}: {exc}")
    sys.exit(0)
