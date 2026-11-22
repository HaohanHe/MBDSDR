# SPDX-License-Identifier: MIT
"""On-demand target search: real Qt input, astronomy and isolated example site."""
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from rf_sky_view import RFSkyView, _PLANET_STYLE
from desktop.sky_target_candidates import build_target_results
from mbdsdr_ai.astronomy import unix_to_jd
from mbdsdr_ai.new_spacetime import get_time_engine


UTC = datetime(2026, 1, 15, tzinfo=timezone.utc)
JD = unix_to_jd(UTC.timestamp())
CAVEAT = "所选时刻的计算候选；非整夜预报，天气、遮挡和器材条件未评估"


class ObservedSkyView(RFSkyView):
    body_picked = Signal(object)
    goto_requested = Signal(float, float)

    def on_body_picked(self, body):
        super().on_body_picked(body)
        self.body_picked.emit(body)

    def goto_animated(self, az_deg, alt_deg, *args, **kwargs):
        super().goto_animated(az_deg, alt_deg, *args, **kwargs)
        self.goto_requested.emit(az_deg, alt_deg)


@pytest.fixture
def view(isolated_profile):
    app = QApplication.instance() or QApplication([])
    # 不要往进程级字体库 add/remove 字体：注册后再撤掉会让 Qt 的字体度量
    # 缓存留下不一致结果（实测：本文件跑过后 test_sky_label_collision 的
    # 标签数不变、test_spectrum_slider_overflow 的工具栏最小宽 126→94）。
    # 本文件只断言文本内容与交互，不需要额外字体；CJK 交给系统字体引擎。
    engine = get_time_engine()
    engine.set_unix(UTC.timestamp())
    engine.pause()
    v = ObservedSkyView()
    v.setFont(QFont("Microsoft YaHei", 10))
    v.resize(1000, 800)
    v.set_observer(0.0, 0.0, 0.0)
    v.set_site_note("隔离测试示例站点（非真实位置）")
    v.show()
    v.activateWindow()
    app.processEvents()
    yield v
    v.close()
    v.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def _real_results():
    return build_target_results(JD, 0.0, 0.0, planet_labels={
        key: str(style["zh"]) for key, style in _PLANET_STYLE.items()
    })


def _click_result(view, row):
    popup = view._search_popup
    item = popup.item(row)
    popup.scrollToItem(item)
    QTest.mouseClick(popup.viewport(), Qt.LeftButton,
                     pos=popup.visualItemRect(item).center())


def test_open_empty_search_real_candidates_mouse_card_and_one_goto(view):
    expected = _real_results()
    assert expected.rows and not expected.errors
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    before_site = dict(view._observer)
    before_layers = view.layer_state()
    assert not view._search_popup.isVisible()
    assert view.reveal_search()
    QApplication.processEvents()
    assert view._search_popup.isVisible()
    assert view._search_results == expected.rows
    assert view._search_popup.count() == len(expected.rows)
    for i, row in enumerate(expected.rows):
        assert row["label"] in view._search_popup.item(i).text()
        assert row["sub"] in view._search_popup.item(i).text()
    row = expected.rows[0]
    _click_result(view, 0)
    assert picked.count() == located.count() == 1
    assert view._picked_body["key"] == row["key"]
    assert view._body_info["ra"] == pytest.approx(row["ra"])
    assert view._body_info["dec"] == pytest.approx(row["dec"])
    assert view._body_info["mag"] == row["magnitude"]
    assert located.at(0) == pytest.approx([row["az_deg"], row["alt_deg"]])
    assert view._goto_anim is not None
    assert not view._search_popup.isVisible()
    assert view._observer == before_site
    assert view.layer_state() == before_layers
    assert get_time_engine().now_unix() == UTC.timestamp()


