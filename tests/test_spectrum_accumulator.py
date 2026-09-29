# SPDX-License-Identifier: MIT
"""
频谱累加器单元测试
====================

对照 SDR++ waterfall.cpp:914-937 的 EMA 平均与峰值保持。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from mbdsdr_ai.spectrum_accumulator import SpectrumAccumulator


def test_ema_converges_to_constant():
    """喂恒定帧，EMA 平均应收敛到该值。"""
    acc = SpectrumAccumulator(size=8, avg_alpha=0.5, peak_decay_db=0.0)
    frame = np.array([-60.0] * 8)
    out = None
    for _ in range(50):
        out = acc.push(frame)
    # 50 次 EMA 后应非常接近 -60
    np.testing.assert_allclose(out, -60.0, atol=1e-6)
    np.testing.assert_allclose(acc.average, -60.0, atol=1e-6)


def test_peak_hold_keeps_max():
    """先高峰帧、后低帧，峰值保持应停在高峰。"""
    acc = SpectrumAccumulator(size=4, avg_alpha=1.0, peak_decay_db=0.0)
    # 高峰
    acc.push(np.array([-50.0, -40.0, -50.0, -50.0]))
    # 低帧
    acc.push(np.array([-80.0, -80.0, -80.0, -80.0]))
    peak = acc.peak
    # 峰值保持 = 历史最大值（无衰减）：低帧不刷新已更高的峰
    assert peak[1] == -40.0
    assert peak[0] == -50.0
    assert peak[2] == -50.0


def test_peak_hold_decays():
    """peak_decay_db>0 时，低帧后峰值逐帧下降。"""
    acc = SpectrumAccumulator(size=2, avg_alpha=1.0, peak_decay_db=3.0)
    acc.push(np.array([-40.0, -40.0]))
    p1 = acc.peak[0]           # = -40
    acc.push(np.array([-80.0, -80.0]))
    p2 = acc.peak[0]           # = max(-80, -40-3) = -43
    acc.push(np.array([-80.0, -80.0]))
    p3 = acc.peak[0]           # = max(-80, -43-3) = -46
    assert p1 == -40.0
    assert p2 == -43.0
    assert p3 == -46.0


def test_fixed_frame_average():
    """avg_frames=N 时，每 N 帧取一次块平均。"""
    acc = SpectrumAccumulator(size=3, peak_decay_db=0.0)
    acc.cfg.avg_enabled = False
    acc.cfg.avg_frames = 3
    acc.push(np.array([0.0, 0.0, 0.0]))
    acc.push(np.array([6.0, 6.0, 6.0]))
    out = acc.push(np.array([3.0, 3.0, 3.0]))   # 块平均 = 3
    np.testing.assert_allclose(out, 3.0, atol=1e-9)


def test_reset():
    acc = SpectrumAccumulator(size=4)
    acc.push(np.array([-50.0] * 4))
    assert acc.frames == 1
    acc.reset()
    assert acc.frames == 0
    assert acc.average is None
    assert acc.peak is None
