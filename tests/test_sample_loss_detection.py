# SPDX-License-Identifier: MIT
"""录音墙钟对账：读成功但吞吐不足时必须留下 sample_loss 证据，不能静默。"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.core.controller import SDRController, _sample_shortfall  # noqa: E402
from mbdsdr_ai.observation_record import TIMELINE_KINDS  # noqa: E402
from desktop.replay_timeline import event_label  # noqa: E402


def test_normal_throughput_has_no_shortfall():
    assert _sample_shortfall(2.5, 2_400_000, 6_000_000) is None
    assert _sample_shortfall(30.0, 2_400_000, 71_884_800) is None


def test_shortfall_reports_expected_and_ratio():
    ev = _sample_shortfall(2.5, 2_400_000, 1_277_952)
    assert ev is not None
    assert ev["expected_samples"] == 6_000_000
    assert ev["accounted_samples"] == 1_277_952
    assert 0.21 < ev["ratio"] < 0.22


def test_shortfall_guards_bad_inputs():
    assert _sample_shortfall(None, 2_400_000, 1000) is None
    assert _sample_shortfall(0, 2_400_000, 1000) is None
    assert _sample_shortfall(2.5, 0, 1000) is None
    assert _sample_shortfall(2.5, 2_400_000, 0) is None


def test_decoder_shared_samples_count_as_accounted():
    """解码线程分流的样本没进文件，但不是丢失；合计后不应误报短录。"""
    written, shared = 5_000_000, 1_000_000
    assert _sample_shortfall(2.5, 2_400_000, written) is not None
    assert _sample_shortfall(2.5, 2_400_000, written + shared) is None


def _ctrl_with(rec_samples: int, wall_s: float) -> SDRController:
    class _FakeRec:
        n_samples = rec_samples

        def set_annotations(self, _a):
            pass

        def close(self):
            return None

    ctrl = SDRController()
    ctrl._recorder = _FakeRec()
    ctrl._recording_events = []
    ctrl._recording_start_event = None
    ctrl._recording_snapshot = None
    ctrl._recording_sample_rate = 2_400_000.0
    ctrl._recording_start_mono = time.monotonic() - wall_s
    return ctrl


def test_stop_recording_surfaces_shortfall():
    r = _ctrl_with(rec_samples=1_000, wall_s=2.5).stop_recording()
    assert "sample_loss" in r
    assert r["sample_loss"]["ratio"] < 0.01


def test_stop_recording_no_false_positive_on_full_rate():
    r = _ctrl_with(rec_samples=6_000_000, wall_s=2.5).stop_recording()
    assert "sample_loss" not in r


def test_sample_loss_kind_and_label_are_wired():
    assert "sample_loss" in TIMELINE_KINDS
    assert event_label({"kind": "sample_loss"}) == "录制短录 / 样本丢失"
