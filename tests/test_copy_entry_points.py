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


#: 控件类型词固定词表：非可操作文字（组名/标签）必须与其中一个词同句出现，
#: 并证明同一容器里真有该类型控件，才算「双命中」。
_TYPE_WORD_NAMES = ("下拉框", "输入框", "按钮", "滑块", "页签")

_ACTION_VERBS = ("点", "选", "输入")
_VIEW_VERBS = ("看", "查看", "确认")

#: 例外表冻结条数：出现第 N+1 条即红「新增例外需评审」，防止长成白名单。
_EXCEPTION_LIMIT = 1


def _exceptions_ok() -> bool:
    return len(_DIALOG_ONLY) <= _EXCEPTION_LIMIT


def _clauses(text: str) -> list:
    """按句号/分号切句，动词只对所在分句生效（一句里可同时有「点」和「看」）。"""
    return [c for c in re.split(r"[；;。\n]", text or "") if c.strip()]


def _type_classes(type_word: str) -> tuple:
    from PySide6.QtWidgets import (
        QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit,
        QPlainTextEdit, QSlider, QTabWidget,
    )
    return {
        "下拉框": (QComboBox,),
        "输入框": (QLineEdit, QAbstractSpinBox, QPlainTextEdit),
        "按钮": (QAbstractButton,),
        "滑块": (QSlider,),
        "页签": (QTabWidget,),
    }.get(type_word, ())


def _is_operable(widget, kind: str) -> bool:
    from PySide6.QtWidgets import (
        QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QSlider,
    )
    if kind in ("action", "menu", "item", "tab"):
        return True
    return isinstance(
        widget, (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox, QSlider))


def _is_visible(widget) -> bool:
    from PySide6.QtWidgets import QWidget
    if isinstance(widget, QWidget):
        return not widget.isHidden()
    return True  # QAction 属菜单，启动态未展开；存在性已由索引证明


def _text_index(window) -> dict:
    """可见文字 → [(控件, 类别, 是否可操作)]。

    只收用户真能看到的文字（控件 text / 下拉项 / 菜单动作 / 分组标题 / 页签
    文字 / 设置分节头）；**不收 tooltip 与 windowTitle**——tooltip 要悬停才
    出现，不能当定位依据。
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QGroupBox, QLabel, QMenu, QTabWidget, QWidget

    index = {}

    def add(text, kind, widget):
        t = (text or "").strip()
        if t:
            index.setdefault(t, []).append((widget, kind, _is_operable(widget, kind)))

    for wdg in window.findChildren(QWidget):
        if type(wdg).__name__ == "_SectionHeader":
            add(wdg.text(), "section", wdg)
        elif isinstance(wdg, QGroupBox):
            add(wdg.title(), "group", wdg)
        elif isinstance(wdg, QLabel):
            add(wdg.text(), "label", wdg)
        else:
            try:
                add(wdg.text(), "control", wdg)
            except Exception:  # noqa: BLE001 - 不是所有 QWidget 都有 text()
                pass
            if isinstance(wdg, QTabWidget):
                for i in range(wdg.count()):
                    add(wdg.tabText(i), "tab", wdg)
            try:
                for i in range(wdg.count()):
                    add(wdg.itemText(i), "item", wdg)
            except Exception:  # noqa: BLE001 - 非 item 型控件没有 count/itemText
                pass
    for action in window.findChildren(QAction):
        add(action.text(), "action", action)
    for menu in window.findChildren(QMenu):
        add(menu.title(), "menu", menu)
    return index


def _type_hit(widget, type_word: str) -> bool:
    """组名/标签所在容器里是否真有「类型词」对应的控件。"""
    from PySide6.QtWidgets import QGroupBox

    classes = _type_classes(type_word)
    if not classes:
        return False
    container = widget if isinstance(widget, QGroupBox) else widget.parentWidget()
    return container is not None and any(
        container.findChildren(cls) for cls in classes)


def _unresolved(window, texts) -> list:
    """返回在真窗口里找不到落点的入口段。

    口径：
      * 精确匹配可见文字，不收 tooltip；
      * 分句判动词：点/选/输入 → 必须命中可操作控件；
        看/查看/确认 → 可命中可见文字（卡片标题、标签），但须此刻存在且
        未隐藏；无动词时按动作类从严；
      * 组名/标签 + 类型词（下拉框/输入框/按钮/滑块/页签）双命中，并断言
        同一容器里确有该类型控件；
      * 对话框专属段只认 ``_DIALOG_ONLY``，条数冻结。
    """
    index = _text_index(window)
    missing = []

    def mark(seg):
        if seg not in missing:
            missing.append(seg)

    for text in texts:
        for clause in _clauses(text):
            if any(v in clause for v in _ACTION_VERBS):
                mode = "ACTION"
            elif any(v in clause for v in _VIEW_VERBS):
                mode = "VIEW"
            else:
                mode = "ACTION"
            type_words = [tw for tw in _TYPE_WORD_NAMES if tw in clause]
            for seg in _segments(clause):
                if seg in _DIALOG_ONLY:
                    continue
                if seg in _SCOPED_SEGMENTS:
                    holder = getattr(window, "control_panel", None)
                    widgets = getattr(holder, _SCOPED_SEGMENTS[seg], [])
                    if any(w.text().strip() == seg for w in widgets):
                        continue
                    mark(seg)
                    continue
                entries = index.get(seg)
                if not entries:
                    for alias in _WIDGET_ALIASES.get(seg, ()):
                        entries = index.get(alias)
                        if entries:
                            break
                if not entries:
                    mark(seg)
                    continue
                if any(operable for _, _, operable in entries):
                    continue
                if any(_type_hit(wdg, tw) for wdg, _, _ in entries
                       for tw in type_words):
                    continue
                if any(kind == "section" and _is_visible(wdg)
                       for wdg, kind, _ in entries):
                    continue
                if mode == "VIEW" and all(_is_visible(wdg)
                                          for wdg, _, _ in entries):
                    continue
                mark(seg)
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


def test_exception_table_is_frozen():
    assert _exceptions_ok(), (
        f"例外表已有 {len(_DIALOG_ONLY)} 条，上限 {_EXCEPTION_LIMIT}；"
        "新增例外需评审——只有既无可操作控件、又无稳定可见文字承载区的段才可登记")


def test_counterexample_new_exception_turns_red(monkeypatch):
    monkeypatch.setitem(_DIALOG_ONLY, "量子面板", "desktop/x.py")
    assert not _exceptions_ok()


def test_counterexample_view_to_action_turns_red(main_window):
    """「看」类可指卡片标题；同一段改成「点」类后必须命中可操作控件。"""
    ok = _unresolved(main_window, ["在「状态」页签看「解码结果」卡片。"])
    assert ok == [], ok
    bad = _unresolved(main_window, ["在「状态」页签点「解码结果」卡片。"])
    assert bad == ["解码结果"], bad


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
