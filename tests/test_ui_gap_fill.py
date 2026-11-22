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
def qapp(isolated_profile):
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


def test_time_machine_night_receipt_survives_refresh(qapp):
    """A3：极昼/极夜的「不猜时刻」是终态，刷新不得改写成模拟差。"""
    from time_machine import TimeMachineDialog
    t0 = 1_800_000_000.0
    dlg = TimeMachineDialog(None, now_unix=t0, real_unix=t0,
                            night_resolver=lambda: None)
    try:
        _pump(3)
        dlg._go_night()
        assert "不猜" in dlg.status.text()
        dlg._refresh_status()  # 排队/主动刷新
        _pump(3)
        assert "不猜" in dlg.status.text()
        assert "模拟中" not in dlg.status.text()
    finally:
        dlg.close()


def test_time_machine_now_receipt_survives_refresh(qapp):
    """A3（成功侧）：回到现在的回执同样要留住。"""
    from time_machine import TimeMachineDialog
    t0 = 1_800_000_000.0
    dlg = TimeMachineDialog(None, now_unix=t0 - 7200, real_unix=t0)
    try:
        _pump(3)
        dlg._go_now()
        receipt = dlg.status.text()
        assert "回到现在" in receipt
        dlg._refresh_status()
        assert dlg.status.text() == receipt
    finally:
        dlg.close()


def test_time_machine_apply_failure_receipt_survives_refresh(qapp):
    """A3：跳转失败是终态；用户重新选时刻才算新一轮，回执让位。"""
    from time_machine import TimeMachineDialog
    t0 = 1_800_000_000.0

    def boom(_ts):
        raise RuntimeError("引擎不可用")

    dlg = TimeMachineDialog(None, now_unix=t0, real_unix=t0, on_apply=boom)
    try:
        _pump(3)
        dlg._shift(3600)
        dlg._apply()
        assert "跳转失败" in dlg.status.text()
        dlg._refresh_status()
        assert "跳转失败" in dlg.status.text()
        # 用户重新选时刻 = 新一轮，回执让位给实时差
        dlg._shift(3600)
        _pump(3)
        assert "跳转失败" not in dlg.status.text()
    finally:
        dlg.close()


def test_time_machine_timezone_conversion_uses_qt(qapp):
    """带时区的 QDateTime 只能按 Qt 口径换算成 Unix 秒（F6）。

    反证：把 ``unix_from_qdatetime`` 改回 ``toPython().timestamp()``，本机
    (+08:00) 会把 2026-11-01 01:30 America/New_York 算成 1793467800，
    与 Qt 的 1793511000 差 43200 s，本用例必红。
    """
    from PySide6.QtCore import QDate, QDateTime, QTime, QTimeZone

    from time_machine import qdatetime_from_unix, unix_from_qdatetime

    ny = QDateTime(QDate(2026, 11, 1), QTime(1, 30, 0),
                   QTimeZone(b"America/New_York"))
    assert unix_from_qdatetime(ny) == 1793511000

    # 本地时区往返：进出一致，跨日边界同样成立。
    for unix in (1793511000, 1767225600, 1798761600):
        assert unix_from_qdatetime(qdatetime_from_unix(unix)) == unix


def test_time_machine_go_now_uses_current_wall_clock(qapp):
    """「回到现在」取点按那一刻，不是构造时冻结的时刻（F8）。

    反证：改回 ``self._now_unix = self._real_unix``，selected_unix 停在 t0，
    与 t0+300 差 300 s，本用例必红。
    """
    from time_machine import TimeMachineDialog

    t0 = 1_800_000_000.0
    clock = {"t": t0}
    dlg = TimeMachineDialog(None, now_unix=t0 - 7200, real_unix=t0,
                            real_now_provider=lambda: clock["t"])
    try:
        _pump(3)
        clock["t"] = t0 + 300.0
        dlg._go_now()
        assert dlg.selected_unix() == t0 + 300.0
        assert "已回到现在" in dlg.status.text()
        # 只改输入框、没改引擎：不得声称「实时」，必须写明还要点跳过去。
        assert "实时" not in dlg.status.text()
        assert "点「跳过去」生效" in dlg.status.text()

        dlg._go_now()   # 已在现在：幂等回执
        assert "已在现在" in dlg.status.text()
    finally:
        dlg.close()


