# SPDX-License-Identifier: MIT
"""Sky search UI integration: real catalogs, isolated neutral example station."""
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from rf_sky_view import RFSkyView, format_distance_ly
from mbdsdr_ai.new_spacetime import get_time_engine
from mbdsdr_ai.skyengine import constellations, stars
from mbdsdr_ai.skyengine.star_catalog import get_catalog


class ObservedSkyView(RFSkyView):
    """Observe real selection/navigation without replacing their implementation."""
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
    engine = get_time_engine()
    engine.set_unix(datetime(2026, 9, 27, tzinfo=timezone.utc).timestamp())
    engine.pause()
    v = ObservedSkyView()
    v.resize(1000, 800)
    # Synthetic observer only; no real/user station is read or written.
    v.set_observer(0.0, 0.0, 0.0)
    v.set_site_note("隔离测试示例站点（非真实位置）")
    v.show()
    v.activateWindow()
    v.reveal_search()
    app.processEvents()
    yield v
    v.close()
    v.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def _click_result(view, row):
    popup = view._search_popup
    item = popup.item(row)
    popup.scrollToItem(item)
    QTest.mouseClick(popup.viewport(), Qt.LeftButton,
                     pos=popup.visualItemRect(item).center())


def _assert_catalog_card(view, star):
    info = view._body_info
    culture = stars.star_culture(star.name)
    assert info["title"] == (culture["zh"] if culture else star.name)
    assert info["ra"] == star.ra_deg
    assert info["dec"] == star.dec_deg
    assert info["mag"] == star.vmag
    assert star.dist_pc > 0
    assert info["dist_text"] == format_distance_ly(star.dist_pc * 3.26156)
    assert "HYG v4.1" in info["desc"]
    assert star.spect in info["desc"]
    for field in ("hyg_id", "hip", "name", "con", "spect", "ra_deg",
                  "dec_deg", "vmag", "dist_pc"):
        assert view._picked_body[field] == getattr(star, field)


def test_mouse_search_catalog_only_star_keeps_real_card(view):
    star, = get_catalog().search_by_name("Acamar")
    assert star.name not in constellations.merged_star_index()
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    QTest.keyClicks(view._search_edit, star.name)
    row = next(i for i, r in enumerate(view._search_results)
               if r.get("name") == star.name)
    _click_result(view, row)
    assert picked.count() == located.count() == 1
    _assert_catalog_card(view, star)


@pytest.mark.parametrize("enter", [Qt.Key_Return, Qt.Key_Enter])
def test_keyboard_search_keeps_focus_navigates_and_locates_once(view, enter):
    app = QApplication.instance()
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    before_time = get_time_engine().now_unix()
    for char in "Ac":
        QTest.keyClicks(app.focusWidget(), char)
        app.processEvents()
        assert app.focusWidget() is view._search_edit
    assert view._search_edit.text() == "Ac"
    assert view._search_popup.isVisible()
    assert len(view._search_results) >= 2
    assert view._search_popup.currentRow() == 0
    QTest.keyClick(app.focusWidget(), Qt.Key_Down)
    assert view._search_popup.currentRow() == 1
    QTest.keyClick(app.focusWidget(), Qt.Key_Up)
    assert view._search_popup.currentRow() == 0
    row = next(i for i, r in enumerate(view._search_results)
               if r.get("name") == "Acamar")
    for _ in range(row):
        QTest.keyClick(app.focusWidget(), Qt.Key_Down)
    star = get_catalog().find_by_name("Acamar")
    assert star.name not in constellations.merged_star_index()
    QTest.keyClick(app.focusWidget(), enter)
    assert picked.count() == located.count() == 1
    assert not view._search_popup.isVisible()
    _assert_catalog_card(view, star)
    QTest.keyClick(app.focusWidget(), enter)
    assert picked.count() == located.count() == 1
    assert get_time_engine().now_unix() == before_time


def test_dipper_alias_mouse_cards_match_real_hip_and_one_culture(view):
    from mbdsdr_ai.skyengine.sky_culture import names_for_hip

    view.set_constellation_systems([constellations.SYSTEM_CHINESE, "indian"])
    names = ["Dubhe", "Merak", "Phecda", "Megrez", "Alioth", "Mizar", "Alkaid"]
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    for row, name in enumerate(names):
        # Qt keyClicks only accepts Latin text; use the real IME commit event.
        QTest.keyClick(view._search_edit, Qt.Key_A, Qt.ControlModifier)
        ime = QInputMethodEvent()
        ime.setCommitString("北斗七星")
        QApplication.sendEvent(view._search_edit, ime)
        assert [r["name"] for r in view._search_results] == names
        star = get_catalog().find_by_name(name)
        hip_names = names_for_hip(star.hip)
        assert hip_names[constellations.SYSTEM_IAU]["en"] == name
        _click_result(view, row)
        _assert_catalog_card(view, star)
        culture = stars.star_culture(name)
        tag = constellations.system_info("indian")["tag"]
        # Exact parts forbid duplicate notes or a second Chinese name.
        assert view._body_info["desc"].split(" · ") == [
            f"光谱型 {star.spect}",
            f"星座 {constellations.CONSTELLATION_NAMES[star.con]}",
            f"{tag}：{hip_names['indian']['native']}",
            culture["note"], "HYG v4.1",
        ]
    assert picked.count() == located.count() == len(names)


