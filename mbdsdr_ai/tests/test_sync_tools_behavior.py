# SPDX-License-Identifier: MIT
"""
sync_tools.py（同步/调制解调原子工具适配层）行为测试
====================================================

合成信号钉行为，oracle 来自被测语义：
  - _as_iq 三种 IQ 形状统一（交错 / Nx2 / 复数）；
  - FLL 对已知频偏纯音收敛（frequency_estimate ≈ ω）；
  - GMSK 调制→解调往返（高 SNR 无噪，比特必须逐位还原）；
  - iq_freq_track 对已知频率纯音恢复 STFT 峰值轨迹（时刻/频率/点数）；
  - SymbolSync 恢复符号数 = 样本数/sps；
  - Costas 环 <64 样本给诚实空态；频偏恰 0.0 时 Hz 换算仍展示
    （旧实现 `if off_hz` 把 0.0 当缺省吞掉）；
  - 所有参数/计算错误走统一错误信封（success=False + error）。

运行：python -m pytest mbdsdr_ai/tests/test_sync_tools_behavior.py -q
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.sync_tools import (  # noqa: E402
    _as_iq, _costas_track, _fll_track, _gmsk_demodulate, _gmsk_modulate,
    _iq_freq_track, _symbol_sync, register_sync_brick_tools,
    register_tool_registry,
)

TOOL_NAMES = {"fll_track", "symbol_sync_recover", "gmsk_modulate",
              "gmsk_demodulate", "iq_freq_track", "costas_track"}


def _tone(n: int, w: float) -> np.ndarray:
    return np.exp(1j * w * np.arange(n))


def _assert_error_envelope(res, keyword: str = ""):
    assert res.success is False
    assert res.data.get("error")
    assert res.content.startswith("参数/计算错误")
    if keyword:
        assert keyword in res.content


# ─────────────────────────── _as_iq ───────────────────────────

def test_as_iq_unifies_three_shapes():
    expect = np.array([1 + 2j, 3 + 4j])
    interleaved = [1.0, 2.0, 3.0, 4.0]
    pairs = [[1.0, 2.0], [3.0, 4.0]]
    cplx = [1 + 2j, 3 + 4j]
    for src in (interleaved, pairs, cplx):
        got = _as_iq(src)
        assert np.allclose(got, expect)


def test_as_iq_odd_length_raises():
    with pytest.raises(ValueError):
        _as_iq([1.0, 2.0, 3.0])


# ─────────────────────────── FLL ───────────────────────────

def test_fll_recovers_known_frequency_offset():
    w = 0.05  # rad/sample
    res = _fll_track({"iq": _tone(2000, w).tolist()})
    assert res.success is True
    est = res.data["frequency_estimate_rad_per_sample"]
    assert abs(est - w) < 1e-3
    assert len(res.data["corrected_iq"]) == 2000


def test_fll_bad_input_error_envelope():
    _assert_error_envelope(_fll_track({"iq": [1.0, 2.0, 3.0]}))


# ─────────────────────────── GMSK ───────────────────────────

@pytest.mark.parametrize("sps,bt", [(8, 0.3), (4, 0.3), (8, 0.5), (4, 0.5)])
def test_gmsk_modulate_demodulate_roundtrip(sps, bt):
    bits = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0]
    mod = _gmsk_modulate({"bits": bits, "sps": sps, "bt": bt})
    assert mod.success is True
    assert mod.data["samples"] == len(bits) * sps
    dem = _gmsk_demodulate({"iq": mod.data["iq"], "sps": sps})
    assert dem.success is True
    assert dem.data["bits"] == bits


def test_gmsk_error_envelopes():
    _assert_error_envelope(_gmsk_modulate({"bits": []}))
    _assert_error_envelope(_gmsk_modulate({"bits": [0, 2]}))
    _assert_error_envelope(_gmsk_demodulate({"iq": [1 + 0j] * 4, "sps": 8}))


# ─────────────────────────── 频率轨迹 ───────────────────────────

def test_freq_track_recovers_tone_frequency():
    fs, f0 = 16000.0, 1000.0
    iq = _tone(4096, 2 * math.pi * f0 / fs)
    res = _iq_freq_track({"iq": iq.tolist(), "sample_rate": fs,
                          "window": 1024, "hop": 512})
    assert res.success is True
    assert res.data["count"] == 7
    freqs = res.data["freqs_hz"]
    assert all(abs(f - f0) < 0.05 for f in freqs)
    assert res.data["times_s"][0] == pytest.approx((0 + 512) / fs)
    assert res.data["times_s"][-1] == pytest.approx((6 * 512 + 512) / fs)


def test_freq_track_param_error_envelopes():
    iq = _tone(64, 0.1).tolist()
    _assert_error_envelope(_iq_freq_track({"iq": iq, "sample_rate": 1000,
                                          "window": 4}))
    _assert_error_envelope(_iq_freq_track({"iq": iq, "sample_rate": 1000,
                                          "window": 64, "hop": 100}))
    _assert_error_envelope(_iq_freq_track({"iq": iq, "sample_rate": 1000,
                                          "window": 128}))


# ─────────────────────────── 符号同步 ───────────────────────────

def test_symbol_sync_recovers_symbol_count():
    res = _symbol_sync({"iq": (_tone(200, 0.0)).tolist(),
                        "samples_per_symbol": 8})
    assert res.success is True
    assert res.data["count"] == 25          # 200 样本 / sps=8
    assert res.data["sps_converged"] == 8.0
    assert len(res.data["symbols"]) == 25


def test_symbol_sync_error_envelopes():
    _assert_error_envelope(_symbol_sync({"iq": [1 + 0j] * 16,
                                         "samples_per_symbol": 8,
                                         "ted": "bogus"}), "未知 TED")
    _assert_error_envelope(_symbol_sync({"iq": [1 + 0j] * 16}),
                           "缺少必需参数")


# ─────────────────────────── Costas ───────────────────────────

def test_costas_short_input_honest_empty():
    # 诚实空态：不足 64 样本拒绝伪造锁定结果
    res = _costas_track({"iq": _tone(63, 0.01).tolist()})
    assert res.success is False
    assert "64" in res.content
    assert "诚实空态" in res.content


def test_costas_zero_offset_still_shows_hz():
    # 频偏恰 0.0 时旧实现 `if off_hz else` 把 0.0 当缺省，吞掉 Hz 换算
    res = _costas_track({"iq": [1 + 1j] * 200, "psk_order": 4,
                         "sample_rate_hz": 1000})
    assert res.success is True
    assert res.data["carrier_offset_rad_per_sample"] == 0.0
    assert res.data["carrier_offset_hz"] == 0.0
    assert "0.0 Hz @ 1000 Sa/s" in res.content


@pytest.mark.parametrize("psk_order", [2, 4])
def test_costas_recovers_known_offset(psk_order):
    w = 0.02
    res = _costas_track({"iq": _tone(2000, w).tolist(),
                         "psk_order": psk_order, "loop_bw": 0.05,
                         "sample_rate_hz": 8000})
    assert res.success is True
    assert abs(res.data["carrier_offset_rad_per_sample"] - w) < 5e-3
    assert res.data["carrier_offset_hz"] == pytest.approx(
        res.data["carrier_offset_rad_per_sample"] * 8000)
    assert len(res.data["corrected_iq"]) == 2000


# ─────────────────────────── 注册 ───────────────────────────

def test_register_exposes_each_brick_as_small_tool():
    from mbdsdr_ai.tool_registry import ToolRegistry
    reg = ToolRegistry()
    register_sync_brick_tools(reg)
    assert TOOL_NAMES <= set(reg.tools)
    reg2 = ToolRegistry()
    register_tool_registry(reg2)  # CLI 面入口（pkgutil 发现）
    assert TOOL_NAMES <= set(reg2.tools)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
