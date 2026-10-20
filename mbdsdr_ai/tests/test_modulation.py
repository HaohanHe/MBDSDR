# SPDX-License-Identifier: MIT
"""统一调制/解调内核与积木工具的行为测试。

覆盖：
- 无噪往返零误码（BPSK/QPSK/8PSK，NRZ 与 RRC 两种成形）
- 实测 BER 与理论值同量级对照（BPSK/QPSK）
- 星座质量：无噪 EVM 近零、加噪 EVM 上升
- ASK 幅度判决（bit1=有载波）与"不给理论对照"的诚实标注
- 工具层：注册可用、参数错误诚实失败
"""
import math
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.fir_taps import root_raised_cosine_taps
from mbdsdr_ai.modulation import (SCHEMES, constellation_metrics,
                                  demodulate_iq, link_ber_test,
                                  modulate_bits, register_modulation_tools,
                                  theoretical_ber)
from mbdsdr_ai.tool_registry import ToolRegistry


@pytest.fixture()
def reg():
    r = ToolRegistry()
    register_modulation_tools(r)
    return r


def _bits(n, seed=11):
    return np.random.default_rng(seed).integers(0, 2, n).astype(np.int8)


# ── 无噪往返 ─────────────────────────────────────────────────
@pytest.mark.parametrize("scheme", ["bpsk", "qpsk", "8psk"])
@pytest.mark.parametrize("shaping", ["rect", "rrc"])
def test_roundtrip_zero_errors(scheme, shaping):
    """无噪往返必须零误码（调制/解调/判决自洽）。"""
    # 多比特调制要求比特数为每符号位数的整数倍
    k = {"bpsk": 1, "qpsk": 2, "8psk": 3}[scheme]
    bits = _bits(999 * k)
    mod = modulate_bits(bits, scheme, sps=4, shaping=shaping, alpha=0.35)
    dem = demodulate_iq(mod["waveform"], scheme, sps=4, shaping=shaping, alpha=0.35)
    n = min(len(bits), len(dem["bits"]))
    assert int(np.sum(bits[:n] != np.asarray(dem["bits"][:n]))) == 0


def test_ask_roundtrip_zero_errors():
    """ASK（OOK）：bit1=有载波，判决按有载波电平门限。"""
    bits = _bits(600)
    mod = modulate_bits(bits, "ask", sps=4, shaping="rrc", alpha=0.35)
    dem = demodulate_iq(mod["waveform"], "ask", sps=4, shaping="rrc", alpha=0.35)
    n = min(len(bits), len(dem["bits"]))
    assert int(np.sum(bits[:n] != np.asarray(dem["bits"][:n]))) == 0


def test_ask_symbol_semantics():
    """ASK 星座点：索引即位值，bit1 对应有载波幅度。"""
    from mbdsdr_ai.modulation import symbol_points
    pts = symbol_points("ask")
    assert abs(pts[0]) < 1e-12      # bit0 → 静默
    assert abs(pts[1] - 1.0) < 1e-12  # bit1 → 满载波


# ── BER 与理论对照 ───────────────────────────────────────────
@pytest.mark.parametrize("scheme", ["bpsk", "qpsk"])
@pytest.mark.parametrize("ebn0", [4.0, 8.0])
def test_ber_matches_theory(scheme, ebn0):
    """实测 BER 与理论值同量级（比值 0.4–2.5 内，样本量有限允许偏离）。"""
    r = link_ber_test(scheme, n_bits=20000, ebn0_db=ebn0, sps=4, shaping="rrc")
    theo = r["theoretical_ber"]
    assert theo > 0
    ratio = r["measured_ber"] / theo
    assert 0.4 < ratio < 2.5, (scheme, ebn0, r["measured_ber"], theo)


def test_ber_decreases_with_snr():
    """Eb/N0 升高，实测 BER 必须单调下降（链路实现自洽）。"""
    bers = [link_ber_test("bpsk", n_bits=20000, ebn0_db=e, sps=4)["measured_ber"]
            for e in (2.0, 6.0, 10.0)]
    assert bers[0] > bers[1] >= bers[2]


