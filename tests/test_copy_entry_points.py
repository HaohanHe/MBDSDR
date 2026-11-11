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
    # 设置页是惰性构建的：SettingsPanel 只在首次 showEvent 里调
    # ``_ensure_ui_built()`` 建出四个分节标题与逐行控件。启动态（从未 show()）
    # 的控件树里没有「接收与频谱」「数据与操作」「调谐步进」「选择目录…」，
    # 扫描会把指向设置分节的帮助路径误判为「真窗口里找不到」。
    #
    # 修在检查侧而不让产品提前构建：检查不该依赖「某个面板恰好被显示过」，
    # 也不该为一个文本断言去改设置面板的启动成本。这里显式物化，手法与
    # tests/test_settings_schema.py 的 ``env`` fixture 一致（调补建入口，
    # 不 show()——show 会让 reset_all(confirm=True) 弹出确认框）。
    # 物化只建控件，不放宽断言：分节标题写错照样在 ``_text_index`` 里查不到。
    panel = getattr(w, "settings_panel", None)
    if panel is not None:
        panel._ensure_ui_built()
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

#: 分句里出现这些容器名时，把该段的同名候选限到那个容器（显式词表，
#: 不做自然语言解析）。值为 MainWindow 属性名；``None`` 表示按 QGroupBox 标题认。
_PANEL_SCOPES = {
    "控制面板": "control_panel",
    "控制": "control_panel",
    "设备": "device_panel",
    "设置": "settings_panel",
    "状态": "status_panel",
    "AI 助手": "ai_panel",
    "参数": None,
}

#: 可折叠分组的标题前缀装饰符（「▾ 频率」实为「频率」组）。
_TITLE_DECOR = "▾▸▶◀▼▲ "


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
        t = (text or "").strip().lstrip(_TITLE_DECOR).strip()
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


def _type_container(widget):
    from PySide6.QtWidgets import QGroupBox
    return widget if isinstance(widget, QGroupBox) else widget.parentWidget()


def _type_hit(widget, type_word: str) -> bool:
    """组名/标签所在容器里是否真有「类型词」对应的控件。"""
    classes = _type_classes(type_word)
    container = _type_container(widget)
    return bool(classes) and container is not None and any(
        container.findChildren(cls) for cls in classes)


def _type_count(widget, type_word: str) -> int:
    container = _type_container(widget)
    if container is None:
        return 0
    return sum(len(container.findChildren(cls))
               for cls in _type_classes(type_word))


def _row_pair_hit(widget, type_word: str) -> bool:
    """同行配对：标签与该类型控件必须在同一行，而不是「同容器里碰巧有一个」。

    * 最近的 QFormLayout 行：只认同一行的 FieldRole 控件；
    * 没有 QFormLayout（控制面板的横向行）：只认同一个水平行布局里的兄弟控件。
    """
    from PySide6.QtWidgets import QBoxLayout, QFormLayout

    classes = _type_classes(type_word)
    if not classes:
        return False

    def is_type(wd) -> bool:
        return wd is not None and isinstance(wd, classes)

    node = widget.parentWidget()
    while node is not None:
        for lay in node.findChildren(QFormLayout):
            row = lay.getWidgetPosition(widget)[0]
            if row >= 0:
                item = lay.itemAt(row, QFormLayout.ItemRole.FieldRole)
                return is_type(item.widget() if item else None)
        node = node.parentWidget()

    node = widget.parentWidget()
    while node is not None:
        lay = _direct_layout(widget, node.layout())
        if lay is not None:
            if not (isinstance(lay, QBoxLayout)
                    and lay.direction() == QBoxLayout.Direction.LeftToRight):
                return False
            return any(is_type(lay.itemAt(i).widget())
                       for i in range(lay.count()) if lay.itemAt(i))
        node = node.parentWidget()
    return False


def _direct_layout(widget, lay):
    """返回直接包含该控件的布局（会递归进嵌套布局，行布局常是子项）。"""
    if lay is None:
        return None
    if lay.indexOf(widget) != -1:
        return lay
    for i in range(lay.count()):
        item = lay.itemAt(i)
        sub = item.layout() if item else None
        if sub is not None:
            found = _direct_layout(widget, sub)
            if found is not None:
                return found
    return None


