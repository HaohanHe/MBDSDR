# SPDX-License-Identifier: MIT
"""补齐 Stellarium 那两件「标配」的契约（2026-11-09）。

对照 ``reference/stellarium/src/gui`` 盘点后，我们缺的是**入口**而不是
能力：

* ``SearchDialog``：搜索链早就全通（卫星/恒星/行星/深空 → 居中出信息
  卡），但沉浸态把输入框藏了，只能靠跟模型说话；
* ``DateTimeDialog``：只有「⏱ 流速」和「今夜」，用户没法跳到某一天。

本文件钉住这四条行为，防止下次改版又把入口收回去：
1. 星象仪里能唤出搜索，Esc 先收搜索、不退出形态；
2. 图层面板开合与 ``layer_state()`` 同源，11 项一个不少；
3. 时间机器的「与现在的差」按真实时间算、随输入实时更新；
4. 「今夜」走注入的求解器，不写死钟点。
"""
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def win(isolated_profile, qapp):
    # 共用窗口：每个用例新建 MainWindow 会累积 Qt 对象/定时器挂起
    # （2026-11-08 日志），本目录的窗口测试一律 module scope。
    from PySide6.QtCore import Qt
    from main_window import MainWindow
    w = MainWindow()
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1280, 800)
    w.show()
    _pump(20)
    yield w
    try:
        w.set_app_mode("ide")
    except Exception:
        pass
    w.close()
    from mbdsdr_ai.new_spacetime import get_time_engine
    eng = get_time_engine()
    eng.set_time_now()
    eng.set_rate(1.0)


def _pump(n=12):
    from PySide6.QtWidgets import QApplication
    for _ in range(n):
        QApplication.processEvents()


# ---------------------------------------------------------------------- #
# 时间机器（对照 Stellarium DateTimeDialog）
# ---------------------------------------------------------------------- #
def test_format_offset_wording():
    from time_machine import format_offset
    assert format_offset(0.0) == "与现在同步"
    assert format_offset(30.0) == "与现在同步"
    assert format_offset(3600.0).startswith("+1 小时")
    assert format_offset(-7200.0).startswith("−2 小时")
    got = format_offset(2 * 86400 + 3600)
    assert "2 天" in got and "1 小时" in got
    # 差值是"与现在的差"，符号必须能区分过去/未来，不能一律显示正数
    assert format_offset(60.0).startswith("+")
    assert format_offset(-60.0).startswith("−")


def test_time_machine_reports_real_delta(qapp):
    from time_machine import TimeMachineDialog
    t0 = 1_800_000_000.0
    dlg = TimeMachineDialog(None, now_unix=t0 - 7200, real_unix=t0)
    try:
        _pump(3)
        assert "−2 小时" in dlg.status.text()
        # 回到现在必须归零，而不是继续显示旧差值
        dlg._go_now()
        assert "现在" in dlg.status.text()
    finally:
        dlg.close()


def test_time_machine_shift_updates_status_without_apply(qapp):
    """点「+1 天」必须立刻有反馈（首版漏了刷新，界面像没反应）。"""
    from time_machine import TimeMachineDialog
    t0 = 1_800_000_000.0
    dlg = TimeMachineDialog(None, now_unix=t0, real_unix=t0)
    try:
        _pump(3)
        before = dlg.status.text()
        dlg._shift(86400)
        _pump(3)
        after = dlg.status.text()
        assert before != after
        assert "1 天" in after
    finally:
        dlg.close()


def test_time_machine_night_uses_injected_resolver(qapp):
    from time_machine import TimeMachineDialog
    t0 = 1_800_000_000.0
    fixed = datetime(2026, 11, 9, 18, 30, tzinfo=timezone.utc)
    dlg = TimeMachineDialog(None, now_unix=t0, real_unix=t0,
                            night_resolver=lambda: (fixed, -18.0))
    try:
        _pump(3)
        dlg._go_night()
        got = dlg.edit.dateTime().toPython()
        want = fixed.astimezone().replace(tzinfo=None)
        assert abs((got - want).total_seconds()) < 2
        assert "-18" in dlg.status.text()
    finally:
        dlg.close()


def test_time_machine_night_without_event_says_so(qapp):
    """极昼/极夜没有暮光终：明说，不硬凑一个时刻。"""
    from time_machine import TimeMachineDialog
    t0 = 1_800_000_000.0
    dlg = TimeMachineDialog(None, now_unix=t0, real_unix=t0,
                            night_resolver=lambda: None)
    try:
        _pump(3)
        before = dlg.edit.dateTime().toPython()
        dlg._go_night()
        assert dlg.edit.dateTime().toPython() == before
        assert "不猜" in dlg.status.text()
    finally:
        dlg.close()


def test_time_machine_apply_hands_unix_to_callback(qapp):
    from time_machine import TimeMachineDialog
    t0 = 1_800_000_000.0
    seen = []
    dlg = TimeMachineDialog(None, now_unix=t0, real_unix=t0,
                            on_apply=seen.append)
    try:
        _pump(3)
        dlg._shift(3600)
        want = dlg.selected_unix()
        dlg._apply()
        assert seen and abs(seen[0] - want) < 1.0
    finally:
        dlg.close()


# ---------------------------------------------------------------------- #
# 星象仪里的搜索与图层面板
# ---------------------------------------------------------------------- #
def test_search_shortcut_is_registered(win):
    from shortcuts import BY_ID
    s = BY_ID["planetarium.search"]
    assert s.key == "Ctrl+F" and s.scope == "planetarium"
    assert getattr(win, "_planetarium_search_key", None) is not None


def test_immersive_search_reveals_at_bottom_and_esc_hides(win):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    win.set_app_mode("planetarium")
    _pump(25)
    sv = win.sky_view
    try:
        assert sv.search_visible() is False, "沉浸态默认收起搜索框"
        assert sv.reveal_search() is True
        _pump(5)
        assert sv.search_visible() is True
        # 星象仪顶部被 AI 胶囊占着，搜索框必须落在下半屏
        geo = sv._search_edit.geometry()
        assert geo.y() > sv.height() / 2, f"搜索框不该回到顶部: {geo}"

        # Esc 只收搜索，不该顺手把形态也退了
        sv.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape,
                                   Qt.NoModifier))
        _pump(5)
        assert sv.search_visible() is False
        assert win._app_mode == "planetarium"
    finally:
        win.set_app_mode("ide")
        _pump(10)


def test_layer_popover_toggle_mirrors_layer_state(win):
    win.set_app_mode("planetarium")
    _pump(25)
    try:
        pop = win._layer_popover
        btn = win._planetarium_layer_btn
        assert pop.isVisible() is False
        assert len(win._layer_btns) == len(win.sky_view.LAYER_KEYS) == 11

        btn.setChecked(True)
        _pump(10)
        assert pop.isVisible() is True
        # 面板必须留在天空视图内（跑出窗口就等于点不到）
        geo = pop.geometry()
        assert geo.left() >= 0 and geo.top() >= 0
        assert geo.right() <= win.sky_view.width()
        assert geo.bottom() <= win.sky_view.height()

        # 开关必须落到同一个事实源：点按钮改视图，视图回读一致
        target = "eqgrid"
        assert win.sky_view.layer_state()[target] is False
        win._layer_btns[target].click()
        _pump(5)
        assert win.sky_view.layer_state()[target] is True
        assert win._layer_btns[target].isChecked() is True
        win._layer_btns[target].click()          # 还原

        btn.setChecked(False)
        _pump(5)
        assert pop.isVisible() is False
    finally:
        win.set_app_mode("ide")
        _pump(10)
