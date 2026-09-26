"""
SDR 真实数据链路测试
=====================
验证数据处理链路的回归测试（确定性输入，无需真实硬件/声卡）：

(A) 后端读取契约：确定性后端 read_samples 返回 complex64；未连接返回 None；
    采样率可读；主窗口归一化到 complex64 不丢样本。
(B) 频谱控件真做 FFT：SpectrumDataGenerator.push_iq 对已知复音，峰值落在
    正确的频率 bin；无数据时频谱为 NaN。
(C) baseband 录制真存盘：baseband_io.save_iq + load_iq round-trip，虚部不丢。
(D) 声卡实时输出：AudioPlayer 在无 sounddevice 时安全降级，available=False。
(E) 主窗口录制/断开流程：录制真落盘、断开后频谱清空。

说明：这里的确定性后端只在测试内生成已知 IQ 以验证处理管线，它不进入设备
枚举、不在 UI 中显示为真实设备，与"无硬件不造假"的产品原则不冲突。

运行（offscreen Qt）：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_sdr_pipeline.py -v
"""
import os
import sys
import json
import time

# 必须在 import PySide6 之前设置 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

# 仓库根 + desktop 目录
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# 确定性测试后端：循环输出一段已知复数 IQ（实部、虚部都非零）
# ---------------------------------------------------------------------------
class DeterministicIQBackend:
    """测试专用后端，不进设备枚举、不进 UI。

    循环输出一段已知 100kHz 复音，用于验证读取/归一化/录制链路。
    实现主窗口依赖的最小后端接口：connect/disconnect、read_samples、
    get_sample_rate、get_status。
    """

    def __init__(self, sample_rate: float = 2_400_000.0, n: int = 16384):
        from mbdsdr_ai.sdr_backend import SDRDevice, SDRBackend
        dev = SDRDevice(
            device_type="test", device_id="test-0", name="deterministic-test",
            frequency_range=(1e6, 2e9), sample_rate_range=(1e5, 3.2e6),
            max_gain=40.0)
        self._backend = SDRBackend(dev)
        self.device = dev
        self.status = self._backend.status
        self._sr = float(sample_rate)
        t = np.arange(n) / self._sr
        self._iq = np.exp(1j * 2 * np.pi * 100_000.0 * t).astype(np.complex64)
        self._pos = 0
        self.status.sample_rate_hz = self._sr
        self.status.frequency_hz = 98_500_000.0

    def connect(self) -> bool:
        self.status.connected = True
        self._backend._start_time = time.time()
        return True

    def disconnect(self):
        self.status.connected = False

    def get_sample_rate(self) -> float:
        return self._sr

    def get_status(self):
        return self._backend.get_status()

    def read_samples(self, num_samples: int):
        if not self.status.connected:
            return None
        total = len(self._iq)
        chunks = []
        idx = self._pos
        remaining = int(num_samples)
        while remaining > 0:
            take = min(remaining, total - idx)
            chunks.append(self._iq[idx:idx + take])
            idx = (idx + take) % total
            remaining -= take
        self._pos = idx
        self._backend._samples_read += int(num_samples)
        return np.concatenate(chunks)[:num_samples].astype(np.complex64)


# ---------------------------------------------------------------------------
# (A) 后端读取契约
# ---------------------------------------------------------------------------
class TestBackendReadIQ:
    def test_read_samples_complex64(self):
        b = DeterministicIQBackend()
        assert b.connect() is True
        iq = np.asarray(b.read_samples(4096))
        assert np.iscomplexobj(iq), f"期望复数 IQ，实际 dtype={iq.dtype}"
        assert len(iq) == 4096
        # 真复数：实部/虚部都不该全为 0
        assert np.any(iq.real != 0)
        assert np.any(iq.imag != 0)
        # 主窗口归一化链路：转 complex64 不丢样本
        normed = np.asarray(iq, dtype=np.complex64)
        assert normed.dtype == np.complex64
        assert len(normed) == 4096

    def test_read_samples_disconnected_returns_none(self):
        b = DeterministicIQBackend()  # 不 connect
        assert b.read_samples(1024) is None

    def test_get_sample_rate_exists(self):
        b = DeterministicIQBackend()
        sr = b.get_sample_rate()
        assert isinstance(sr, float) and sr > 0