def _type_resolution(widget, type_word: str, mode: str) -> bool:
    """组名/标签 + 类型词是否可放行。

    容器级默认可过；但**同容器同类候选 > 2 且分句动词是 ACTION** 时，
    必须过 ``_row_pair_hit`` 的行级配对——否则「频率」可以靠同组里另一个
    「频率」标签旁边的输入框蒙过。

    标签（QLabel）另有一条更严的约束：标签天然属于某一行，ACTION 句里
    只认同行的类型控件，不再看容器候选数——否则拆掉同行控件后，剩 2 个
    候选就会滑回容器级判定。
    """
    from PySide6.QtWidgets import QLabel

    if not _type_hit(widget, type_word):
        return False
    if mode != "ACTION":
        return True
    if isinstance(widget, QLabel):
        return _row_pair_hit(widget, type_word)
    if _type_count(widget, type_word) <= 2:
        return True
    return _row_pair_hit(widget, type_word)


def _in_scope(widget, attr, window) -> bool:
    """控件是否落在分句点名的那只容器里。"""
    from PySide6.QtWidgets import QGroupBox, QWidget

    if not isinstance(widget, QWidget):
        return False
    node = widget if isinstance(widget, QGroupBox) else widget.parentWidget()
    if attr is None:  # QGroupBox[参数]
        while node is not None:
            if isinstance(node, QGroupBox) and node.title() == "参数":
                return True
            node = node.parentWidget()
        return False
    panel = getattr(window, attr, None)
    if panel is None:
        return False
    return widget is panel or panel.isAncestorOf(widget)


def _scope_words(clause: str) -> list:
    return [word for word in _PANEL_SCOPES if word in clause]


def _scoped(entries, words, window):
    attrs = [_PANEL_SCOPES[w] for w in words]
    return [e for e in entries if any(_in_scope(e[0], a, window) for a in attrs)]


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
                words = _scope_words(clause)
                if words:
                    entries = _scoped(entries, words, window)
                    if not entries:
                        mark(seg)   # 分句点名的容器里没有这段文字
                        continue
                if any(_type_resolution(wdg, tw, mode) for wdg, _, _ in entries
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


def test_counterexample_wrong_panel_turns_red(main_window):
    """分句点名面板时，同名文字落在别的面板不算命中。"""
    assert _unresolved(
        main_window, [ts.NEXT_STEPS["sample_rate_not_supported"]]) == []
    bad = _unresolved(
        main_window, ["在「设备」页签把「采样率」下拉框降到 2.048 MS/s。"])
    assert bad == ["采样率"], bad


def test_counterexample_new_exception_turns_red(monkeypatch):
    monkeypatch.setitem(_DIALOG_ONLY, "量子面板", "desktop/x.py")
    assert not _exceptions_ok()


def test_counterexample_label_and_control_on_different_rows_turns_red(qtapp):
    """同类候选 >2 且 ACTION 时，标签与控件不在同一行必须红。"""
    from PySide6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel

    box = QGroupBox("采样率")
    form = QFormLayout(box)
    label = QLabel("采样率")
    form.addRow(label, QLabel("说明"))          # 标签同行没有下拉
    for i in range(3):
        form.addRow(f"档位{i}", QComboBox(box))  # 同容器里 3 个下拉

    assert _type_count(label, "下拉框") == 3
    assert not _row_pair_hit(label, "下拉框")
    assert not _type_resolution(label, "下拉框", "ACTION")
    assert _type_resolution(label, "下拉框", "VIEW")  # 只有 ACTION 才要求行级

    paired = QGroupBox("采样率")
    form2 = QFormLayout(paired)
    label2 = QLabel("采样率")
    form2.addRow(label2, QComboBox(paired))      # 标签与下拉同一行
    form2.addRow("档位", QComboBox(paired))
    form2.addRow("档位2", QComboBox(paired))
    assert _row_pair_hit(label2, "下拉框")
    assert _type_resolution(label2, "下拉框", "ACTION")


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
