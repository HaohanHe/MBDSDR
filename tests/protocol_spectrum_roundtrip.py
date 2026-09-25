#!/usr/bin/env python3
"""
MBDSDR AI - 协议/频谱分析往返测试
==================================
tests/protocol_spectrum_roundtrip.py

覆盖（对应完成标准）：
  1. URH 符号判决往返：已知位流 → ASK/FSK 调制 → 解调 → 位流一致
  2. inspectrum 测量精度：已知信号测 -3dB 带宽，误差 < 5%
  3. protocol_stack 注册/调用：可注册并调用至少 2 个现有解码器（ax25/aprs）
  4. peak_detect：在合成多峰信号上检出正确峰数
  5. sigutils 信道检测 / 峰值检测器 / 调制识别冒烟
  6. spectrum_processor.measure_cursors：游标测量
  7. 所有 register_*_tools 可调用（用假 registry）
"""

import os
import sys
import json
import numpy as np

# 让测试能从仓库根 import mbdsdr_ai
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai import urh_adapter as U          # noqa: E402
from mbdsdr_ai import inspectrum_adapter as I    # noqa: E402
from mbdsdr_ai import sigdigger_adapter as S     # noqa: E402
from mbdsdr_ai import protocol_stack as P        # noqa: E402
from mbdsdr_ai.spectrum_processor import (       # noqa: E402
    SpectrumProcessor, SpectrumData)


PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# ═══════════════════════════════════════════════════════════════════════
# 1. URH 符号判决往返
# ═══════════════════════════════════════════════════════════════════════
def test_urh_roundtrip():
    print("\n== 1. URH 符号判决往返 ==")
    np.random.seed(42)
    bits = np.array([1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 0, 0, 1, 0,
                      1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 1, 0, 0], dtype=np.uint8)
    sps = 120

    # --- ASK (OOK 用双电平避免零电平被当噪声) ---
    mod_ask = U.URHModulator(modulation_type="ASK", samples_per_symbol=sps,
                             sample_rate=1e6, carrier_freq_hz=40e3,
                             parameters=[30.0, 100.0])
    iq_ask = mod_ask.modulate(bits)
    rx_ask = U.URHDecoder(modulation_type="ASK", samples_per_symbol=sps).decode(iq_ask)
    check("ASK 往返位流一致", np.array_equal(bits, rx_ask),
          f"exp {bits.tolist()} got {rx_ask.tolist()}")

    # --- FSK ---
    mod_fsk = U.URHModulator(modulation_type="FSK", samples_per_symbol=sps,
                             sample_rate=1e6, carrier_freq_hz=40e3,
                             parameters=[30e3, 50e3])
    iq_fsk = mod_fsk.modulate(bits)
    rx_fsk = U.URHDecoder(modulation_type="FSK", samples_per_symbol=sps).decode(iq_fsk)
    check("FSK 往返位流一致", np.array_equal(bits, rx_fsk),
          f"exp {bits.tolist()} got {rx_fsk.tolist()}")

    # --- 同步字检测 ---
    stream = np.concatenate([np.zeros(10, dtype=np.uint8),
                             np.array([1, 0, 1, 0, 1, 1, 0, 1], dtype=np.uint8),
                             np.ones(20, dtype=np.uint8)])
    hits = U.sync_word_correlate(stream, [1, 0, 1, 0, 1, 1, 0, 1])
    check("同步字检测命中", 10 in hits, f"hits={hits}")


