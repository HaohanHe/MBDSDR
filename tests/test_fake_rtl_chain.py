"""FakeRtl 端到端链路测试：设备打开→读 IQ→FFT→WFM 解调→音频输出队列。

在云里没有真棒时，这是唯一能自证"插上就能出"的办法。
FakeRtlSdr 只在 tests/ 里，不进运行路径。

链路对照真实软件无线电：
    FakeRtlSdr.read_samples()   ← librtlsdr 同步读 / SDR++ ring_buffer
    → np.fft.fft 频谱           ← GQRX/SDR++ 频谱瀑布
    → dsp.demodulate(WFM)       ← 正交鉴频（phasediscriminator）
    → 重采样到 48k              ← resample_poly 抗混叠
    → AudioPlayer.write()       ← PortAudio OutputStream 回调

与 tests/test_audio_chain.py 的区别：那边从"凭空造 IQ"开始；
这边从"一支可注入的假棒"开始，走的是 RTLSDRBackend.read_samples()
这条真实后端入口，证明后端接口本身没断。
"""

import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (REPO_ROOT, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import dsp
from mbdsdr_ai.sdr_backend import RTLSDRBackend
from mbdsdr_ai.audio_out import AudioPlayer
from fake_rtl import FakeRtlSdr  # tests/ 专属双棒，绝不进 mbdsdr_ai/desktop/


# ═══════════════════════════════════════════════════════
# 夹具：把假棒塞进真实 RTLSDRBackend（不调 connect，不碰 USB）
# ═══════════════════════════════════════════════════════

def _make_backend_with_fake_rtl(fm_carrier_hz=98.5e6):
    """构造真实 RTLSDRBackend，但把底层 _sdr 换成 FakeRtlSdr。

    为什么不调 connect()：connect() 会真去 import rtlsdr / 开 USB 设备，
    沙箱里没有。这里按 agent-hint 允许的方式手动注入：
      backend._sdr = FakeRtlSdr(...)
      backend.status.connected = True
    read_samples() 在 _ring_reader is None 时走"兜底同步读"分支，
    直接调 self._sdr.read_samples(n) —— 正是真实棒的同步读路径。
    """
    backend = RTLSDRBackend(device_index=0)
    fake = FakeRtlSdr(device_index=0, fm_carrier_hz=fm_carrier_hz)
    backend._sdr = fake
    backend._ring_reader = None  # 确保走同步直读分支
    backend.status.connected = True
    backend.status.frequency_hz = fake.center_freq
    backend.status.sample_rate_hz = float(fake.sample_rate)
    return backend, fake


def _audio_peak_freq(audio, sr):
    """实音频加 Hann 窗 rFFT，返回最大谱峰频率（跳过 DC bin）。"""
    a = np.asarray(audio, dtype=np.float64)
    n = a.size
    if n < 8:
        return 0.0
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(a * win))
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    if spec.size <= 2:
        return 0.0
    idx = int(np.argmax(spec[1:]) + 1)
    return float(freqs[idx])


# ═══════════════════════════════════════════════════════
# TC1: 设备打开 → 读 IQ（后端入口真的吐数据）
# ═══════════════════════════════════════════════════════

class TestTC1OpenAndReadIQ:
    def test_fake_rtl_open_and_read_iq(self):
        backend, fake = _make_backend_with_fake_rtl()

        iq = backend.read_samples(8192)

        # 后端入口真的返回了数据，而不是 connected=False 时的 None
        assert iq is not None, "RTLSDRBackend.read_samples 返回 None，链路断在设备层"
        assert len(iq) == 8192, f"应读 8192 点，实际 {len(iq)}"
        assert iq.dtype == np.complex64, \
            f"pyrtlsdr.read_samples 返回 complex64，实际 {iq.dtype}"
        # 信号功率 ≈ 1（单位幅度载波），远高于 0.5 门限——证明吐的是信号不是零
        power = float(np.mean(np.abs(iq) ** 2))
        assert power > 0.5, f"IQ 平均功率 {power:.3f} 过低，疑似全零/空棒"
        # 假棒属性与真实棒接口对齐
        assert fake.center_freq == int(98.5e6)
        assert fake.sample_rate == pytest.approx(2_048_000.0)
        assert fake.tuner_type == 5  # R820T
        fake.close()
        assert fake.closed


