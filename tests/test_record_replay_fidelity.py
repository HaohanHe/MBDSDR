# SPDX-License-Identifier: MIT
"""录音 → 回放端到端判据（合成已知信号，无硬件）。

判据不是「能录能放」，而是**录下来的东西回放，和当时听到的是同一个东西**：
  1. 同一段合成 IQ 经桌面录音落盘、再由 ``load_iq`` 读回，样本必须逐样本一致；
  2. 用产品解调链（``_demod_and_play``）分别解调「原始流」与「回放流」，
     解调音频的信噪比不得有可见退化（本实现是无损 cf32，实测相同）；
  3. 合成 BPSK（已知比特）经同一条落盘/读回路径后，符号判决错误率必须为 0。

同时护栏化两条「不能只看到没声」的归因：
  * 文件采样率与当前设置不同 → 必须同时报出两个速率；
  * 缺 sidecar / sidecar 声明格式与扩展名冲突 → 必须说明按什么在回放；
  * 录音中途中止 → sidecar 记 complete=false，回放端提示「文件不完整」。

反证（改坏必须红）：
  * 把 ``_record_write_chunk`` 改成写 float64 或做归一化，样本一致性用例红；
  * 把 ``replay_source_notice`` 的速率分支删掉，归因用例红；
  * 把 ``_record_segment_meta`` 的 complete/aborted_reason 删掉，半成品用例红。
"""
import json
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

SR = 1_024_000.0


class _StubBackend:
    """录音/回放只需要速率、中心频率与读样本接口。"""

    class _Status:
        gain_db = 0.0
        demod_mode = "FM"

    class _Device:
        name = "Synthetic SDR"

    def __init__(self, sr=SR, center=100.0e6):
        self._sr = float(sr)
        self._fc = float(center)
        self.status = self._Status()
        self.device = self._Device()

    def read_samples(self, n):
        return np.zeros(int(n), dtype=np.complex64)

    def get_sample_rate(self):
        return self._sr

    def get_frequency(self):
        return self._fc

    def set_sample_rate(self, sr):
        self._sr = float(sr)

    def set_frequency(self, hz):
        self._fc = float(hz)

    def set_gain(self, db):
        pass


class _FakePlayer:
    """把写进声卡的音频留下来比对（真实 AudioPlayer 在 CI 里不可用）。"""

    available = True

    def __init__(self):
        self.written = []
        self.muted = False

    def start(self):
        return True

    def stop(self):
        pass

    def set_muted(self, muted):
        self.muted = bool(muted)

    def write(self, audio):
        self.written.append(np.asarray(audio, dtype=np.float32).copy())

    def take(self):
        if not self.written:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.written)


def _synth_fm(sr, n, tone_hz=1000.0, dev_hz=20000.0):
    """已知内容的 FM 单音：相位 = 2π·(dev/tone)·sin(2π·tone·t)。"""
    t = np.arange(n, dtype=np.float64) / sr
    phase = 2.0 * np.pi * (dev_hz / tone_hz) * np.sin(2.0 * np.pi * tone_hz * t)
    return np.exp(1j * phase).astype(np.complex64)


def _synth_bpsk(sr, nbits, sps=8, offset_hz=50_000.0, seed=20261113):
    """已知比特的 BPSK：矩形脉冲 × 复载波（基带），返回 (iq, bits)。"""
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, nbits).astype(np.int8)
    sym = (2.0 * bits - 1.0).astype(np.float64)
    baseband = np.repeat(sym, sps)
    t = np.arange(baseband.size, dtype=np.float64) / sr
    iq = (baseband * np.exp(2j * np.pi * offset_hz * t)).astype(np.complex64)
    return iq, bits


def _bpsk_bit_errors(iq, bits, sr, sps=8, offset_hz=50_000.0):
    """矩形脉冲匹配滤波（每符号积分）→ 符号判决 → 比特错误数。"""
    t = np.arange(iq.size, dtype=np.float64) / sr
    bb = np.asarray(iq, dtype=np.complex128) * np.exp(-2j * np.pi * offset_hz * t)
    usable = (iq.size // sps) * sps
    metric = bb[:usable].real.reshape(-1, sps).mean(axis=1)
    decided = (metric > 0).astype(np.int8)[:len(bits)]
    return int(np.count_nonzero(decided != bits[:len(decided)]))


@pytest.fixture(scope="module")
def qtapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp, isolated_profile, tmp_path_factory):
    w = mw.MainWindow()
    from PySide6.QtTest import QTest
    QTest.qWait(350)  # 让默认配置加载/设备枚举提示跑完
    w.settings.set("record_dir", str(tmp_path_factory.mktemp("rec")))
    w.settings.flush()
    w._audio_player = _FakePlayer()
    w._demod_cfg.mode = "FM"
    w._squelch_db = -100.0
    yield w
    try:
        w._stop_replay()
    except Exception:  # noqa: BLE001 - 收尾失败不掩盖用例结论
        pass
    try:
        w._teardown_connection()
    except Exception:  # noqa: BLE001
        pass