def test_ask_has_no_theory_comparison():
    """ASK 不给理论对照（口径依赖），如实返回 nan 并在文本里说明。"""
    r = link_ber_test("ask", n_bits=4000, ebn0_db=6.0, sps=4)
    assert math.isnan(r["theoretical_ber"])
    assert r["measured_ber"] >= 0.0


def test_theoretical_ber_values():
    """理论式取值：BPSK Q(sqrt(2Eb/N0))。"""
    # 0 dB：Q(sqrt(2)) = 0.5*erfc(1) ≈ 0.0786
    assert theoretical_ber("bpsk", 0.0) == pytest.approx(0.0786, abs=1e-3)
    assert theoretical_ber("bpsk", 10.0) < theoretical_ber("bpsk", 0.0)


# ── 星座质量 ─────────────────────────────────────────────────
def test_constellation_clean_is_small_evm():
    """无噪 QPSK：EVM 接近 0。"""
    bits = _bits(2000)
    mod = modulate_bits(bits, "qpsk", sps=4, shaping="rrc")
    dem = demodulate_iq(mod["waveform"], "qpsk", sps=4, shaping="rrc")
    m = constellation_metrics(dem["symbols"][20:-20], "qpsk")
    assert m["evm_rms_pct"] < 5.0
    assert m["symbol_error_rate"] == 0.0


def test_constellation_noise_raises_evm():
    """加噪后 EVM 显著上升（度量灵敏）。"""
    bits = _bits(2000)
    mod = modulate_bits(bits, "qpsk", sps=4, shaping="rrc")
    rng = np.random.default_rng(3)
    noisy = mod["waveform"].astype(np.complex128) + 0.1 * (
        rng.standard_normal(len(mod["waveform"]))
        + 1j * rng.standard_normal(len(mod["waveform"])))
    dem = demodulate_iq(noisy, "qpsk", sps=4, shaping="rrc")
    clean = constellation_metrics(
        demodulate_iq(mod["waveform"], "qpsk", sps=4, shaping="rrc")["symbols"][20:-20],
        "qpsk")
    dirty = constellation_metrics(dem["symbols"][20:-20], "qpsk")
    assert dirty["evm_rms_pct"] > clean["evm_rms_pct"] * 2


# ── 工具层 ───────────────────────────────────────────────────
def test_tools_registered(reg):
    for name in ("modulate_symbols", "demodulate_symbols", "constellation_metrics",
                 "measure_ber", "link_ber_test", "pulse_shape_taps"):
        assert name in reg.tools, name


def test_tool_modulate_demodulate_roundtrip(reg):
    bits = [0, 1, 1, 0, 1, 0, 0, 1]
    m = reg.call("modulate_symbols", {"bits": bits, "scheme": "bpsk", "sps": 4})
    assert m.success is True
    d = reg.call("demodulate_symbols", {"iq": m.data["waveform"], "scheme": "bpsk",
                                        "sps": 4})
    assert d.success is True
    assert d.data["bits"][:len(bits)] == bits


def test_tool_measure_ber(reg):
    r = reg.call("measure_ber", {"bits_tx": [1, 0, 1, 1], "bits_rx": [1, 1, 1, 1]})
    assert r.success is True
    assert r.data["ber"] == pytest.approx(0.25)


def test_tool_link_ber_test(reg):
    r = reg.call("link_ber_test", {"scheme": "bpsk", "n_bits": 4000, "ebn0_db": 8.0})
    assert r.success is True
    assert 0.0 <= r.data["measured_ber"] <= 1.0
    assert r.data["theoretical_ber"] > 0


