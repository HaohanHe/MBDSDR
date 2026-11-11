# SPDX-License-Identifier: MIT
"""文案入口可解析为真实控件（P1-41，W86）。

与 ``tests/test_help_path_truthfulness.py`` 的分工：
  * 那边验「生产源码里有这个字符串字面量」——防文案自证、防注释假绿；
  * 本文件验「**启动后的真窗口控件树里点得到**」——防字面量还活着、控件
    却已被删/搬走的死引用（P1-41 的实际形态：用户照着做找不到）。

覆盖对象：
  * 帮助「怎么办」全部路径（精选 + 族级 + 逐码，``troubleshooting``）；
  * 帮助面板三步上手（``help_panel.ONBOARDING_STEPS``，此前无人守）；
  * 几条已修文案的回归钉：星象仪入口（E6）、观测地点入口（E5）。

反证：把某条路径改成不存在的「量子面板」→ ``_unresolved`` 必须报出来。
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest  # noqa: E402

import check_help_paths as chp  # noqa: E402
import help_panel  # noqa: E402
import troubleshooting as ts  # noqa: E402

#: 只出现在「点开才构造」的对话框里、启动态控件树中不存在的入口。
#: 值写明字面量所在文件：这些退回 ``check_help_paths._literal_ok`` 兜底，
#: 不因为它们不在启动态树里就放弃核验。
_DIALOG_ONLY = {
    "远程 rtl_tcp 源": "desktop/main_window.py",
}

_TOKEN_RE = re.compile(r"「([^」]+)」")

#: 段与控件原文不同、但确属同一入口的情况：逐条登记预期控件原文。
#: 只收菜单助记符后缀（``文件(F)`` / ``文件(&F)``）与产品名后缀
#: （``更新卫星 TLE (Celestrak)``）；禁止用包含判断或正则兜底——那会让
#: 「频率 −1 格」靠父分组「频率」混过（2026-11-11 实测发现的漏网）。
_WIDGET_ALIASES = {
    "文件": ("文件(F)", "文件(&F)"),
    "工具": ("工具(T)", "工具(&T)"),
    "视图": ("视图(V)", "视图(&V)"),
    "更新卫星 TLE": ("更新卫星 TLE (Celestrak)",),
}

#: 单字符段只在指定控件集合里核验：全窗口另有多处 "+"（悬浮面板折叠、
#: 地点面板缩放），全局精确匹配会被这些同名按钮蒙过。
_SCOPED_SEGMENTS = {
    "-": "_step_buttons",
    "+": "_step_buttons",
}


def _segments(text: str) -> list:
    out = []
    for token in _TOKEN_RE.findall(text or ""):
        for seg in token.split("→"):
            seg = seg.strip()
            if seg and seg not in out:
                out.append(seg)
    return out


def _remedy_texts() -> list:
    texts = [r.next_step for r in ts.build_remedies()]
    texts += list(ts.VISIBLE_NEXT_STEPS.values())
    texts += list(ts.CODE_NEXT_STEPS.values())
    return texts


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def main_window(qtapp, isolated_profile):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001 - 收尾失败不拖垮用例
        pass
    w.close()


def _vocabulary(window) -> set:
    """启动态真窗口里用户能看到的全部文字（控件 / 提示 / 菜单 / 下拉 / 分组）。"""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QGroupBox, QMenu, QWidget

    seen = set()

    def add(text):
        t = (text or "").strip()
        if t:
            seen.add(t)

    for wdg in window.findChildren(QWidget):
        try:
            add(wdg.text())
        except Exception:  # noqa: BLE001 - 不是所有 QWidget 都有 text()
            pass
        add(wdg.toolTip())
        add(wdg.windowTitle())
        try:
            for i in range(wdg.count()):
                add(wdg.itemText(i))
        except Exception:  # noqa: BLE001 - 非 item 型控件没有 count/itemText
            pass
    for action in window.findChildren(QAction):
        add(action.text())
        add(action.toolTip())
    for group in window.findChildren(QGroupBox):
        add(group.title())
    for menu in window.findChildren(QMenu):
        add(menu.title())
    return seen


def _unresolved(window, texts) -> list:
    """返回在真窗口里找不到的入口段（对话框专属项除外）。

    非菜单段一律**精确**匹配控件原文：``seg in vocab`` / ``seen in seg``
    这类包含判断会让「频率 −1 格」靠父分组「频率」蒙过。只有
    ``_WIDGET_ALIASES`` 里逐条登记的菜单/产品名后缀例外。
    """
    vocab = _vocabulary(window)
    missing = []
    for text in texts:
        for seg in _segments(text):
            if seg in _DIALOG_ONLY:
                continue
            if seg in _SCOPED_SEGMENTS:
                holder = getattr(window, "control_panel", None)
                widgets = getattr(holder, _SCOPED_SEGMENTS[seg], [])
                if any(w.text().strip() == seg for w in widgets):
                    continue
                if seg not in missing:
                    missing.append(seg)
                continue
            if seg in vocab:
                continue
            aliases = _WIDGET_ALIASES.get(seg)
            if aliases and any(alias in vocab for alias in aliases):
                continue
            if seg not in missing:
                missing.append(seg)
    return missing


def test_remedy_entries_resolve_to_live_widgets(main_window):
    missing = _unresolved(main_window, _remedy_texts())
    assert not missing, f"帮助路径在真窗口里找不到：{missing}"


def test_onboarding_entries_resolve_to_live_widgets(main_window):
    texts = [step["body"] for step in help_panel.onboarding_steps()]
    missing = _unresolved(main_window, texts)
    assert not missing, f"三步上手入口在真窗口里找不到：{missing}"


def test_dialog_only_entries_still_have_source_literal():
    cache = {}
    for seg, path in _DIALOG_ONLY.items():
        ok, why = chp._literal_ok(seg, cache)
        assert ok, f"{seg}（{path}）不在启动态控件树里，且源码字面量也不成立：{why}"


def test_planetarium_entry_is_left_tab_not_topbar():
    """E6 回归：星象仪入口是左栏页签 + 视图菜单，不是顶栏按钮。"""
    body = help_panel.onboarding_steps()[2]["body"]
    assert "顶栏" not in body, body
    assert "左栏" in body and "星象仪模式" in body and "视图" in body, body


def test_observer_hint_points_at_live_change_location_button(main_window):
    """E5 回归：观测地点提示指向真实按钮，不再提已删除的经纬度输入框。"""
    from new_spacetime_panel import _NO_OBSERVER_HINT
    from PySide6.QtWidgets import QPushButton

    assert "更改地点…" in _NO_OBSERVER_HINT
    assert "输入框" not in _NO_OBSERVER_HINT
    buttons = [b.text() for b in main_window.findChildren(QPushButton)]
    assert "更改地点…" in buttons, buttons[:20]


def test_counterexample_fake_entry_turns_red(main_window, monkeypatch):
    monkeypatch.setitem(
        ts.NEXT_STEPS, "disk_full",
        "在「量子面板」把「录音目录」换到剩余空间更大的盘。")
    missing = _unresolved(main_window, _remedy_texts())
    assert "量子面板" in missing, missing


def test_counterexample_removing_step_buttons_turns_red(main_window):
    """反证：调谐步进的「-」「+」按钮没了，频率超范围文案必须红。

    旧实现用包含判断，删掉按钮后仍能靠父分组「频率」蒙过；现在只有按钮
    本身的原文能证明入口存在。清空按钮文字等同按钮被删（控件树里不再有
    该段），收尾恢复，避免影响同模块后续用例。
    """
    panel = main_window.control_panel
    buttons = [b for b in panel._step_buttons if b.text() in ("-", "+")]
    assert len(buttons) == 2, [b.text() for b in panel._step_buttons]

    saved = [(btn, btn.text()) for btn in buttons]
    try:
        for btn, _ in saved:
            btn.setText("")
        missing = _unresolved(
            main_window, [ts.NEXT_STEPS["frequency_out_of_range"]])
        assert "-" in missing and "+" in missing, missing
    finally:
        for btn, text in saved:
            btn.setText(text)
