# SPDX-License-Identifier: MIT
"""情境音效接线（FR-11）：识别/点中按类别出声，3 秒去重，静音开关生效。"""
import sys

import pytest
from PySide6.QtWidgets import QApplication

from situation_sound import SituationSound
from rf_sky_view import RFSkyView, RFSkyViewPanel

app = QApplication.instance() or QApplication(sys.argv)


class _Recorder:
    def __init__(self):
        self.sources = []
        self.volumes = []
        self.plays = 0

    def setSource(self, source):  # noqa: N802
        self.sources.append(source)

    def setVolume(self, volume):  # noqa: N802
        self.volumes.append(volume)

    def play(self):  # noqa: N802
        self.plays += 1


def _view_with_recorder(tmp_path):
    view = RFSkyView()
    rec = _Recorder()
    view._situation_sound = SituationSound(
        cache_dir=str(tmp_path), player_factory=lambda: rec)
    return view, rec


def test_identify_sound_plays_once_per_target_and_category(tmp_path):
    view, rec = _view_with_recorder(tmp_path)

    view._maybe_play_identify_sound([{"kind": "star", "name": "Vega"}])
    assert rec.plays == 1
    view._maybe_play_identify_sound([{"kind": "star", "name": "Vega"}])
    assert rec.plays == 1, "同一目标 3 秒内不得连响"

    view._maybe_play_identify_sound([{"kind": "star", "name": "Sirius"}])
    assert rec.plays == 2, "换目标应立即响"

    view._maybe_play_identify_sound([{"kind": "satellite", "name": "ISS"}])
    assert rec.plays == 2, "未知类别不硬凑音"

    view._maybe_play_identify_sound([{"kind": "planet", "key": "jupiter"}])
    view._maybe_play_identify_sound([{"kind": "dso", "name": "M31"}])
    assert rec.plays == 4
    assert rec.volumes and rec.volumes[-1] == 0.2, "默认低音量"
    view.close()


def test_mute_switch_stops_sound(tmp_path):
    view, rec = _view_with_recorder(tmp_path)
    view.set_situation_sound_enabled(False)
    assert view.situation_sound_enabled() is False
    view._maybe_play_identify_sound([{"kind": "star", "name": "Vega"}])
    assert rec.plays == 0

    view.set_situation_sound_enabled(True)
    view._maybe_play_identify_sound([{"kind": "star", "name": "Vega"}])
    assert rec.plays == 1
    view.close()


def test_clicking_body_also_plays(tmp_path, monkeypatch):
    view, rec = _view_with_recorder(tmp_path)
    monkeypatch.setattr(view, "_build_body_info", lambda *_a, **_k: None)
    monkeypatch.setattr(view, "_body_context_key", lambda: "test-key")
    monkeypatch.setattr(view, "update", lambda *_a, **_k: None)

    view.on_body_picked({"kind": "planet", "key": "jupiter"})
    assert rec.plays == 1
    view.close()


def test_panel_has_mute_toggle_default_on():
    panel = RFSkyViewPanel()
    assert hasattr(panel, "_sound_button")
    assert panel._sound_button.isChecked() is True
    assert panel.sky_view.situation_sound_enabled() is True

    panel._sound_button.click()
    assert panel.sky_view.situation_sound_enabled() is False
    panel._sound_button.click()
    assert panel.sky_view.situation_sound_enabled() is True
    panel.close()
