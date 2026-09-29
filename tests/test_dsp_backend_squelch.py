# SPDX-License-Identifier: MIT
"""
静噪门控测试
============

对照上游：
- gqrx/src/receivers/nbrx.cpp:77-78  信号链 filter→meter→sql→agc（sql 在 agc 前）
- gqrx/src/receivers/nbrx.cpp:48      simple_squelch_cc(threshold, alpha)

测试：
1. 噪声(-60dBFS)阶段门关
2. 突发信号(-20dBFS)阶段门开
3. 信号结束后 hang 时间内门仍开，hang 结束后门关
"""
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.squelch import Squelch, rms_dbfs


def _noise_block(block, amp, rng):
    return (rng.standard_normal(block) * amp).astype(np.float32)


def test_squelch_closed_on_noise():
    """-60dBFS 噪声下，门应关闭。"""
    sr = 48000.0
    block = 960  # 20ms
    sql = Squelch(threshold_db=-40.0, hang_ms=0.0,
                  attack_ms=5.0, decay_ms=50.0, sample_rate=sr)
    rng = np.random.default_rng(42)
    noise_amp = 10 ** (-60 / 20)
    states = [sql.push_rms_db(rms_dbfs(_noise_block(block, noise_amp, rng)), block)
              for _ in range(10)]
    assert not any(states), f"噪声阶段门不应打开: {states}"


def test_squelch_opens_on_signal():
    """-20dBFS 突发信号出现后，门应打开。"""
    sr = 48000.0
    block = 960
    sql = Squelch(threshold_db=-40.0, hang_ms=0.0,
                  attack_ms=5.0, decay_ms=50.0, sample_rate=sr)
    rng = np.random.default_rng(42)
    noise_amp = 10 ** (-60 / 20)
    sig_amp = 10 ** (-20 / 20)
    # 先喂噪声
    for _ in range(5):
        sql.push_rms_db(rms_dbfs(_noise_block(block, noise_amp, rng)), block)
    # 喂信号
    sig_states = [sql.push_rms_db(rms_dbfs(_noise_block(block, sig_amp, rng)), block)
                  for _ in range(5)]
    assert all(sig_states), f"信号阶段门应全打开: {sig_states}"


def test_squelch_hang_then_closes():
    """信号结束后，hang_ms 内门保持开；hang 结束后关门。"""
    sr = 48000.0
    block = 960  # 20ms
    hang_ms = 200.0
    sql = Squelch(threshold_db=-40.0, hang_ms=hang_ms,
                  attack_ms=5.0, decay_ms=30.0, sample_rate=sr)
    rng = np.random.default_rng(42)
    noise_amp = 10 ** (-60 / 20)
    sig_amp = 10 ** (-20 / 20)

    states = []
    # 5 块噪声
    for _ in range(5):
        states.append(sql.push_rms_db(rms_dbfs(_noise_block(block, noise_amp, rng)), block))
    # 5 块信号
    for _ in range(5):
        states.append(sql.push_rms_db(rms_dbfs(_noise_block(block, sig_amp, rng)), block))
    # 后续噪声：记录每块门状态
    after_signal_open = []
    for _ in range(30):  # 600ms
        after_signal_open.append(sql.push_rms_db(
            rms_dbfs(_noise_block(block, noise_amp, rng)), block))

    # 找最后一个 open=True 的块
    last_open = max(i for i, o in enumerate(after_signal_open) if o)
    first_closed = next(i for i, o in enumerate(after_signal_open) if not o)

    # hang=200ms = 10 块（20ms/块）。信号结束后平滑 RMS 衰减到阈值需约 3-5 块，
    # 然后 hang 200ms ≈ 10 块。总关门时刻应在信号结束后 ~250-350ms 内。
    # 即 after_signal_open 的第 ~12-17 块关门。
    assert first_closed > 5, f"hang 太短，关门太快: {first_closed}"
    assert first_closed < 25, f"hang 太长，关门太慢: {first_closed}"
    assert last_open == first_closed - 1


def test_squelch_apply_zeros_when_closed():
    """门关闭时 apply() 返回全零块。"""
    sr = 48000.0
    block = 960
    sql = Squelch(threshold_db=-40.0, hang_ms=0.0, sample_rate=sr)
    rng = np.random.default_rng(0)
    noise_amp = 10 ** (-60 / 20)
    blk = _noise_block(block, noise_amp, rng)
    out = sql.apply(blk)
    assert np.all(out == 0.0)


def test_squelch_apply_passes_when_open():
    """门打开时 apply() 原样返回块。"""
    sr = 48000.0
    block = 960
    sql = Squelch(threshold_db=-40.0, hang_ms=0.0, sample_rate=sr)
    rng = np.random.default_rng(0)
    sig_amp = 10 ** (-20 / 20)
    blk = _noise_block(block, sig_amp, rng)
    out = sql.apply(blk)
    np.testing.assert_array_equal(out, blk)
