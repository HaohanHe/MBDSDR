# SPDX-License-Identifier: MIT
"""机械守卫：`hasattr(self, "X")` / `getattr(self, "X", ...)` 里的 X 必须真实存在。

背景：`hasattr(self, "_on_vfo_bandwidth_changed_ui")` 在 main_window 里包着
一行假调用，全文件从无该方法定义——守卫永远为假，带宽永不下发，而静态看
「有 hasattr 保护」像是安全的。同类已被 hasattr 骗过两次，所以这里把它变成
机械门禁：

1. 扫描 `desktop/*.py` 每个类的 `hasattr(self, "<字面量>")` /
   `getattr(self, "<字面量>", ...)`；
2. 名字必须来自该类自身 API：方法、`self.X = ...`（含元组/列表解包、
   `AnnAssign`、`AugAssign`）、`setattr(self, "<字面量>", ...)`、
   `for name in ("a", "b"): setattr(self, name, ...)` 这几种动态赋值；
3. 同名基类（本模块或另一个 desktop 模块）的 API 合并计入；
4. 仍解析不到的，必须进 `_WHITELIST` 并写**理由**；白名单条目若已失效
   （源码里再也找不到该守卫）同样红——不允许用白名单兜住历史噪音。

反证（本地手工复现，见文件末注释）：在任一 desktop 类里加
`hasattr(self, "_no_such_method")` → 本文件必红。
"""
import ast
import pathlib

import pytest

_DESKTOP = pathlib.Path(__file__).resolve().parents[1] / "desktop"

#: 无法从 AST 解析到、但确实存在的属性。key = (文件名, 类名, 属性名)。
#: 每条都必须写清「谁提供」。只允许跨模块/运行期注入这类 AST 看不到的来源。
_WHITELIST = {
    ("main_window.py", "MainWindow", "app"):
        "desktop/main.py:612 `window.app = app` 在构造后注入，供 _apply_theme 用；"
        "MainWindow.__init__ 里没有也不该有第二个 app。",
    ("spectrum_widget.py", "SpectrumCurveWidget", "init_pinch_zoom"):
        "touch_helpers.PinchZoomMixin:123 提供；SpectrumCurveWidget 通过动态基类"
        "元组 `_PinchBases` 组合它，AST 里看不到（PinchZoomMixin 兜底为 object 时"
        "该守卫本就为假，是刻意写法）。",
}

_GUARD_CALLS = ("hasattr", "getattr")


def _self_assigned(node) -> set:
    """赋值目标里的 `self.<attr>`（含元组/列表/星号解包、单例 `cls._instance.X`）。"""
    out = set()
    if isinstance(node, ast.Attribute):
        base = node.value
        if isinstance(base, ast.Name) and base.id == "self":
            out.add(node.attr)
        elif isinstance(base, ast.Attribute) and base.attr == "_instance" \
                and isinstance(base.value, ast.Name) and base.value.id == "cls":
            out.add(node.attr)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            out |= _self_assigned(elt)
    elif isinstance(node, ast.Starred):
        out |= _self_assigned(node.value)
    return out