def _click(button):
    """Exercise the actual Qt signal path, not the private handler."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(button, Qt.LeftButton)


@pytest.mark.parametrize("zone_spec, local_date, event_utc, utc_dates", [
    (14 * 3600, (2031, 4, 12), (2031, 4, 11, 23, 0), ((2031, 4, 11), (2031, 4, 12))),
    (-10 * 3600, (2031, 4, 12), (2031, 4, 13, 5, 0), ((2031, 4, 12), (2031, 4, 13))),
    (b"America/New_York", (2026, 3, 8), (2026, 3, 9, 3, 30),
     ((2026, 3, 8), (2026, 3, 9))),
    (b"America/New_York", (2026, 11, 1), (2026, 11, 2, 4, 30),
     ((2026, 11, 1), (2026, 11, 2))),
])
def test_selected_night_uses_local_date_and_covering_utc_days(
        qapp, monkeypatch, zone_spec, local_date, event_utc, utc_dates):
    from types import SimpleNamespace
    from PySide6.QtCore import QDate, QDateTime, QTime, QTimeZone
    from mbdsdr_ai import atmosphere
    from main_window import _night_observation_time
    from time_machine import TimeMachineDialog, unix_from_qdatetime

    # Neutral 0,0 station fixture, not a real observing location. Qt timezone
    # belongs to the input, never inferred from station longitude.
    zone = QTimeZone(zone_spec)
    assert zone.isValid()
    selected = QDateTime(QDate(*local_date), QTime(12, 0), zone)
    event = datetime(*event_utc, tzinfo=timezone.utc)
    days = []

    def twilight(day, station):
        assert (station.lat_deg, station.lon_deg) == (0.0, 0.0)
        days.append(day)
        return SimpleNamespace(
            astronomical_twilight_end=event if day == event.date() else None,
            civil_twilight_end=None)

    monkeypatch.setattr(atmosphere, "compute_twilight_times", twilight)
    applied = []
    dlg = TimeMachineDialog(
        now_unix=unix_from_qdatetime(selected), on_apply=applied.append,
        night_resolver_for_date=lambda qdt: _night_observation_time(0, 0, qdt))
    try:
        dlg.edit.setTimeZone(zone)
        dlg.edit.setDateTime(selected)
        dlg.show()
        _click(dlg.night_btn)
        assert dlg.selected_unix() == event.timestamp()
        assert dlg.edit.dateTime().date() == selected.date()
        assert days == [datetime(*d).date() for d in utc_dates]
        assert dlg.night_btn.text() == "所选日入夜"
        assert "天文暮光终" in dlg.status.text()
        assert applied == [], "Selecting twilight only fills the input"
    finally:
        dlg.close()


@pytest.mark.parametrize("scenario", ["civil", "prefer_astro", "next_local_day", "no_event"])
def test_selected_night_fallback_and_no_cross_day(qapp, monkeypatch, scenario):
    from types import SimpleNamespace
    from PySide6.QtCore import QDate, QDateTime, QTime, QTimeZone
    from mbdsdr_ai import atmosphere
    from main_window import _night_observation_time
    from time_machine import TimeMachineDialog, unix_from_qdatetime

    zone = QTimeZone(14 * 3600)
    selected = QDateTime(QDate(2031, 4, 12), QTime(12, 0), zone)
    civil = datetime(2031, 4, 11, 23, 30, tzinfo=timezone.utc)
    astro = datetime(2031, 4, 12, 0, 30, tzinfo=timezone.utc)
    tomorrow = datetime(2031, 4, 12, 23, 30, tzinfo=timezone.utc)
    seen = []

    def twilight(day, _station):
        seen.append(day)
        ast = astro if scenario == "prefer_astro" else None
        civ = civil if scenario in ("civil", "prefer_astro") else None
        if scenario == "next_local_day":
            ast = civ = tomorrow  # UTC candidate, but NOT the selected local day
        return SimpleNamespace(
            astronomical_twilight_end=ast if ast and ast.date() == day else None,
            civil_twilight_end=civ if civ and civ.date() == day else None)

    monkeypatch.setattr(atmosphere, "compute_twilight_times", twilight)
    applied = []
    dlg = TimeMachineDialog(
        now_unix=unix_from_qdatetime(selected), on_apply=applied.append,
        night_resolver_for_date=lambda qdt: _night_observation_time(0, 0, qdt))
    try:
        dlg.edit.setTimeZone(zone)
        dlg.edit.setDateTime(selected)
        dlg.show()
        before = dlg.selected_unix()
        _click(dlg.night_btn)
        assert seen == [civil.date(), astro.date()]
        if scenario == "civil":
            assert dlg.selected_unix() == civil.timestamp()
            assert "民用暮光终，天未全黑" in dlg.status.text()
            assert "天文暮光终" not in dlg.status.text()
        elif scenario == "prefer_astro":
            assert dlg.selected_unix() == astro.timestamp()
            assert "天文暮光终" in dlg.status.text()
        else:
            assert dlg.selected_unix() == before
            assert "所选本地日" in dlg.status.text()
            assert "不猜" in dlg.status.text()
        assert applied == []
        assert dlg.isVisible()
    finally:
        dlg.close()


def test_night_without_station_is_disabled_and_explained(qapp):
    from time_machine import TimeMachineDialog
    dlg = TimeMachineDialog(now_unix=1_800_000_000.0)
    try:
        dlg.show()
        before = dlg.selected_unix()
        assert not dlg.night_btn.isEnabled()
        assert "站址" in dlg.night_btn.toolTip()
        _click(dlg.night_btn)
        assert dlg.selected_unix() == before
        assert "站址" in dlg.status.text()
    finally:
        dlg.close()


def test_night_resolver_typeerror_is_failure_not_signature_probe(qapp):
    from time_machine import TimeMachineDialog
    calls = []

    def broken(qdt):
        calls.append(qdt)
        raise TypeError("solver failed internally")

    dlg = TimeMachineDialog(night_resolver_for_date=broken)
    try:
        dlg.show()
        before = dlg.selected_unix()
        _click(dlg.night_btn)
        assert len(calls) == 1
        assert "计算失败" in dlg.status.text()
        assert dlg.selected_unix() == before
        assert dlg.isVisible()
    finally:
        dlg.close()


def test_now_callback_runs_on_click_after_delay_not_on_edit(qapp):
    from time_machine import TimeMachineDialog
    clock = {"t": 1_800_000_000.0}
    returned, applied = [], []
    dlg = TimeMachineDialog(
        now_unix=clock["t"] - 7200, real_unix=clock["t"],
        real_now_provider=lambda: clock["t"], on_apply=applied.append,
        on_now=lambda: returned.append(clock["t"]))
    try:
        dlg.show()
        _click(dlg._quick_buttons[-1])
        assert returned == applied == [], "Editing alone must not apply globally"
        clock["t"] += 600
        _click(dlg.now_btn)
        assert returned == [clock["t"]]
        assert applied == [], "Returning live must not apply a wall-clock snapshot"
        assert dlg.selected_unix() == clock["t"]
        assert "已回到现在" in dlg.status.text()
        assert "实时" in dlg.status.text()
        assert "点「跳过去」生效" not in dlg.status.text()
    finally:
        dlg.close()
    assert returned == [clock["t"]] and applied == []


@pytest.mark.parametrize("failure", ["raise", "false"])
def test_now_callback_failure_does_not_claim_success_or_close(qapp, failure):
    from time_machine import TimeMachineDialog
    calls = []

    def broken():
        calls.append(1)
        if failure == "raise":
            raise RuntimeError("engine unavailable")
        return False

    dlg = TimeMachineDialog(now_unix=1_800_000_000.0, on_now=broken)
    try:
        dlg.show()
        before = dlg.selected_unix()
        _click(dlg.now_btn)
        assert calls == [1]
        assert dlg.isVisible()
        assert dlg.selected_unix() == before
        assert "失败" in dlg.status.text()
        assert "已回到现在" not in dlg.status.text()
        dlg._refresh_status()
        assert "失败" in dlg.status.text()
    finally:
        dlg.close()


def test_legacy_now_button_only_fills_until_apply(qapp):
    from time_machine import TimeMachineDialog
    applied = []
    clock = {"t": 1_800_000_000.0}
    dlg = TimeMachineDialog(now_unix=clock["t"] - 7200, on_apply=applied.append,
                            real_now_provider=lambda: clock["t"])
    try:
        dlg.show()
        clock["t"] += 600
        _click(dlg.now_btn)
        assert dlg.selected_unix() == clock["t"]
        assert applied == []
        assert "点「跳过去」生效" in dlg.status.text()
        assert "实时" not in dlg.status.text()
        _click(dlg.ok_btn)
        assert applied == [clock["t"]]
    finally:
        dlg.close()


def _use_main_time_panel(win, action):
    """Open the modal panel by QTest; always unwind its event loop on failure."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from time_machine import TimeMachineDialog
    errors, visited = [], []

    def interact():
        visited.append(True)
        dlg = QApplication.activeModalWidget()
        try:
            assert isinstance(dlg, TimeMachineDialog)
            action(dlg)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if dlg is not None:
                dlg.reject()

    assert win._planetarium_time_jump_btn.isVisible()
    timer = QTimer(win)
    timer.setSingleShot(True)
    timer.timeout.connect(interact)
    timer.start(0)
    try:
        _click(win._planetarium_time_jump_btn)
    finally:
        timer.stop()
        timer.deleteLater()
    assert visited == [True], "The real modal panel must actually be exercised"
    if errors:
        raise errors[0]


