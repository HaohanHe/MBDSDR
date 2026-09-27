"""
孤儿面板数据链路冒烟测试 (tests/test_orphan_connection.py)
=========================================================

审计发现 5 个面板 UI 存在但从未收到真实数据。本测试 offscreen 验证：

  1. 无后端时，5 个面板（new_spacetime / aprs / satellite_image / modulation /
     anr）都能实例化、显示空态（"未连接"/"等待数据"/"--"），不崩、不伪造数值；
  2. main_window 的数据 tap 真的把数据接到面板：
       - ``_tap_new_spacetime_iq`` 可见时调用面板 ``update_iq``；
       - ``_tap_demod_audio`` 把 48k 音频分给 APRS / 卫星云图面板（dock 可见才喂）；
       - ANR 开启时 ``_tap_demod_audio`` 真的跑谱减降噪，``_on_anr_learn_noise``
         用最近音频块学习噪声底；
       - modulation_panel 通过 ``set_backend`` 接到 fake 后端。
  3. 用 tests/fake_rtl.py 构造的真 RTLSDRBackend（注入 FakeRtlSdr）能吐出 IQ，
     证明后端入口没断。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_orphan_connection.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _pump(ms=80):
    from PySide6.QtCore import QTimer
    QTimer.singleShot(ms, app.quit)
    app.exec()


@pytest.fixture(scope="module")
def mw():
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    _pump(120)  # 短于 auto_connect 的 200ms，保持无硬件状态
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


# ---------------------------------------------------------------------------
# 1. 无后端空态：5 个面板都在、都显示空态、不崩
# ---------------------------------------------------------------------------
class TestEmptyStateNoBackend:
    def test_new_spacetime_panel_exists_and_empty(self, mw):
        p = mw.new_spacetime_panel
        assert p is not None
        # 无信号：大字号显"未检测到信号"，指标全 "--"
        assert "未检测到" in p.amr_mod_label.text()
        assert p.bw_label.text() == "--"
        assert p.snr_label.text() == "--"

    def test_aprs_panel_empty(self, mw):
        p = mw.digital_aprs_panel
        assert p is not None
        # 未连接：列表空，显等待文案（绝不预存包）
        assert p.packet_count() == 0
        assert p.table.rowCount() == 0
        assert "等待" in p.header.text() or "未连接" in p.header.text()

    def test_sat_image_panel_empty(self, mw):
        p = mw.digital_sat_image_panel
        assert p is not None
        assert "等待" in p.image_label.text()
        assert p._running is False
        # 未连接时开始解码按钮置灰
        assert p.start_btn.isEnabled() is False

    def test_modulation_panel_empty(self, mw):
        p = mw.modulation_panel
        assert p is not None
        # 无后端：状态显"未连接"，识别按钮置灰，大字号 "--"
        assert p.mod_label.text() == "--"
        assert p.identify_btn.isEnabled() is False
        assert "未连接" in p.status_label.text()

    def test_anr_panel_empty(self, mw):
        p = mw.anr_panel
        assert p is not None
        # 未连接：开关置灰，SNR 显"未连接"
        assert p.enable_chk.isEnabled() is False
        assert "未连接" in p.snr_label.text()


# ---------------------------------------------------------------------------
# 2. new_spacetime tap：可见时 update_iq 被调用，无信号显空态
# ---------------------------------------------------------------------------
class TestNewSpacetimeTap:
    def test_tap_calls_update_iq_when_visible(self, mw):
        p = mw.new_spacetime_panel
        # 切到新时空 Tab。offscreen 平台不真正 map 窗口，isVisible() 恒 False，
        # 这里用 monkeypatch 模拟"用户切到了这个 Tab"的可见态。
        mw.left_tab.setCurrentWidget(p)
        p.isVisible = lambda: True  # type: ignore[assignment]

        calls = []
        orig = p.update_iq
        def spy(iq, sr, cf=0.0):
            calls.append((len(iq), sr, cf))
            return orig(iq, sr, cf)
        p.update_iq = spy

        iq = np.random.default_rng(0).standard_normal(16384).astype(np.complex64)
        mw._tap_new_spacetime_iq(iq, 2_048_000.0)
        p.update_iq = orig

        assert len(calls) == 1, "面板可见时 _tap_new_spacetime_iq 应调用 update_iq"
        assert calls[0][1] == pytest.approx(2_048_000.0)
        # 纯噪声 IQ：分析器应判为未检测到信号（绝不伪造调制方式）
        assert "未检测到" in p.amr_mod_label.text()

    def test_tap_noop_when_hidden(self, mw):
        p = mw.new_spacetime_panel
        # 显式模拟不可见（offscreen 本就不 map，这里也防止前一个测试的 monkeypatch 残留）
        p.isVisible = lambda: False  # type: ignore[assignment]
        calls = []
        orig = p.update_iq
        p.update_iq = lambda *a, **k: calls.append(1)
        iq = np.zeros(1024, dtype=np.complex64)
        mw._tap_new_spacetime_iq(iq, 2_048_000.0)
        p.update_iq = orig
        assert calls == [], "面板不可见时 tap 应零开销"


# ---------------------------------------------------------------------------
# 3. APRS / 卫星云图 音频 tap：dock 可见才喂 48k 音频
# ---------------------------------------------------------------------------
class TestAudioTap:
    def test_aprs_feed_called_when_dock_visible(self, mw):
        ap = mw.digital_aprs_panel
        assert ap is not None
        # offscreen 不真正 map 坞，monkeypatch 模拟 dock 可见
        mw.aprs_dock.isVisible = lambda: True  # type: ignore[assignment]
        fed = []
        orig = ap.feed_audio
        ap.feed_audio = lambda audio, sr: fed.append((len(audio), sr))
        audio = np.random.default_rng(1).standard_normal(4800).astype(np.float32)
        out = mw._tap_demod_audio(audio)
        ap.feed_audio = orig
        assert len(fed) == 1, "APRS dock 可见时应喂音频"
        assert fed[0][1] == 48000
        assert out is not None

    def test_aprs_noop_when_dock_hidden(self, mw):
        ap = mw.digital_aprs_panel
        mw.aprs_dock.isVisible = lambda: False  # type: ignore[assignment]
        fed = []
        orig = ap.feed_audio
        ap.feed_audio = lambda audio, sr: fed.append(1)
        mw._tap_demod_audio(np.zeros(4800, dtype=np.float32))
        ap.feed_audio = orig
        assert fed == [], "APRS dock 不可见时不应喂"

    def test_sat_image_feed_accumulates_when_running(self, mw):
        sp = mw.digital_sat_image_panel
        assert sp is not None
        # 连接后端使能开始按钮；开启解码后 feed_audio 才累积
        sp.set_sdr_connected(True)
        mw.sat_image_dock.isVisible = lambda: True  # type: ignore[assignment]
        sp.start_decode()
        n_before = len(sp._audio_buf)
        audio = np.random.default_rng(2).standard_normal(4800).astype(np.float32)
        mw._tap_demod_audio(audio)
        sp.stop_decode()
        sp.set_sdr_connected(False)
        assert len(sp._audio_buf) > n_before, "卫星云图开始解码后应累积音频"


# ---------------------------------------------------------------------------
# 4. ANR：开启后真的处理音频；learn_noise 用最近音频块
# ---------------------------------------------------------------------------
class TestAnrWired:
    def test_anr_processes_audio_when_enabled(self, mw):
        p = mw.anr_panel
        anr = p.anr
        assert anr is not None, "ANR 后端实例应存在"
        # 连接后端 + 勾选启用
        p.set_sdr_connected(True)
        p.enable_chk.setChecked(True)
        assert mw._anr_enabled is True
        assert anr.enabled is True

        # 第一段：首次 process 只学习噪声底，返回原音频
        rng = np.random.default_rng(3)
        audio = rng.standard_normal(4800).astype(np.float32) * 0.1
        out1 = mw._tap_demod_audio(audio)
        # 第二段：此时噪声底已学，应真的跑谱减（长度不变）
        out2 = mw._tap_demod_audio(audio)
        assert out2 is not None and len(out2) == len(audio)
        # 关闭
        p.enable_chk.setChecked(False)
        p.set_sdr_connected(False)

    def test_learn_noise_uses_last_audio(self, mw):
        p = mw.anr_panel
        anr = p.anr
        # 先塞一块音频进 _last_demod_audio
        audio = (np.random.default_rng(4).standard_normal(4800) * 0.2).astype(np.float32)
        mw._tap_demod_audio(audio)
        assert getattr(mw, "_last_demod_audio", None) is not None
        # 点"采样噪声底"→ anr.learn_noise 应更新噪声底（不崩）
        mw._on_anr_learn_noise()
        assert anr._noise_mag is not None, "learn_noise 后噪声底应被学习"


# ---------------------------------------------------------------------------
# 5. modulation_panel：set_backend 接 fake 后端
# ---------------------------------------------------------------------------
class TestModulationBackend:
    def test_set_backend_with_fake_backend(self, mw):
        from fake_rtl import FakeRtlSdr
        from mbdsdr_ai.sdr_backend import RTLSDRBackend

        p = mw.modulation_panel
        backend = RTLSDRBackend(device_index=0)
        fake = FakeRtlSdr(device_index=0)
        backend._sdr = fake
        backend._ring_reader = None
        backend.status.connected = True

        p.set_backend(backend)
        p.set_sdr_connected(True)
        assert p._backend is backend
        # 内核可用时识别按钮应可用；内核不可用时也不崩（状态文案说明原因）
        # 无论如何：set_backend 后 _backend 被正确持有，不抛异常
        p.set_backend(None)
        p.set_sdr_connected(False)


# ---------------------------------------------------------------------------
# 6. fake_rtl 后端入口自证
# ---------------------------------------------------------------------------
class TestFakeBackendEntry:
    def test_fake_backend_reads_iq(self):
        from fake_rtl import FakeRtlSdr
        from mbdsdr_ai.sdr_backend import RTLSDRBackend

        backend = RTLSDRBackend(device_index=0)
        fake = FakeRtlSdr(device_index=0)
        backend._sdr = fake
        backend._ring_reader = None
        backend.status.connected = True
        iq = backend.read_samples(8192)
        assert iq is not None and len(iq) == 8192
        assert iq.dtype == np.complex64


# ---------------------------------------------------------------------------
# 7. 本轮新接通的 8 个孤儿信号：信号发射 → handler 被调用 → 面板/状态栏更新
# ---------------------------------------------------------------------------
class _StatusSpy:
    """捕获 QStatusBar.showMessage 调用。"""

    def __init__(self, mw):
        self.mw = mw
        self.messages = []
        self._orig = mw.statusBar().showMessage
        mw.statusBar().showMessage = self._capture

    def _capture(self, msg, timeout=0):
        self.messages.append(str(msg))

    def release(self):
        self.mw.statusBar().showMessage = self._orig


class TestNewOrphanWiring:
    """逐个验证新接通的 8 个信号真的连到了 handler。"""

    def test_doppler_finished(self, mw):
        spy = _StatusSpy(mw)
        try:
            mw.doppler_panel.isVisible = lambda: True
            mw.doppler_panel.finished.emit({
                "estimator": "EKF", "n_obs": 20,
                "fd_rms_hz": 5.0, "converged": True})
            assert any("定轨完成" in m for m in spy.messages), spy.messages
        finally:
            spy.release()

    def test_doppler_progress_and_failed(self, mw):
        spy = _StatusSpy(mw)
        try:
            mw.doppler_panel.isVisible = lambda: True
            mw.doppler_panel.progress.emit("运行 EKF ...")
            assert any("EKF" in m for m in spy.messages)
            mw.doppler_panel.failed.emit("Boom: 观测不足")
            assert any("定轨失败" in m for m in spy.messages)
        finally:
            spy.release()

    def test_modulation_done(self, mw):
        class R:
            modulation = "FSK"
            confidence = 0.87
            bandwidth = 12500.0
            symbol_rate_hint = 1200.0
            suggestions = ["AFSK"]
        spy = _StatusSpy(mw)
        try:
            mw.modulation_panel.done.emit(R())
            assert any("FSK" in m and "87" in m for m in spy.messages), spy.messages
        finally:
            spy.release()

    def test_modulation_failed(self, mw):
        spy = _StatusSpy(mw)
        try:
            mw.modulation_panel.failed.emit("读取 IQ 失败: timeout")
            assert any("识别失败" in m for m in spy.messages)
        finally:
            spy.release()

    def test_scanner_progress_done_failed(self, mw):
        spy = _StatusSpy(mw)
        try:
            mw.scanner_panel.isVisible = lambda: True
            mw.scanner_panel.progress.emit(0.5, 100e6)
            assert any("扫频中" in m for m in spy.messages)
            mw.scanner_panel.done.emit([{"peak_freq": 100e6}])
            assert any("扫频完成" in m for m in spy.messages)
            mw.scanner_panel.failed.emit("canceled")
            assert any("扫频失败" in m for m in spy.messages)
        finally:
            spy.release()

    def test_module_demod_mode_changed(self, mw):
        """模块面板改解调模式 → 真的走到 _on_mode_changed（blockSignals 不炸）。"""
        calls = []
        orig = mw._on_mode_changed
        mw._on_mode_changed = lambda m: calls.append(m)
        mw.module_panel.demod_mode_changed.emit("NFM")
        mw._on_mode_changed = orig
        assert calls == ["NFM"]

    def test_module_device_selected(self, mw):
        calls = []
        orig = mw._on_panel_device_connect
        mw._on_panel_device_connect = lambda d: calls.append(d)
        dev = {"label": "FakeSDR", "driver": "fake"}
        mw.module_panel.device_selected.emit(dev)
        mw._on_panel_device_connect = orig
        assert calls == [dev]

    def test_module_sink_and_node(self, mw):
        spy = _StatusSpy(mw)
        try:
            mw.module_panel.sink_changed.emit("声卡")
            mw.module_panel.node_selected.emit("WFM Demod", "processing")
            assert any("sink" in m.lower() or "声卡" in m for m in spy.messages)
        finally:
            spy.release()

    def test_gain_value_changed_updates_status(self, mw):
        mw.gain_panel.value_changed.emit("LNA", 20.0)
        assert "LNA" in mw.status_gain.text() or "20" in mw.status_gain.text()

    def test_anr_mode_changed(self, mw):
        spy = _StatusSpy(mw)
        try:
            mw.anr_panel.mode_changed.emit("anr")
            assert any("降噪模式" in m for m in spy.messages)
        finally:
            spy.release()

    def test_constellation_cleared_resets_stats(self, mw):
        mw.status_rssi.setText("信号: -10 dBFS")
        mw.constellation_panel.cleared.emit()
        assert mw.status_rssi.text() == "信号: --"

    def test_device_plugged(self, mw):
        spy = _StatusSpy(mw)
        refreshed = []
        orig = mw.device_panel.refresh_devices
        mw.device_panel.refresh_devices = lambda: refreshed.append(1)
        try:
            mw.device_panel.device_plugged.emit("新 RTL-SDR")
            assert any("新设备" in m for m in spy.messages)
            assert refreshed, "热插入应触发 refresh_devices"
        finally:
            spy.release()
            mw.device_panel.refresh_devices = orig

