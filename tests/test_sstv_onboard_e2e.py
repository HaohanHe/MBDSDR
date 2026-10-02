#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SSTV 真机闭环端到端测试：真实 SSTV 音频 -> FM 调制合成 IQ -> onboard sstv 解码出图。

音频 real_sstv.wav 是真实 SSTV 录制；这里用它重新 FM 调制，验证 onboard 的
"采集 IQ -> FM 解调 -> 重采样 48k -> SSTV 解码" 整条链路（物理层接线）真实可用。
"""
import os
import sys
import tempfile

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "onboarding"))

import onboard  # noqa: E402

FDEV = 5000.0
RX_SR = 250_000.0


def synth_fm_iq(wav_path):
    sr, d = wavfile.read(wav_path)
    if d.ndim > 1:
        d = d.mean(axis=1)
    audio = d.astype(np.float32) / 32768.0
    # 重采样到接收采样率（44100 -> 250000）
    from math import gcd
    up = int(RX_SR)
    down = int(sr)
    g = gcd(up, down)
    audio_rx = resample_poly(audio, up // g, down // g).astype(np.float64)
    # FM 调制：相位增量 = 2*pi*fdev*audio/fs
    phase_inc = 2 * np.pi * FDEV * audio_rx / RX_SR
    iq = np.exp(1j * np.cumsum(phase_inc)).astype(np.complex64)
    return iq


def main():
    wav = os.path.join(ROOT, "real_sstv.wav")
    assert os.path.exists(wav), "缺 real_sstv.wav"
    iq = synth_fm_iq(wav)
    print(f"合成 FM IQ: {iq.size:,} 样本 @ {RX_SR/1e3:.0f} ksps "
          f"({iq.size/RX_SR:.1f}s)")

    with tempfile.TemporaryDirectory() as tmp:
        data_path = os.path.join(tmp, "capture.sigmf-data")
        iq.tofile(data_path)
        res = onboard.step_decode(data_path, "sstv", RX_SR, 145.8e6)
        print("status:", res.status)
        print("message:", res.message)
        for e in res.evidence:
            print("  evidence:", e)
        png = res.detail.get("_sstv_output_path")
        if res.status == "PASS" and png and os.path.exists(png):
            sz = os.path.getsize(png)
            print(f"PNG OK: {png} ({sz} bytes)")
            assert sz > 1000, "PNG 太小，疑似空图"
            print("RESULT: PASS - SSTV 真机闭环链路验证成功")
            return 0
        print("RESULT: FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
