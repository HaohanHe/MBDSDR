# SPDX-License-Identifier: MIT
"""防呆守卫：破坏性操作确认 / 参数越界前置拦截 / 重复点击幂等。

覆盖本轮「UI 操作逻辑 + 防呆」中可在 ControlPanel / ScannerPanel 内闭环的项：

  D1  频率输入越界：键入超出 10 kHz–6 GHz 时**拒绝调谐并明确告知**，
      不再静默夹到边界后照常下发（旧行为 = 把 7 GHz 伪装成 6 GHz 调谐成功）。
  D2  扫描结果清空：已有结果时点「开始扫描」必须先确认，取消则原结果保留。
  D3  重复点击「开始扫描」：扫描线程在跑时再次点击不新建 worker（幂等）。
  D4  无设备时射频控件（调谐/模式/AGC/采样率/增益/静噪）一律置灰，
      而不是可点后再报错。

反证：删掉对应守卫后，D1 会重新收到 tune 信号、D2 结果被清空、
D3 worker 被替换——测试即红（见提交说明里的变异记录）。

运行:
    python -m pytest tests/test_destructive_confirm_guards.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import logging  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_log = logging.getLogger(__name__)

app = QApplication.instance() or QApplication(sys.argv)


def _control_panel():
    from control_panel import ControlPanel
    cp = ControlPanel()
    cp.set_sdr_connected(True)
    return cp


def _scanner_panel():
    from scanner_panel import ScannerPanel
    return ScannerPanel()


# ---------------------------------------------------------------- D1 频率越界
def test_typed_out_of_range_frequency_is_rejected(monkeypatch):
    """7 GHz 超出 6 GHz 上限：不发调谐、不改内部真值、给出明确告警。"""
    cp = _control_panel()
    tuned = []
    cp.tune_sdr_requested.connect(lambda hz, mode: tuned.append(hz))
    warned = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **k: warned.append(a[2] if len(a) > 2 else ""))

    cp.freq_input.setText("7000M")
    cp._on_freq_input()

    assert tuned == [], "越界输入不得下发调谐（旧行为会夹到 6000 MHz）"
    assert cp._freq_hz is None, "越界输入不得写入内部频率真值"
    assert warned, "越界输入必须明确告知用户，而不是静默夹边界"


def test_typed_below_min_frequency_is_rejected(monkeypatch):
    """低于 10 kHz 下限同样拒绝，不留「夹到 10 kHz 算成功」的假象。"""
    cp = _control_panel()
    tuned = []
    cp.tune_sdr_requested.connect(lambda hz, mode: tuned.append(hz))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    cp.freq_input.setText("1k")
    cp._on_freq_input()

    assert tuned == []
    assert cp._freq_hz is None


def test_typed_in_range_frequency_still_tunes():
    """正常范围不受影响（防止守卫把合法输入也拦掉）。"""
    cp = _control_panel()
    tuned = []
    cp.tune_sdr_requested.connect(lambda hz, mode: tuned.append(hz))

    cp.freq_input.setText("144.390")
    cp._on_freq_input()

    assert tuned and abs(tuned[-1] - 144_390_000.0) < 1.0
    assert abs(cp._freq_hz - 144_390_000.0) < 1.0


# ---------------------------------------------------------------- D2 扫描清空
def test_rescan_with_existing_results_requires_confirmation(monkeypatch):
    """已有结果 → 开始新扫描前必须确认；选「否」时结果原样保留、不起线程。"""
    panel = _scanner_panel()
    panel.result_list.addItem("87.500 MHz  -80 dBm")
    panel.result_list.addItem("99.500 MHz  -75 dBm")
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: (asked.append(a[2] if len(a) > 2 else ""),
                         QMessageBox.No)[1])

    panel._on_start_clicked()

    assert asked, "有结果时未弹确认"
    assert panel.result_list.count() == 2, "取消后不得清空已有结果"
    assert panel._worker is None, "取消后不得启动扫描线程"


def test_rescan_without_results_starts_without_prompt(monkeypatch):
    """无结果时不应多问一句（避免每次扫描都被打扰）。"""
    import scanner_panel as sp_mod
    from PySide6.QtCore import QObject, Signal

    class _WorkerStub(QObject):
        progress = Signal(float, float)
        done = Signal(list)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()
            self.started = 0

        def start(self):
            self.started += 1

        def isRunning(self):  # noqa: N802 - 模拟 QThread 接口
            return False

        def cancel(self):
            pass

    monkeypatch.setattr(sp_mod, "_ScanWorker", _WorkerStub)
    panel = _scanner_panel()
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: asked.append(a) or QMessageBox.No)

    panel._on_start_clicked()

    assert asked == [], "无已有结果时不应弹「清空确认」"
    assert panel._worker is not None and panel._worker.started == 1


# ---------------------------------------------------------------- D3 重复点击
def test_start_scan_is_idempotent_while_running(monkeypatch):
    """扫描线程在跑时再次点击「开始扫描」→ 不替换、不新建 worker。"""
    panel = _scanner_panel()

    class _RunningWorker:
        def isRunning(self):  # noqa: N802 - 模拟 QThread 接口
            return True

    stub = _RunningWorker()
    panel._worker = stub
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: pytest_fail("扫描中不应弹确认"))

    panel._on_start_clicked()

    assert panel._worker is stub, "扫描中重复点击不得新建 worker"


def pytest_fail(msg):
    raise AssertionError(msg)


# ---------------------------------------------------------------- D4 无设备置灰
def test_rf_controls_disabled_without_device():
    """未连接设备时调谐/模式/AGC/采样率/增益/静噪全部置灰。"""
    from control_panel import ControlPanel
    cp = ControlPanel()
    cp.set_sdr_connected(False)

    for name in ("freq_input", "tune_button", "mode_combo", "agc_check",
                 "agc_speed_combo", "sample_rate_combo", "gain_slider",
                 "squelch_slider", "record_button"):
        widget = getattr(cp, name)
        assert widget.isEnabled() is False, f"{name} 在无设备时应置灰"

    cp.set_sdr_connected(True)
    assert cp.freq_input.isEnabled() is True
    assert cp.agc_check.isEnabled() is True


# ======================================================================
# M 组：MainWindow 破坏性动作（发射中断开/切设备/关窗）
#       + 录音文件同名不覆盖
# ======================================================================
@pytest.fixture(scope="module")
def window(isolated_profile):
    import main_window as mw
    w = mw.MainWindow()
    yield w, mw
    w._tx_session = None
    try:
        w._teardown_connection()
    except Exception as exc:  # noqa: BLE001 - Qt 对象可能已随 app 销毁；
        # 收尾失败必须留证，不静默（用例主体结果已定，这里只记录清理异常）
        _log.warning("用例收尾失败（断开连接）：%s: %s",
                     type(exc).__name__, exc)


def test_disconnect_button_while_tx_cancel_keeps_transmitting(window,
                                                              monkeypatch):
    """发射中点「断开」→ 必须确认；选「否」不拆连接、不停波。"""
    w, mw = window
    asked = []
    monkeypatch.setattr(
        mw.QMessageBox, "question",
        lambda *a, **k: (asked.append(a), mw.QMessageBox.No)[1])
    torn = []
    monkeypatch.setattr(w, "_teardown_connection",
                        lambda **k: torn.append(1))
    w._tx_session = object()
    try:
        w._on_disconnect_clicked()
        assert asked, "发射中断开必须先确认"
        assert "发射" in str(asked[0][1] + asked[0][2])
        assert torn == [], "取消后不得拆连接/停波"
    finally:
        w._tx_session = None


def test_disconnect_button_while_tx_confirmed_tears_down(window, monkeypatch):
    """确认断开后走原拆除路径（停波由 _teardown_connection 负责）。"""
    w, mw = window
    monkeypatch.setattr(
        mw.QMessageBox, "question",
        lambda *a, **k: mw.QMessageBox.Yes)
    torn = []
    monkeypatch.setattr(w, "_teardown_connection",
                        lambda **k: torn.append(1))
    w._tx_session = object()
    try:
        w._on_disconnect_clicked()
        assert torn == [1], "确认后必须真正断开"
    finally:
        w._tx_session = None


def test_close_while_tx_cancel_keeps_window(window, monkeypatch):
    """发射中关窗 → 必须确认；选「否」窗口保持打开。"""
    from PySide6.QtGui import QCloseEvent
    w, mw = window
    asked = []
    monkeypatch.setattr(
        mw.QMessageBox, "question",
        lambda *a, **k: (asked.append(a), mw.QMessageBox.No)[1])
    w._tx_session = object()
    try:
        event = QCloseEvent()
        w.closeEvent(event)
        assert asked, "发射中关窗必须先确认"
        assert event.isAccepted() is False, "取消后窗口必须保持打开"
    finally:
        w._tx_session = None


def test_close_while_tx_confirmed_exits(window, monkeypatch):
    """确认关窗后继续原退出流程（断开路径会停波）。"""
    from PySide6.QtGui import QCloseEvent
    w, mw = window
    monkeypatch.setattr(
        mw.QMessageBox, "question",
        lambda *a, **k: mw.QMessageBox.Yes)
    monkeypatch.setattr(w, "_release_runtime_resources", lambda: None)
    monkeypatch.setattr(w, "_save_window_state", lambda: None)
    monkeypatch.setattr(w, "_save_gui_config", lambda: None)
    monkeypatch.setattr(w, "settings", type("S", (), {"flush": lambda self: None})())
    torn = []
    monkeypatch.setattr(w, "_teardown_connection",
                        lambda **k: torn.append(1))
    w._tx_session = object()
    try:
        event = QCloseEvent()
        w.closeEvent(event)
        assert event.isAccepted() is True
        assert torn == [1], "确认关窗后必须走断开路径（停波）"
    finally:
        w._tx_session = None


def test_switch_device_while_tx_cancel_keeps_current(window, monkeypatch):
    """发射中切设备 → 确认选「否」时不拆旧连接、不连新设备。"""
    w, mw = window
    asked = []
    monkeypatch.setattr(
        mw.QMessageBox, "question",
        lambda *a, **k: (asked.append(a), mw.QMessageBox.No)[1])
    torn = []
    monkeypatch.setattr(w, "_teardown_connection",
                        lambda **k: torn.append(1))

    class _OldBackend:
        pass

    class _NewBackend:
        def connect(self):
            raise AssertionError("取消切换后不得连接新设备")

    w._active_sdr_backend = _OldBackend()
    w._tx_session = object()
    try:
        ok = w._connect_backend(_NewBackend())
        assert ok is False, "取消切换必须返回失败"
        assert asked, "发射中切设备必须先确认"
        assert torn == [], "取消后不得拆旧连接"
    finally:
        w._tx_session = None
        w._active_sdr_backend = None


def test_switch_device_keeps_current_tuning(window, monkeypatch):
    """切换设备沿用切换前的调谐，不得退回落 98.5 MHz 默认。"""
    w, mw = window
    w.control_panel.set_sdr_connected(True)
    w.control_panel._freq_hz = 144_390_000.0
    captured = {}

    class _OldBackend:
        pass

    class _NewBackend:
        device = type("Dev", (), {"name": "new-dev", "max_gain": 40.0})()
        status = type("St", (), {"connected": True, "error": "",
                                 "gain_db": 0.0})()

        def connect(self):
            return True

        def get_status(self):
            return self.status

        def set_sample_rate(self, v):
            captured["sr"] = v

        def set_bandwidth(self, v):
            captured["bw"] = v

        def set_frequency(self, v):
            captured["freq"] = v

        def set_ppm(self, v):
            pass

        def set_offset_tuning(self, v):
            pass

        def set_agc(self, v):
            pass

        def set_gain(self, v):
            pass

        def disconnect(self):
            pass

    # 模拟真实 teardown：连接态清零（频率真值随之清空）
    monkeypatch.setattr(
        w, "_teardown_connection",
        lambda: w.control_panel.set_connected(False))
    for name in ("_panels_set_sdr_connected", "_start_iq_streams",
                 "_update_status_bar", "_refresh_tx_readout",
                 "_refresh_tx_button_state", "_show_error_status"):
        monkeypatch.setattr(w, name, lambda *a, **k: None)

    w._active_sdr_backend = _OldBackend()
    try:
        assert w._connect_backend(_NewBackend()) is True
        assert captured["freq"] == 144_390_000.0, \
            f"切换设备后频率回落到 {captured.get('freq')}"
    finally:
        w._active_sdr_backend = None
        w.control_panel.set_sdr_connected(False)
        w.control_panel._freq_hz = None


def test_record_toggle_is_idempotent(window, monkeypatch):
    """连点两次「录音」只起一次/停一次（重复点击不得重入）。"""
    w, mw = window
    calls = []

    def _fake_start():
        if not w._recording:      # 与真实 _start_recording 相同的状态副作用
            w._recording = True
            calls.append("start")

    def _fake_stop():
        if w._recording:
            w._recording = False
            calls.append("stop")

    monkeypatch.setattr(w, "_start_recording", _fake_start)
    monkeypatch.setattr(w, "_stop_recording", _fake_stop)
    w._recording = False
    try:
        w._apply_recording(True)
        w._apply_recording(True)
        assert calls == ["start"], f"重复开始录音被重入: {calls}"
        w._apply_recording(False)
        w._apply_recording(False)
        assert calls == ["start", "stop"], f"重复停止录音被重入: {calls}"
    finally:
        w._recording = False


class _FixedDateTime:
    _fixed = __import__("datetime").datetime(2026, 11, 6, 1, 23, 45)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def test_record_filename_collision_does_not_overwrite(window, monkeypatch,
                                                      tmp_path):
    """同一秒内再次开录：换名保存，旧 .iq 内容一个字节都不能少。"""
    import numpy as np
    w, mw = window
    monkeypatch.setattr(mw, "datetime", _FixedDateTime)
    monkeypatch.setattr(w.settings, "ensure_record_dir",
                        lambda: (str(tmp_path), None))
    existing = tmp_path / "baseband_20261106_012345.iq"
    existing.write_bytes(b"OLD-RECORDING")

    class _Backend:
        status = type("St", (), {"gain_db": 10.0})()
        device = type("Dev", (), {"name": "stub"})()

        def read_samples(self, n):
            return np.zeros(n, dtype=np.complex64)

        def get_sample_rate(self):
            return 1024000.0

        def get_frequency(self):
            return 145000000.0

    w._active_sdr_backend = _Backend()
    w._recording = False
    try:
        w._start_recording()
        assert w._recording is True
        new_path = w._record_file_path
        assert os.path.basename(new_path) == "baseband_20261106_012345_2.iq", \
            f"同名录音必须换名，实际 {new_path}"
        assert existing.read_bytes() == b"OLD-RECORDING", \
            "旧录音被覆盖（open('wb') 截断）"
        w._record_write_chunk(np.ones(64, dtype=np.complex64))
        w._apply_recording(False)
    finally:
        w._active_sdr_backend = None
        w._recording = False


# ======================================================================
# P 组：其余面板忙锁 / 重复点击实测
#   调制：识别 worker 在跑时重复点击必须幂等；
#   星座：无异步操作，重复「清空」是空操作；
#   ADS-B：无异步操作，重复喂同一帧不得造出两架飞机；
#   频谱：扫频 worker 在跑时重复点击不得再弹参数框；
#   卫星：跟踪中重复「开始」不得重复发 track_requested。
# ======================================================================
def test_modulation_identify_is_idempotent_while_running():
    """识别线程在跑时再点「识别」→ 不替换 worker、不新起线程、按钮置灰。"""
    import modulation_panel as mp

    panel = mp.ModulationPanel()
    panel._backend = object()
    panel._connected = True

    class _RunningWorker:
        def __init__(self):
            self.start_calls = 0

        def isRunning(self):  # noqa: N802 - 模拟 QThread 接口
            return True

        def start(self):
            self.start_calls += 1

    stub = _RunningWorker()
    panel._worker = stub
    panel._on_identify_clicked()

    assert panel._worker is stub, "识别中重复点击不得替换 worker"
    assert stub.start_calls == 0, "识别中重复点击不得再起线程"
    assert panel.identify_btn.isEnabled() is False, "识别中按钮必须置灰"


def test_constellation_clear_is_idempotent():
    """星座面板没有异步操作：连点「清空」只是重复的空操作。"""
    from constellation_panel import ConstellationPanel

    panel = ConstellationPanel()
    signals = []
    panel.cleared.connect(lambda: signals.append(1))
    panel._on_clear()
    panel._on_clear()

    assert panel.view._points == []
    assert panel.view._has_data is False
    assert signals == [1, 1], "重复清空不得改其它状态或抛异常"


def test_adsb_duplicate_frame_does_not_duplicate_aircraft():
    """ADS-B 面板无异步操作：同一 ICAO 的重复帧必须合并成一架。"""
    from adsb_panel import AdsbPanel

    panel = AdsbPanel()
    frame = {"icao": "abc123", "callsign": "CCA123", "altitude_ft": 30000}
    panel.submit_decoded_frame(frame)
    panel.submit_decoded_frame(frame)

    assert panel.aircraft_count() == 1, "同一 ICAO 重复帧不得变成两架"
    assert panel.table.rowCount() == 1


def test_spectrum_sweep_is_idempotent_while_running(window, monkeypatch):
    """扫频在跑时再点「扫频找台」→ 直接返回，不再弹参数框、不重起 worker。"""
    w, mw = window

    class _RunningSweep:
        def isRunning(self):  # noqa: N802 - 模拟 QThread 接口
            return True

        def cancel(self):
            pass

        def wait(self, *_):
            pass

    class _Backend:
        def read_samples(self, n):
            return []

        def set_frequency(self, hz):
            pass

    class _SweepDialogSpy:
        """扫频进行中若仍构造参数框，说明忙锁失守 → 直接失败（不阻塞事件循环）。"""

        def __init__(self, *a, **k):
            raise AssertionError("扫频在跑时不得再弹参数框")

    stub = _RunningSweep()
    w._sweep_worker = stub
    w._active_sdr_backend = _Backend()
    monkeypatch.setattr(mw, "QDialog", _SweepDialogSpy)
    try:
        w._start_sweep()
        assert w._sweep_worker is stub, "扫频中重复点击不得重起/替换 worker"
    finally:
        w._sweep_worker = None
        w._active_sdr_backend = None


def test_sat_track_start_is_idempotent_while_tracking():
    """跟踪中重复「开始」→ 幂等不发请求（面板层，菜单/外部调用也走这里）。"""
    from sat_track_panel import SatTrackPanel

    panel = SatTrackPanel()
    panel.populate_satellites(["NOAA 19"])
    sent = []
    panel.track_requested.connect(lambda name, hz: sent.append(name))

    panel._on_start_clicked()
    assert sent == ["NOAA 19"], "首次开始应发一次请求"

    panel.set_tracking_state(True)
    panel._on_start_clicked()
    assert sent == ["NOAA 19"], "跟踪中重复开始不得再发请求"

    panel.set_tracking_state(False)
    panel._on_start_clicked()
    assert sent == ["NOAA 19", "NOAA 19"], "停止后应能再次开始"


# ======================================================================
# Q 组：误触收口（回放连点 / 跟踪中关窗）
# ======================================================================
def test_replay_second_click_stops_without_file_dialog(window, monkeypatch):
    """回放中再点「回放」→ 只停当前回放，绝不再弹文件框。

    旧行为：先 _stop_replay 再 getOpenFileName；用户点取消 = 误触停播，
    且界面没有显式「停止回放」入口。二次点击现在是确定的停止动作。
    """
    w, mw = window
    stopped, dialogs = [], []
    monkeypatch.setattr(w, "_stop_replay", lambda: stopped.append(1))
    monkeypatch.setattr(
        mw.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: dialogs.append(1) or ("", "")))
    w._replay_timer = object()
    try:
        w._replay_recording()
        assert stopped == [1], "回放中再点必须先停止回放"
        assert dialogs == [], "停止回放后不得再弹文件框"
        assert "已停止回放" in w.statusBar().currentMessage(), \
            "停止后必须给出可逆操作说明"
    finally:
        w._replay_timer = None


def test_satellite_start_is_idempotent_while_tracking(window, monkeypatch):
    """跟踪中重复开始同一颗星：不重建定时器、不覆盖跟踪前频率。"""
    w, mw = window
    assert mw.LoopSatTracker is not None, "前提：测试环境装有 skyfield"

    class _Tracker:
        selected_name = "NOAA 19"
        ground_station = None

        def select_satellite(self, name):
            return True

        def set_downlink_freq(self, hz):
            pass

    class _Timer:
        def __init__(self):
            self.starts = 0

        def isActive(self):  # noqa: N802 - 模拟 QTimer 接口
            return True

        def start(self):
            self.starts += 1

        def stop(self):
            pass

    tracker, timer = _Tracker(), _Timer()
    monkeypatch.setattr(w, "_poll_satellite_track", lambda: None)
    w._loop_sat_tracker = tracker
    w._sat_track_timer = timer
    w._observer_lat, w._observer_lon = 30.0, 120.0
    w._vfo_center_hz = 145_000_000.0
    w._sat_track_prev_freq_hz = 145_000_000.0
    try:
        w._start_satellite_tracking("NOAA 19", 137.1e6)
        assert timer.starts == 0, "跟踪中重复开始不得重建/重启定时器"
        assert w._sat_track_prev_freq_hz == 145_000_000.0, \
            "重复开始不得覆盖「跟踪前频率」（否则停止回不到原调谐）"
        assert "忽略重复开始" in w.statusBar().currentMessage()
    finally:
        w._loop_sat_tracker = None
        w._sat_track_timer = None
        w._observer_lat = w._observer_lon = None
        w._sat_track_prev_freq_hz = None


def test_close_while_tracking_restores_tuning_before_save(window, monkeypatch):
    """跟踪中关窗：先停跟踪（写回原调谐）再落盘，否则下次启动停在卫星频点。

    重判结论：跟踪停止不丢录音/数据（只停定时器 + 云台 + 写回频率），
    真正会丢的是「跟踪前调谐」——旧顺序先 flush/_save_gui_config 再
    _disconnect，保存的是多普勒频点。故修顺序，不加二次确认。
    """
    from PySide6.QtGui import QCloseEvent
    w, mw = window
    order = []
    monkeypatch.setattr(w, "_stop_satellite_tracking",
                        lambda: order.append("stop_track"))
    monkeypatch.setattr(w, "_save_window_state",
                        lambda: order.append("save_window"))
    monkeypatch.setattr(w, "_save_gui_config", lambda: order.append("save_gui"))
    monkeypatch.setattr(w, "_disconnect", lambda: order.append("disconnect"))
    monkeypatch.setattr(w, "_release_runtime_resources", lambda: None)
    monkeypatch.setattr(w, "settings",
                        type("S", (), {"flush": lambda self: order.append("flush")})())
    w._tx_session = None
    w._recording = False
    try:
        w.closeEvent(QCloseEvent())
        assert "stop_track" in order, "跟踪中关窗必须先停跟踪并写回原调谐"
        assert order.index("stop_track") < order.index("flush"), \
            "先停跟踪再 flush，否则保存的是多普勒频点"
        assert order.index("stop_track") < order.index("save_gui"), \
            "先停跟踪再存运行参数"
    finally:
        w._tx_session = None
        w._recording = False
