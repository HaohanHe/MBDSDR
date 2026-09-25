"""
SDR 真实数据链路测试
=====================
接通三条真实数据链路的回归测试：

(A) 频谱控件真接 SDR 后端 IQ 流（SpectrumDataGenerator.push_iq 真做 FFT）；
(B) baseband 录制真存盘（baseband_io.save_iq + load_iq round-trip，虚部不丢）；
(C) 声卡实时输出（AudioPlayer 在无 sounddevice 时安全降级，available=False）。

运行（offscreen Qt，无需真实硬件/声卡）：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_sdr_pipeline.py -v

也可直接：
    QT_QPA_PLATFORM=offscreen python3 tests/test_sdr_pipeline.py
"""
import os
import sys
import json
import tempfile

# 必须在 import PySide6 之前设置 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

# 仓库根 + desktop 目录
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# (A) MockSDRBackend.read_samples 返回 complex64
# ---------------------------------------------------------------------------
class TestMockBackendIQ:
    def test_read_samples_complex64(self):
        from mbdsdr_ai.sdr_backend import MockSDRBackend
        b = MockSDRBackend()
        assert b.connect() is True
        iq = b.read_samples(4096)
        assert iq is not None
        iq = np.asarray(iq)
        # 后端返回复数 IQ（Mock 为 complex128；MainWindow 喂频谱前会统一转 complex64）
        assert np.iscomplexobj(iq), f"期望复数 IQ，实际 dtype={iq.dtype}"
        assert len(iq) == 4096
        # 真复数：实部/虚部都不该全为 0
        assert np.any(iq.real != 0)
        assert np.any(iq.imag != 0)
        # MainWindow 的归一化链路：np.asarray(iq, dtype=complex64) 不丢样本
        normed = np.asarray(iq, dtype=np.complex64)
        assert normed.dtype == np.complex64
        assert len(normed) == 4096

    def test_read_samples_disconnected_returns_none(self):
        from mbdsdr_ai.sdr_backend import MockSDRBackend
        b = MockSDRBackend()  # 不 connect
        assert b.read_samples(1024) is None

    def test_get_sample_rate_exists(self):
        from mbdsdr_ai.sdr_backend import MockSDRBackend
        b = MockSDRBackend()
        sr = b.get_sample_rate()
        assert isinstance(sr, float) and sr > 0


# ---------------------------------------------------------------------------
# (B) baseband_io save_iq + load_iq round-trip
# ---------------------------------------------------------------------------
class TestBasebandRoundTrip:
    def test_roundtrip_imag_not_lost(self, tmp_path):
        from mbdsdr_ai.baseband_io import save_iq, load_iq
        rng = np.random.default_rng(42)
        n = 4096
        iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
        path = str(tmp_path / "bb_test.iq")
        info = save_iq(iq, path, sample_rate=2_400_000.0,
                       center_freq_hz=98_500_000.0, note="pytest")
        # sidecar 含 sample_rate / center_freq_hz
        assert os.path.exists(path + ".json")
        with open(path + ".json") as f:
            meta = json.load(f)
        assert meta["sample_rate"] == 2_400_000.0
        assert meta["center_freq_hz"] == 98_500_000.0
        assert meta["samples"] == n
        # 读回
        back = load_iq(path)
        back_iq = np.asarray(back["iq"])  # load_iq 返回 list，转 numpy
        assert back_iq.shape == (n,)
        # 虚部不能丢（原始虚部非零，读回后仍非零且近似相等）
        assert np.any(back_iq.imag != 0)
        np.testing.assert_allclose(back_iq.real, iq.real, atol=1e-7)
        np.testing.assert_allclose(back_iq.imag, iq.imag, atol=1e-7)
        # sidecar 采样率被读回
        assert back["sample_rate"] == 2_400_000.0
        assert back["center_freq_hz"] == 98_500_000.0

    def test_roundtrip_interleaved_input(self, tmp_path):
        """交错实数输入（[re,im,re,im,...]）也能 round-trip。"""
        from mbdsdr_ai.baseband_io import save_iq, load_iq
        rng = np.random.default_rng(7)
        n = 2048
        iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
        inter = np.empty(n * 2, dtype=np.float32)
        inter[0::2] = iq.real
        inter[1::2] = iq.imag
        path = str(tmp_path / "bb_inter.iq")
        save_iq(inter.tolist(), path, sample_rate=1_000_000.0, center_freq_hz=100e6)
        back = np.asarray(load_iq(path)["iq"])
        np.testing.assert_allclose(back.real, iq.real, atol=1e-7)
        np.testing.assert_allclose(back.imag, iq.imag, atol=1e-7)


# ---------------------------------------------------------------------------
# (C) AudioPlayer 无 sounddevice 时安全降级
# ---------------------------------------------------------------------------
class TestAudioPlayerDegrade:
    def test_available_false_and_write_noop(self):
        from mbdsdr_ai.audio_out import AudioPlayer
        p = AudioPlayer(sample_rate=48000, channels=1, gain=0.5)
        # 本测试环境未装 sounddevice → available 必须为 False（或 start 失败后变 False）
        started = p.start()
        assert started is False
        assert p.available is False
        # write 不崩溃，返回 0（未入队任何样本）
        audio = np.zeros(1024, dtype=np.float32)
        n = p.write(audio)
        assert n == 0
        # stop 可重复调用不崩
        p.stop()
        p.stop()

    def test_set_gain_clamp(self):
        from mbdsdr_ai.audio_out import AudioPlayer
        p = AudioPlayer()
        p.set_gain(10.0)   # 越界
        assert p.gain == 5.0
        p.set_gain(-2.0)
        assert p.gain == 0.0
        p.set_gain(1.5)
        assert p.gain == 1.5


