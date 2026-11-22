# SPDX-License-Identifier: MIT
"""回放条的真实 Qt 信号测试；仅使用明确的事件/数组夹具。"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    return QApplication.instance() or QApplication([])


@pytest.fixture
def timeline(qtapp):
    from replay_timeline import ReplayTimeline
    widget = ReplayTimeline()
    yield widget
    widget.close()
    widget.deleteLater()


def test_navigation_signals_preserve_sample_indices_and_reject_outside(timeline):
    events = [
        {"sample": 0, "at_s": 0, "utc": "2026-11-20T00:00:00+00:00", "kind": "record_start"},
        {"sample": 15, "at_s": .015, "kind": "tune", "index_source": "wall"},
        {"sample": 15, "at_s": .015, "kind": "decode_hit", "detail": {"text": "fixture"}},
        {"sample": 1500, "at_s": 1.5, "kind": "future_kind"},
    ]
    assert timeline.isHidden()
    timeline.set_recording("fixture.cf32", 1000, 1000, events, "仅当前产物 / 载入范围")
    seek = QSignalSpy(timeline.seek_requested)
    toggle = QSignalSpy(timeline.toggle_requested)
    stop = QSignalSpy(timeline.stop_requested)
    QTest.mouseClick(timeline.play_button, Qt.LeftButton)
    QTest.mouseClick(timeline.stop_button, Qt.LeftButton)
    assert toggle.count() == stop.count() == 1
    QTest.mouseClick(timeline.events_button, Qt.LeftButton)
    QTest.mouseClick(timeline.next_button, Qt.LeftButton)
    QTest.mouseClick(timeline.next_button, Qt.LeftButton)
    QTest.mouseClick(timeline.next_button, Qt.LeftButton)
    assert [seek.at(i)[0] for i in range(seek.count())] == [0, 15, 15]
    QTest.mouseClick(timeline.previous_button, Qt.LeftButton)
    assert seek.at(3) == [15]
    timeline.events.setCurrentItem(timeline.events.topLevelItem(3))
    assert not timeline.jump_button.isEnabled()
    timeline.events.itemDoubleClicked.emit(timeline.events.topLevelItem(3), 0)
    assert seek.count() == 4
    assert "超出" in timeline.jump_button.toolTip()
    assert "估算" in timeline.events.topLevelItem(1).text(3)
    assert "调谐" in timeline.events.topLevelItem(1).text(0)
    assert "解码命中" in timeline.events.topLevelItem(2).text(0)
    assert "future_kind" in timeline.events.topLevelItem(3).text(0)
    assert timeline.events.headerItem().text(2) == "原始 UTC"
    assert "录制偏移" in timeline.events.headerItem().text(1)


def test_slider_release_seeks_but_progress_updates_do_not(timeline):
    timeline.set_recording("plain.cf32", 1000, 1000, [], "仅载入范围")
    spy = QSignalSpy(timeline.seek_requested)
    timeline.set_progress(120, False)
    assert spy.count() == 0
    assert timeline.slider.value() == 120
    assert "120" in timeline.progress_label.text()
    assert "播放" in timeline.play_button.text()
    assert "未记录" in timeline.events_button.text()
    timeline.slider.setSliderDown(True)
    timeline.slider.setValue(600)
    timeline.set_progress(150, True)
    assert timeline.slider.value() == 600, "后台 tick 不得抢走拖动位置"
    timeline.slider.setSliderDown(False)
    assert spy.count() == 1 and spy.at(0) == [600]
    timeline.clear_recording()
    assert timeline.isHidden()
    assert timeline.events.topLevelItemCount() == 0


def test_replacing_recording_is_silent_and_large_indices_are_not_truncated(timeline):
    timeline.set_recording("first.cf32", 1000, 1000, [], "载入范围")
    timeline.set_progress(900, True)
    spy = QSignalSpy(timeline.seek_requested)
    timeline.set_recording("second.cf32", 10, 1000, [], "载入范围")
    assert spy.count() == 0, "换文件的进度复位不应发出旧位置 seek"
    timeline.set_recording("large.cf32", 4_000_000_000, 1_000_000,
                           [{"sample": 3_000_000_001, "at_s": 3000,
                             "kind": "read_gap", "index_source": "wall"}], "夹具")
    timeline.events_button.setChecked(True)
    timeline.next_button.click()
    assert spy.count() == 1 and spy.at(0) == [3_000_000_001]
    assert "读空" in timeline.events.topLevelItem(0).text(0)