# ═══════════════════════════════════════════════════════════════════════
# 2. inspectrum 测量精度（已知信号测 -3dB 带宽，误差 < 5%）
# ═══════════════════════════════════════════════════════════════════════
def test_inspectrum_bandwidth():
    print("\n== 2. inspectrum 半功率带宽测量精度 ==")
    sr = 1_000_000.0
    fft_size = 4096
    # 直接构造一个高斯形功率谱峰：P(f) = exp(-(f/sigma)^2)
    # -3dB 点满足 exp(-(f/sigma)^2)=1/2 → f = sigma*sqrt(ln2)
    # 全宽（-3dB）= 2*sigma*sqrt(ln2)，解析已知。
    freqs = np.fft.fftshift(np.fft.fftfreq(fft_size, 1.0 / sr))
    sigma = 10_000.0  # Hz
    true_bw = 2 * sigma * np.sqrt(np.log(2))  # ≈ 16.65 kHz
    peak = 10.0  # 峰值线性功率
    powers = peak * np.exp(-((freqs / sigma) ** 2)) + 0.001  # 加一点底噪
    powers_db = 10 * np.log10(powers + 1e-12)
    peak_bin = int(np.argmax(powers_db))

    m = I.InspectrumMeasurer(sr, fft_size)
    res = m.measure_bandwidth_half_power(freqs, powers_db, peak_bin, level_db=-3.0)
    meas_bw = res["bandwidth_hz"]
    err = abs(meas_bw - true_bw) / true_bw
    print(f"    真实-3dB带宽={true_bw/1e3:.2f}kHz  测得={meas_bw/1e3:.2f}kHz  误差={err*100:.1f}%")
    check("inspectrum 带宽测量误差 < 5%", err < 0.05,
          f"meas={meas_bw:.1f} true={true_bw:.1f} err={err*100:.1f}%")

    # 周期测量：已知脉冲间隔
    starts = np.array([0, 1000, 2000, 3000, 4000], dtype=float)  # 1000 样本间隔
    pres = m.measure_period(starts)
    period_s = pres["period_s"]
    true_period_s = 1000.0 / sr
    check("周期测量精度", abs(period_s - true_period_s) / true_period_s < 0.01,
          f"meas={period_s*1e3:.3f}ms true={true_period_s*1e3:.3f}ms")

    # 占空比
    dc = m.measure_duty_cycle(on_samples=300, period_samples=1000)
    check("占空比测量", abs(dc["duty_cycle"] - 0.3) < 1e-6, f"duty={dc['duty_cycle']}")


# ═══════════════════════════════════════════════════════════════════════
# 3. protocol_stack 注册/调用（至少 2 个现有解码器）
# ═══════════════════════════════════════════════════════════════════════
def test_protocol_stack():
    print("\n== 3. protocol_stack 注册/调用现有解码器 ==")
    import mbdsdr_ai.protocol_stack as ps
    reg = ps.ProtocolStackRegistry()
    # 复用全局适配器构造
    for ad in ps._build_existing_protocol_adapters():
        reg.register_protocol_layer(ad)
    names = sorted(reg._proto_instances.keys())
    print(f"    已注册协议层: {names}")
    check("至少注册 2 个现有解码器", len(names) >= 2, f"names={names}")

    # 构造一个合法 AX.25 UI 帧：用 ax25 自身的 AX25Frame.to_bytes 再 decode_frame
    from mbdsdr_ai.ax25 import AX25Frame
    fr = AX25Frame()
    fr.destination = "AQST-0"
    fr.source = "DN42-9"
    fr.control = 0x03  # UI
    fr.pid = 0xF0      # no layer3
    fr.info = b"Hello MBDSDR"
    raw = fr.to_bytes()

    ax25 = reg.get_protocol("ax25")
    if ax25 is not None:
        out = ax25.decode_frame(raw)
        # decode_address 会把 SSID 剥离，source 呼号为 "DN42"
        check("AX.25 协议层往返",
              out.get("source") == "DN42" and "Hello" in out.get("info", "") and out.get("fcs_valid") is True,
              f"out={out}")
    else:
        check("AX.25 协议层已注册", False, "ax25 not found")

    # 符号层切帧
    sl = ps.FixedFrameSymbolLayer(frame_len=8, sync_word=[1, 0, 1, 0])
    bits = np.array([1, 0, 1, 0, 1, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 1], dtype=np.uint8)
    frames = sl.find_frames(bits)
    check("FixedFrameSymbolLayer 切帧", len(frames) >= 1, f"frames={len(frames)}")


