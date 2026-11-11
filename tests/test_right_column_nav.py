# SPDX-License-Identifier: MIT
"""右栏 20 节跳转落点 + 「跳到分组」菜单覆盖（roadmap W50 / P1-01+P1-02）。

判据都对着真 MainWindow 量，不看「调用了某个函数」：

1. **逐节落点**：对 20 节逐个 ``setCurrentIndex(i)``（菜单 / ``_show_right_tab_of``
   / 浮动面板归位走的都是它），目标节必须展开、标题**完整落在视口内**且顶在
   视口顶（``y <= 2``）；唯一例外是滚动条已到上限（贴底），此时物理上无法再
   往上滚，只要求标题完整可见。改前 ``ensureWidgetVisible(s, 0, 8)`` 的两种
   失真都会被这里抓到：已露一条边时一帧不动（y=-281，标题被切在视口上方）、
   目标比视口高时把标题压到视口底边（y=529/562）。
2. **菜单条数与目标一致**：现有菜单项触发后，落点节的标题必须等于菜单项
   文本（不允许「点了 A 跳到 B」）。
3. **菜单覆盖 20/20**：这条目前是 **xfail(strict)**——W31 未落库，菜单只有
   后 13 节，前 7 节（控制/状态/AI 智能体/卫星跟踪/模块/气象云图/新时空）
   是 ``right_nav`` 创建之前注册的，没有对应菜单项。W31 落地后本用例会
   XPASS→红，强制把 xfail 拆掉，别让缺口静默留着。

反证（实测，见 W50 回报）：把 ``RightColumn._reveal_section`` 换回旧实现
``self.ensureWidgetVisible(s, 0, 8)``，本文件在「控制」及后段多节上红。

运行:
    QT_QPA_PLATFORM=offscreen MBDSDR_NULL_AUDIO=1 MBDSDR_NO_HW=1 \\
        python -m pytest tests/test_right_column_nav.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QPoint, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_SECTION_COUNT = 20
_TOLERANCE_PX = 2          # 标题顶与视口顶的最大偏差（取整/边框余量）


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(app, ms=150):
    QTimer.singleShot(ms, app.quit)
    app.exec()


@pytest.fixture(scope="module")
def main_window(qapp, isolated_profile):
    """真 MainWindow（同 test_interaction_wiring.py 的起法，但不连真硬件）。"""
    from main_window import MainWindow

    win = MainWindow()
    for timer in getattr(win, "_startup_timers", []):
        try:
            timer.stop()
        except (AttributeError, RuntimeError):
            pass
    win._panels_set_sdr_connected(False)
    win.resize(1366, 768)
    win.show()
    _pump(qapp, 200)
    yield win
    try:
        win._disconnect()
    except (AttributeError, RuntimeError):
        pass
    win.close()
    win.deleteLater()
    _pump(qapp, 60)


def _nav_actions(win):
    return [a for a in win.right_nav.menu().actions() if not a.isSeparator()]


def _landing_problem(col, index):
    """落点是否合格；合格返回 None，否则返回人话原因。"""
    sec = col._sections[index]
    bar = col.verticalScrollBar()
    vp_h = col.viewport().height()
    y = sec.header.mapTo(col.viewport(), QPoint(0, 0)).y()
    head_h = sec.header.height()
    at_bottom = bar.value() >= bar.maximum()

    if col.currentIndex() != index:
        return f"currentIndex={col.currentIndex()}，跳转未生效"
    if not sec.is_expanded():
        return "目标节没有展开"
    if y < 0:
        return (f"标题被切在视口上方：y={y}（滚动 {bar.value()}/"
                f"{bar.maximum()}）")
    if y + head_h > vp_h:
        return f"标题不完整：y={y} 高={head_h} 视口={vp_h}"
    if y > _TOLERANCE_PX and not at_bottom:
        return (f"标题没贴视口顶：y={y}，滚动 {bar.value()}/{bar.maximum()}"
                f"（未到上限，还能往上滚）")
    return None


def test_each_section_jump_lands_at_viewport_top(main_window, qapp):
    """20 节逐个跳转：该节、展开、标题完整可见且贴顶（P1-01）。"""
    col = main_window.right_tab
    assert col.count() == _SECTION_COUNT, (
        f"右栏节数不是 20 而是 {col.count()}")
    assert col.viewport().height() > 100

    failures = []
    for i in range(_SECTION_COUNT):
        name = col.tabText(i)
        col.setCurrentIndex(i)
        _pump(qapp, 150)
        problem = _landing_problem(col, i)
        if problem:
            failures.append(f"[{i}]「{name}」{problem}")

    assert not failures, "逐节跳转不合格：\n" + "\n".join(failures)


def test_menu_actions_land_on_the_section_they_name(main_window, qapp):
    """菜单项 ↔ 落点一致：点「设置」必须落在「设置」节且贴顶，不许跳错。"""
    win = main_window
    col = win.right_tab
    acts = _nav_actions(win)
    assert acts, "「跳到分组」菜单是空的"

    titles = [col.tabText(i) for i in range(col.count())]
    failures = []
    for act in acts:
        act.trigger()
        _pump(qapp, 150)
        idx = col.currentIndex()
        landed = titles[idx] if 0 <= idx < len(titles) else "<越界>"
        if landed != act.text():
            failures.append(f"「{act.text()}」跳到了「{landed}」(index={idx})")
            continue
        problem = _landing_problem(col, idx)
        if problem:
            failures.append(f"「{act.text()}」{problem}")

    assert not failures, "菜单跳转不合格：\n" + "\n".join(failures)


@pytest.mark.xfail(strict=True, reason=(
    "W31 未落库：「跳到分组」菜单只有后 13 节；前 7 节（控制/状态/AI 智能体/"
    "卫星跟踪/模块/气象云图/新时空）在 right_nav 创建之前注册，没有菜单项。"
    "W31 落地后本用例 XPASS→红，届时删掉 xfail。"))
def test_nav_menu_covers_all_sections(main_window):
    """菜单集合 20/20：条数、顺序、文本都与右栏节一致（P1-02）。"""
    col = main_window.right_tab
    titles = [col.tabText(i) for i in range(col.count())]
    acts = _nav_actions(main_window)

    assert len(acts) == _SECTION_COUNT, (
        f"「跳到分组」菜单缺项：{len(acts)}/20，现有={[a.text() for a in acts]}")
    assert [a.text() for a in acts] == titles, (
        "菜单顺序/文本与右栏节不一致：\n菜单="
        f"{[a.text() for a in acts]}\n节={titles}")
