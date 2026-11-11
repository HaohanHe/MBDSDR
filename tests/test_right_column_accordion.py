#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
右栏手风琴互斥 + 跨会话记忆 + 读屏信息（W48 / D-10 用户裁定）
==============================================================

用户 2026-11-11 裁定：**默认单展开**（同一时刻最多一节展开），保留
「全部折叠 / 全部展开」两个显式动作，不做 Ctrl 叠加这类隐藏交互，展开
状态跨会话记住上次那一节，且读屏信息（accessibleName/Description +
checked）必须与展开态一致。折叠状态**入库**（desktop_settings.json，
键登记在 config_schema.py），不用 QSettings 另开一套。

判据（全部是状态/数值断言）：

1. 展开第二节时第一节自动收起——全列**只有一节**展开；
2. `setTabText()`（切语言/改标题）不许被当成「用户展开」把别节收起；
3. 关掉重建（同一条 desktop_settings.json）后仍是上次那一节；
4. 设置里是未知/空白节 id 时只展开首节（不静默全展开、不静默全折叠）；
5. `accessibleName` 跟随标题、`accessibleDescription` 与 `checked` 跟随
   展开态；
6. 「全部展开」真的多开（显式动作允许），「全部折叠」后 0 节展开且
   滚动上限随之变小；
7. 不再写 QSettings（`right_column/expanded_section` 在 QSettings 存储里
   不存在）；
8. 同一进程里两个 `RightColumn` 互不影响（展开态是实例自己的，不是
   全局单例）。

改前反证（`git show HEAD:desktop/right_column.py` 单独放到临时目录跑同一
场景，实测）：判据 3 失败（旧实现写 QSettings，DesktopSettings 里读不到
该键）；判据 5 失败（旧 header 的 accessibleName/Description 均为空）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_right_column_accordion.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_VIEW_W = 320
_VIEW_H = 300
_SECTION_COUNT = 8
_BODY_H = 400
_KEY = "right_column_expanded_section"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(app, ms=80):
    QTimer.singleShot(ms, app.quit)
    app.exec()


def _settings():
    from settings import DesktopSettings
    return DesktopSettings.load()


def _build_column(qapp, settings=None):
    from right_column import RightColumn
    col = RightColumn(settings=settings)
    col.resize(_VIEW_W, _VIEW_H)
    for i in range(_SECTION_COUNT):
        body = QWidget()
        body.setFixedHeight(_BODY_H)
        col.addTab(body, f"节{i}")
    col.show()
    _pump(qapp, 120)
    return col


def _expanded(col):
    return [i for i, section in enumerate(col._sections)
            if section.is_expanded()]


def test_expanding_another_section_collapses_previous(qapp, isolated_profile):
    """展开第 4 节：第 1 节自动收起，全列只剩一节展开。"""
    col = _build_column(qapp)
    try:
        assert _expanded(col) == [0], "前置条件不成立：默认应只展开首节"

        col._sections[3].header.click()
        _pump(qapp, 120)

        assert _expanded(col) == [3], (
            f"手风琴没生效，同时展开 {_expanded(col)}（改前是 [0, 3]）")

        # 切语言/改标题只重刷标题文本，不能被当成「用户展开了这一节」。
        col.setTabText(0, "控制")
        _pump(qapp, 40)
        assert _expanded(col) == [3], (
            f"setTabText 把手风琴带跑了：展开 {_expanded(col)}")
    finally:
        col.close()


def test_last_expanded_section_restored_after_restart(qapp, isolated_profile):
    """关掉重建（同一 desktop_settings.json）：仍是上次展开的那一节。"""
    s1 = _settings()
    col = _build_column(qapp, s1)
    try:
        col._sections[5].header.click()
        _pump(qapp, 120)
        assert _expanded(col) == [5]
        # 入库的是稳定 id，不是下标。
        assert s1.get(_KEY) == "节5", (
            f"折叠状态没写进 DesktopSettings：{s1.get(_KEY)!r}（改前写的是"
            " QSettings，这个键恒为空）")
        s1.flush()
    finally:
        col.close()
        col.deleteLater()

    s2 = _settings()
    col2 = _build_column(qapp, s2)
    try:
        assert _expanded(col2) == [5], (
            f"重启后没记住上次那一节，展开的是 {_expanded(col2)}（改前是 [0]）")
    finally:
        col2.close()


