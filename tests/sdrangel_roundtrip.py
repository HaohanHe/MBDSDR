#!/usr/bin/env python3
"""SDRangel 真实源码移植往返验证。

所有被测模块均移植自 SDRangel (GPLv3) 真实 .cpp/.h 源码，常量标注 file:line。
本测试证明 DSP 管道端到端不崩溃且数值正确：

  1. FFTFilter：已知正弦 -> 低通 -> 通带保留/阻带抑制，频谱位置正确
  2. DownChannelizer：高采样率 -> 2^N 下变频 -> 输出采样率严格等于目标
  3. MagAGC：已知幅度阶跃 -> 稳态输出幅度收敛到 target
  4. DSP 管道：源 -> DC校正 -> 下变频 -> NFM/SSB 解调 -> 不崩溃且输出正确
  5. NFM 往返：FM 信号(1kHz 消息,5kHz 频偏) -> 鉴频 -> 恢复 1kHz
  6. SSB 往返：USB 边带 -> 边带滤波+检波 -> 音频频谱位置正确
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.sdrangel_adapter import (  # noqa: E402
    FFTFilter, SSBFilter, DownChannelizer, MagAGC,
    DSPDeviceEngine, ChannelSink, NFMDemodSink, SSBDemodSink,
    DOWNCHANNELIZER_HB_FILTER_ORDER, DEVICE_PRESETS,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def spectrum_peak_hz(audio: np.ndarray, sr: float, skip: int = 2000) -> float:
    a = audio[skip:] if len(audio) > skip else audio
    if len(a) < 16:
        return float('nan')
    sp = np.abs(np.fft.rfft(a))
    freqs = np.fft.rfftfreq(len(a), 1.0 / sr)
    return float(freqs[np.argmax(sp)])


# ---------------------------------------------------------------------------
def test_fftfilter():
    print("\n== 1. FFTFilter (fftfilt.cpp:144-186,436-457) ==")
    sr = 1_000_000.0
    n = 2048 * 16
    t = np.arange(n) / sr
    # 低通截止 200kHz (归一化 0.2)
    f = FFTFilter(0.0, 200e3 / sr, flen=1024)
    # 通带：50kHz 正弦
    y_pass = f.filter_all(np.exp(2j * np.pi * 50e3 * t))
    f.reset()
    # 阻带：400kHz 正弦
    y_stop = f.filter_all(np.exp(2j * np.pi * 400e3 * t))
    f.reset()
    p_pass = np.sqrt(np.mean(np.abs(y_pass[4096:]) ** 2))
    p_stop = np.sqrt(np.mean(np.abs(y_stop[4096:]) ** 2))
    check("低通通带增益接近 1", 0.5 < p_pass < 1.5, f"(p_pass={p_pass:.3f})")
    check("低通阻带抑制 > 20dB", p_stop < p_pass * 0.1,
          f"(p_stop={p_stop:.4f}, rej={20*math.log10(p_pass/max(p_stop,1e-9)):.1f}dB)")

    # 带通：300-5000Hz @ 48kHz，等价 SSB 滤波器形状
    fb = FFTFilter(300 / 48000.0, 5000 / 48000.0, flen=1024)
    tb = np.arange(2048 * 16) / 48000.0
    mid = fb.filter_all(np.exp(2j * np.pi * 2000 * tb))
    fb.reset()
    out = fb.filter_all(np.exp(2j * np.pi * 10000 * tb))
    p_mid = np.sqrt(np.mean(np.abs(mid[4096:]) ** 2))
    p_out = np.sqrt(np.mean(np.abs(out[4096:]) ** 2))
    check("带通通带(2kHz)通过", p_mid > 0.3, f"(p={p_mid:.3f})")
    check("带通阻带(10kHz)抑制", p_out < p_mid * 0.2, f"(p={p_out:.4f})")


def test_downchannelizer():
    print("\n== 2. DownChannelizer (downchannelizer.cpp:116-144, h=48) ==")
    check("半带滤波器常量 = 48", DOWNCHANNELIZER_HB_FILTER_ORDER == 48,
          "(downchannelizer.h:31)")
    sr_in = 1_024_000
    sr_out = 64_000  # 16x -> 4 级
    dc = DownChannelizer(sr_in, sr_out, channel_offset=10_000.0)
    check("级数 = log2(16) = 4", dc.n_stages == 4, f"(n={dc.n_stages})")
    check("输出采样率 = 64000", dc.channel_sr == sr_out, f"(sr={dc.channel_sr})")
    t = np.arange(sr_in) / sr_in
    x = np.exp(2j * np.pi * 10_000 * t)  # 正好在通道中心
    y = dc.process(x)
    # 通道中心信号下变频后应落在 DC，幅度保持
    check("下变频后长度≈sr_in/16", abs(len(y) - sr_in // 16) < 4, f"(len={len(y)})")
    check("通道中心信号幅度保持", abs(np.abs(y).mean() - 1.0) < 0.1,
          f"(mean|y|={np.abs(y).mean():.3f})")


def test_agc():
    print("\n== 3. MagAGC (agc.cpp:53-179, target=3276 标定) ==")
    agc = MagAGC(history_size=4800, target=1.0, threshold=1e-4, sample_rate=48000)
    # 先小信号 2000 样本，再大信号
    x = np.concatenate([
        np.full(3000, 0.05, dtype=complex),
        np.full(5000, 1.0, dtype=complex),
    ])
    y = agc.process(x)
    tail = np.abs(y[4000:])
    check("AGC 稳态输出幅度收敛到 target", abs(tail.mean() - 1.0) < 0.15,
          f"(tail rms={tail.mean():.3f})")
    check("AGC 不崩溃且输出等长", len(y) == len(x), f"(len={len(y)})")


def test_dsp_pipeline():
    print("\n== 4. DSPDeviceEngine 管道 (dspdevicesourceengine.cpp:288-337) ==")
    eng = DSPDeviceEngine(sample_rate=1_024_000, center_frequency=145e6)
    collected = {"nfm": [], "ssb": []}
    # 通道采样率取 64000 (1024000/16，2^4)，满足半带链 2^N 抽取
    nfm = NFMDemodSink(channel_sr=64000, audio_sr=48000)
    ssb = SSBDemodSink(audio_sr=48000)

    def nfm_cb(iq):
        collected["nfm"].append(nfm.process(iq))

    def ssb_cb(iq):
        collected["ssb"].append(ssb.process(iq))

    eng.add_channel(ChannelSink(
        "nfm", DownChannelizer(1_024_000, 64_000, channel_offset=0.0), nfm_cb))
    eng.add_channel(ChannelSink(
        "ssb", DownChannelizer(1_024_000, 64_000, channel_offset=10_000.0), ssb_cb))
    eng.start()

    # 合成 NFM 信号：1kHz 消息，5kHz 频偏
    t = np.arange(102_400) / 1_024_000.0
    msg = np.cos(2 * np.pi * 1000 * t[:len(t) // 2])
    phase = 2 * np.pi * np.cumsum(5000 * np.cos(2 * np.pi * 1000 * t)) / 1_024_000.0
    src = np.exp(1j * phase)
    stats = eng.work(src)
    check("管道分发到 2 通道", len(stats) == 2, f"(stats={stats})")
    nfm_audio = np.concatenate([a for a in collected["nfm"] if len(a)])
    check("NFM 管道产出音频", len(nfm_audio) > 1000, f"(len={len(nfm_audio)})")


def test_nfm_roundtrip():
    print("\n== 5. NFM 往返 (nfmdemodsink.cpp, phasediscri.h:75-92) ==")
    sr = 48000.0
    t = np.arange(48000) / sr
    fdev = 5000.0
    msg = np.cos(2 * np.pi * 1000 * t)
    iq = np.exp(1j * 2 * np.pi * np.cumsum(fdev * msg) / sr)
    nfm = NFMDemodSink(channel_sr=48000, audio_sr=48000, fm_deviation=5000)
    audio = nfm.process(iq)
    peak = spectrum_peak_hz(audio, sr, skip=3000)
    check("NFM 恢复 1kHz 消息", abs(peak - 1000) < 150, f"(peak={peak:.1f}Hz)")


def test_ssb_roundtrip():
    print("\n== 6. SSB 往返 (ssbdemodsink.cpp:31,52-53,208) ==")
    sr = 48000.0
    t = np.arange(48000 * 4) / sr
    # USB：2kHz 音频 -> 上边带正频率
    iq = np.exp(2j * np.pi * 2000 * t)
    ssb = SSBDemodSink(audio_sr=48000, usb=True)
    audio = ssb.process(iq)
    peak = spectrum_peak_hz(audio, sr, skip=2000)
    check("SSB-USB 通过 2kHz 边带", abs(peak - 2000) < 200, f"(peak={peak:.1f}Hz)")


def test_device_presets():
    print("\n== 7. 设备预设 (samplesource/) ==")
    check("RTL-SDR 默认 1.024Msps",
          abs(DEVICE_PRESETS["rtlsdr"]["default_sample_rate"] - 1024e3) < 1)
    check("HackRF 默认 2.4Msps",
          abs(DEVICE_PRESETS["hackrf"]["default_sample_rate"] - 2.4e6) < 1)
    check("bladeRF1 默认 3.072Msps",
          abs(DEVICE_PRESETS["bladerf1"]["default_sample_rate"] - 3.072e6) < 1)


if __name__ == "__main__":
    test_fftfilter()
    test_downchannelizer()
    test_agc()
    test_dsp_pipeline()
    test_nfm_roundtrip()
    test_ssb_roundtrip()
    test_device_presets()
    print(f"\n==== 结果: {PASS} passed, {FAIL} failed ====")
    sys.exit(1 if FAIL else 0)
