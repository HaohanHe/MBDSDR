# SPDX-License-Identifier: MIT
"""情境音效（FR-11）：自制短音、默认低音量、静音/未知类别/无设备都静默降级。"""
import io
import wave

import pytest

from situation_sound import (
    CATEGORY_TONES,
    DEFAULT_VOLUME,
    SAMPLE_RATE,
    SituationSound,
    category_for_kind,
    wav_bytes,
)


class _Recorder:
    def __init__(self):
        self.sources = []
        self.volumes = []
        self.plays = 0

    def setSource(self, source):  # noqa: N802 - 模拟 QSoundEffect API
        self.sources.append(source)

    def setVolume(self, volume):  # noqa: N802
        self.volumes.append(volume)

    def play(self):  # noqa: N802
        self.plays += 1


def test_category_mapping():
    assert category_for_kind("star") == "star"
    assert category_for_kind(" PLANET ") == "planet"
    assert category_for_kind("moon") == "planet"
    assert category_for_kind("dso") == "dso"
    assert category_for_kind("satellite") is None
    assert category_for_kind(None) is None


def test_generated_wav_is_valid_and_not_silent(tmp_path):
    for category in ("star", "planet", "dso"):
        raw = wav_bytes(CATEGORY_TONES[category])
        with wave.open(io.BytesIO(raw)) as w:
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getframerate() == SAMPLE_RATE
            frames = w.readframes(w.getnframes())
            nframes = w.getnframes()
        assert len(frames) > 0
        peak = max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True))
                   for i in range(0, len(frames), 2))
        assert peak > 1000, f"{category} 生成的音不应是静音"
        assert nframes / SAMPLE_RATE == pytest.approx(
            sum(d for _, d, _ in CATEGORY_TONES[category]), abs=0.01)


def test_play_dispatches_by_category_with_low_default_volume(tmp_path):
    sound = SituationSound(cache_dir=str(tmp_path),
                           player_factory=lambda: _Recorder())
    assert sound.volume == DEFAULT_VOLUME == 0.2
    assert sound.play("star") is True
    assert sound.play("planet") is True
    assert sound.play("dso") is True
    assert sound.play("dso") is True
    assert set(sound._players) == {"star", "planet", "dso"}
    for rec in sound._players.values():
        assert rec.plays >= 1
        assert rec.volumes and rec.volumes[-1] == 0.2
        assert rec.sources and rec.sources[0].endswith(".wav")


def test_muted_and_unknown_kind_do_not_play(tmp_path):
    rec = _Recorder()
    sound = SituationSound(cache_dir=str(tmp_path),
                           player_factory=lambda: rec)
    sound.set_enabled(False)
    assert sound.play("star") is False
    assert rec.plays == 0

    sound.set_enabled(True)
    assert sound.play("satellite") is False
    assert sound.play(None) is False
    assert rec.plays == 0


def test_no_audio_device_is_honest_noop(tmp_path, monkeypatch):
    from PySide6.QtMultimedia import QMediaDevices

    monkeypatch.setattr(QMediaDevices, "audioOutputs", staticmethod(lambda: []))
    sound = SituationSound(cache_dir=str(tmp_path))
    assert sound.play("star") is False
    assert sound.last_error == "no_audio_device"


def test_volume_clamped(tmp_path):
    sound = SituationSound(cache_dir=str(tmp_path),
                           player_factory=lambda: _Recorder())
    sound.set_volume(2.0)
    assert sound.volume == 1.0
    sound.set_volume(-1.0)
    assert sound.volume == 0.0
