# SPDX-License-Identifier: MIT
"""
S 表单元测试
==============

覆盖：已知功率→dBm、S-unit 映射、峰值保持、AI 异常检测。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from mbdsdr_ai.s_meter import (
    SMeter, rms_dbm, dbm_to_s_unit, _S0_DBM, _S9_DBM,
)


# ── 1. 已知功率 → dBm ──────────────────────────────────────────

def test_rms_dbm_known_tone():
    # 造一个 RMS 幅度 A 的单音：|z|=A 恒定，功率 = A^2
    A = 0.1
    fs = 48000.0
    t = np.arange(4096) / fs
    tone = A * np.exp(2j * np.pi * 1000.0 * t)
    # ref=-10 表示 0 dBFS = +10 dBm
    dbm = rms_dbm(tone, ref_dbfs_per_dbm=-10.0)
    # power_dbfs = 20log10(A) = -20 dBFS；dBm = -20 - (-10) = -10 dBm
    expected = 20.0 * np.log10(A) - (-10.0)
    assert abs(dbm - expected) < 0.1, f"got {dbm}, want {expected}"


# ── 2. S-unit 映射 ────────────────────────────────────────────

def test_s_unit_endpoints():
    # S0 = -127 dBm
    s, label = dbm_to_s_unit(_S0_DBM)
    assert label == "S0"
    assert s == 0.0
    # S9 = -73 dBm
    s, label = dbm_to_s_unit(_S9_DBM)
    assert label == "S9"
    assert abs(s - 9.0) < 1e-9
    # S9+10 = -63 dBm
    s, label = dbm_to_s_unit(_S9_DBM + 10.0)
    assert label == "S9+10"
    assert abs(s - 10.0) < 1e-9


def test_s_unit_below_s0_clamps():
    s, label = dbm_to_s_unit(-200.0)
    assert label == "S0"
    assert s == 0.0


def test_s_unit_non_finite_is_no_reading():
    """NaN/±Inf：必须返回「没有读数」，不抛 ValueError，也不伪装成 S0。

    反证：旧实现走 ``int(round(nan))`` → ValueError；``inf`` 会得到
    ``S9+inf`` 这种无法显示的字符串。
    """
    for bad in (float("nan"), float("inf"), float("-inf")):
        s, label = dbm_to_s_unit(bad)
        assert math.isnan(s), f"{bad!r} 应返回 NaN 数值，得到 {s!r}"
        assert label == "—", f"{bad!r} 应显示「—」，得到 {label!r}"


def test_push_non_finite_does_not_poison_state():
    """非有限块不污染平滑值/峰值，且 s_unit 仍可安全取用。"""
    m = SMeter(peak_decay_db=0.0)
    good = 0.1 * np.ones(1024, dtype=np.complex64)
    m.push(good)
    dbm_good, peak_good = m.dbm, m.peak

    for bad in (np.full(1024, np.nan, dtype=np.complex64),
                np.full(1024, np.inf, dtype=np.complex64)):
        assert math.isnan(m.push(bad)), "非有限读数必须显式返回 NaN"
        assert m.dbm == dbm_good, "平滑值被非有限读数污染"
        assert m.peak == peak_good, "峰值被非有限读数污染"

    # 仍能安全读 S 单位（不抛，且保留最后一次有效读数）
    s, label = m.s_unit
    assert math.isfinite(s)
    assert label.startswith("S") and label != "—"


# ── 3. 峰值保持 ────────────────────────────────────────────────

def test_peak_hold():
    m = SMeter(peak_decay_db=0.0)
    # 强信号
    strong = 0.5 * np.ones(1024, dtype=np.complex64)
    m.push(strong)
    p_strong = m.peak
    # 弱信号（多次 push 让 EMA 平滑值追上弱信号）
    weak = 0.001 * np.ones(1024, dtype=np.complex64)
    for _ in range(30):
        m.push(weak)
    # 峰值应保持在强信号电平（无衰减）
    assert m.peak >= p_strong - 0.01
    # 平滑值应落到弱信号附近（远低于强信号）
    assert m.dbm < p_strong - 20.0


# ── 4. AI 异常检测 ────────────────────────────────────────────

def test_anomaly_rise():
    m = SMeter(anomaly_threshold_db=12.0, anomaly_window=8)
    # 先喂 8 块弱信号建立基线
    weak = 0.001 * np.ones(2048, dtype=np.complex64)
    for _ in range(8):
        m.push(weak)
    assert m.alarm == ""
    # 突然强信号（>20 dB）
    strong = 0.3 * np.ones(2048, dtype=np.complex64)
    m.push(strong)
    assert m.alarm == "rise"


def test_anomaly_drop():
    m = SMeter(anomaly_threshold_db=12.0, anomaly_window=8)
    strong = 0.3 * np.ones(2048, dtype=np.complex64)
    for _ in range(8):
        m.push(strong)
    weak = 0.001 * np.ones(2048, dtype=np.complex64)
    m.push(weak)
    assert m.alarm == "drop"