# ---------------------------------------------------------------------------
# (B) SpectrumDataGenerator.push_iq 真做 FFT
# ---------------------------------------------------------------------------
class TestSpectrumRealFFT:
    def test_tone_appears_at_correct_bin(self):
        from spectrum_widget import SpectrumDataGenerator
        gen = SpectrumDataGenerator(num_bins=512)
        sr = 2_400_000.0
        n = gen.fft_size
        t = np.arange(n) / sr
        center_bin = gen.num_bins // 2

        # +100 kHz 复音 → 峰值应在中心 bin 右侧
        tone = np.exp(1j * 2 * np.pi * 100_000.0 * t).astype(np.complex64)
        gen.push_iq(tone, 98_500_000.0, sr)
        assert gen.has_data() is True
        peak_bin = int(np.argmax(gen.spectrum))
        assert peak_bin > center_bin + 5, \
            f"+100kHz 峰值应在中心右侧，实际 peak_bin={peak_bin}"

        # 换 -100 kHz → 峰值应在中心左侧
        gen.clear_data()
        tone2 = np.exp(1j * 2 * np.pi * (-100_000.0) * t).astype(np.complex64)
        gen.push_iq(tone2, 98_500_000.0, sr)
        peak_bin2 = int(np.argmax(gen.spectrum))
        assert peak_bin2 < center_bin - 5, \
            f"-100kHz 峰值应在中心左侧，实际 peak_bin2={peak_bin2}"

    def test_no_data_shows_nan(self):
        from spectrum_widget import SpectrumDataGenerator
        gen = SpectrumDataGenerator(num_bins=512)
        assert gen.has_data() is False
        # 无数据：频谱全为 NaN（不画谱线）
        assert np.isnan(gen.spectrum).all()
        # 喂 <64 样本不触发
        gen.push_iq(np.zeros(32, dtype=np.complex64), 98_500_000.0, 2_400_000.0)
        assert gen.has_data() is False


# ---------------------------------------------------------------------------
# (C) baseband_io save_iq + load_iq round-trip
# ---------------------------------------------------------------------------
class TestBasebandRoundTrip:
    def test_roundtrip_imag_not_lost(self, tmp_path):
        from mbdsdr_ai.baseband_io import save_iq, load_iq
        rng = np.random.default_rng(42)
        n = 4096
        iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
        path = str(tmp_path / "bb_test.iq")
        save_iq(iq, path, sample_rate=2_400_000.0,
                center_freq_hz=98_500_000.0, note="pytest")
        assert os.path.exists(path + ".json")
        with open(path + ".json") as f:
            meta = json.load(f)
        assert meta["sample_rate"] == 2_400_000.0
        assert meta["center_freq_hz"] == 98_500_000.0
        assert meta["samples"] == n
        back = load_iq(path)
        back_iq = np.asarray(back["iq"])
        assert back_iq.shape == (n,)
        assert np.any(back_iq.imag != 0)
        np.testing.assert_allclose(back_iq.real, iq.real, atol=1e-7)
        np.testing.assert_allclose(back_iq.imag, iq.imag, atol=1e-7)
        assert back["sample_rate"] == 2_400_000.0
        assert back["center_freq_hz"] == 98_500_000.0

    def test_roundtrip_interleaved_input(self, tmp_path):
        from mbdsdr_ai.baseband_io import save_iq, load_iq
        rng = np.random.default_rng(7)
        n = 2048
        iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
        inter = np.empty(n * 2, dtype=np.float32)
        inter[0::2] = iq.real
        inter[1::2] = iq.imag
        path = str(tmp_path / "bb_inter.iq")
        save_iq(inter.tolist(), path, sample_rate=1_000_000.0, center_freq_hz=100e6)
        back_iq = np.asarray(load_iq(path)["iq"])
        np.testing.assert_allclose(back_iq.real, iq.real, atol=1e-7)
        np.testing.assert_allclose(back_iq.imag, iq.imag, atol=1e-7)


