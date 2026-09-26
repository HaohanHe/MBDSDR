"""
音频重采样器确定性测试
======================

对照上游：
- sdrpp/core/src/dsp/multirate/rational_resampler.h:120-165
    gcd 约分 + 多相 FIR 低通（截止 min(in,out)/2，过渡带 10%，抽头×interp）
- sdrpp/core/src/dsp/multirate/polyphase_resampler.h:20-90
    有状态多相：块间保留相位游标与延迟线

测试：
1. 12kHz 采样的 1kHz 正弦 → 48kHz，断言 1kHz 幅度 > -1dB，无混叠镜像。
2. 有状态：分块喂入与一次性喂入结果一致（块间无边界断裂）。
3. 抽取方向：48k→12k 时 >6kHz 信号被抗混叠滤波抑制。
"""
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.audio_resampler import AudioResampler, resample_audio


def _tone(fs, freq, dur_s, amp=0.5):
    t = np.arange(int(fs * dur_s)) / fs
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _peak_freq_and_amp(y, fs):
    """返回输出中最强分量的频率(Hz)与峰值幅度。"""
    y = y[200:]  # 去滤波器建立瞬态
    win = np.hanning(len(y))
    S = np.abs(np.fft.rfft(y * win)) / np.sum(win) * 2
    freqs = np.fft.rfftfreq(len(y), 1.0 / fs)
    k = int(np.argmax(S))
    return float(freqs[k]), float(S[k])


def test_resample_12k_to_48k_passes_1khz():
    """输入 12kHz 采样的 1kHz 正弦 → 48kHz；幅度 > -1dB，峰频 = 1kHz。"""
    x = _tone(12000.0, 1000.0, dur_s=2.0, amp=0.5)
    y = resample_audio(x, 12000.0, 48000.0)
    f_peak, amp_peak = _peak_freq_and_amp(y, 48000.0)
    # 峰频应在 1kHz ± 50Hz 内
    assert abs(f_peak - 1000.0) < 50.0, f"峰频 {f_peak} 偏离 1kHz"
    # 幅度相对 0.5 满幅：> -1dB → 幅度 > 0.5 * 10^(-1/20) ≈ 0.446
    assert amp_peak > 0.446, f"幅度 {amp_peak} 低于 -1dB"


def test_resample_12k_to_48k_no_image():
    """插值镜像（11kHz/13kHz 等）应被抗混叠 FIR 抑制 >40dB。"""
    x = _tone(12000.0, 1000.0, dur_s=3.0, amp=0.5)
    y = resample_audio(x, 12000.0, 48000.0)[300:]
    win = np.hanning(len(y))
    S = np.abs(np.fft.rfft(y * win)) / np.sum(win) * 2
    freqs = np.fft.rfftfreq(len(y), 1.0 / 48000.0)

    def db_at(f):
        i = int(np.argmin(np.abs(freqs - f)))
        return 20 * np.log10(S[i] + 1e-9)

    sig_db = db_at(1000.0)
    # 镜像位于 fs_in ± 1kHz = 11k, 13k（48k 域）
    image_db = max(db_at(11000.0), db_at(13000.0))
    # 镜像比信号低 40dB 以上
    assert image_db - sig_db < -40.0, \
        f"镜像未充分抑制: sig={sig_db:.1f}dB, image={image_db:.1f}dB"


def test_stateful_block_continuity():
    """分块喂入与一次性喂入结果一致（块间无边界断裂）。

    对照 polyphase_resampler.h 的 phase 寄存器：
    跨块应保留相位游标与滤波器延迟线。
    """
    x = _tone(12000.0, 1000.0, dur_s=2.0, amp=0.5)
    # 一次性
    y_one = resample_audio(x, 12000.0, 48000.0)
    # 分块
    rs = AudioResampler(12000.0, 48000.0)
    chunks = [rs.process(x[i:i + 1024]) for i in range(0, len(x), 1024)]
    y_block = np.concatenate(chunks)
    # 长度应几乎一致
    assert abs(len(y_one) - len(y_block)) <= 2, \
        f"分块/整块输出长度不一致: {len(y_one)} vs {len(y_block)}"
    n = min(len(y_one), len(y_block))
    # 去掉各自前 300 样本的建立瞬态后，主体应高度相关
    a = y_one[300:n - 300]
    b = y_block[300:n - 300]
    corr = float(np.corrcoef(a, b)[0, 1])
    assert corr > 0.999, f"分块重采样与一次性不一致: corr={corr:.4f}"


def test_decimation_anti_alias():
    """48k→12k 抽取：>6kHz（输出 Nyquist）信号应被抑制。"""
    # 1kHz 信号（通带内）+ 7kHz 信号（>6k out Nyquist，应被抗混叠滤波挡住）
    t = np.arange(48000 * 2) / 48000.0
    x = 0.5 * np.sin(2 * np.pi * 1000.0 * t) \
        + 0.5 * np.sin(2 * np.pi * 7000.0 * t)
    x = x.astype(np.float32)
    y = resample_audio(x, 48000.0, 12000.0)[300:]
    win = np.hanning(len(y))
    S = np.abs(np.fft.rfft(y * win)) / np.sum(win) * 2
    freqs = np.fft.rfftfreq(len(y), 1.0 / 12000.0)

    def db_at(f):
        i = int(np.argmin(np.abs(freqs - f)))
        return 20 * np.log10(S[i] + 1e-9)

    # 1kHz 通带内应保留
    pass_db = db_at(1000.0)
    # 7kHz 若没被滤掉，会在 12k 域混叠到 5kHz；检查 5kHz 处
    alias_db = db_at(5000.0)
    # 混叠镜像应比通带低至少 20dB
    assert alias_db - pass_db < -20.0, \
        f"抽取抗混叠不足: pass={pass_db:.1f}dB, alias={alias_db:.1f}dB"


def test_identity_rate_passthrough():
    """in_sr == out_sr 时应直通，不产生缩放/滤波。"""
    x = _tone(48000.0, 1000.0, dur_s=0.5, amp=0.5)
    y = resample_audio(x, 48000.0, 48000.0)
    assert len(y) == len(x)
    np.testing.assert_allclose(y, x, atol=1e-6)
