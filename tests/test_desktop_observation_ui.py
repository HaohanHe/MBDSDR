# SPDX-License-Identifier: MIT
"""Desktop recording integration using explicit test IQ and a substitute backend.

Exercises the real desktop writer/stop/export/load path, not hardware reception.
"""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from main_window import MainWindow
from mbdsdr_ai.observation_record import load_record, replay_plan
from settings import DesktopSettings


class TestIQBackend:
    __test__ = False

    def __init__(self):
        self.rate = 1_000_000.0
        self.frequency = 100_000_000.0
        self.status = SimpleNamespace(gain_db=7.0)
        self.device = SimpleNamespace(name="Test IQ backend (not hardware)")

    def read_samples(self, count):
        raise AssertionError("Test drives explicit IQ chunks; no acquisition")

    def get_sample_rate(self):
        return self.rate

    def set_sample_rate(self, rate):
        self.rate = float(rate)

    def get_frequency(self):
        return self.frequency

    def set_frequency(self, frequency):
        self.frequency = float(frequency)
        return True


@pytest.fixture
def window(isolated_profile, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.settings = DesktopSettings.load(str(isolated_profile / "desktop_settings.json"))
    w.settings.set("record_dir", str(tmp_path))
    w._active_sdr_backend = TestIQBackend()
    monkeypatch.setattr(w, "_stop_iq_streams", lambda: None)
    monkeypatch.setattr(w, "_start_iq_streams", lambda: None)
    monkeypatch.setattr(w, "_update_status_bar", lambda: None)
    w.set_observer_site(0.0, 0.0, source="manual", persist=False)
    app.processEvents()
    w.control_panel.set_sdr_connected(True)
    yield w
    if w._recording:
        w._stop_recording()
    w._active_sdr_backend = None
    w._disconnect()
    w.close()
    app.processEvents()  # Drain close-triggered singleShot callbacks before deletion.
    w.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def runtime(monkeypatch):
    # Shared controller station deliberately differs from desktop's manual site.
    data = {"site": {"source": "manual", "lat_deg": 1.0, "lon_deg": 1.0},
            "gnss": {"connected": False, "fix": False},
            "time": {"source": "system", "nested": {"value": "at start"}}}
    calls = []

    def snapshot():
        calls.append(1)
        return data

    monkeypatch.setattr("mbdsdr_ai.runtime_sources.get_runtime_sources",
                        lambda: SimpleNamespace(snapshot=snapshot))
    return data, calls


def _start(w):
    w.control_panel.record_button.click()
    assert w._recording
    return Path(w._record_file_path)


def _write(w, count=1000):
    iq = np.full(count, 0.25 + 0.5j, dtype=np.complex64)
    w._record_write_chunk(iq)
    return iq


def _load(path):
    record, error = load_record(str(path.with_suffix(".observation.json")))
    assert error is None, error
    assert record is not None
    return record


def test_desktop_record_stop_archive_roundtrip_keeps_initial_tuning(window, runtime):
    path = _start(window)
    first = _write(window)
    window._active_sdr_backend.set_frequency(101_000_000)
    window._note_record_center_change(101_000_000)
    second = _write(window)
    window.control_panel.record_button.click()

    record = _load(path)
    plan = replay_plan(record)
    assert plan["can_replay"], plan
    assert plan["data_paths"] == [str(path)]
    assert record.tuning["center_freq_hz"] == 100_000_000
    assert record.products[0]["final_center_freq_hz"] == 101_000_000
    assert [event["samples"] for event in record.timeline] == [0, 1000, 2000]
    assert record.site["source"] == "manual"
    assert (record.site["lat_deg"], record.site["lon_deg"]) == (0.0, 0.0)
    assert record.time["source"] == "system"
    assert datetime.fromisoformat(record.time["recording_started_at"]).utcoffset() is not None
    assert datetime.fromisoformat(window._record_start_time).utcoffset() is not None
    assert runtime[1] == [1]
    assert path.read_bytes() == np.concatenate((first, second)).tobytes()


def test_multi_rate_products_are_selectable_without_automatic_replay(window, runtime, monkeypatch):
    panel = window.control_panel
    assert panel.record_review_button.isHidden()
    calls = []
    monkeypatch.setattr(window, "_stop_tx", lambda *a, **k: calls.append("tx"))
    monkeypatch.setattr(window, "_start_replay_load", lambda path: calls.append(path))
    first = _start(window)
    assert window.record_btn.text() == "停止录音"
    _write(window, 1000)
    window._on_sample_rate_changed(2_000_000)
    second = Path(window._record_file_path)
    _write(window, 4000)
    window._stop_recording()

    assert calls == []
    assert panel.record_product_combo.count() == 4
    assert not panel.record_review_button.isHidden()
    for path, rate, samples in [(first, 1_000_000, 1000), (second, 2_000_000, 4000)]:
        record = _load(path)
        assert record.tuning["sample_rate_hz"] == rate
        assert record.products[0]["samples"] == samples
        assert replay_plan(record)["data_paths"] == [str(path)]
        assert datetime.fromisoformat(record.started_utc).utcoffset() is not None
        for product in [path.with_suffix(".observation.json"), path]:
            index = panel.record_product_combo.findData(str(product))
            assert index >= 0
            panel.record_product_combo.setCurrentIndex(index)
            panel.record_review_button.click()
            assert calls == ["tx", str(path)]
            calls.clear()
            if product == path:
                assert window._replay_plan is None
            else:
                assert window._replay_plan["data_paths"] == [str(path)]
    _start(window)
    assert panel.record_review_button.isHidden()
    assert panel.record_product_combo.count() == 0


@pytest.mark.parametrize("source", ["user", "config", "place", "manual", "gnss"])
def test_start_snapshot_survives_site_changes_and_gnss_loss(window, runtime, source, monkeypatch):
    data, calls = runtime
    window.set_observer_site(0.0, 0.0, source=source, persist=False)
    if source == "gnss":
        data["gnss"].update(connected=True, fix=True, lat_deg=0.0, lon_deg=0.0)
    # Any attempt to query/publish a new fix or change time is a test failure.
    def forbidden(*args, **kwargs):
        raise AssertionError("Recording must not query GNSS or initiate time synchronization")
    monkeypatch.setattr(window, "_gnss", SimpleNamespace(get_position=forbidden))
    observer = window.sky_view.get_observer()
    path = _start(window)
    _write(window)
    observer["lat"] = 2.0  # get_observer returns a shared dict, not a copy.
    data["time"]["nested"]["value"] = "after start"
    data["gnss"].update(connected=False, fix=False, lat_deg=None, lon_deg=None)
    window.set_observer_site(None, None, persist=False)
    window._stop_recording()
    record = _load(path)
    assert calls == [1]
    assert record.site["source"] == ("gnss" if source == "gnss" else "manual")
    assert record.site["lat_deg"] == 0.0
    assert record.site["lon_deg"] == 0.0
    assert record.time["runtime_snapshot_time"]["nested"]["value"] == "at start"
    assert record.time["source"] == "system"
    monkeypatch.setattr(window, "_gnss", None)


@pytest.mark.parametrize("case", ["missing", "preview", "coordinate_conflict",
                                  "gnss_without_fix", "gnss_mismatch"])
def test_unverified_site_archives_keep_blocker_and_require_explicit_iq_choice(
        window, runtime, monkeypatch, case):
    if case == "missing":
        window.set_observer_site(None, None, persist=False)
    elif case == "preview":
        window.set_observer_site(0.0, 0.0, source="preview", persist=False)
    elif case == "coordinate_conflict":
        window.sky_view.set_observer(1.0, 1.0)
    elif case in ("gnss_without_fix", "gnss_mismatch"):
        window._observer_source = "gnss"
    if case == "gnss_mismatch":
        runtime[0]["gnss"].update(connected=True, fix=True, lat_deg=1.0, lon_deg=1.0)
    path = _start(window)
    _write(window)
    window._stop_recording()
    record = _load(path)
    assert record.site["source"] == "none"
    assert record.site["lat_deg"] is None
    assert record.site["reason"]
    plan = replay_plan(record)
    assert not plan["can_replay"] and plan["blockers"]
    calls, warnings = [], []
    monkeypatch.setattr(window, "_stop_tx", lambda *a, **k: calls.append("tx"))
    monkeypatch.setattr(window, "_start_replay_load", lambda p: calls.append(p))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append(text))
    panel = window.control_panel
    panel.record_product_combo.setCurrentIndex(
        panel.record_product_combo.findData(str(path.with_suffix(".observation.json"))))
    panel.record_review_button.click()
    assert calls == ["tx"], "A blocked archive must never silently start IQ replay"
    assert warnings == ["；\n".join(plan["blockers"])]
    index = panel.record_product_combo.findData(str(path))
    assert "仅回放录音（不重演观测上下文）" in panel.record_product_combo.itemText(index)
    panel.record_product_combo.setCurrentIndex(index)
    panel.record_review_button.click()
    assert calls == ["tx", "tx", str(path)]
    assert window._replay_plan is None