def _class_api(cls: ast.ClassDef) -> set:
    """类自身能提供的名字集合（类体名字 + 方法 + self 属性 + 动态 setattr 名）。"""
    api = set()
    # 类体直接绑定的名字（常量、类级 Signal）经 self.X 同样可读
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    api.add(target.id)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            api.add(stmt.target.id)
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            api.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                api |= _self_assigned(target)
        elif isinstance(node, ast.AnnAssign):
            api |= _self_assigned(node.target)
        elif isinstance(node, ast.AugAssign):
            api |= _self_assigned(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            # `for name in ("a", "b"): setattr(self, name, ...)`
            if isinstance(node.target, ast.Name) and isinstance(
                    node.iter, (ast.Tuple, ast.List)):
                for elt in node.iter.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        api.add(elt.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "setattr" and len(node.args) >= 2:
            tgt, name = node.args[0], node.args[1]
            if isinstance(tgt, ast.Name) and tgt.id == "self" \
                    and isinstance(name, ast.Constant) and isinstance(name.value, str):
                api.add(name.value)
    return api


def _module_classes(tree) -> dict:
    return {node.name: node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)}


def _base_name(base) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _api_with_bases(mod: str, cls: ast.ClassDef, index: dict, seen=None) -> set:
    """类 API ∪ 可达基类 API（基类按类名在同模块/其他 desktop 模块里解析）。"""
    seen = seen if seen is not None else set()
    api = _class_api(cls)
    for base in cls.bases:
        name = _base_name(base)
        if not name or name in seen:
            continue
        seen.add(name)
        found = (mod, index[mod][name]) if name in index[mod] else next(
            ((m, index[m][name]) for m in index if m != mod and name in index[m]),
            None)
        if found:
            api |= _api_with_bases(found[0], found[1], index, seen)
    return api


def _guards_in(cls: ast.ClassDef):
    """类里所有 `hasattr/getattr(self, "<字面量>", ...)` 调用。"""
    for node in ast.walk(cls):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id in _GUARD_CALLS):
            continue
        if len(node.args) < 2 or not isinstance(node.args[0], ast.Name) \
                or node.args[0].id != "self":
            continue
        name = node.args[1]
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            yield node.lineno, name.value


def scan(modules: dict) -> list:
    """返回越界守护清单 [(模块名, 类名, 行号, 属性名)]，白名单已剔除。"""
    index = {mod: _module_classes(tree) for mod, tree in modules.items()}
    bad = []
    for mod, classes in index.items():
        for cls_name, cls in classes.items():
            api = _api_with_bases(mod, cls, index)
            for lineno, attr in _guards_in(cls):
                if attr in api:
                    continue
                if (mod, cls_name, attr) in _WHITELIST:
                    continue
                bad.append((mod, cls_name, lineno, attr))
    return bad


def _load_desktop() -> dict:
    return {
        path.name: ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for path in sorted(_DESKTOP.glob("*.py"))
    }


def test_no_guard_on_missing_self_attribute():
    """桌面端不得存在指向不存在属性的 hasattr/getattr 守卫。"""
    bad = scan(_load_desktop())
    assert not bad, "存在指向不存在方法的 hasattr/getattr 守卫：\n" + "\n".join(
        f"  {mod}:{line} {cls}.{attr}" for mod, cls, line, attr in bad)


def test_whitelist_entries_are_live_and_explained():
    """白名单不得过期：条目对应的守卫必须还在源码里，且理由非空。"""
    modules = _load_desktop()
    index = {mod: _module_classes(tree) for mod, tree in modules.items()}
    for (mod, cls, attr), reason in _WHITELIST.items():
        assert reason.strip(), f"白名单 {mod}:{cls}.{attr} 没写理由"
        cls_def = index.get(mod, {}).get(cls)
        assert cls_def is not None, f"白名单指向不存在的类 {mod}:{cls}"
        assert any(name == attr for _, name in _guards_in(cls_def)), \
            f"白名单 {mod}:{cls}.{attr} 已失效（源码里没有这处守卫），请删掉"


def test_guard_detects_a_bogus_name():
    """反证：合成一个 hasattr(self, "_no_such_method") 的类 → 必须被扫出来。"""
    src = (
        "class Fake:\n"
        "    def __init__(self):\n"
        "        self.real = 1\n"
        "    def go(self):\n"
        "        if hasattr(self, 'real'):\n"
        "            pass\n"
        "        if getattr(self, '_no_such_method', None):\n"
        "            self._no_such_method()\n"
    )
    bad = scan({"fake_mod.py": ast.parse(src)})
    assert ("fake_mod.py", "Fake", 7, "_no_such_method") in bad, bad
    assert all(attr != "real" for _, _, _, attr in bad), "真实存在的属性被误报"