# ═══════════════════════════════════════════════════════════════════════
# 4. peak_detect 在合成信号上检出正确峰数
# ═══════════════════════════════════════════════════════════════════════
def test_peak_detect():
    print("\n== 4. spectrum_processor.peak_detect ==")
    sr = 1_000_000.0
    fft_size = 2048
    sp = SpectrumProcessor(fft_size=fft_size)
    # 合成 3 个单音 + 噪声
    t = np.arange(fft_size) / sr
    sig = (np.exp(1j * 2 * np.pi * 50e3 * t) +
           0.8 * np.exp(1j * 2 * np.pi * -100e3 * t) +
           0.6 * np.exp(1j * 2 * np.pi * 200e3 * t))
    rng = np.random.default_rng(1)
    sig += 0.01 * (rng.standard_normal(fft_size) + 1j * rng.standard_normal(fft_size))
    spec = sp.compute_spectrum(sig, center_freq=0.0, sample_rate=sr)
    peaks = sp.peak_detect(spec, prominence_db=40.0, distance=30)
    print(f"    检出 {len(peaks)} 个峰: "
          f"{[round(p['freq_hz']/1e3,1) for p in peaks[:10]]} kHz")
    check("peak_detect 检出正确峰数(3)", len(peaks) == 3,
          f"got {len(peaks)}")

    # measure_cursors
    res = sp.measure_cursors(spec, left_freq_hz=-110e3, right_freq_hz=-90e3)
    check("measure_cursors 返回 SNR", "snr_db" in res and res["snr_db"] > 0,
          f"res={res}")


# ═══════════════════════════════════════════════════════════════════════
# 5. sigutils 信道检测 / 峰值检测器 / 调制识别冒烟
# ═══════════════════════════════════════════════════════════════════════
def test_sigdigger():
    print("\n== 5. sigutils 信号检测 + AMR ==")
    # 信道检测：造两个信道
    sr = 1e6
    freqs = np.fft.fftshift(np.fft.fftfreq(2048, 1.0 / sr))
    powers = np.full(len(freqs), -80.0)  # 噪声底
    powers += np.random.default_rng(2).standard_normal(len(freqs)) * 1.0
    # 信道1: 中心 100kHz, 宽 5kHz
    powers[np.abs(freqs - 100e3) < 2.5e3] += 30
    # 信道2: 中心 -200kHz, 宽 8kHz
    powers[np.abs(freqs + 200e3) < 4e3] += 25

    det = S.SigutilsChannelDetector(sr)
    chans = det.detect(freqs, powers)
    print(f"    检出 {len(chans)} 个信道: {[(round(c.fc_hz/1e3), round(c.snr_db,1)) for c in chans]}")
    check("sigutils 检出 2 个信道", len(chans) == 2, f"got {len(chans)}")

    # 峰值检测器
    pd = S.SigutilsPeakDetector(size=10, thres_sigmas=2.0)
    vals = [0.0] * 10 + [5.0] + [0.0] * 20 + [6.0] + [0.0] * 10
    peaks = [pd.feed(v) for v in vals]
    n_up = sum(1 for p in peaks if p > 0)
    check("sigutils 峰值检测器检出上峰", n_up >= 1, f"up peaks={n_up}")

    # AMR：CW 单音应识别为 CW
    t = np.arange(1024) / sr
    cw = np.exp(1j * 2 * np.pi * 5e3 * t)
    amr = S.identify_modulation(cw, sr)
    print(f"    CW 识别结果: {amr['modulation']}")
    check("AMR 识别 CW", "CW" in amr["modulation"], f"got {amr['modulation']}")


# ═══════════════════════════════════════════════════════════════════════
# 6. 所有 register_*_tools 可调用（用假 registry）
# ═══════════════════════════════════════════════════════════════════════
class FakeRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, name, description, parameters, handler, category="general", available=True):
        self.tools[name] = {"handler": handler, "description": description}


def test_register_tools():
    print("\n== 6. register_*_tools 注册冒烟 ==")
    reg = FakeRegistry()
    U.register_urh_tools(reg)
    I.register_inspectrum_tools(reg)
    S.register_sigdigger_tools(reg)
    P.register_protocol_stack_tools(reg)
    print(f"    注册了 {len(reg.tools)} 个工具: {sorted(reg.tools.keys())}")
    check("四个 register 函数共注册 >=10 工具", len(reg.tools) >= 10,
          f"got {len(reg.tools)}")

    # 实际调用 protocol_stack_list
    res = reg.tools["protocol_stack_list"]["handler"]({})
    check("protocol_stack_list 可调用", res.success, res.error)


# ═══════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    test_urh_roundtrip()
    test_inspectrum_bandwidth()
    test_protocol_stack()
    test_peak_detect()
    test_sigdigger()
    test_register_tools()

    print(f"\n{'='*50}\n结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
