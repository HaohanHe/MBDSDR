# SPDX-License-Identifier: MIT
"""
快捷键登记表与实现一致性守卫
============================

防"加了按钮没登记快捷键"，双向核对：
  * main_window 里每个 `_key("id")` 的 id 都必须在 desktop/shortcuts.py 登记；
  * window/planetarium scope 的带键条目必须真的被 main_window 使用（死条目红）；
  * spectrum scope 的条目必须被 spectrum_widget 用 `bind(...)` 取键（同一事实源）；
  * main_window 里不许再出现裸 `QKeySequence("Ctrl+X")` 字面量；
  * 菜单 QAction 的中文标签必须能在登记表里找到（新增菜单项必须先登记）；
  * 同一 scope 内键位不得重复；跨 scope 允许同键（如 A：全天 / AM），
    归属由「焦点在哪，局部优先」规则决定（见 shortcuts.py 顶部注释）。

另锁：Esc 分层退出（先关星象仪 AI 回复卡，再退形态）+ 顶栏 Tab 顺序存在。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_shortcuts_guard.py -q
"""
import ast
import inspect
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from shortcuts import SHORTCUTS, BY_ID  # noqa: E402

_MAIN_WINDOW = os.path.join(_ROOT, "desktop", "main_window.py")
_SPECTRUM_WIDGET = os.path.join(_ROOT, "desktop", "spectrum_widget.py")

#: 由 main_window 装配的 scope（其余由所属控件自己装配）
_WINDOW_SCOPES = ("window", "planetarium")


def _main_tree():
    with open(_MAIN_WINDOW, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=_MAIN_WINDOW)


def _collect_calls(tree):
    """收集 _key("id") 实参、裸 QKeySequence("...") 实参、QAction("label") 标签。"""
    used_ids, literal_keys, action_labels = set(), [], set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = getattr(func, "id", getattr(func, "attr", ""))
        first = node.args[0] if node.args else None
        if fname == "_key" and isinstance(first, ast.Constant) \
                and isinstance(first.value, str):
            used_ids.add(first.value)
        if fname == "QKeySequence" and isinstance(first, ast.Constant) \
                and isinstance(first.value, str):
            literal_keys.append(first.value)
        if fname == "QAction" and isinstance(first, ast.Constant) \
                and isinstance(first.value, str):
            action_labels.add(first.value)
    return used_ids, literal_keys, action_labels


def test_registry_has_no_duplicate_or_conflicting_keys():
    # 同一 scope 内同键 = 真冲突（同一个控件里 Qt 无法消歧）；
    # 跨 scope 同键是「焦点在哪，局部优先」的合法用法。
    seen = {}
    for s in SHORTCUTS:
        if not s.key:
            continue
        slot = (s.scope, s.key)
        assert slot not in seen, (
            f"scope={s.scope} 内快捷键重复: {s.id} 与 {seen[slot]} 都是 {s.key}")
        seen[slot] = s.id
    ids = [s.id for s in SHORTCUTS]
    assert len(ids) == len(set(ids)), "登记 id 重复"


def test_unbound_entries_explain_why():
    """key=None 的条目必须写原因，否则等于漏配快捷键。"""
    for s in SHORTCUTS:
        if s.key is None:
            assert s.note.strip(), f"{s.id} 未配快捷键且没写原因"


def test_every_registered_id_is_used_by_main_window():
    used_ids, _, _ = _collect_calls(_main_tree())
    declared = {s.id for s in SHORTCUTS
                if s.key is not None and s.scope in _WINDOW_SCOPES}
    missing = declared - used_ids
    assert not missing, f"登记了但 main_window 没用上（死条目）: {sorted(missing)}"


def test_spectrum_scoped_ids_are_owned_by_spectrum_widget():
    """spectrum scope 的键由频谱控件自己装配，必须真的在那里出现。"""
    with open(_SPECTRUM_WIDGET, "r", encoding="utf-8") as f:
        src = f.read()
    declared = {s.id for s in SHORTCUTS if s.scope == "spectrum"}
    missing = [sid for sid in sorted(declared) if f'"{sid}"' not in src]
    assert not missing, f"spectrum scope 登记了但频谱控件没用上: {missing}"
    assert "_bind_shortcut(" in src, "频谱控件没走 shortcuts.bind 取键（第二事实源）"


def test_main_window_only_uses_registered_ids():
    used_ids, _, _ = _collect_calls(_main_tree())
    assert used_ids <= set(BY_ID), (
        f"main_window 用了未登记的 id: {sorted(used_ids - set(BY_ID))}")


def test_no_raw_key_literals_outside_registry():
    """不许绕过登记表直接写键位。"""
    _, literal_keys, _ = _collect_calls(_main_tree())
    registered = {s.key for s in SHORTCUTS if s.key}
    raw = [k for k in literal_keys if k not in registered]
    assert not raw, f"main_window 存在未登记的裸键位: {sorted(set(raw))}"


def test_every_menu_action_label_is_registered():
    """新增菜单项必须先登记（哪怕 key=None 也要写原因）。"""
    _, _, labels = _collect_calls(_main_tree())
    known = {s.label for s in SHORTCUTS}
    unknown = sorted(labels - known)
    assert not unknown, f"菜单项未在 shortcuts.py 登记: {unknown}"


# ---------------------------------------------------------------------------
# Esc 分层退出
# ---------------------------------------------------------------------------
class _FakeCard:
    def __init__(self, visible: bool):
        self._visible = visible

    def isVisible(self):  # noqa: N802 (Qt 命名)
        return self._visible

    def setVisible(self, visible):  # noqa: N802
        self._visible = visible


class _FakeWindow:
    def __init__(self, mode: str, card_visible: bool):
        self._app_mode = mode
        self._planetarium_ai_card = _FakeCard(card_visible)
        self.mode_calls = []

    def set_app_mode(self, mode: str) -> None:
        self.mode_calls.append(mode)
        self._app_mode = mode


def test_escape_closes_ai_card_before_leaving_planetarium():
    from main_window import MainWindow
    win = _FakeWindow("planetarium", card_visible=True)
    MainWindow._on_escape(win)
    assert win._planetarium_ai_card.isVisible() is False, "Esc 没关掉 AI 回复卡"
    assert win.mode_calls == [], "弹层还开着就退了形态，层级顺序错"


def test_escape_exits_planetarium_when_no_overlay():
    from main_window import MainWindow
    win = _FakeWindow("planetarium", card_visible=False)
    MainWindow._on_escape(win)
    assert win.mode_calls == ["ide"], "Esc 没退出星象仪形态"


def test_escape_is_noop_in_ide_mode():
    from main_window import MainWindow
    win = _FakeWindow("ide", card_visible=False)
    MainWindow._on_escape(win)
    assert win.mode_calls == [], "IDE 形态下 Esc 不应劫持"


# ---------------------------------------------------------------------------
# Tab 顺序
# ---------------------------------------------------------------------------
def test_top_bar_tab_order_covers_all_interactive_controls():
    from main_window import MainWindow
    src = inspect.getsource(MainWindow._setup_tab_order)
    for widget in ("toolbar_mode_combo", "theme_combo", "lang_combo",
                   "layout_combo", "layout_save_btn", "layout_delete_btn",
                   "new_vfo_btn", "cycle_vfo_btn", "toolbar_volume",
                   "toolbar_mute_btn", "record_btn", "replay_btn"):
        assert widget in src, f"顶栏 {widget} 未进入 Tab 顺序"
    assert "setTabOrder" in src