@pytest.mark.parametrize("immersive", [False, True])
def test_escape_closes_results_without_changing_body_time_or_mode(view, immersive):
    view.set_immersive(immersive)
    view.set_all_sky_mode(True)
    view.reveal_search()
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    QTest.keyClicks(view._search_edit, "Acamar")
    QTest.keyClick(view._search_edit, Qt.Key_Return)
    before_body = dict(view._picked_body)
    before_info = dict(view._body_info)
    before_time = get_time_engine().now_unix()
    QTest.keyClick(view._search_edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(view._search_edit, "M")
    QTest.keyClick(view._search_edit, Qt.Key_Down)
    popup = view._search_popup
    assert popup.isVisible()
    assert view.rect().contains(popup.geometry())
    if immersive:
        assert popup.geometry().bottom() <= view._search_edit.y()
    else:
        assert popup.y() >= view._search_edit.geometry().bottom()
    QTest.keyClick(view._search_edit, Qt.Key_Escape)
    assert not popup.isVisible()
    assert view._search_edit.isVisible()
    assert QApplication.focusWidget() is view._search_edit
    assert view._picked_body == before_body
    assert view._body_info == before_info
    assert get_time_engine().now_unix() == before_time
    assert view._all_sky_active
    assert picked.count() == located.count() == 1
    QTest.keyClicks(QApplication.focusWidget(), "31")
    assert view._search_edit.text() == "M31"
    assert popup.isVisible()


def test_no_result_row_cannot_activate_by_keys_or_mouse(view):
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    QTest.keyClicks(view._search_edit, "Acamar")
    QTest.keyClick(view._search_edit, Qt.Key_Return)
    body = dict(view._picked_body)
    before_time = get_time_engine().now_unix()
    QTest.keyClick(view._search_edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(view._search_edit, "zzzzzz")
    assert view._search_results == []
    assert view._search_popup.count() == 1
    assert not view._search_popup.item(0).flags() & Qt.ItemIsEnabled
    for key in (Qt.Key_Down, Qt.Key_Up, Qt.Key_Return, Qt.Key_Enter):
        QTest.keyClick(view._search_edit, key)
    _click_result(view, 0)
    assert picked.count() == located.count() == 1
    assert view._picked_body == body
    assert get_time_engine().now_unix() == before_time
    QTest.keyClick(view._search_edit, Qt.Key_Escape)
    assert not view._search_popup.isVisible()


@pytest.mark.parametrize("activation", ["keyboard", "mouse"])
def test_search_without_station_reports_notice_without_false_location(view, activation):
    view.set_observer(None, None)
    before_time = get_time_engine().now_unix()
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    QTest.keyClicks(view._search_edit, "Acamar")
    if activation == "keyboard":
        QTest.keyClick(view._search_edit, Qt.Key_Return)
    else:
        _click_result(view, 0)
    assert picked.count() == located.count() == 0
    assert view._picked_body is None
    assert view._goto_anim is None
    assert "未设置观测地点" in view._camera_notice
    assert "无法计算方位" in view._camera_notice
    assert get_time_engine().now_unix() == before_time


def test_mouse_double_click_search_result_locates_only_once(view):
    picked = QSignalSpy(view.body_picked)
    located = QSignalSpy(view.goto_requested)
    QTest.keyClicks(view._search_edit, "Acamar")
    QApplication.processEvents()
    popup = view._search_popup
    row_pos = popup.visualItemRect(popup.item(0)).center()
    pos = view.mapFromGlobal(popup.viewport().mapToGlobal(row_pos))
    # Use the window handle: Qt, not the test, routes clicks to the child at pos.
    assert view.childAt(pos) is popup.viewport(), (pos, popup.geometry(), row_pos)
    clicked = QSignalSpy(popup.clicked)
    QTest.mouseClick(view.windowHandle(), Qt.LeftButton, pos=pos)
    QApplication.processEvents()
    assert picked.count() == located.count() == 1, (
        clicked.count(), popup.isHidden(), popup.currentRow(),
        QApplication.focusWidget(), view._camera_notice)
    QTest.mouseDClick(view.windowHandle(), Qt.LeftButton, pos=pos)
    assert picked.count() == located.count() == 1
    _assert_catalog_card(view, get_catalog().find_by_name("Acamar"))
    # The suppression is local to the dismissed result row, not the whole sky.
    QTest.mouseDClick(view.windowHandle(), Qt.LeftButton,
                     pos=view.rect().bottomRight() - QPoint(40, 40))
    assert located.count() == 2


def test_unavailable_catalog_keeps_real_bright_star_fallback(view, monkeypatch):
    from mbdsdr_ai.skyengine import star_catalog

    def unavailable():
        raise OSError("isolated test: catalog unavailable")

    monkeypatch.setattr(star_catalog, "get_catalog", unavailable)
    QTest.keyClicks(view._search_edit, "Sirius")
    _click_result(view, 0)
    star = constellations.merged_star_index()["Sirius"]
    assert view._body_info["title"] == stars.star_culture(star.name)["zh"]
    assert view._body_info["ra"] == star.ra_deg
    assert view._body_info["dec"] == star.dec_deg
    assert view._body_info["mag"] == star.vmag
    assert view._body_info["dist_text"] == format_distance_ly(star.dist_ly)
    assert "HYG v4.1" not in view._body_info["desc"]
    assert "ra_deg" not in view._picked_body
    assert "全天星表不可用" in view._camera_notice
