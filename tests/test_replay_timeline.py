# SPDX-License-Identifier: MIT
"""回放时间轴与「回放中改设备设置」的一致性护栏（无硬件，offscreen）。

判据：
  * 进度/时间就是**已送入解调的样本数 / 文件采样率**，每拍严格 +block；
  * 回放中改设备采样率：只记设置，不停声卡、不接实时流水线、不动时间轴，
    并明确告诉用户「回放结束后生效、本次按文件速率」；
  * 回放中调谐：拦下并说明原因（离线回放固定在文件中心频率），频率读数
    立即回到文件中心，不制造「频率显示新值、耳朵里还是旧内容」的假读数。

说明（实测事实，不是用例）：本版本回放**没有暂停/快进/跳转控件**，只有
开始与停止；`_stop_replay` 把 `_replay_pos` 归零，重新回放从头开始。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import main_window as mw  # noqa: E402
from mbdsdr_ai import baseband_io  # noqa: E402

FILE_SR = 2_400_000.0
FILE_CENTER = 100e6
DURATION_S = 0.5


class _StubBackend:
    class _Status:
        gain_db = 0.0
        demod_mode = "FM"

    class _Device:
        name = "Synthetic SDR"

    def __init__(self, sr=FILE_SR, center=FILE_CENTER):
        self._sr = float(sr)
        self._fc = float(center)
        self.status = self._Status()
        self.device = self._Device()

    def read_samples(self, n):
        return np.zeros(int(n), dtype=np.complex64)

    def get_sample_rate(self):
        return self._sr

    def set_sample_rate(self, sr):
        self._sr = float(sr)

    def get_frequency(self):
        return self._fc

    def set_frequency(self, hz):
        self._fc = float(hz)

    def set_gain(self, db):
        pass

    def get_status(self):
        import types
        return types.SimpleNamespace(connected=True, error=None)


class _FakePlayer:
    available = True

    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.written = 0

    def start(self):
        self.starts += 1
        return True

    def stop(self):
        self.stops += 1

    def set_muted(self, muted):
        pass

    def write(self, audio):
        self.written += int(np.asarray(audio).size)


@pytest.fixture(scope="module")
def qtapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp, isolated_profile, tmp_path_factory):
    w = mw.MainWindow()
    from PySide6.QtTest import QTest
    QTest.qWait(350)
    tmp = tmp_path_factory.mktemp("timeline")
    w._audio_player = _FakePlayer()
    w._demod_cfg.mode = "FM"
    w._squelch_db = -100.0
    n = int(FILE_SR * DURATION_S)
    iq = np.exp(2j * np.pi * 1000.0 * np.arange(n) / FILE_SR).astype(np.complex64)
    w._replay_file = str(tmp / "timeline.iq")
    baseband_io.save_iq(iq, w._replay_file, FILE_SR, FILE_CENTER,
                        note="timeline guard")
    yield w
    try:
        w._stop_replay()
    except Exception:  # noqa: BLE001
        pass
    try:
        w._teardown_connection()
    except Exception:  # noqa: BLE001
        pass


def _start_replay(window):
    """起播并等到定时器/数据就绪（读盘在后台线程）。"""
    from PySide6.QtTest import QTest
    window._active_sdr_backend = _StubBackend()
    window._record_sr = FILE_SR
    window._stop_replay()
    window._start_replay_load(window._replay_file)
    waited = 0
    while waited < 5000 and window._replay_timer is None:
        QTest.qWait(50)
        waited += 50
    assert window._replay_timer is not None, "回放没有起播"
    return window._replay_timer


def _ticks(window, n):
    for _ in range(n):
        window._replay_tick()


def test_timeline_position_is_exactly_samples_consumed(window):
    _start_replay(window)
    block = int(window._replay_block)
    total = int(window._replay_total)
    assert block == int(FILE_SR * 0.05)

    _ticks(window, 3)
    assert window._replay_pos == 3 * block, "进度必须是已消费样本数，不是墙钟"
    msg = window.statusBar().currentMessage()
    assert f"{3 * block / FILE_SR:.1f}/{total / FILE_SR:.1f}s" in msg

    # 尾块：不足 block 时只推进剩余样本；读完即停表并复位时间轴
    window._replay_pos = total - 10
    window._replay_tick()
    assert window._replay_timer is None, "读完后必须自行停表"
    assert window._replay_pos == 0, "停止后时间轴归零，再放从头开始"
    assert "回放完成" in window.statusBar().currentMessage()


def test_sample_rate_change_mid_replay_keeps_audio_and_timeline(
        window, monkeypatch):
    _start_replay(window)
    _ticks(window, 3)
    pos_before = window._replay_pos
    player = window._audio_player
    stops_before = player.stops
    written_before = player.written

    seen = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: seen.append(a)))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: seen.append(a)))
    window._on_sample_rate_changed(1_024_000.0)

    assert seen, "回放中改采样率必须给出说明"
    text = seen[0][2]
    assert "回放结束后生效" in text
    assert "2.400 MS/s" in text and "1.024 MS/s" in text
    assert player.stops == stops_before, "不得停声卡：停了就是「进度在走、耳朵没声」"
    assert window._pipeline is None, "不得把实时流水线接进来污染回放"
    assert window._replay_sr == FILE_SR, "回放仍按文件速率解码"
    assert window._replay_pos == pos_before, "改设置不得动时间轴"
    assert window._replay_timer is not None
    assert window._active_sdr_backend.get_sample_rate() == 1_024_000.0

    _ticks(window, 2)
    assert window._replay_pos > pos_before
    assert player.written > written_before, "改设置后回放音频必须继续产出"
    window._stop_replay()


def test_tune_mid_replay_is_blocked_and_readout_restored(window, monkeypatch):
    _start_replay(window)
    seen = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: seen.append(a)))
    backend = window._active_sdr_backend

    window._on_tune_fm(101.0)
    assert backend.get_frequency() == FILE_CENTER, "回放中不得下发设备调谐"
    assert "100.000 MHz" in window.freq_label.text()
    assert seen and "回放中不支持调谐" in seen[0][2]
    assert "100.000000 MHz" in seen[0][2]

    # 同一段回放里只提示一次，拖频谱不会刷屏
    window._on_spectrum_freq_changed(103.0)
    assert len(seen) == 1
    assert backend.get_frequency() == FILE_CENTER

    assert window._on_tune_sdr(102e6, "FM") is False
    assert backend.get_frequency() == FILE_CENTER

    window._on_module_tune(104e6)
    assert backend.get_frequency() == FILE_CENTER
    window._stop_replay()

    # 停止回放后调谐恢复可用
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    window._on_tune_fm(101.0)
    assert backend.get_frequency() == 101.0e6