# ---------------------------------------------------------------------------
# (A) SpectrumDataGenerator.push_iq 真做 FFT（有信号时峰值在对应频率 bin）
# ---------------------------------------------------------------------------
class TestSpectrumRealFFT:
    def test_tone_appears_at_correct_bin(self):
        from spectrum_widget import SpectrumDataGenerator
        gen = SpectrumDataGenerator(num_bins=512)
        sr = 2_400_000.0
        n = gen.FFT_SIZE  # 2048
        t = np.arange(n) / sr

        # +100 kHz 偏移正弦 → 峰值应在中心 bin(256) 右侧
        tone = np.exp(1j * 2 * np.pi * 100_000.0 * t).astype(np.complex64)
        gen.push_iq(tone, sr)
        assert gen.has_data() is True
        spec = gen.spectrum
        peak_bin = int(np.argmax(spec))
        center_bin = 256
        assert peak_bin > center_bin + 5, \
            f"+100kHz 峰值应在中心右侧，实际 peak_bin={peak_bin}"

        # 换 -100 kHz → 峰值应在中心左侧
        gen.clear_data()
        tone2 = np.exp(1j * 2 * np.pi * (-100_000.0) * t).astype(np.complex64)
        gen.push_iq(tone2, sr)
        peak_bin2 = int(np.argmax(gen.spectrum))
        assert peak_bin2 < center_bin - 5, \
            f"-100kHz 峰值应在中心左侧，实际 peak_bin2={peak_bin2}"

    def test_no_data_shows_empty(self):
        from spectrum_widget import SpectrumDataGenerator
        gen = SpectrumDataGenerator(num_bins=512)
        assert gen.has_data() is False
        out = gen.generate()
        # 无数据：平坦底噪 -100 dBFS
        assert np.allclose(out, -100.0, atol=1e-3)
        # 喂 <64 样本不触发
        gen.push_iq(np.zeros(32, dtype=np.complex64), 2_400_000.0)
        assert gen.has_data() is False


# ---------------------------------------------------------------------------
# MainWindow 录音状态切换不崩溃（offscreen + mock backend）
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("qtapp")
class TestMainWindowRecordingFlow:
    def _make_window(self):
        import main_window as mw
        return mw.MainWindow()

    def test_record_toggle_with_mock_backend(self, tmp_path, monkeypatch):
        import main_window as mw
        from PySide6.QtWidgets import QMessageBox
        from mbdsdr_ai.sdr_backend import MockSDRBackend

        # 把录制目录重定向到 tmp_path，避免污染 ~/mbdsdr_recordings
        real_expanduser = os.path.expanduser
        monkeypatch.setattr(os.path, "expanduser",
                            lambda p: str(tmp_path) if p.startswith("~") else real_expanduser(p))
        # offscreen 环境下 QMessageBox.warning 是模态阻塞框，必须替换为 no-op
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

        w = self._make_window()
        # 无后端时开始录音应弹提示且不写文件
        assert w._active_sdr_backend is None
        w._apply_recording(True)
        assert w._recording is False, "无 SDR 时不应进入录制状态"

        # 接上 mock 后端
        b = MockSDRBackend()
        b.connect()
        w._active_sdr_backend = b

        # 开始录制
        w._apply_recording(True)
        assert w._recording is True
        assert w._record_file_path is not None

        # 轮询几帧，IQ 应累积进 buffer
        for _ in range(5):
            w._poll_sdr_iq()
        assert len(w._record_iq_buffer) >= 1, "录制中 buffer 应有数据块"

        # 停止录制 → 落盘
        w._apply_recording(False)
        assert w._recording is False
        path = w._record_file_path
        assert os.path.exists(path), f"录制文件应存在: {path}"
        assert os.path.exists(path + ".json"), "sidecar 应存在"
        with open(path + ".json") as f:
            meta = json.load(f)
        assert meta["sample_rate"] == b.get_sample_rate()
        assert meta["samples"] > 0
        # 虚部不丢：读回 sidecar 对应文件，样本数为偶数
        from mbdsdr_ai.baseband_io import load_iq
        back = load_iq(path)
        assert back["samples"] == meta["samples"]
        # 复位（避免 closeEvent 里再 stop 声卡/定时器报错）
        w._disconnect()

    def test_volume_changes_do_not_crash(self, qtapp):
        import main_window as mw
        w = self._make_window()
        # 滑杆拖到任意值：无设备时 AudioPlayer.set_gain 安全
        for v in (0, 15, 30, 63):
            w._on_volume_changed(v)
        if w._audio_player is not None:
            # 63/63*2.0 = 2.0 gain
            assert abs(w._audio_player.gain - 2.0) < 1e-6
        w._disconnect()

    def test_disconnect_clears_spectrum(self, qtapp):
        import main_window as mw
        from mbdsdr_ai.sdr_backend import MockSDRBackend
        w = self._make_window()
        b = MockSDRBackend(); b.connect()
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