def test_config_site_stays_usable_when_gnss_reports_another_position(
        window, runtime):
    """配置站址 + GNSS 定位在别处：档案仍按配置站址落盘、可回放。

    2026-11-22 真机：gui_config 站址与 GNSS 位置相差约 100 km，旧逻辑把
    「GNSS 与桌面站址不一致」判成不可核实 → site=none → 回放报
    「没有可用站址」。站址优先级本来就是配置站址 > GNSS 实时 fix，
    GNSS 位置不同只能作为备注，不能据此抹掉用户显式站址。
    """
    window.set_observer_site(0.0, 0.0, source="manual", persist=False)
    runtime[0]["gnss"].update(connected=True, fix=True,
                              lat_deg=1.0, lon_deg=1.0)
    path = _start(window)
    _write(window)
    window._stop_recording()

    record = _load(path)
    assert record.site["source"] == "manual"
    assert (record.site["lat_deg"], record.site["lon_deg"]) == (0.0, 0.0)
    assert "冲突" not in str(record.site.get("reason") or "")
    assert any("GNSS" in note for note in record.notes), record.notes
    plan = replay_plan(record)
    assert plan["can_replay"], plan


@pytest.mark.parametrize("busy", ["recording", "replaying", "loading"])
def test_review_busy_guard_never_stops_or_cancels_current_work(window, runtime, monkeypatch, busy):
    path = _start(window)
    _write(window)
    window._stop_recording()
    calls = []
    for name in ("_stop_tx", "_stop_recording", "_stop_replay", "_start_replay_load",
                 "_clear_replay_plan_view", "_open_observation_archive"):
        monkeypatch.setattr(window, name, lambda *a, **k: calls.append(1))
    sentinel = object()
    if busy == "recording":
        monkeypatch.setattr(window, "_recording", True)
    elif busy == "replaying":
        monkeypatch.setattr(window, "_replay_timer", sentinel)
    else:
        monkeypatch.setattr(window, "_replay_load_thread", sentinel)
    window._replay_load_cancel.clear()
    window.control_panel.record_review_button.click()
    assert calls == []
    assert not window._replay_load_cancel.is_set()
    assert "未受影响" in window.statusBar().currentMessage()
    # Restore only the state injected by this test before fixture shutdown.
    window._recording = False
    window._replay_timer = None
    window._replay_load_thread = None


