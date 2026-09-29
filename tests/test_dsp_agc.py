# SPDX-License-Identifier: MIT
"""
test_dsp_agc.py — deterministic AGC attack/decay unit tests (synthetic signal).
非硬件 / NOT HARDWARE.

Checks GqrxAGC:
  * output level settles after a burst (variation < 3 dB)
  * attack time < 10 ms
Targets mbdsdr_ai/gqrx_receiver.py:GqrxAGC.
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
    """信号出现后 attack 收敛时间（尖峰→稳态）< 10ms。

    GqrxAGC 是纯包络跟踪 AGC（无延迟线）：静音期包络在 dB 域缓慢释放、
    增益被拉高压低噪声；突发到来时输入 RMS 跳变超过残留包络，attack
    （tau≈1.5ms）快速把包络拉到突发电平、增益压到目标。输出先尖峰
    （静音期高增益 × 突发样本），随后随 attack 指数压回稳态。这里测尖峰
    之后包络从 90% 超调衰减到 10% 超调的时间，即 attack 时间常数。
    """
    sr = 48_000.0
    # 静音开头，然后突然加 -20dBFS 信号
    pre = int(sr * 0.05)  # 50ms 静音
    burst = int(sr * 0.2)  # 200ms 信号
    t = np.arange(burst) / sr
    sig = np.zeros(pre + burst, dtype=np.complex128)
    sig[:pre] = 1e-4 * (np.random.randn(pre) + 1j * np.random.randn(pre))
    sig[pre:] = (10 ** (-20 / 20.0)) * np.exp(1j * 2 * np.pi * 1000 * t)

    agc = GqrxAGC(sr, agc_on=True, use_hang=False, threshold_db=-100,
                  slope=0, decay_ms=500)
    out = agc.process(sig)

    # 输出包络（滑动 RMS）
    env = np.sqrt(np.convolve(np.abs(out) ** 2, np.ones(300) / 300, mode="same"))
    # 稳态包络（后 100ms）
    steady_level = float(np.mean(env[pre + int(sr * 0.1):]))
    post = env[pre:]
    # 突发后前 20ms 的尖峰：静音期高增益 × 突发造成的过冲。
    peak_rel = int(np.argmax(post[: int(sr * 0.02)]))
    peak = float(post[peak_rel])
    assert peak > steady_level, "突发未产生过冲，AGC 可能未 attack"
    # 超调衰减：从 90% 超调（靠近尖峰）衰减到 10% 超调（靠近稳态）。
    hi = steady_level + 0.9 * (peak - steady_level)
    lo = steady_level + 0.1 * (peak - steady_level)
    after = post[peak_rel:]
    idx_hi = np.where(after < hi)[0]
    idx_lo = np.where(after < lo)[0]
    assert len(idx_hi) and len(idx_lo), "输出包络未衰减到稳态"
    decay_ms = (idx_lo[0] - idx_hi[0]) / sr * 1000.0
    assert decay_ms < 10.0, f"AGC attack 衰减时间 {decay_ms:.1f}ms > 10ms"
