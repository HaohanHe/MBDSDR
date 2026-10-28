# SPDX-License-Identifier: MIT
"""
digital_modes.py（FT8/FT4 参数与结果解析）行为测试
==================================================

oracle 来源：
  - FT8/FT4 物理层参数为公开协议事实（QEX "FT4 and FT8 Protocols" /
    ft8_lib constants.h / WSJT-X 文档）：FT8 15s、8FSK、6.25 Hz 音隔、
    160 ms 符号、50 Hz 带宽；FT4 7.5s、4FSK、20.83 Hz 音隔、48 ms 符号、
    ~90 Hz 带宽；两者同用 LDPC(174,91) + 14 位 CRC；
  - LDPC 参数再与仓内核 ft8_ldpc.py（单一事实源）交叉钉扎；
  - WSJT-X 日志解析用已知向量行（含负 dt——真实日志有 -0.3 这类值）；
  - Viterbi 桥接用 (171,133) 整数位掩码卷积码（与 demod.ViterbiDecoder
    转移表一致）自编自解 + 少量位翻转纠错钉行为。

运行：python -m pytest mbdsdr_ai/tests/test_digital_modes_behavior.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai import digital_modes as dm  # noqa: E402
from mbdsdr_ai.digital_modes import (  # noqa: E402
    FT4_PARAMS, FT8_PARAMS, FT_FREQUENCIES, WSJTInterface,
    decode_viterbi, get_ft_params, get_qpsk_viterbi,
    list_ft_frequencies,
)


# ─────────────────────────── 公开协议参数 ───────────────────────────

def test_ft8_params_public_protocol_facts():
    assert FT8_PARAMS["period_s"] == 15
    assert FT8_PARAMS["modulation"] == "8FSK"
    assert FT8_PARAMS["tone_spacing_hz"] == 6.25
    assert FT8_PARAMS["symbol_duration_ms"] == 160       # = 1/6.25 Hz
    assert FT8_PARAMS["bandwidth_hz"] == 50              # 8 音 6.25 Hz 占 50 Hz
    assert FT8_PARAMS["coding"] == "LDPC (K=91, N=174)"  # 77 消息 + 14 CRC
    assert FT8_PARAMS["crc"] == "14-bit"


def test_ft4_params_public_protocol_facts():
    assert FT4_PARAMS["period_s"] == 7.5
    assert FT4_PARAMS["modulation"] == "4FSK"
    assert FT4_PARAMS["tone_spacing_hz"] == 20.83        # = 1/0.048 s
    assert FT4_PARAMS["symbol_duration_ms"] == 48
    assert FT4_PARAMS["coding"] == "LDPC (K=91, N=174)"  # 与 FT8 同一码
    assert FT4_PARAMS["crc"] == "14-bit"


def test_ldpc_strings_match_in_repo_kernel():
    # 仓内 ft8_ldpc.py 是 (174,91) 单一事实源，参数表不得另说一套
    from mbdsdr_ai import ft8_ldpc
    want = f"LDPC (K={ft8_ldpc._K}, N={ft8_ldpc._N})"
    assert FT8_PARAMS["coding"] == want
    assert FT4_PARAMS["coding"] == want


# ─────────────────────────── 频率表 / 分发 ───────────────────────────

def test_list_ft_frequencies_units_and_values():
    rows = list_ft_frequencies()
    assert len(rows) == len(FT_FREQUENCIES) == 11
    for row in rows:
        src = FT_FREQUENCIES[row["band"]]
        assert row["ft8_mhz"] == src["FT8"] / 1e6
        assert row["ft4_mhz"] == src["FT4"] / 1e6
    by_band = {r["band"]: r for r in rows}
    assert by_band["20m"]["ft8_mhz"] == pytest.approx(14.074)
    assert by_band["20m"]["ft4_mhz"] == pytest.approx(14.080)
    assert by_band["40m"]["ft8_mhz"] == pytest.approx(7.074)


def test_get_ft_params_dispatch_and_unknown_mode():
    assert get_ft_params("FT8") is FT8_PARAMS
    assert get_ft_params("ft8") is FT8_PARAMS
    assert get_ft_params("FT4") is FT4_PARAMS
    assert get_ft_params("ft4") is FT4_PARAMS
    # 未知模式不得静默返回 FT4 参数（旧实现 else 分支吞掉一切）
    with pytest.raises(ValueError):
        get_ft_params("JT9")


# ─────────────────────────── WSJT-X 日志解析 ───────────────────────────

def test_wsjt_log_parsing_known_vector(tmp_path):
    log = tmp_path / "wsjtx_all.txt"
    log.write_text(
        "2230 -15 -0.3 14074.0 CQ BI1AAA\n"
        "2231 5 0.4 14075.0 BI1AAA BI1BBB -10\n",
        encoding="utf-8")
    msgs = WSJTInterface().get_decoded_messages(str(log))
    assert msgs == [
        {"time": "2230", "snr": "-15", "dt": "-0.3", "freq": "14074.0",
         "message": "CQ BI1AAA"},
        {"time": "2231", "snr": "5", "dt": "0.4", "freq": "14075.0",
         "message": "BI1AAA BI1BBB -10"},
    ]


def test_wsjt_log_edges_empty_missing_short_lines(tmp_path):
    w = WSJTInterface()
    assert w.get_decoded_messages(str(tmp_path / "nope.txt")) == []
    assert w.get_decoded_messages(None) == []
    log = tmp_path / "edge.txt"
    log.write_text("\n   \nxx yy\n", encoding="utf-8")
    assert w.get_decoded_messages(str(log)) == []


def test_list_available_modes_mentions_ft():
    modes = WSJTInterface().list_available_modes()
    assert "FT8" in modes and "FT4" in modes


# ─────────────────────────── QPSK/Viterbi 桥接 ───────────────────────────

def _conv_encode(info_bits, g1=171, g2=133, k=7):
    """(g1,g2) 整数位掩码卷积编码（与 demod.ViterbiDecoder 转移表互逆）。"""
    state = 0
    coded = []
    for b in info_bits:
        reg = (state << 1) | int(b)
        o1 = o2 = 0
        for i in range(k):
            if (g1 >> i) & 1:
                o1 ^= (reg >> i) & 1
            if (g2 >> i) & 1:
                o2 ^= (reg >> i) & 1
        coded += [o1, o2]
        state = (state >> 1) | (int(b) << (k - 2))
    return coded


def test_decode_viterbi_roundtrip_and_error_tolerance():
    import numpy as np
    rng = np.random.default_rng(7)
    info = rng.integers(0, 2, 48).tolist()
    coded = _conv_encode(info + [0] * 6)  # 6 位收尾使寄存器归零
    got = [int(x) for x in decode_viterbi(coded)]
    assert got[:48] == info
    assert all(b == 0 for b in got[48:])
    # 少量信道位翻转应被纠错（K=7 r=1/2 的设计语义）
    noisy = list(coded)
    for i in (3, 17, 40, 55, 62):
        noisy[i] ^= 1
    got_n = [int(x) for x in decode_viterbi(noisy)]
    assert got_n[:48] == info


def test_meteor_viterbi_delegate_pair_consistent():
    # meteor_sat.viterbi_encode/viterbi_decode_demo 是同组多项式的一对
    # （此前解码端硬编码 171/133，与编码端 0x79/0x5F 不一致，自编自解必败）
    import numpy as np
    from mbdsdr_ai import meteor_sat
    rng = np.random.default_rng(11)
    info = rng.integers(0, 2, 32).tolist()
    coded = meteor_sat.viterbi_encode(np.array(info + [0] * 6, dtype=np.uint8))
    got = [int(x) for x in meteor_sat.viterbi_decode_demo(coded)]
    assert got[:32] == info


def test_get_qpsk_viterbi_wired_to_demod():
    from mbdsdr_ai.demod import QPSKDemodulator, ViterbiDecoder
    q, v = get_qpsk_viterbi()
    assert q is QPSKDemodulator and v is ViterbiDecoder


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