def test_empty_recording_has_no_clickable_products(window, runtime):
    path = _start(window)
    window._stop_recording()
    assert not path.exists()
    assert not path.with_suffix(".observation.json").exists()
    panel = window.control_panel
    assert panel.record_product_combo.count() == 0
    assert panel.record_review_button.isHidden()
    assert not panel.record_review_button.isEnabled()
    assert "没有数据落盘" in panel.record_status.text()
    assert "没有已落盘" in panel.record_products_status.text()


def test_sidecar_failure_does_not_invent_archive_or_guess_iq_rate(window, runtime, monkeypatch):
    path = _start(window)
    iq = _write(window)
    def fail(*args, **kwargs):
        raise OSError("test sidecar unavailable")
    monkeypatch.setattr("mbdsdr_ai.baseband_io.write_sidecar", fail)
    window._stop_recording()
    panel = window.control_panel
    assert path.read_bytes() == iq.tobytes()
    assert not path.with_suffix(".observation.json").exists()
    assert "已保存" in panel.record_status.text()
    assert "sidecar" in panel.record_products_status.text()
    assert "test sidecar unavailable" in panel.record_products_status.text()
    assert "test sidecar unavailable" in window.statusBar().currentMessage()
    assert not panel.record_review_button.isEnabled()
    assert panel.record_product_combo.count() == 1


def test_archive_write_failure_keeps_successful_iq_recording(window, runtime, monkeypatch):
    path = _start(window)
    iq = _write(window)
    monkeypatch.setattr("mbdsdr_ai.observation_record.ObservationRecord.write_json",
                        lambda *a, **k: (False, "test archive write denied"))
    window._stop_recording()
    panel = window.control_panel
    assert path.read_bytes() == iq.tobytes()
    assert not path.with_suffix(".observation.json").exists()
    assert "已保存" in panel.record_status.text()
    assert "录音失败" not in panel.record_status.text()
    assert "test archive write denied" in panel.record_products_status.text()
    assert "test archive write denied" in window.statusBar().currentMessage()
    assert panel.record_review_button.isEnabled()
    assert panel.record_product_combo.currentData() == str(path)