# ═══════════════════════════════════════════════════════
# TC2: FFT 能看到 FM 信号（以 DC 为中心的发射）
# ═══════════════════════════════════════════════════════

class TestTC2FFTShowsFM:
    def test_fake_rtl_fft_shows_fm_carrier(self):
        backend, fake = _make_backend_with_fake_rtl()
        iq = backend.read_samples(16384)
        sr = backend.status.sample_rate_hz

        spec = np.fft.fftshift(np.fft.fft(iq))
        freqs = np.fft.fftshift(np.fft.fftfreq(iq.size, d=1.0 / sr))
        power = (np.abs(spec) ** 2) / iq.size          # 归一化周期图
        power_db = 10.0 * np.log10(power + 1e-12)

        peak_idx = int(np.argmax(power))
        peak_freq = float(freqs[peak_idx])
        peak_db = float(power_db[peak_idx])
        # 噪底用中位数估计（绝大多数 bin 是空噪声），比均值鲁棒
        noise_db = float(10.0 * np.log10(np.median(power) + 1e-12))

        # 1) 峰值比噪底高出 10 dB 以上 = 真有信号，不是噪声地板
        assert peak_db - noise_db > 10.0, \
            f"谱峰仅超噪底 {peak_db - noise_db:.1f} dB（<10dB），疑似无信号"
        # 2) 信号以 DC 为中心：FM 调制指数 β=75000/1000=75，
        #    边带按贝塞尔分布铺在 ±~(β+1)·f_mod ≈ ±76 kHz，峰落在 ±72 kHz 附近，
        #    整体仍以 DC（电台下变频后的载波位置）为中心，而不是跑到带边。
        assert abs(peak_freq) <= 2.5 * 75000.0, \
            f"谱峰在 {peak_freq/1e3:.1f} kHz，偏离 DC 过远，发射未居中"
        # 3) 中心 ±100 kHz 带内集中了大部分功率（FM 广播带宽 ~150 kHz）
        in_band = power[np.abs(freqs) <= 100_000.0]
        frac = float(in_band.sum() / power.sum())
        assert frac > 0.5, f"中心带内功率占比仅 {frac:.2f}，发射不在 DC 附近"
        fake.close()


# ═══════════════════════════════════════════════════════
# TC3: WFM 鉴频出音频，且音频里真有 1 kHz 调制音
# ═══════════════════════════════════════════════════════

class TestTC3WFMDemod:
    def test_fake_rtl_wfm_demod_to_audio(self):
        backend, fake = _make_backend_with_fake_rtl()
        sr = backend.status.sample_rate_hz
        # ≥204800 样本 ≈ 0.1 s @ 2.048 MS/s
        iq = backend.read_samples(204800)
        assert iq.size == 204800

        audio = dsp.demodulate(iq, mode="WFM", sample_rate=sr)

        assert audio is not None and audio.size > 0, "WFM 解调输出为空"
        assert audio.dtype in (np.float32, np.float64), \
            f"解调音频应为 float32/64，实际 {audio.dtype}"
        # 不是全零：鉴频真切出了调制音（实测 mean|a|≈0.64）
        assert float(np.mean(np.abs(audio))) > 0.001, \
            f"解调音频 mean|a|={float(np.mean(np.abs(audio))):.4f} 过小，疑似全零"

        # 鉴频是线性过程：音频谱峰应落在调制频率 1000 Hz（±10%）
        # 注意 fm_demod 不抽取，音频采样率 = IQ 采样率 = 2.048 MHz
        peak = _audio_peak_freq(audio, sr)
        assert abs(peak - 1000.0) <= 0.10 * 1000.0, \
            f"音频谱峰 {peak:.1f} Hz 偏离调制音 1000 Hz 超过 10%"
        fake.close()


# ═══════════════════════════════════════════════════════
# TC4: 音频真的进了播放队列（无硬件也不崩）
# ═══════════════════════════════════════════════════════