@pytest.fixture
def astro_controls(win, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from mbdsdr_ai import new_spacetime, spacetime_features

    win.set_app_mode("planetarium")
    _pump(3)
    eng = new_spacetime.TimeEngine()
    bus = spacetime_features.TemporalBus()
    monkeypatch.setattr(new_spacetime, "_global_engine", eng)
    monkeypatch.setattr(spacetime_features, "_global_bus", bus)
    # Count GUI refresh boundaries without starting an ephemeris worker or I/O.
    refresh, update = Mock(), Mock(wraps=win.sky_view.update)
    monkeypatch.setattr(win, "sat_tracker", SimpleNamespace(refresh=refresh))
    monkeypatch.setattr(win.sky_view, "update", update)
    # Neutral station only; no persistent observer/config writes.
    monkeypatch.setattr(win.sky_view, "get_observer", lambda: {"lat": 0.0, "lon": 0.0})
    win._planetarium_link_timer.timeout.emit()
    yield SimpleNamespace(eng=eng, bus=bus, refresh=refresh, update=update)
    eng.set_time_now()
    eng.set_rate(1.0)
    win.set_app_mode("ide")


@pytest.mark.allow_modal
def test_main_now_button_returns_live_once_after_ten_minutes(win, astro_controls, monkeypatch):
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock
    from mbdsdr_ai import new_spacetime
    import time_machine

    c = astro_controls
    clock = {"t": 1_800_000_000.0}
    clock_api = SimpleNamespace(time=lambda: clock["t"], monotonic=time.monotonic)
    monkeypatch.setattr(new_spacetime, "time", clock_api)
    monkeypatch.setattr(time_machine, "time", clock_api)
    c.eng.set_unix(clock["t"] - 86400)
    c.eng.set_rate(600)
    live = Mock(wraps=c.eng.set_time_now)
    jump = Mock(wraps=c.eng.set_unix)
    monkeypatch.setattr(c.eng, "set_time_now", live)
    monkeypatch.setattr(c.eng, "set_unix", jump)
    contexts = []
    c.bus.subscribe(contexts.append)

    def interact(dlg):
        _click(dlg._quick_buttons[-1])
        assert not live.called and not jump.called
        clock["t"] += 600
        c.refresh.reset_mock()
        c.update.reset_mock()
        contexts.clear()
        _click(dlg.now_btn)
        live.assert_called_once_with()
        jump.assert_not_called()
        assert c.eng.is_following_system()
        assert c.eng.get_rate() == 1.0
        assert c.eng.now_unix() == pytest.approx(clock["t"], abs=0.001)
        c.refresh.assert_called_once_with()
        assert c.update.called
        assert len(contexts) == 1 and contexts[0].basis == "real"
        assert contexts[0].at_unix == pytest.approx(clock["t"], abs=0.001)
        assert "实时" in win._planetarium_time_btn.text()
        assert "已回到现在" in dlg.status.text()
        clock["t"] += 15  # Following the wall, not a snapshot plus rate=1.
        assert c.eng.now_unix() == pytest.approx(clock["t"], abs=0.001)

    _use_main_time_panel(win, interact)
    live.assert_called_once_with()
    jump.assert_not_called()


@pytest.mark.allow_modal
def test_main_time_panel_selected_day_and_top_tonight_are_distinct(win, astro_controls, monkeypatch):
    from PySide6.QtCore import QDate, QDateTime, QTime
    from time_machine import unix_from_qdatetime
    import main_window

    c = astro_controls
    c.eng.set_unix(1_800_000_000.0)
    c.eng.pause()  # Hold simulation steady while checking edit/close side effects.
    original = c.eng.now_unix()
    selected = QDateTime(QDate(2031, 4, 12), QTime(12, 0))
    seen = []

    def resolver(lat, lon, local_time=None):
        assert (lat, lon) == (0.0, 0.0)
        day = QDateTime.currentDateTime() if local_time is None else local_time
        seen.append(day.date())
        target = QDateTime(day.date(), QTime(20, 0), day.timeZone())
        return datetime.fromtimestamp(unix_from_qdatetime(target), timezone.utc), -18.0

    monkeypatch.setattr(main_window, "_night_observation_time", resolver)

    def interact(dlg):
        dlg.edit.setDateTime(selected)
        _click(dlg.night_btn)
        assert seen == [selected.date()]
        assert dlg.edit.date() == selected.date()
        assert dlg.edit.time().hour() == 20
        assert c.eng.now_unix() == original
        assert "本机本地" in dlg.night_btn.toolTip()

    _use_main_time_panel(win, interact)
    assert c.eng.now_unix() == original, "Closing without applying must leave engine alone"
    _click(win._planetarium_tonight_btn)
    assert seen == [selected.date(), QDateTime.currentDateTime().date()]
    assert "本机今天" in win._planetarium_tonight_btn.toolTip()
    assert "时间→回到现在" in win.statusBar().currentMessage()


@pytest.mark.allow_modal
def test_main_apply_near_now_is_still_simulation_and_cycle_is_compatible(win, astro_controls):
    from time_machine import qdatetime_from_unix
    c = astro_controls
    contexts = []
    c.bus.subscribe(contexts.append)

    def interact(dlg):
        # 以被测引擎的当前时刻为基准（不是裸墙钟），差 10 秒仍算「近现在」。
        dlg.edit.setDateTime(qdatetime_from_unix(c.eng.now_unix() + 10))
        c.refresh.reset_mock()
        contexts.clear()
        _click(dlg.ok_btn)
        assert not c.eng.is_following_system()
        assert c.eng.get_rate() == 1.0
        c.refresh.assert_called_once_with()
        assert contexts[-1].basis == "simulated"
        assert "模拟" in win._planetarium_time_btn.text()
        message = win.statusBar().currentMessage()
        assert "模拟" in message and "实时" not in message
        assert "同步" not in message and "时间→回到现在" in message
        assert "时间→回到现在" in win._planetarium_time_btn.toolTip()

    _use_main_time_panel(win, interact)
    for expected in (60.0, 600.0, 0.0, 1.0):
        _click(win._planetarium_time_btn)
        assert c.eng.get_rate() == expected
    assert c.eng.is_following_system()
    assert "实时" in win._planetarium_time_btn.text()


@pytest.mark.allow_modal
def test_main_now_engine_failure_keeps_panel_open(win, astro_controls, monkeypatch):
    c = astro_controls
    c.eng.set_unix(1_800_000_000.0)

    def fail():
        raise RuntimeError("engine unavailable")

    def interact(dlg):
        before = dlg.selected_unix()
        _click(dlg.now_btn)
        assert dlg.isVisible()
        assert dlg.selected_unix() == before
        assert "失败" in dlg.status.text() and "已回到现在" not in dlg.status.text()
        assert not c.eng.is_following_system()

    with monkeypatch.context() as patch:
        patch.setattr(c.eng, "set_time_now", fail)
        _use_main_time_panel(win, interact)


@pytest.mark.allow_modal
def test_main_panel_missing_station_disables_night(win, astro_controls, monkeypatch):
    monkeypatch.setattr(win.sky_view, "get_observer", lambda: None)

    def interact(dlg):
        assert not dlg.night_btn.isEnabled()
        assert "站址" in dlg.night_btn.toolTip()
        assert "站址" in dlg.status.text()

    _use_main_time_panel(win, interact)


@pytest.mark.allow_modal
@pytest.mark.parametrize("alt", [-6.0, None])
def test_main_twilight_receipts_and_empty_result_never_apply(win, astro_controls, monkeypatch, alt):
    from PySide6.QtCore import QDate, QDateTime, QTime
    from time_machine import unix_from_qdatetime
    import main_window

    c = astro_controls
    c.eng.set_unix(1_800_000_000.0)
    c.eng.pause()
    original = c.eng.now_unix()
    selected = QDateTime(QDate(2031, 4, 12), QTime(12, 0))
    event = datetime.fromtimestamp(unix_from_qdatetime(selected.addSecs(3600)), timezone.utc)
    monkeypatch.setattr(main_window, "_night_observation_time",
                        lambda lat, lon, local_time=None: (event, alt) if alt else None)

    def interact(dlg):
        dlg.edit.setDateTime(selected)
        _click(dlg.night_btn)
        assert c.eng.now_unix() == original
        if alt:
            assert "民用暮光终，天未全黑" in dlg.status.text()
        else:
            assert dlg.selected_unix() == unix_from_qdatetime(selected)
            assert "不猜" in dlg.status.text()

    _use_main_time_panel(win, interact)
    assert c.eng.now_unix() == original
    _click(win._planetarium_tonight_btn)
    if alt:
        assert "民用暮光终，天未全黑" in win.statusBar().currentMessage()
        assert not c.eng.is_following_system()
    else:
        assert c.eng.now_unix() == original
        assert "不猜" in win.statusBar().currentMessage()


@pytest.mark.allow_modal
@pytest.mark.parametrize("boundary", ["refresh", "bus", "sky"])
def test_main_now_partial_failure_is_reported_honestly(win, astro_controls, monkeypatch, boundary):
    c = astro_controls

    def fail(*_args):
        raise RuntimeError("refresh boundary failed")

    target, name = {"refresh": (win.sat_tracker, "refresh"),
                    "bus": (c.bus, "publish_from_engine"),
                    "sky": (win.sky_view, "update")}[boundary]

    def interact(dlg):
        with monkeypatch.context() as patch:
            patch.setattr(target, name, fail)
            _click(dlg.now_btn)
            assert dlg.isVisible()
            assert "失败" in dlg.status.text()
            assert "已回到现在" not in dlg.status.text()
            assert "时刻未改变" not in dlg.status.text(), "Engine may already be live"

    _use_main_time_panel(win, interact)


@pytest.mark.parametrize("offset", [14, -10])
def test_selected_night_real_solar_crossing(qapp, offset):
    """Real solver at the neutral 0,0 fixture, not synthetic observing data."""
    from PySide6.QtCore import QDate, QDateTime, QTime, QTimeZone
    from main_window import _night_observation_time
    from mbdsdr_ai.atmosphere import sun_altitude_deg
    from time_machine import TimeMachineDialog, unix_from_qdatetime

    zone = QTimeZone(offset * 3600)
    selected = QDateTime(QDate(2031, 4, 12), QTime(12, 0), zone)
    dlg = TimeMachineDialog(
        now_unix=unix_from_qdatetime(selected),
        night_resolver_for_date=lambda qdt: _night_observation_time(0, 0, qdt))
    try:
        dlg.edit.setTimeZone(zone)
        dlg.edit.setDateTime(selected)
        dlg.show()
        _click(dlg.night_btn)
        assert dlg.edit.date() == selected.date()
        assert "天文暮光终" in dlg.status.text()
        jd = dlg.selected_unix() / 86400.0 + 2440587.5
        assert abs(sun_altitude_deg(jd, 0, 0) + 18) < 0.1
        assert sun_altitude_deg(jd - 60 / 86400, 0, 0) > -18
        assert sun_altitude_deg(jd + 60 / 86400, 0, 0) < -18
    finally:
        dlg.close()


def test_top_tonight_disabled_without_station_then_reenabled(win, astro_controls, monkeypatch):
    from PySide6.QtTest import QTest
    station = {"value": None}
    monkeypatch.setattr(win.sky_view, "get_observer", lambda: station["value"])
    # Wait for the existing once-per-second planetarium UI refresh, not I/O.
    QTest.qWait(1200)
    assert not win._planetarium_tonight_btn.isEnabled()
    assert "站址" in win._planetarium_tonight_btn.toolTip()
    station["value"] = {"lat": 0.0, "lon": 0.0}
    QTest.qWait(1200)
    assert win._planetarium_tonight_btn.isEnabled()
    assert "本机今天" in win._planetarium_tonight_btn.toolTip()


def test_legacy_zero_arg_night_resolver_button_remains_supported(qapp):
    from time_machine import TimeMachineDialog
    event = datetime(2031, 4, 12, 20, tzinfo=timezone.utc)
    calls = []

    def legacy_resolver():
        calls.append(True)
        return event, -18.0

    dlg = TimeMachineDialog(night_resolver=legacy_resolver)
    try:
        dlg.show()
        _click(dlg.night_btn)
        assert calls == [True]
        assert dlg.selected_unix() == event.timestamp()
        assert "天文暮光终" in dlg.status.text()
    finally:
        dlg.close()


def test_time_panel_keeps_local_time_degradation_note(qapp, monkeypatch):
    from PySide6.QtWidgets import QLabel
    import time_machine
    note = "固定偏移，不随夏令时调整"
    monkeypatch.setattr(time_machine, "local_time_note", lambda: note)
    dlg = time_machine.TimeMachineDialog()
    try:
        dlg.show()
        _click(dlg._quick_buttons[-1])
        hints = "\n".join(label.text() for label in dlg.findChildren(QLabel))
        assert note in hints
        assert "本机本地时间，不是站址时区" in hints
    finally:
        dlg.close()


@pytest.mark.parametrize("inside", [False, True])
def test_selected_local_day_midnight_fraction_is_not_rounded_across_date(qapp, monkeypatch, inside):
    from types import SimpleNamespace
    from PySide6.QtCore import QDate, QDateTime, QTime, QTimeZone
    from mbdsdr_ai import atmosphere
    from main_window import _night_observation_time
    from time_machine import TimeMachineDialog, unix_from_qdatetime
    zone = QTimeZone(14 * 3600)
    selected = QDateTime(QDate(2031, 4, 12), QTime(12, 0), zone)
    # One fractional second before the start/end of the selected local day.
    event = datetime(2031, 4, 12 if inside else 11, 9, 59, 59, 750000, timezone.utc)
    monkeypatch.setattr(atmosphere, "compute_twilight_times", lambda day, station:
                        SimpleNamespace(astronomical_twilight_end=
                                        event if day == event.date() else None,
                                        civil_twilight_end=None))
    dlg = TimeMachineDialog(night_resolver_for_date=lambda dt: _night_observation_time(0, 0, dt))
    try:
        dlg.edit.setTimeZone(zone)
        dlg.edit.setDateTime(selected)
        dlg.show()
        _click(dlg.night_btn)
        assert dlg.edit.date() == selected.date()
        if inside:
            assert "天文暮光终" in dlg.status.text()
            assert dlg.selected_unix() == int(event.timestamp())
        else:
            assert "不猜" in dlg.status.text()
            assert dlg.selected_unix() == unix_from_qdatetime(selected)
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