def test_partial_archives_and_recording_abort_are_both_visible(window, runtime):
    first = _start(window)
    _write(window)
    window._on_sample_rate_changed(2_000_000)
    second = Path(window._record_file_path)
    _write(window)
    window._report_record_abort("test recording disk fault")
    assert _load(first).products[0]["complete"] is True
    assert not second.with_suffix(".observation.json").exists()
    panel = window.control_panel
    assert panel.record_product_combo.count() == 3
    assert "录音失败" in panel.record_status.text()
    assert "test recording disk fault" in panel.record_status.text()
    assert "观测档案已保存 1 份" in panel.record_products_status.text()
    assert "test recording disk fault" in panel.record_products_status.text()
    assert "test recording disk fault" in window.statusBar().currentMessage()
    assert "录制完成" not in window.statusBar().currentMessage()
    assert "不完整" in panel.record_product_combo.itemText(2)


@pytest.mark.parametrize("failure", ["cancel", "directory", "open", "backend"])
def test_new_start_failure_clears_previous_products_without_snapshot(
        window, runtime, monkeypatch, failure):
    path = _start(window)
    _write(window)
    window._stop_recording()
    assert _load(path)
    panel = window.control_panel
    assert panel.record_product_combo.count() == 2
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(window, "_show_error_dialog", lambda *a, **k: None)
    if failure == "cancel":
        monkeypatch.setattr(window, "_confirm_record_dir_safe", lambda: False)
    elif failure == "directory":
        monkeypatch.setattr(window.settings, "ensure_record_dir", lambda: (None, "test directory denied"))
    elif failure == "backend":
        window._active_sdr_backend = None
    else:
        import builtins
        original = builtins.open
        def fail_open(file, mode="r", *a, **k):
            if mode == "wb" and str(file).endswith(".iq"):
                raise OSError("test IQ open denied")
            return original(file, mode, *a, **k)
        monkeypatch.setattr(builtins, "open", fail_open)
    window._start_recording()
    assert not window._recording
    assert panel.record_review_button.isHidden()
    assert not panel.record_review_button.isEnabled()
    assert panel.record_product_combo.count() == 0
    assert len(runtime[1]) == (2 if failure == "open" else 1)


def test_snapshot_is_after_directory_confirmation_and_validation(window, runtime, monkeypatch):
    original_ensure = window.settings.ensure_record_dir
    def confirm():
        window.set_observer_site(1.0, 1.0, source="manual", persist=False)
        assert runtime[1] == []
        return True
    def ensure():
        assert runtime[1] == []
        window.set_observer_site(2.0, 2.0, source="manual", persist=False)
        return original_ensure()
    monkeypatch.setattr(window, "_confirm_record_dir_safe", confirm)
    monkeypatch.setattr(window.settings, "ensure_record_dir", ensure)
    path = _start(window)
    _write(window)
    window._stop_recording()
    assert _load(path).site["lat_deg"] == 2.0
    assert runtime[1] == [1]


def test_missing_selected_file_cannot_emit_review(window, runtime, monkeypatch):
    path = _start(window)
    _write(window)
    window._stop_recording()
    panel = window.control_panel
    archive = path.with_suffix(".observation.json")
    archive.unlink()  # Only this test's own output.
    calls = []
    panel.record_product_requested.connect(calls.append)
    panel.record_review_button.click()
    assert calls == []
    assert not panel.record_review_button.isEnabled()
    assert "不可用" in panel.record_review_button.toolTip()


def test_rate_rejection_continuation_also_has_offset(window, runtime, monkeypatch):
    first = _start(window)
    _write(window)
    monkeypatch.setattr(window._active_sdr_backend, "set_sample_rate", lambda rate: False)
    window._on_sample_rate_changed(2_000_000)
    second = Path(window._record_file_path)
    _write(window)
    window._stop_recording()
    assert first != second
    for segment in window._record_segments:
        assert datetime.fromisoformat(segment["start_time"]).utcoffset() is not None
    assert _load(first).tuning["sample_rate_hz"] == 1_000_000
    assert _load(second).tuning["sample_rate_hz"] == 1_000_000


