"""
test_dsp_agc.py — AGC attack/decay 确定性单测（合成信号）。

验证 GqrxAGC（移植自 gqrx src/dsp/agc_impl.cpp）：
  * 突发信号后输出幅度稳定（变化 < 3dB）
  * attack 时间 < 10ms
对应 mbdsdr_ai/gqrx_receiver.py:GqrxAGC。
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.gqrx_receiver import GqrxAGC


def _rms_db(x):
    return 20.0 * np.log10(float(np.sqrt(np.mean(np.abs(x) ** 2))) + 1e-12)


def test_agc_stabilizes_burst():
    """前半静音、后半 -20dBFS 突发信号：AGC 后输出幅度稳定（< 3dB 变化）。"""
    sr = 48_000.0
    n = int(sr * 0.5)
    t = np.arange(n) / sr
    # 前半静音（小噪声底），后半 -20dBFS 正弦复信号
    sig = np.zeros(n, dtype=np.complex128)
    half = n // 2
    noise = 1e-4 * (np.random.randn(half) + 1j * np.random.randn(half))
    burst = (10 ** (-20 / 20.0)) * np.exp(1j * 2 * np.pi * 1000 * t[:half])
    sig[:half] = noise
    sig[half:] = burst

    agc = GqrxAGC(sr, agc_on=True, use_hang=False, threshold_db=-100,
                  slope=0, decay_ms=500)
    out = agc.process(sig)

    # 取突发稳态段（跳过 attack 过渡：后 0.2s）
    steady = out[int(sr * 0.35):]
    db_levels = []
    # 分 10 段测 RMS，稳态段间变化应 < 3dB
    segs = np.array_split(steady, 10)
    for s in segs:
        db_levels.append(_rms_db(s))
    spread = max(db_levels) - min(db_levels)
    assert spread < 3.0, f"AGC 稳态输出幅度变化 {spread:.2f}dB > 3dB"


def test_agc_attack_fast():
    """信号出现后 attack 收敛时间 < 10ms。"""
    sr = 48_000.0
    # 静音开头，然后突然加 -20dBFS 信号
    pre = int(sr * 0.05)  # 50ms 静音
    burst = int(sr * 0.1)  # 100ms 信号
    t = np.arange(burst) / sr
    sig = np.zeros(pre + burst, dtype=np.complex128)
    sig[:pre] = 1e-4 * (np.random.randn(pre) + 1j * np.random.randn(pre))
    sig[pre:] = (10 ** (-20 / 20.0)) * np.exp(1j * 2 * np.pi * 1000 * t)

    agc = GqrxAGC(sr, agc_on=True, use_hang=False, threshold_db=-100,
                  slope=0, decay_ms=500)
    out = agc.process(sig)

    # 稳态目标电平（信号出现后后 50ms 的 RMS）
    steady_seg = out[pre + int(sr * 0.05):]
    target_db = _rms_db(steady_seg)

    # 逐样本找达到目标 -1dB 内的位置
    post = out[pre:]
    rms_rolling = np.sqrt(np.convolve(np.abs(post) ** 2,
                                     np.ones(200) / 200, mode="same"))
    rms_db = 20.0 * np.log10(rms_rolling + 1e-12)
    reached = np.where(rms_db > target_db - 1.0)[0]
    if len(reached):
        attack_ms = reached[0] / sr * 1000.0
        assert attack_ms < 10.0, f"AGC attack {attack_ms:.1f}ms > 10ms"