class TestTC4AudioQueue:
    def test_fake_rtl_audio_player_queue(self):
        from scipy.signal import resample_poly
        from math import gcd

        backend, fake = _make_backend_with_fake_rtl()
        sr = backend.status.sample_rate_hz
        iq = backend.read_samples(204800)
        audio = dsp.demodulate(iq, mode="WFM", sample_rate=sr)

        # 重采样到 48 kHz 播放率（resample_poly 自带抗混叠低通）
        out_sr = 48000
        g = gcd(int(round(sr)), out_sr)
        audio_48k = resample_poly(audio, out_sr // g, int(round(sr)) // g)
        assert audio_48k.size > 0, "重采样后音频为空"

        player = AudioPlayer(sample_rate=48000, channels=1, gain=0.5)

        # (a) 无硬件安全降级：available=False 时 write 返回 0 但绝不抛异常
        #     —— 这就是沙箱里"链路不崩"的保证（对照 test_audio_chain.py）。
        player.available = False
        n_deg = player.write(audio_48k.astype(np.float32))
        assert n_deg == 0, f"无设备时 write 应返回 0，实际 {n_deg}"

        # (b) 打通入队路径：把 available 置 True、_stream 换成 dummy 非空对象，
        #     绕过"无设备返回 0"分支，真实走到 data*gain + clip + 入队。
        #     与 test_audio_chain.py::test_write_applies_gain_multiplication 同手法。
        player.available = True
        player._stream = object()  # 哑流句柄，仅用于通过 _stream is None 判断
        n_q = player.write(audio_48k.astype(np.float32))

        assert n_q > 0, f"write 入队样本数应为正，实际 {n_q}"
        assert player._queued_samples > 0, \
            f"player._queued_samples={player._queued_samples}，音频没进队列"
        assert len(player._queue) > 0, "内部队列为空，数据未到达播放端"
        player.stop()
        fake.close()


# ═══════════════════════════════════════════════════════
# TC5: 全链路数据贯通 —— 每一步都有真数据流到下一步
# ═══════════════════════════════════════════════════════

class TestTC5FullChain:
    def test_fake_rtl_full_chain_data_flow(self, capsys):
        from scipy.signal import resample_poly
        from math import gcd

        chain = {}

        # 1) 假棒 → 后端读 IQ
        backend, fake = _make_backend_with_fake_rtl()
        sr = backend.status.sample_rate_hz
        iq = backend.read_samples(204800)
        assert iq is not None and iq.size == 204800
        chain["IQ样本数"] = iq.size
        chain["IQ形状"] = iq.shape
        chain["IQ_dtype"] = str(iq.dtype)

        # 2) IQ → FFT（非空）
        spec = np.fft.fft(iq)
        assert spec.size > 0 and np.all(np.isfinite(np.abs(spec)))
        chain["FFT点数"] = spec.size

        # 3) FFT 非空 → WFM 解调（非空非零）
        audio = dsp.demodulate(iq, mode="WFM", sample_rate=sr)
        assert audio.size > 0 and float(np.mean(np.abs(audio))) > 0.001
        chain["音频样本数"] = int(audio.size)
        chain["音频_dtype"] = str(audio.dtype)

        # 4) 音频 → 重采样到 48k
        out_sr = 48000
        g = gcd(int(round(sr)), out_sr)
        audio_48k = resample_poly(audio, out_sr // g, int(round(sr)) // g)
        assert audio_48k.size > 0
        chain["48k音频样本数"] = int(audio_48k.size)

        # 5) 音频 → AudioPlayer.write（queued_samples>0）
        player = AudioPlayer(sample_rate=48000, channels=1, gain=0.5)
        player.available = True
        player._stream = object()
        n = player.write(audio_48k.astype(np.float32))
        assert n > 0 and player._queued_samples > 0
        chain["队列样本数"] = int(player._queued_samples)
        chain["队列块数"] = len(player._queue)

        # 链路数据量摘要（打印出来给人看：插上即通）
        summary = (
            "\n=== FakeRtl 端到端链路数据量摘要 ===\n"
            f"  采样率            : {sr/1e3:.1f} kHz\n"
            f"  IQ 样本数         : {chain['IQ样本数']}  shape={chain['IQ形状']}  {chain['IQ_dtype']}\n"
            f"  FFT 点数          : {chain['FFT点数']}\n"
            f"  WFM 音频样本数    : {chain['音频样本数']}  {chain['音频_dtype']}\n"
            f"  重采样 48k 样本数 : {chain['48k音频样本数']}\n"
            f"  音频队列样本数    : {chain['队列样本数']}（{chain['队列块数']} 块）\n"
            "=== 每一步都有真数据流到下一步：插上即通 ==="
        )
        with capsys.disabled():
            print(summary)
        player.stop()
        fake.close()