def test_tool_pulse_shape_taps_matches_fir_taps(reg):
    """成形抽头与 fir_taps 单一来源一致（不重复实现）。"""
    r = reg.call("pulse_shape_taps", {"sps": 4, "alpha": 0.35, "num_taps": 41})
    assert r.success is True
    ref = root_raised_cosine_taps(41, 0.35, 1.0, 4.0)
    assert np.allclose(np.asarray(r.data["taps"]), ref)


# ── 诚实失败 ─────────────────────────────────────────────────
def test_unknown_scheme_fails(reg):
    assert reg.call("modulate_symbols", {"bits": [0, 1], "scheme": "psk9"}).success is False
    assert reg.call("demodulate_symbols", {"iq": [0, 1], "scheme": "psk9"}).success is False


def test_non_binary_bits_fail(reg):
    assert reg.call("modulate_symbols", {"bits": [0, 2, 1]}).success is False


def test_empty_inputs_fail(reg):
    assert reg.call("modulate_symbols", {"bits": []}).success is False
    assert reg.call("demodulate_symbols", {"iq": []}).success is False
    assert reg.call("constellation_metrics", {"symbols": []}).success is False


def test_odd_bit_count_for_multibit_scheme_fails():
    """QPSK 要求比特数为 2 的整数倍。"""
    with pytest.raises(ValueError):
        modulate_bits([0, 1, 0], "qpsk")


# ── 映射参数化（gray/binary）与外部收敛等价性 ──
def test_gray_mapping_matches_legacy_qpsk():
    """Gray 映射与历史 QPSK 映射逐点一致（防回归漂移）。"""
    from mbdsdr_ai.freedv_modem import bits_to_qpsk
    from mbdsdr_ai.modulation import symbol_points
    bits = np.array([0, 0, 0, 1, 1, 0, 1, 1], dtype=np.uint8)
    pts = symbol_points("qpsk", "gray")
    expect = np.array([pts[(b0 << 1) | b1]
                       for b0, b1 in zip(bits[0::2], bits[1::2])])
    assert np.allclose(bits_to_qpsk(bits), expect)


def test_gray_mapping_is_single_bit_adjacent():
    """Gray 映射：相邻位值对应的星座点只差 1 bit。"""
    from mbdsdr_ai.modulation import symbol_points
    pts = symbol_points("qpsk", "gray")
    # 灰序码的性质：位值测（泰量距离）为 1 的两个位值，
    # 对应星座点只差 1 bit（I/Q 中恰好一个变化）
    for v in range(4):
        for w in range(v + 1, 4):
            if bin(v ^ w).count("1") != 1:
                continue
            a, b = pts[v], pts[w]
            d = int(np.sign(a.real) != np.sign(b.real)) + \
                int(np.sign(a.imag) != np.sign(b.imag))
            assert d == 1, (v, w, a, b)


def test_gray_roundtrip_zero_errors():
    """Gray 映射下无噪往返零误码。"""
    bits = _bits(998 * 2)
    mod = modulate_bits(bits, "qpsk", sps=4, shaping="rrc", mapping="gray")
    dem = demodulate_iq(mod["waveform"], "qpsk", sps=4, shaping="rrc",
                        mapping="gray")
    n = min(len(bits), len(dem["bits"]))
    assert int(np.sum(bits[:n] != np.asarray(dem["bits"][:n]))) == 0


def test_binary_and_gray_differ_but_both_valid():
    """binary 与 gray 映射不同（映射不得被静默写死），但各自都自洽。"""
    from mbdsdr_ai.modulation import symbol_points
    b = symbol_points("qpsk", "binary")
    g = symbol_points("qpsk", "gray")
    assert not np.allclose(b, g)
    for pts in (b, g):
        # QPSK 点幅度为 1（I/Q 各分量 1/sqrt(2)）
        assert np.allclose(np.abs(pts), 1.0)
        assert np.allclose(np.abs(pts.real), 1.0 / np.sqrt(2.0))


def test_unknown_mapping_fails():
    with pytest.raises(ValueError):
        modulate_bits([0, 1, 1, 0], "qpsk", mapping="weird")
