# SPDX-License-Identifier: MIT
"""观测档案 → 桌面回放的真实接线护栏（无硬件，offscreen）。

判据：
  * 选到 ``.observation.json`` 走档案解析，绝不把 JSON 当 IQ 交给 load_iq；
  * 档案有阻塞项（数据文件不存在等）时如实提示，不设计划、不起播；
  * 合法档案把计划挂到窗口上，读盘目标是**数据文件**而不是档案本身；
  * 按回放时间推进计划：只换中心频率，不动文件采样率；同一步不重复改状态；
  * 停止回放清空计划，避免套用到下一段录音。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import main_window as mw  # noqa: E402
from mbdsdr_ai.observation_record import ObservationRecord  # noqa: E402

FILE_SR = 2_400_000.0
FILE_CENTER = 100_100_000.0
N_SAMPLES = 48_000
T0 = datetime(2026, 11, 20, 3, 0, 0, tzinfo=timezone.utc)


def _ev(offset_s, kind, detail, samples):
    return {
        "utc": (T0 + timedelta(seconds=offset_s)).isoformat(),
        "kind": kind,
        "detail": dict(detail),
        "samples": samples,
    }


def _write_data(tmp_path, name="cap"):
    """真实 cf32 数据 + 同名 SigMF meta（回放加载路径按这两份文件走）。"""
    n = N_SAMPLES
    iq = np.exp(2j * np.pi * 1000.0 * np.arange(n) / FILE_SR).astype(np.complex64)
    data = tmp_path / f"{name}.sigmf-data"
    iq.tofile(str(data))
    meta = tmp_path / f"{name}.sigmf-meta"
    meta.write_text(json.dumps({
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": FILE_SR,
            "core:frequency": FILE_CENTER,
            "core:total_num_samples": n,
            "core:dataset": data.name,
        },
        "captures": [{"core:sample_start": 0,
                      "core:datetime": T0.isoformat()}],
        "annotations": [],
    }), encoding="utf-8")
    return str(meta), str(data)


def _record(data_path, meta_path):
    return ObservationRecord(
        observation_id="obs-ui-replay",
        started_utc=T0.isoformat(),
        ended_utc=(T0 + timedelta(seconds=0.02)).isoformat(),
        time={"source": "system"},
        site={"source": "gnss", "lat_deg": 43.7, "lon_deg": 126.6},
        device={"driver": "rtlsdr", "name": "RTL-SDR #0"},
        tuning={"center_freq_hz": FILE_CENTER,
                "sample_rate_hz": FILE_SR, "gain_db": 30.0},
        products=[{"path": meta_path, "data_path": data_path,
                   "kind": "sigmf-meta", "samples": N_SAMPLES}],
        evidence="real_hw",
        timeline=[
            _ev(0.0, "record_start", {}, 0),
            _ev(0.005, "tune",
                {"from_hz": FILE_CENTER, "to_hz": 98_000_000.0},
                12_000),
            _ev(0.010, "tune",
                {"from_hz": 98_000_000.0, "to_hz": FILE_CENTER},
                24_000),
        ],
    )


def _write_archive(tmp_path, record, name="obs.observation.json"):
    p = tmp_path / name
    p.write_text(json.dumps(record.to_dict(), ensure_ascii=False),
                 encoding="utf-8")
    return str(p)


@pytest.fixture(scope="module")
def qtapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp, isolated_profile):
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(350)
    yield w
    try:
        w._stop_replay()
    except Exception:  # noqa: BLE001
        pass
    try:
        w._teardown_connection()
    except Exception:  # noqa: BLE001
        pass
    w.close()
    qtapp.processEvents()  # 排掉 close 触发的布局 singleShot，仍在隔离 profile 内
    w.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture(autouse=True)
def _reset_plan_state(window):
    """模块窗口跨用例复用：计划与回放定时器在每个用例前归零。"""
    window._stop_replay()
    window._replay_load_cancel.clear()
    yield
    window._stop_replay()
    window._replay_load_cancel.clear()


def test_archive_routes_to_data_file_not_json(window, monkeypatch, tmp_path):
    meta, data = _write_data(tmp_path)
    archive = _write_archive(tmp_path, _record(data, meta))
    calls = []
    monkeypatch.setattr(window, "_start_replay_load",
                        lambda path: calls.append(path))

    assert window._open_observation_archive(archive) is True

    assert window._replay_plan is not None
    assert window._replay_plan_index == -1
    assert calls == [data], "必须把数据文件交给 load_iq，而不是 .observation.json"
    assert calls[0].endswith(".sigmf-data")


def test_archive_with_missing_data_file_is_refused(window, monkeypatch, tmp_path):
    data = str(tmp_path / "gone.sigmf-data")
    meta = str(tmp_path / "gone.sigmf-meta")
    archive = _write_archive(tmp_path, _record(data, meta))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a)))
    calls = []
    monkeypatch.setattr(window, "_start_replay_load",
                        lambda path: calls.append(path))

    assert window._open_observation_archive(archive) is False

    assert window._replay_plan is None, "不可回放的档案不得挂计划"
    assert calls == [], "有阻塞项时不得起播"
    assert warnings, "必须把阻塞项告诉用户"
    assert "不存在" in warnings[0][2]


def test_plan_step_applies_center_and_is_idempotent(window):
    steps = [
        {"at_s": 0.0, "kind": "record_start",
         "state": {"center_freq_hz": 100_100_000.0}},
        {"at_s": 1.0, "kind": "tune",
         "state": {"center_freq_hz": 98_000_000.0}},
        {"at_s": 2.0, "kind": "tune",
         "state": {"center_freq_hz": 100_100_000.0}},
    ]
    window._replay_plan = {"steps": steps}
    window._replay_plan_index = -1

    assert window._apply_replay_plan_step(0.5) is steps[0]
    assert window._replay_center_hz == 100.1e6

    assert window._apply_replay_plan_step(1.5) is steps[1]
    assert window._replay_center_hz == 98e6

    assert window._apply_replay_plan_step(2.5) is steps[2]
    assert window._replay_center_hz == 100.1e6

    # 同一时间点再调：返回同一步，但不得把状态再改回去
    window._replay_center_hz = 555e6
    assert window._apply_replay_plan_step(2.5) is steps[2]
    assert window._replay_center_hz == 555e6, "重复调用不得重复改状态"
    assert window._replay_plan_index == 2


def test_stop_replay_clears_plan(window):
    window._replay_timer = None
    window._replay_plan = {"steps": [{"at_s": 0.0, "kind": "record_start",
                                      "state": {"center_freq_hz": 1e6}}]}
    window._replay_plan_index = 0
    window._replay_resume_poll = False

    window._stop_replay()

    assert window._replay_plan is None
    assert window._replay_plan_index == -1


def _begin_fixture(window, monkeypatch, total=1050, plan=None):
    """明确的数组夹具；不读取硬件，不声称 OTA 验证。"""
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    monkeypatch.setattr(window, "_active_sdr_backend", None)
    monkeypatch.setattr(window._audio_player, "start", lambda: True)
    window._replay_plan = plan
    window._begin_replay_from_rec({
        "iq": np.arange(total, dtype=np.float32).astype(np.complex64),
        "sample_rate": 20480.0, "center_freq_hz": FILE_CENTER,
        "duration_s": total / 20480.0,
    }, "fixture.cf32")
    window._replay_timer.stop()  # 手动 tick，避免测试依赖墙钟调度


def test_tick_splits_at_tune_and_consumes_short_tail(window, monkeypatch):
    sr = 20480.0
    steps = [
        {"sample": 0, "at_s": 0.0, "kind": "record_start",
         "state": {"center_freq_hz": FILE_CENTER}},
        {"sample": 500, "at_s": 500 / sr, "kind": "tune",
         "state": {"center_freq_hz": 98e6}},
        {"sample": 1050, "at_s": 1050 / sr, "kind": "record_stop",
         "state": {"center_freq_hz": 98e6}},
    ]
    _begin_fixture(window, monkeypatch, plan={"steps": steps})
    shown, demod = [], []
    monkeypatch.setattr(window.spectrum, "update_iq",
                        lambda iq, cf, sr: shown.append((iq.copy(), cf)))
    monkeypatch.setattr(window, "_demod_and_play",
                        lambda iq, sr: demod.append(iq.copy()))
    ended = []
    stop = window._stop_replay

    def capture_end():
        ended.append((window._replay_pos, window._replay_plan_index))
        stop()

    monkeypatch.setattr(window, "_stop_replay", capture_end)
    window._replay_tick()
    window._replay_tick()
    assert [len(iq) for iq, _ in shown] == [500, 524, 26]
    assert [cf for _, cf in shown] == [FILE_CENTER, 98e6, 98e6]
    np.testing.assert_array_equal(np.concatenate(demod), np.arange(1050))
    assert ended == [(1050, 2)], "消费量不能超尾，末尾事件不得遗漏"


def test_paused_seek_and_reverse_seek_reset_history_without_start(window, monkeypatch):
    steps = [
        {"sample": 0, "at_s": 0.0, "kind": "record_start",
         "state": {"center_freq_hz": FILE_CENTER}},
        {"sample": 500, "at_s": 500 / 20480, "kind": "tune",
         "state": {"center_freq_hz": 98e6}},
    ]
    _begin_fixture(window, monkeypatch, plan={"steps": steps})
    window._replay_timer.start()
    window._apply_listen_state("idle")
    monkeypatch.setattr(window._audio_player, "start",
                        lambda: pytest.fail("暂停 seek 不得开声卡"))
    monkeypatch.setattr(window, "_start_iq_streams",
                        lambda: pytest.fail("暂停 seek 不得启动设备"))
    window._replay_wfm_rx = object()
    window._replay_squelch = object()
    window._vfo = object()
    window._audio_player._queue.append(np.ones(32))
    window._audio_player._queued_samples = 32
    window._audio_player._resamp_cache[(44100, 48000)] = object()
    window.spectrum.update_iq(np.ones(1024, dtype=np.complex64), FILE_CENTER, 20480)
    assert window._seek_replay_sample(700)
    assert window._replay_pos == 700
    assert window._replay_center_hz == 98e6
    assert window._replay_plan_index == 1
    assert window._replay_wfm_rx is None and window._replay_squelch is None
    assert window._vfo is None
    assert not window._audio_player._queue
    assert not window._audio_player._resamp_cache
    assert "98.000000 MHz" in window.replay_timeline.progress_label.text()
    assert not window.spectrum.generator.has_live_data
    assert not window._replay_timer.isActive()
    assert window._listen_state == "idle"
    assert window._seek_replay_sample(0)
    assert window._replay_center_hz == FILE_CENTER
    assert window._replay_plan_index == 0
    assert not window._replay_timer.isActive()
    for invalid in (float("nan"), float("inf"), -1, 1051, 1.5, "invalid"):
        assert window._seek_replay_sample(invalid) is False
        assert window._replay_pos == 0


def test_stop_discards_success_already_queued_by_loader(window, monkeypatch):
    """线程已结束但 Qt 结果尚未交付；停止仍必须使这次读取失效。"""
    from types import SimpleNamespace
    window._replay_load_thread = SimpleNamespace(is_alive=lambda: False)
    window._replay_load_cancel.clear()
    window._replay_plan = {"steps": [{"sample": 0}]}
    monkeypatch.setattr(window, "_begin_replay_from_rec",
                        lambda *a: pytest.fail("停止后的过期读取结果不得起播"))
    try:
        window._stop_replay()
        window._on_replay_loaded({"iq": np.ones(1024), "sample_rate": 20480})
        assert window._replay_plan is None
        assert window.replay_timeline.isHidden()
    finally:
        window._replay_load_thread = None
        window._replay_load_cancel.clear()


def test_successful_load_closes_progress_without_cancelling_result(window, monkeypatch):
    from PySide6.QtWidgets import QProgressDialog
    dlg = QProgressDialog("读取夹具", "取消", 0, 100, window)
    dlg.canceled.connect(window._replay_load_cancel.set)
    window._replay_progress_dialog = dlg
    window._replay_load_path = "fixture.cf32"
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(window._audio_player, "start", lambda: True)
    window._on_replay_loaded({"iq": np.ones(2048), "sample_rate": 20480,
                              "duration_s": .1, "center_freq_hz": FILE_CENTER})
    assert window._replay_iq is not None, "程序收起进度框不能冒充用户取消"
    assert not window.replay_timeline.isHidden()
    assert window._replay_progress_dialog is None


def test_main_timeline_buttons_seek_pause_play_stop(window, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    _begin_fixture(window, monkeypatch)
    bar = window.replay_timeline
    assert not bar.isHidden()
    assert "未记录" in bar.events_button.text()
    window._replay_timer.start()
    QTest.mouseClick(bar.play_button, Qt.LeftButton)
    assert window._listen_state == "idle"
    assert not window._replay_timer.isActive()
    bar.slider.setValue(600)
    assert window._replay_pos == 600
    assert "600" in bar.progress_label.text()
    assert not window._replay_timer.isActive()
    QTest.mouseClick(bar.play_button, Qt.LeftButton)
    assert window._replay_timer.isActive()
    assert window._listen_state == "listen"
    QTest.mouseClick(bar.stop_button, Qt.LeftButton)
    assert window._replay_iq is None and window._replay_timer is None
    assert window._listen_state == "idle"
    assert bar.isHidden()


def test_same_sample_events_and_status_use_event_offset(window, monkeypatch):
    steps = [
        {"sample": 0, "at_s": 0, "kind": "record_start",
         "state": {"center_freq_hz": FILE_CENTER}},
        {"sample": 100, "at_s": .007, "kind": "tune",
         "state": {"center_freq_hz": 98e6}},
        {"sample": 100, "at_s": .007, "kind": "tune",
         "state": {"center_freq_hz": 99e6}},
        {"sample": 100, "at_s": .007, "kind": "read_empty",
         "state": {"center_freq_hz": 99e6}, "index_source": "wall"},
    ]
    _begin_fixture(window, monkeypatch, total=3000, plan={"steps": steps})
    pieces = []
    monkeypatch.setattr(window.spectrum, "update_iq",
                        lambda iq, cf, sr: pieces.append((len(iq), cf)))
    monkeypatch.setattr(window, "_demod_and_play", lambda *a: None)
    window._replay_tick()
    assert pieces == [(100, FILE_CENTER), (924, 99e6)]
    assert window._replay_plan_index == 3
    message = window.statusBar().currentMessage()
    assert "读空" in message and "0.007s" in message and "估算" in message
    assert "0.050s" not in message
    assert window._seek_replay_sample(0)
    window._replay_tick()
    assert window._replay_plan_index == 3 and window._replay_center_hz == 99e6


@pytest.mark.parametrize("result", [{"cancelled": True}, {"error": "fixture failure"}])
def test_loader_cancel_and_failure_clear_plan_without_touching_live(window, monkeypatch, result):
    from PySide6.QtWidgets import QProgressDialog
    window._replay_plan = {"steps": [{"sample": 0}]}
    window._replay_progress_dialog = QProgressDialog(window)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    monkeypatch.setattr(window, "_stop_iq_streams",
                        lambda: pytest.fail("失败/取消不应干扰实时接收"))
    monkeypatch.setattr(window, "_apply_recording",
                        lambda *a: pytest.fail("失败/取消不应干扰录制"))
    window._on_replay_loaded(result)
    assert window._replay_progress_dialog is None
    assert window._replay_load_thread is None
    assert window._replay_plan is None and window._replay_plan_index == -1
    assert window.replay_timeline.isHidden()


@pytest.mark.parametrize("sr", [0, float("nan"), float("inf")])
def test_invalid_loaded_rate_does_not_start(window, monkeypatch, sr):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    monkeypatch.setattr(window, "_stop_iq_streams", lambda: pytest.fail("无效读取不得接管实时流"))
    window._replay_plan = {"steps": [{"sample": 0}]}
    window._begin_replay_from_rec({"iq": np.ones(100), "sample_rate": sr}, "fixture.cf32")
    assert window._replay_plan is None
    assert window._replay_timer is None
    assert window.replay_timeline.isHidden()


def test_processing_failure_cleans_replay(window, monkeypatch):
    _begin_fixture(window, monkeypatch)
    def fail(*args):
        raise ValueError("fixture DSP failure")
    monkeypatch.setattr(window.spectrum, "update_iq", fail)
    window._replay_tick()
    assert window._replay_timer is None and window._replay_iq is None
    assert window.replay_timeline.isHidden()
    assert "fixture DSP failure" in window.statusBar().currentMessage()


def test_archive_scope_and_unindexed_events_are_not_lost(window, monkeypatch, tmp_path):
    meta, data = _write_data(tmp_path)
    record = _record(data, meta)
    record.products.append(dict(record.products[0]))
    record.timeline.append(_ev(.03, "future_kind", {"raw": "kept"}, 60000))
    record.timeline.append({"kind": "unindexed_kind", "detail": {"raw": "kept"}})
    archive = _write_archive(tmp_path, record)
    monkeypatch.setattr(window, "_start_replay_load", lambda p: None)
    assert window._open_observation_archive(archive)
    plan = window._replay_plan
    _begin_fixture(window, monkeypatch, total=48000, plan=plan)
    bar = window.replay_timeline
    assert "仅第 1 个" in bar.scope_label.text()
    assert "非完整时轴" in bar.scope_label.text()  # fixture 文件速率与档案速率不同
    bar.events_button.setChecked(True)
    assert bar.events.topLevelItemCount() == 5
    bar.events.setCurrentItem(bar.events.topLevelItem(3))
    assert not bar.jump_button.isEnabled() and "超出" in bar.jump_button.toolTip()
    bar.events.setCurrentItem(bar.events.topLevelItem(4))
    assert not bar.jump_button.isEnabled()
    bar.detail_button.setChecked(True)
    assert "unindexed_kind" in bar.details.toPlainText()


def test_real_archive_loader_delivers_array_and_timeline(window, monkeypatch, tmp_path):
    import time
    from PySide6.QtTest import QTest
    meta, data = _write_data(tmp_path)
    archive = _write_archive(tmp_path, _record(data, meta))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(window._audio_player, "start", lambda: True)
    begin = window._begin_replay_from_rec

    def begin_paused(rec, path):
        begin(rec, path)
        window._apply_listen_state("idle")

    monkeypatch.setattr(window, "_begin_replay_from_rec", begin_paused)
    assert window._open_observation_archive(archive)
    deadline = time.monotonic() + 5
    while window._replay_load_thread is not None and time.monotonic() < deadline:
        QTest.qWait(10)
    assert window._replay_load_thread is None
    assert window._replay_iq is not None and window._replay_iq.size == N_SAMPLES
    assert window._replay_sr == FILE_SR
    assert window._replay_progress_dialog is None
    bar = window.replay_timeline
    bar.events_button.setChecked(True)
    assert bar.events.topLevelItemCount() == 3
    bar.events.setCurrentItem(bar.events.topLevelItem(1))
    bar.jump_button.click()
    assert window._replay_pos == 12000
    assert window._replay_center_hz == 98e6
    assert not window._replay_timer.isActive()


def test_muted_replay_keeps_audio_tap_but_does_not_unmute_soundcard(window, monkeypatch):
    from types import SimpleNamespace
    _begin_fixture(window, monkeypatch)
    player = window._audio_player
    monkeypatch.setattr(player, "available", True)
    written, tapped = [], []
    monkeypatch.setattr(player, "write", lambda audio: written.append(audio))
    monkeypatch.setattr(window, "_ensure_vfo", lambda sr: True)
    monkeypatch.setattr(window, "_vfo", SimpleNamespace(process=lambda iq: np.ones(128)))
    monkeypatch.setattr(window, "_demod_at_48k", lambda *a: np.ones(128))
    monkeypatch.setattr(window, "_tap_demod_audio", lambda audio: tapped.append(audio) or audio)
    monkeypatch.setattr(window, "_replay_squelch_gate", lambda *a: True)
    monkeypatch.setattr(window._demod_cfg, "mode", "FM")
    window._apply_listen_state("muted")
    window._demod_and_play(np.ones(1024, dtype=np.complex64), 20480)
    assert len(tapped) == 1
    assert not written and player.is_muted


def test_audio_start_failure_preserves_paused_seekable_replay(window, monkeypatch):
    _begin_fixture(window, monkeypatch)
    window._replay_timer.start()
    monkeypatch.setattr(window._audio_player, "start", lambda: False)
    window._apply_listen_state("listen")
    assert window._listen_state == "idle"
    assert not window._replay_timer.isActive()
    assert window._replay_paused_by_idle
    assert not window.replay_timeline.isHidden()
    assert window._seek_replay_sample(600)
    assert window._replay_pos == 600
    assert not window._replay_timer.isActive()


def test_archive_out_of_record_bounds_remains_blocked(window, monkeypatch, tmp_path):
    meta, data = _write_data(tmp_path)
    record = _record(data, meta)
    record.timeline.append(_ev(.04, "tune", {"to_hz": 99e6}, N_SAMPLES + 1))
    archive = _write_archive(tmp_path, record)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    monkeypatch.setattr(window, "_start_replay_load", lambda p: pytest.fail("阻塞档案不得加载"))
    assert not window._open_observation_archive(archive)
    assert any("超出录制长度" in msg for msg in warnings)
    assert window._replay_plan is None
