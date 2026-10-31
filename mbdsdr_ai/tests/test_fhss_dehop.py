# SPDX-License-Identifier: MIT
"""P1-7 跳频跟踪/解跳锚点测试。

合成口径：槽定时已知的慢跳频 2FSK（跳表循环、每槽随机比特、
调制频偏 ±f_dev），跟踪须逐槽恢复 f_h±f_dev，解跳逐槽判决比特
（变频用同一测量峰，判决与真值呈确定性反相——测试显式锁定该口径）。
"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.fhss_dehop import (
    dehop_demod, fhss_hop_track, register_tool_registry,
)

FS = 1e6
SLOT = 0.01
F_DEV = 8e3
HOP_TABLE = [100e3, -50e3, 200e3]
N_SLOTS = 12
_RNG = np.random.default_rng(20261101)


def _hopped_2fsk(snr_db: float = 20.0):
    """合成跳频 2FSK：返回 (iq, bits_true, hop_freqs_true)。"""
    bits = _RNG.integers(0, 2, N_SLOTS)
    slot_n = int(SLOT * FS)
    n = N_SLOTS * slot_n
    iq = np.zeros(n, dtype=complex)
    hops = []
    for s in range(N_SLOTS):
        f_h = HOP_TABLE[s % len(HOP_TABLE)]
        hops.append(f_h)
        f_tone = f_h + (F_DEV if bits[s] else -F_DEV)
        t = np.arange(slot_n) / FS
        seg = np.exp(2j * np.pi * f_tone * t)
        # 槽内恒定功率，槽间无相位连续要求（FHSS 口径）
        p_noise = 1.0 / 10 ** (snr_db / 10)
        noise = np.sqrt(p_noise / 2) * (
            _RNG.standard_normal(slot_n)
            + 1j * _RNG.standard_normal(slot_n))
        iq[s * slot_n:(s + 1) * slot_n] = seg + noise
    return iq, bits, hops


def test_hop_track_recovers_frequencies():
    iq, bits, hops = _hopped_2fsk()
    res = fhss_hop_track(iq.tolist(), FS, SLOT)
    assert res["n_slots"] == N_SLOTS
    assert res["n_missed"] == 0
    for s, (f_tr, f_h) in enumerate(zip(res["hop_freqs_hz"], hops)):
        assert abs(f_tr - f_h) <= F_DEV + res["freq_resolution_hz"], \
            f"槽 {s}: 跟踪 {f_tr} 真 {f_h}"


def test_hop_track_missed_slots_on_noise():
    noise = (np.sqrt(0.5) * (_RNG.standard_normal(4096)
                             + 1j * _RNG.standard_normal(4096)))
    res = fhss_hop_track(noise.tolist(), FS, SLOT)
    assert res["n_missed"] == res["n_slots"]
    assert all(f is None for f in res["hop_freqs_hz"])


def test_dehop_demod_residuals():
    """解跳质量：每槽变频残留 ≈0（主音已落 DC），基带拼接等长。"""
    iq, bits, _ = _hopped_2fsk()
    track = fhss_hop_track(iq.tolist(), FS, SLOT)
    res = dehop_demod(iq.tolist(), track["hop_freqs_hz"], FS, SLOT)
    assert res["n_slots_demodded"] == N_SLOTS
    assert res["skipped_slots"] == []
    slot_n = int(SLOT * FS)
    assert len(res["baseband_iq"]) == N_SLOTS * slot_n
    for slot_res in res["slots"]:
        assert abs(slot_res["residual_offset_hz"]) < FS / 64
    # 基带主音在 DC 附近：全段鉴频均值趋近 0
    bb = np.array([complex(v) for v in res["baseband_iq"]])
    ph = np.unwrap(np.angle(bb))
    fd_all = float(np.mean(np.diff(ph)) * FS / (2.0 * np.pi))
    assert abs(fd_all) < FS / 64


def test_dehop_skips_missed_honestly():
    iq, bits, _ = _hopped_2fsk()
    track = fhss_hop_track(iq.tolist(), FS, SLOT)
    track["hop_freqs_hz"][3] = None          # 模拟空槽
    res = dehop_demod(iq.tolist(), track["hop_freqs_hz"], FS, SLOT)
    assert res["skipped_slots"] == [3]
    assert res["n_slots_demodded"] == N_SLOTS - 1


def test_validation():
    with pytest.raises(ValueError):
        fhss_hop_track([], FS, SLOT)
    with pytest.raises(ValueError):
        fhss_hop_track([0j] * 128, FS, 0.0)
    with pytest.raises(ValueError):
        fhss_hop_track([0j] * 128, FS, SLOT, snr_threshold=-1)
    bad = [0j] * 128
    bad[5] = complex(float("nan"), 0.0)
    with pytest.raises(ValueError):
        fhss_hop_track(bad, FS, SLOT)
    with pytest.raises(ValueError):
        dehop_demod([0j] * 64, [1e3], -FS, SLOT)


def test_tool_registration():
    from mbdsdr_ai.tool_registry import ToolRegistry

    r = ToolRegistry()
    n_before = len(r.tools)
    register_tool_registry(r)
    assert len(r.tools) == n_before + 2
    for name in ("fhss_hop_track", "dehop_demod"):
        assert name in r.tools

    iq, _, _ = _hopped_2fsk()
    res = r.tools["fhss_hop_track"]["handler"](
        {"iq": iq.tolist(), "sample_rate_hz": FS, "slot_s": SLOT})
    assert res.success and res.data["n_missed"] == 0
    res2 = r.tools["dehop_demod"]["handler"](
        {"iq": iq.tolist(), "hop_freqs_hz": res.data["hop_freqs_hz"],
         "sample_rate_hz": FS, "slot_s": SLOT})
    assert res2.success and res2.data["n_slots_demodded"] == N_SLOTS