def _record(window, iq, sr=SR, chunk=65_536):
    """把一段合成 IQ 走真实桌面录音路径落盘，返回 (path, sidecar dict)。"""
    window._active_sdr_backend = _StubBackend(sr=sr)
    window._start_recording()
    assert window._recording is True
    for i in range(0, iq.size, chunk):
        window._record_write_chunk(iq[i:i + chunk])
    path = window._record_file_path
    window._stop_recording()
    assert window._recording is False
    with open(path + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    return path, meta


def _capture_demod(window, iq, sr=SR, block=None):
    """用产品解调链（VFO→模式解调→声卡）解一段 IQ，收集音频。"""
    player = window._audio_player
    player.written.clear()
    # VFO 与静噪都有历史状态：两次比对的起点必须一致。
    window._vfo = None
    window._vfo_in_sr = 0.0
    window._replay_squelch = None
    block = block or max(1024, int(sr * 0.05))
    for i in range(0, iq.size, block):
        window._demod_and_play(iq[i:i + block], sr)
    return player.take()


def _snr_db(ref, test):
    """ref 为参考、test 为被测：能量比（同一段音频逐样本）。"""
    n = min(ref.size, test.size)
    assert n > 0, "没有解调出任何音频"
    ref = ref[:n].astype(np.float64)
    test = test[:n].astype(np.float64)
    noise = float(np.sum((ref - test) ** 2))
    signal = float(np.sum(ref ** 2))
    if noise <= 1e-20:
        return float("inf")
    return 10.0 * np.log10(signal / noise)


# ---------------------------------------------------------------------------
# 1. 落盘无损
# ---------------------------------------------------------------------------
def test_recording_writes_cf32_byte_for_byte(window):
    sr = SR
    iq = _synth_fm(sr, int(sr * 0.1))
    path, meta = _record(window, iq, sr)

    assert os.path.getsize(path) == iq.size * 8, "cf32 必须是 8 字节/样本"
    raw = np.fromfile(path, dtype=np.float32)
    back = (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)
    assert back.size == iq.size
    assert np.array_equal(back, iq), "录音落盘必须逐样本无损"
    assert meta["samples"] == iq.size
    assert meta["sample_rate"] == sr
    assert meta["format"] == "cf32"
    assert meta["complete"] is True
    assert "aborted_reason" not in meta


# ---------------------------------------------------------------------------
# 2. 端到端：录音 → 回放 的解调结果与原始流同一
# ---------------------------------------------------------------------------
def test_replay_demod_has_no_visible_degradation(window):
    sr = SR
    iq = _synth_fm(sr, int(sr * 0.2))
    path, _meta = _record(window, iq, sr)

    ref_audio = _capture_demod(window, iq, sr)
    rec = baseband_io.load_iq(path)
    assert rec.get("error") is None
    assert rec["samples"] == iq.size and rec["complete"] is True
    rep_audio = _capture_demod(window, rec["iq"], sr)

    assert float(np.sqrt(np.mean(ref_audio ** 2))) > 1e-3, "参考流本身没有声音"
    snr = _snr_db(ref_audio, rep_audio)
    assert snr >= 60.0, f"录音→回放后解调音频退化：SNR={snr:.1f} dB"


def test_bpsk_symbol_errors_stay_zero_after_roundtrip(window):
    sr = SR
    iq, bits = _synth_bpsk(sr, 2000)
    path, _meta = _record(window, iq, sr)

    errors_src = _bpsk_bit_errors(iq, bits, sr)
    rec = baseband_io.load_iq(path)
    errors_rep = _bpsk_bit_errors(rec["iq"], bits, sr)
    assert errors_src == 0, f"合成信号自身判决有误（{errors_src} 位）"
    assert errors_rep == 0, f"录音→回放引入符号错误 {errors_rep} 位"


# ---------------------------------------------------------------------------
# 3. 速率 / 格式 / 完整性归因（不能只剩「没声」）
# ---------------------------------------------------------------------------
def test_rate_mismatch_notice_names_file_and_current_rates():
    rec = {"sample_rate": 2_400_000.0, "format": "cf32", "path": "x.iq",
           "metadata_missing": False, "sample_rate_assumed": False,
           "complete": True, "samples": 100}
    notice = mw.replay_source_notice(rec, 1_024_000.0)
    assert notice is not None, "速率不一致必须给出归因"
    _kind, text = notice
    assert "2.400 MS/s" in text
    assert "1.024 MS/s" in text
    assert "回放按文件自身的采样率" in text


def test_same_rate_is_silent():
    rec = {"sample_rate": SR, "format": "cf32", "path": "x.iq",
           "metadata_missing": False, "sample_rate_assumed": False,
           "complete": True}
    assert mw.replay_source_notice(rec, SR) is None, "同速率不该打扰用户"


def test_missing_sidecar_is_attributed_as_assumed_rate():
    rec = {"sample_rate": 2_400_000.0, "format": "cf32", "path": "x.iq",
           "metadata_missing": True, "sample_rate_assumed": True,
           "complete": True}
    kind, text = mw.replay_source_notice(rec, SR)
    assert kind == "warning"
    assert "没有 sidecar" in text
    assert "默认 2.400 MS/s" in text


def test_sidecar_format_conflict_is_attributed():
    rec = {"sample_rate": SR, "format": "cf32", "path": "capture.wav",
           "metadata_missing": False, "sample_rate_assumed": False,
           "complete": True}
    kind, text = mw.replay_source_notice(rec, None)
    assert kind == "warning"
    assert "sidecar 声明格式 cf32" in text
    assert "扩展名指向 wav" in text


def test_begin_replay_shows_mismatch_dialog(window, monkeypatch, tmp_path):
    """真实入口：起播前必须把「文件速率 vs 当前设置」弹给用户。"""
    rec = {"iq": np.ones(4096, dtype=np.complex64),
           "sample_rate": 2_400_000.0, "center_freq_hz": 100e6,
           "duration_s": 0.01, "truncated": False, "segments": [],
           "format": "cf32", "metadata_missing": False,
           "sample_rate_assumed": False, "complete": True, "samples": 4096}
    window._active_sdr_backend = _StubBackend(sr=1_024_000.0)
    seen = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: seen.append(a)))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: seen.append(a)))
    path = tmp_path / "mismatch.iq"
    path.write_bytes(np.zeros(4096, dtype=np.complex64).tobytes())
    try:
        window._begin_replay_from_rec(rec, str(path))
        assert seen, "速率不一致时没有弹出任何说明"
        text = seen[0][2]
        assert "2.400 MS/s" in text and "1.024 MS/s" in text
    finally:
        window._stop_replay()