@pytest.mark.parametrize("bad", ["已删除的面板", "   ", "0"])
def test_bad_remembered_section_falls_back_to_first(qapp, isolated_profile, bad):
    """未知/空白/下标式旧值：只展开首节（不静默全展开、不静默全折叠）。"""
    s = _settings()
    s.set(_KEY, bad)
    s.flush()

    col = _build_column(qapp, s)
    try:
        assert _expanded(col) == [0], (
            f"坏值 {bad!r} 没有回落为只展开首节，实际 {_expanded(col)}")
    finally:
        col.close()


def test_header_accessibility_follows_expanded_state(qapp, isolated_profile):
    """accessibleName 跟随标题，Description 与 checked 跟随展开态。"""
    col = _build_column(qapp)
    try:
        first = col._sections[0].header
        third = col._sections[3].header
        assert first.isChecked() and not third.isChecked()
        assert first.accessibleName() == "节0"
        assert "已展开" in first.accessibleDescription()
        assert third.accessibleName() == "节3"
        assert "已收起" in third.accessibleDescription(), (
            f"读屏描述没跟上折叠态：{third.accessibleDescription()!r}"
            "（改前两个属性都是空串）")

        third.click()
        _pump(qapp, 120)
        assert third.isChecked() and not first.isChecked()
        assert "已展开" in third.accessibleDescription()
        assert "已收起" in first.accessibleDescription()

        col.setTabText(3, "新时空")
        assert third.accessibleName() == "新时空", (
            "setTabText 后读屏名称仍是旧标题")
    finally:
        col.close()


def test_expand_all_then_collapse_all_shrinks_scroll_range(qapp, isolated_profile):
    """两个显式动作：全部展开允许多开；全部折叠后 0 节展开且上限变小。"""
    col = _build_column(qapp)
    try:
        bar = col.verticalScrollBar()
        one = bar.maximum()

        col.expand_all(True)
        _pump(qapp, 120)
        assert _expanded(col) == list(range(_SECTION_COUNT)), (
            f"「全部展开」没有真的多开：{_expanded(col)}")
        many = bar.maximum()
        assert many > one, f"全部展开后滚动上限没涨：{one} -> {many}"

        col.expand_all(False)
        _pump(qapp, 120)
        assert _expanded(col) == [], f"「全部折叠」后仍有节展开：{_expanded(col)}"
        assert bar.maximum() < many, (
            f"全部折叠后滚动上限没变小：{many} -> {bar.maximum()}")
    finally:
        col.close()


def test_qsettings_store_is_not_written(qapp, isolated_profile):
    """点过标题后，QSettings 里不该再出现 right_column/expanded_section。

    改前实现用 QSettings 存展开节——PySide6 6.12 上两参构造器还会无视
    ``setDefaultFormat`` 恒定写注册表。现在只走 DesktopSettings，这里既查
    模块里已无 QSettings 记忆函数，也查隔离出来的 QSettings 存储确实没键。
    """
    from PySide6.QtCore import QSettings
    import right_column as rc

    assert not hasattr(rc, "_remembered_settings"), (
        "right_column 仍有 QSettings 记忆函数")
    assert not hasattr(rc, "QSettings"), (
        "right_column 仍导入/引用 QSettings 符号")

    col = _build_column(qapp, _settings())
    try:
        col._sections[4].header.click()
        _pump(qapp, 120)
    finally:
        col.close()

    qs = QSettings(QSettings.IniFormat, QSettings.UserScope,
                   "MBDSDR", "Desktop")
    assert "right_column/expanded_section" not in qs.allKeys(), (
        f"展开态仍被写进 QSettings：{qs.allKeys()}")


def test_two_columns_in_one_process_are_independent(qapp, isolated_profile):
    """两个 RightColumn 同进程共存：展开态、expand_all 都不得串台。

    展开态是每列自己的状态；若哪天被做成全局单例（例如共用一个类变量），
    这条会直接红——防止「同一时刻只展开一节」被误实现成「全应用只展开一节」。
    """
    a = _build_column(qapp)
    b = _build_column(qapp)
    try:
        a._sections[2].header.click()
        _pump(qapp, 120)
        assert _expanded(a) == [2]
        assert _expanded(b) == [0], (
            f"点 A 的标题把 B 带跑了：B 展开 {_expanded(b)}")

        b._sections[6].header.click()
        _pump(qapp, 120)
        assert _expanded(b) == [6]
        assert _expanded(a) == [2], (
            f"点 B 的标题把 A 带跑了：A 展开 {_expanded(a)}")

        a.expand_all(True)
        _pump(qapp, 120)
        assert _expanded(a) == list(range(_SECTION_COUNT))
        assert _expanded(b) == [6], (
            f"A 的「全部展开」泄漏到 B：B 展开 {_expanded(b)}")
    finally:
        a.close()
        b.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
