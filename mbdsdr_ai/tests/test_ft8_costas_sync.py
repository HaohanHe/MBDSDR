#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
FT8 Costas 同步测试（合成音频 roundtrip）
========================================

mbdsdr_ai/ft8_costas.py 是 FT8 链的粗/精同步件，此前零直接测试。
用模块自带的 _synthesize_ft8_audio（ft8_encode 真实 79 音调，非手写假音调）
做端到端：合成 → costas_sync 粗同步 → three_stage_sync 精同步。

运行: python3 -m pytest mbdsdr_ai/tests/test_ft8_costas_sync.py -v
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.ft8_costas import (  # noqa: E402
    costas_sync, three_stage_sync, _synthesize_ft8_audio,
)

SR = 12000
TEXT = "CQ K1ABC OM74"


@pytest.fixture(scope="module")
def synth():
    """合成一条带噪声与已知频偏/时偏的 FT8 音频。"""
    samples, true_center = _synthesize_ft8_audio(
        TEXT, center_hz=1500.0, f_offset_hz=3.7, t_offset_s=0.5,
        sigma_noise=0.15, sr=SR, seed=0)
    return samples, true_center


# ---------------------------------------------------------------------------
# 粗同步
# ---------------------------------------------------------------------------
def test_costas_sync_finds_candidates(synth):
    samples, true_center = synth
    cands = costas_sync(samples, SR, search_lo_hz=300.0, search_hi_hz=3000.0)
    assert cands, "有信号时必须检出候选（诚实空态只在真无信号时）"
    best = cands[0]
    for key in ("center_hz", "time_offset_s", "sync_energy",
                "sync_ratio", "snr_est_db"):
        assert key in best, f"候选缺少字段 {key}"


def test_costas_sync_coarse_freq_accuracy(synth):
    samples, true_center = synth
    cands = costas_sync(samples, SR)
    best = cands[0]
    ferr = abs(best["center_hz"] - true_center)
    assert ferr < 2.0, f"粗同步频差应 < 2Hz，实际 {ferr:.2f}Hz"


def test_costas_sync_time_offset_accuracy(synth):
    samples, true_center = synth
    cands = costas_sync(samples, SR)
    best = cands[0]
    terr = abs(best["time_offset_s"] - 0.5)
    # 时间步长是 1/4 符号 = 40ms，允许一个步长误差
    assert terr < 0.05, f"时偏应贴近 0.5s，实际 {best['time_offset_s']:.3f}s"


def test_costas_sync_empty_and_short_inputs():
    """空/过短输入必须返回诚实空列表，不崩不编造。"""
    assert costas_sync([], SR) == []
    assert costas_sync([0.0] * 10, SR) == []


def test_costas_sync_pure_noise_no_false_high_confidence():
    """纯噪声不应给出高置信候选（假阳性=谎报信号）。"""
    rng = np.random.default_rng(42)
    noise = rng.normal(0.0, 1.0, SR * 15).tolist()
    cands = costas_sync(noise, SR)
    if cands:
        # 允许给出候选（噪声里也有峰值），但比值不应高到像真信号
        best = cands[0]
        assert best["sync_ratio"] < 3.0, \
            f"纯噪声 sync_ratio 过高，像假阳性: {best['sync_ratio']:.2f}"


# ---------------------------------------------------------------------------
# 精同步
# ---------------------------------------------------------------------------
def test_three_stage_sync_refines_frequency(synth):
    samples, true_center = synth
    cands = costas_sync(samples, SR)
    best = cands[0]
    ref = three_stage_sync(samples, SR, best)
    ferr = abs(ref["center_hz"] - true_center)
    assert ferr < 0.5, f"精同步频差应 < 0.5Hz，实际 {ferr:.3f}Hz"
    assert "residual_freq_hz" in ref
    assert "snr_est_db" in ref


def test_three_stage_sync_produces_energy_matrix(synth):
    """79×8 能量矩阵是下游解码的输入，形状必须对。"""
    samples, true_center = synth
    best = costas_sync(samples, SR)[0]
    ref = three_stage_sync(samples, SR, best)
    e = ref["energies_79"]
    assert len(e) == 79, f"必须 79 符号，实际 {len(e)}"
    assert all(len(row) == 8 for row in e), "每符号必须 8 音调能量"


def test_three_stage_sync_energies_non_negative(synth):
    """能量是幅度平方量，不得为负（数值异常=算法/输入出错）。"""
    samples, true_center = synth
    best = costas_sync(samples, SR)[0]
    ref = three_stage_sync(samples, SR, best)
    e = np.asarray(ref["energies_79"])
    assert np.all(e >= 0), "能量矩阵出现负值"


def test_three_stage_sync_correct_offset_recovers_tones(synth):
    """已知正确候选下，同步块（0/36/72）能量应显著高于随机位置。

    这是"同步真的在同步"的证据，不是数值形状检查。
    """
    samples, true_center = synth
    best = costas_sync(samples, SR)[0]
    ref = three_stage_sync(samples, SR, best)
    e = np.asarray(ref["energies_79"])
    sync_rows = [0, 36, 72]
    data_rows = [i for i in range(79) if i not in sync_rows
                 and not (1 <= i <= 6) and not (37 <= i <= 42)
                 and not (73 <= i <= 78)]
    sync_peak = e[sync_rows].max()
    data_mean = e[data_rows].mean()
    assert sync_peak > data_mean, \
        f"同步块峰值 {sync_peak:.2f} 应高于数据段均值 {data_mean:.2f}"


# ---------------------------------------------------------------------------
# 端到端（真实解码链）
# ---------------------------------------------------------------------------
def test_end_to_end_decode_recovers_message(synth):
    """合成 → 同步 → 解码 → 解出原文（真实 ft8_decode，非 mock）。"""
    samples, true_center = synth
    best = costas_sync(samples, SR)[0]
    ref = three_stage_sync(samples, SR, best)
    from mbdsdr_ai.ft8_decode import data_symbol_positions, decode_ft8_payload
    from mbdsdr_ai.ft8_unpack import unpack77

    data_pos = data_symbol_positions()
    e58 = np.asarray(ref["energies_79"])[data_pos, :].tolist()
    r = decode_ft8_payload(e58, snr_db=ref["snr_est_db"])
    assert r["crc_ok"] is True, f"CRC 必须过，实际 {r}"
    msg = unpack77(r["data_bits"])
    assert msg.get("text") == TEXT, f"必须解出原文，实际 {msg.get('text')!r}"