@pytest.mark.parametrize("enter", [Qt.Key_Return, Qt.Key_Enter])
def test_clear_query_shows_labeled_snapshot_keyboard_selects_real_dso(view, enter):
    view.reveal_search()
    QTest.keyClicks(view._search_edit, "M31")
    QTest.keyClick(view._search_edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(view._search_edit, Qt.Key_Backspace)
    assert view._search_edit.text() == ""
    assert view._search_popup.isVisible()
    header = view._target_header.toPlainText()
    assert "此时目标" in header
    assert "2026-01-15 00:00:00 UTC" in header
    assert view.get_site_note() in header
    assert "0.0000" in header
    assert CAVEAT in header
    assert view._target_header.isVisible()
    row = next(i for i, r in enumerate(view._search_results) if r["kind"] == "dso")
    expected = view._search_results[row]
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    QTest.keyClick(view._search_edit, Qt.Key_Down)
    QTest.keyClick(view._search_edit, Qt.Key_Up)
    assert view._search_popup.currentRow() == 0
    for _ in range(row):
        QTest.keyClick(view._search_edit, Qt.Key_Down)
    assert QApplication.focusWidget() is view._search_edit
    QTest.keyClick(view._search_edit, enter)
    assert picked.count() == located.count() == 1
    assert view._picked_body["key"] == expected["key"]
    assert view._body_info["ra"] == expected["ra"]
    assert view._body_info["dec"] == expected["dec"]
    assert view._body_info["mag"] == expected["magnitude"]
    assert located.at(0) == pytest.approx([expected["az_deg"], expected["alt_deg"]])
    QTest.keyClick(view._search_edit, enter)
    assert located.count() == 1
    view.reveal_search()
    QTest.keyClick(view._search_edit, Qt.Key_Escape)
    assert not view._search_popup.isVisible()
    assert not view._target_header.isVisible()
    assert view._search_edit.isVisible()
    QTest.keyClicks(view._search_edit, "Acamar")
    assert view._search_popup.isVisible()
    assert not view._target_header.isVisible()


@pytest.mark.parametrize("site", [(None, None), (91.0, 0.0)])
def test_missing_or_invalid_site_has_nonselectable_receipt_and_input_stays_live(view, site):
    view.set_observer(*site)
    view.reveal_search()
    popup = view._search_popup
    assert popup.isVisible()
    assert view._search_results == []
    assert popup.count() == 1
    assert not popup.item(0).flags() & Qt.ItemIsSelectable
    assert "观测地点" in popup.item(0).text()
    assert "无匹配" not in popup.item(0).text()
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    _click_result(view, 0)
    QTest.keyClick(view._search_edit, Qt.Key_Return)
    assert picked.count() == located.count() == 0
    assert CAVEAT in view._target_header.toPlainText()
    QTest.keyClicks(view._search_edit, "M31")
    assert view._search_edit.text() == "M31"
    assert view._search_results


@pytest.mark.parametrize("failure", ["planet", "dso", "both", "empty"])
def test_source_receipts_distinguish_partial_failed_and_empty_without_row_offset(
    view, monkeypatch, failure,
):
    from mbdsdr_ai.astronomy import dso, planets

    def unavailable(*args, **kwargs):
        raise OSError("isolated source unavailable")

    if failure in ("planet", "both"):
        monkeypatch.setattr(planets, "recommend_targets", unavailable)
    if failure in ("dso", "both"):
        monkeypatch.setattr(dso, "recommend_dso", unavailable)
    if failure == "empty":
        # Controlled empty sources, not fabricated display candidates.
        monkeypatch.setattr(planets, "recommend_targets", lambda *a, **k: [])
        monkeypatch.setattr(dso, "recommend_dso", lambda *a, **k: [])
    view.reveal_search()
    header = view._target_header.toPlainText()
    popup = view._search_popup
    if failure == "empty":
        assert "此时无符合条件的候选" in popup.item(0).text()
        assert "失败" not in header
    else:
        assert "isolated source unavailable" in header
        assert "无匹配" not in header
        if failure == "both":
            assert "候选计算失败" in popup.item(0).text()
            assert "行星/月球" in header and "深空天体" in header
        else:
            assert "部分来源失败" in header
            assert {r["kind"] for r in view._search_results} == (
                {"dso"} if failure == "planet" else {"planet"})
            selected = view._search_results[0]
            picked = QSignalSpy(view.body_picked)
            located = QSignalSpy(view.goto_requested)
            _click_result(view, 0)
            assert picked.count() == located.count() == 1
            assert view._picked_body["key"] == selected["key"]
    if failure in ("both", "empty"):
        assert view._search_results == []
        assert not popup.item(0).flags() & Qt.ItemIsEnabled
        QTest.keyClick(view._search_edit, Qt.Key_Return)
        assert view._picked_body is None
    QTest.keyClicks(view._search_edit, "Acamar")
    assert view._search_edit.text() == "Acamar"
    assert view._search_results


@pytest.mark.parametrize("change", ["date", "site", "note", "small-jump"])
@pytest.mark.parametrize("activation", ["keyboard", "mouse"])
def test_context_change_rejects_stale_pick_then_explicit_refresh(view, change, activation):
    view.reveal_search()
    old_header = view._target_header.toPlainText()
    engine = get_time_engine()
    if change == "date":
        engine.set_unix(UTC.timestamp() + 86400)
    elif change == "small-jump":
        engine.set_unix(UTC.timestamp() + 10)
    elif change == "site":
        view.set_observer(0.0, 90.0)
    else:
        view.set_site_note("另一个隔离预览站说明")
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    # No paint/event processing before activation: the pick must revalidate too.
    if activation == "mouse":
        _click_result(view, 0)
    else:
        QTest.keyClick(view._search_edit, Qt.Key_Return)
    assert picked.count() == located.count() == 0
    assert view._picked_body is None
    assert view._search_popup.isVisible()
    assert view._search_results == []
    assert "已失效" in view._target_header.toPlainText()
    assert "F5" in view._target_header.toPlainText()
    assert not view._search_popup.item(0).flags() & Qt.ItemIsEnabled
    QTest.keyClick(view._search_edit, Qt.Key_F5)
    assert "已失效" not in view._target_header.toPlainText()
    assert old_header != view._target_header.toPlainText()
    expected = build_target_results(view._current_jd(), view._observer["lat"],
                                    view._observer["lon"], planet_labels={
        key: str(style["zh"]) for key, style in _PLANET_STYLE.items()
    })
    assert view._search_results == expected.rows
    QTest.keyClick(view._search_edit, Qt.Key_Return)
    assert picked.count() == located.count() == 1
    assert view._picked_body["key"] == expected.rows[0]["key"]


def test_visible_snapshot_invalidates_on_paint_without_recalculating(view, monkeypatch):
    from desktop import sky_target_candidates
    original = sky_target_candidates.build_target_results
    calls = []

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(sky_target_candidates, "build_target_results", observed)
    view.reveal_search()
    assert len(calls) == 1
    get_time_engine().set_unix(UTC.timestamp() + 86400)
    view.repaint()
    QApplication.processEvents()
    assert view._search_results == []
    assert "已失效" in view._target_header.toPlainText()
    assert len(calls) == 1


@pytest.mark.parametrize("immersive", [False, True])
@pytest.mark.parametrize("size", [(1000, 800), (640, 480), (400, 376)])
def test_target_popup_header_rows_and_resize_stay_inside_view(view, immersive, size):
    view.set_immersive(immersive)
    view.resize(*size)
    view.reveal_search()
    QApplication.processEvents()
    popup = view._search_popup
    assert view.size().width() == size[0]
    assert view.size().height() == size[1]
    assert view.rect().contains(view._search_edit.geometry())
    assert view.rect().contains(popup.geometry()), (view.size(), popup.geometry())
    assert popup.rect().contains(view._target_header.geometry())
    assert popup.viewport().height() >= popup.sizeHintForRow(0)
    assert popup.wordWrap()
    assert popup.textElideMode() == Qt.ElideNone
    assert popup.horizontalScrollBar().maximum() == 0
    assert view._target_header.horizontalScrollBar().maximum() == 0
    # Every row, including below the initial viewport, is reachable and intact.
    for i in range(popup.count()):
        popup.setCurrentRow(i)
        popup.scrollToItem(popup.item(i))
        QApplication.processEvents()
        rect = popup.visualItemRect(popup.item(i))
        assert rect.left() >= 0 and rect.right() < popup.viewport().width()
        assert popup.viewport().rect().contains(rect.center())
    view.resize(400, 376)
    QApplication.processEvents()
    assert view.rect().contains(popup.geometry())
    assert popup.rect().contains(view._target_header.geometry())
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    QTest.keyClick(view._search_edit, Qt.Key_Return)
    assert picked.count() == located.count() == 1


def test_candidates_are_on_demand_not_creation_focus_loss_hidden_or_mode_toggle(view, monkeypatch):
    from desktop import sky_target_candidates
    original = sky_target_candidates.build_target_results
    calls = []

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(sky_target_candidates, "build_target_results", observed)
    hidden = ObservedSkyView()
    try:
        hidden.set_observer(0.0, 0.0)
        hidden.reveal_search()
        assert calls == []
    finally:
        hidden.close()
        hidden.deleteLater()
    view.repaint()
    QApplication.processEvents()
    assert calls == []
    view.reveal_search()
    assert len(calls) == 1
    view.hide_search()
    view.set_observer(0.0, 90.0)
    get_time_engine().set_unix(UTC.timestamp() + 86400)
    view.repaint()
    assert len(calls) == 1
    view.set_immersive(True)
    view.set_immersive(False)
    assert not view._search_popup.isVisible()
    view.reveal_search()
    assert len(calls) == 2
    view.setFocus()
    QApplication.processEvents()
    assert not view._search_popup.isVisible()
    view.repaint()
    assert len(calls) == 2
    view.reveal_search()
    assert len(calls) == 3
    view.hide()
    get_time_engine().set_unix(UTC.timestamp() + 2 * 86400)
    view.reveal_search()
    assert len(calls) == 3
    view.show()
    QApplication.processEvents()
    assert not view._search_popup.isVisible()
    assert len(calls) == 3


def test_real_time_small_steps_keep_explicit_snapshot_without_frame_recommendations(view, monkeypatch):
    from desktop import sky_target_candidates
    original = sky_target_candidates.build_target_results
    calls = []

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(sky_target_candidates, "build_target_results", observed)
    engine = get_time_engine()
    engine.resume(1.0)
    view.reveal_search()
    header = view._target_header.toPlainText()
    rows = list(view._search_results)
    for _ in range(5):
        QTest.qWait(30)
        view.repaint()
    assert len(calls) == 1
    assert view._search_results == rows
    assert view._target_header.toPlainText() == header
    engine.set_unix(engine.now_unix() + 65)
    view.repaint()
    assert view._search_results == []
    assert "已失效" in view._target_header.toPlainText()
    assert len(calls) == 1
