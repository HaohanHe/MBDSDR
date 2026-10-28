# SPDX-License-Identifier: MIT
"""
signal_quality.py（信号质量/静噪门控）行为测试
==============================================

合成 IQ 钉行为，oracle 为闭式计算（读代码定语义）：
  - dc/不平衡/PAPR/平均相位：常量信号可手算（±180 dB、0 dB、0°/90°）；
  - 平均相位是圆均值（跨 ±π 边界不得算成 0°）；
  - squelch_gate 按块 RSSI(dBFS，满量程 1.0) 给占空比；
  - 空输入/非法 block 抛 ValueError（旧实现 ZeroDivisionError/裸 numpy 崩）。

运行：python -m pytest mbdsdr_ai/tests/test_signal_quality_behavior.py -q
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.signal_quality import signal_quality, squelch_gate  # noqa: E402


# ─────────────────────────── signal_quality ───────────────────────────

def test_constant_real_signal_closed_form():
    r = signal_quality(np.array([1 + 0j] * 4))
    assert r["dc_offset_i"] == 1.0
    assert r["dc_offset_q"] == 0.0
    assert r["iq_imbalance_db"] == 180.0   # 20*log10((1+1e-9)/(0+1e-9))
    assert r["rms"] == 1.0 and r["peak"] == 1.0
    assert r["papr_db"] == 0.0
    assert r["mean_phase_deg"] == 0.0
    assert r["sample_count"] == 4


def test_pure_quadrature_signal_closed_form():
    r = signal_quality(np.array([0 + 1j] * 4))
    assert r["dc_offset_q"] == 1.0
    assert r["iq_imbalance_db"] == -180.0
    assert r["mean_phase_deg"] == 90.0


def test_balanced_iq_zero_imbalance():
    r = signal_quality(np.array([1 + 0j, 0 + 1j]))
    assert r["iq_imbalance_db"] == 0.0
    assert r["papr_db"] == 0.0


def test_mean_phase_circular_wraparound():
    # ±135° 两样本：算术平均会得 0°，圆均值必须得 180°（±180 皆可）
    ang = np.exp(1j * np.radians([135.0, -135.0]))
    r = signal_quality(ang)
    assert abs(abs(r["mean_phase_deg"]) - 180.0) < 0.01


def test_signal_quality_empty_raises():
    # 旧实现在空输入上崩裸 numpy 错（np.max 空数组）
    with pytest.raises(ValueError):
        signal_quality(np.zeros(0, dtype=complex))


# ─────────────────────────── squelch_gate ───────────────────────────

def test_squelch_two_block_duty_cycle():
    # 块1 近满量程噪声 1e-4（-80 dBFS），块2 幅度 0.5（-6.02 dBFS）
    x = np.concatenate([np.full(1024, 1e-4 + 0j), np.full(1024, 0.5 + 0j)])
    r = squelch_gate(x, threshold_db=-50.0, block=1024)
    assert r["block_count"] == 2
    assert r["duty_cycle"] == 0.5
    assert r["open"] is True
    assert r["peak_rssi_dbfs"] == pytest.approx(-6.02, abs=0.05)
    assert r["rssi_dbfs"] == pytest.approx(-43.01, abs=0.1)
    assert r["threshold_db"] == -50.0


def test_squelch_all_below_threshold_closed():
    x = np.full(2048, 1e-4 + 0j)  # -80 dBFS
    r = squelch_gate(x, threshold_db=-50.0, block=1024)
    assert r["open"] is False
    assert r["duty_cycle"] == 0.0
    assert r["peak_rssi_dbfs"] < -50.0


def test_squelch_short_input_single_block():
    r = squelch_gate(np.full(10, 0.5 + 0j), threshold_db=-50.0, block=1024)
    assert r["block_count"] == 1
    assert r["duty_cycle"] == 1.0
    assert r["open"] is True


def test_squelch_invalid_inputs_raise():
    # 旧实现空输入 0//0 ZeroDivisionError、block=0 同样除零
    with pytest.raises(ValueError):
        squelch_gate(np.zeros(0, dtype=complex))
    with pytest.raises(ValueError):
        squelch_gate(np.ones(8, dtype=complex), block=0)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