def test_session_sidecar_error_and_successful_archives_are_both_visible(window, runtime, monkeypatch):
    from mbdsdr_ai import baseband_io
    original = baseband_io.write_sidecar
    first = _start(window)
    _write(window)
    window._on_sample_rate_changed(2_000_000)
    second = Path(window._record_file_path)
    _write(window)
    def fail_session(path, meta):
        if "segments" in meta:
            raise OSError("test session metadata denied")
        return original(path, meta)
    monkeypatch.setattr(baseband_io, "write_sidecar", fail_session)
    window._stop_recording()
    assert _load(first) and _load(second)
    panel = window.control_panel
    assert "已保存" in panel.record_status.text()
    assert "观测档案已保存 2 份" in panel.record_products_status.text()
    assert "test session metadata denied" in panel.record_products_status.text()
    assert "test session metadata denied" in window.statusBar().currentMessage()


def test_failed_segment_metadata_rebuild_never_enables_guessed_replay(window, runtime, monkeypatch):
    from mbdsdr_ai import baseband_io
    original = baseband_io.write_sidecar
    first = _start(window)
    _write(window)
    def fail_first(path, meta):
        if str(path) == str(first) and "segments" not in meta:
            raise OSError("test first segment metadata denied")
        return original(path, meta)
    monkeypatch.setattr(baseband_io, "write_sidecar", fail_first)
    window._on_sample_rate_changed(2_000_000)
    second = Path(window._record_file_path)
    _write(window)
    window._stop_recording()
    assert not first.with_suffix(".observation.json").exists()
    assert _load(second)
    assert Path(str(first) + ".json").exists(), "Existing session writer reconstructs partial metadata"
    panel = window.control_panel
    panel.record_product_combo.setCurrentIndex(panel.record_product_combo.findData(str(first)))
    assert not panel.record_review_button.isEnabled()
    calls = []
    monkeypatch.setattr(window, "_stop_tx", lambda *a, **k: calls.append("tx"))
    monkeypatch.setattr(window, "_start_replay_load", lambda p: calls.append(p))
    # Even a queued intent must recheck this product's metadata eligibility.
    panel.record_product_requested.emit(str(first))
    assert calls == []
    assert "不可用" in window.statusBar().currentMessage()
    panel.record_product_combo.setCurrentIndex(
        panel.record_product_combo.findData(str(second.with_suffix(".observation.json"))))
    panel.record_review_button.click()
    assert calls == ["tx", str(second)]


def test_set_observer_site_syncs_ai_agent_observer(window):
    """站址唯一写入口必须同步给 AI 的 Observer（否则天空工具仍按 (0,0) 算）。"""
    from mbdsdr_ai.astronomy import Observer

    old = getattr(window.ai_panel, "agent", None)
    agent = SimpleNamespace(observer=Observer())
    window.ai_panel.agent = agent
    try:
        assert window.set_observer_site(43.5, 125.5, 200.0,
                                        source="manual", persist=False)
        assert agent.observer.latitude_deg == pytest.approx(43.5)
        assert agent.observer.longitude_deg == pytest.approx(125.5)
        assert agent.observer.height_m == pytest.approx(200.0)
        # AI 天空工具的实际读点就是这里，必须直接看到同步后的站址
        from mbdsdr_ai.sky_tools import _site_from_observer
        site = _site_from_observer(agent)
        assert site["lat_deg"] == pytest.approx(43.5)
        assert site["lon_deg"] == pytest.approx(125.5)
        assert site["site_warning"] == ""
        # 清空站址：不把 agent 留在旧点，也不填 (0,0) 假坐标
        assert window.set_observer_site(None, None, persist=False)
        assert agent.observer.latitude_deg is None
        assert agent.observer.longitude_deg is None
    finally:
        window.ai_panel.agent = old


def test_sync_agent_observer_after_agent_created(window):
    """启动顺序反了（站址先到、agent 后建）也要回灌。"""
    from mbdsdr_ai.astronomy import Observer

    old = getattr(window.ai_panel, "agent", None)
    window.set_observer_site(10.0, 20.0, 30.0, source="manual", persist=False)
    agent = SimpleNamespace(observer=Observer())
    window.ai_panel.agent = agent
    try:
        window.sync_agent_observer()
        assert agent.observer.latitude_deg == pytest.approx(10.0)
        assert agent.observer.longitude_deg == pytest.approx(20.0)
        assert agent.observer.height_m == pytest.approx(30.0)
    finally:
        window.ai_panel.agent = old