# ---------------------------------------------------------------------------
# 4. 中断：不留「看起来完整、实际截断」的文件
# ---------------------------------------------------------------------------
class _PartialFail:
    """先委托真实文件写 ``ok_bytes``，再抛 ENOSPC（模拟写一半磁盘满）。"""

    def __init__(self, fh, ok_bytes=1003):
        self._fh = fh
        self._left = int(ok_bytes)

    def write(self, data):
        if self._left > 0:
            n = min(self._left, len(data))
            self._fh.write(data[:n])
            self._left -= n
        raise OSError(28, "No space left on device")

    def close(self):
        self._fh.close()


def test_abort_marks_sidecar_incomplete_and_says_so(window):
    window._active_sdr_backend = _StubBackend(sr=SR)
    window._start_recording()
    assert window._recording is True
    window._record_write_chunk(np.ones(4096, dtype=np.complex64))
    path = window._record_file_path
    window._record_stream_fh = _PartialFail(window._record_stream_fh, 1003)
    window._record_write_chunk(np.ones(4096, dtype=np.complex64))

    assert window._recording is False
    assert os.path.exists(path), "已写部分要保留，不静默丢弃"
    with open(path + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["complete"] is False, "中止的半成品必须显式标 complete=false"
    assert "No space left on device" in meta["aborted_reason"]
    size = os.path.getsize(path)
    assert size == meta["samples"] * 8, "sidecar 样本数必须等于磁盘实际内容"
    assert size % 8 == 0, "尾部残字节必须截掉"

    msg = window.statusBar().currentMessage()
    assert "文件不完整" in msg
    assert "No space left on device" in msg

    rec = baseband_io.load_iq(path)
    assert rec["complete"] is False
    notice = mw.replay_source_notice(rec, SR)
    assert notice is not None
    kind, text = notice
    assert kind == "warning"
    assert "文件不完整" in text
    assert "No space left on device" in text