# ---------------------------------------------------------------------------
# (D) AudioPlayer 无 sounddevice 时安全降级
# ---------------------------------------------------------------------------
class TestAudioPlayerDegrade:
    def test_available_false_and_write_noop(self):
        from mbdsdr_ai.audio_out import AudioPlayer
        p = AudioPlayer(sample_rate=48000, channels=1, gain=0.5)
        started = p.start()
        assert started is False
        assert p.available is False
        audio = np.zeros(1024, dtype=np.float32)
        assert p.write(audio) == 0
        p.stop()
        p.stop()

    def test_set_gain_clamp(self):
        from mbdsdr_ai.audio_out import AudioPlayer
        p = AudioPlayer()
        p.set_gain(10.0)
        assert p.gain == 5.0
        p.set_gain(-2.0)
        assert p.gain == 0.0
        p.set_gain(1.5)
        assert p.gain == 1.5


# ---------------------------------------------------------------------------
# (E) MainWindow 录制/断开流程（offscreen + 确定性后端）
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("qtapp")
class TestMainWindowRecordingFlow:
    def _make_window(self):
        import main_window as mw
        return mw.MainWindow()

    def test_record_toggle_with_backend(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        real_expanduser = os.path.expanduser

        def _fake_expanduser(p):
            # matplotlib 会传入 PosixPath（无 startswith），先转 str 再判断
            if str(p).startswith("~"):
                return str(tmp_path)
            return real_expanduser(p)

        monkeypatch.setattr(os.path, "expanduser", _fake_expanduser)
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

        w = self._make_window()
        # 无后端时开始录音应不进入录制状态、不写文件
        assert w._active_sdr_backend is None
        w._apply_recording(True)
        assert w._recording is False

        # 接上确定性后端
        b = DeterministicIQBackend()
        b.connect()
        w._active_sdr_backend = b

        w._apply_recording(True)
        assert w._recording is True
        assert w._record_file_path is not None

        for _ in range(5):
            w._poll_sdr_iq()
        assert len(w._record_iq_buffer) >= 1

        w._apply_recording(False)
        assert w._recording is False
        path = w._record_file_path
        assert os.path.exists(path), f"录制文件应存在: {path}"
        assert os.path.exists(path + ".json")
        with open(path + ".json") as f:
            meta = json.load(f)
        assert meta["sample_rate"] == b.get_sample_rate()
        assert meta["samples"] > 0
        from mbdsdr_ai.baseband_io import load_iq
        assert load_iq(path)["samples"] == meta["samples"]
        w._disconnect()

    def test_volume_changes_do_not_crash(self, qtapp):
        w = self._make_window()
        for v in (0, 15, 30, 63):
            w._on_volume_changed(v)
        if w._audio_player is not None:
            assert abs(w._audio_player.gain - 2.0) < 1e-6
        w._disconnect()

    def test_disconnect_clears_spectrum(self, qtapp):
        w = self._make_window()
        b = DeterministicIQBackend()
        b.connect()
        w._active_sdr_backend = b
        w._start_iq_streams()
        for _ in range(3):
            w._poll_sdr_iq()
        assert w.spectrum.generator.has_data() is True
        w._disconnect()
        assert w._active_sdr_backend is None
        assert w.spectrum.generator.has_data() is False, "断开后频谱应回到未连接"


# ---------------------------------------------------------------------------
# pytest fixture：保证 offscreen QApplication 只建一次
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
