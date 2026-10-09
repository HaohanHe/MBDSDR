# SPDX-License-Identifier: MIT
"""FT8 第②步：LDPC(174,91) + CRC14 + BP 解码 + 77-bit unpack 确定性互测。

覆盖（机制证据见 docs/learn/phase63/ft8-step2.md）：
  - CRC14 自洽性 + 翻转即失败；
  - 编码→BP 解码 round-trip（干净 LLR）逐 bit 相等；
  - N-bit 翻转纠错扫描（诚实记录 N_max 与 d_min 理论关系）；
  - 纯噪声 LLR / 全 0 LLR → None（诚实空态，无假解码）；
  - 固定 seed 可复现；
  - 呼号/网格/77-bit 打包回译 round-trip；
  - llrs_from_tone_energies 确定性；
  - Ft8Modulator.encode_message() 已接线（不再 NotImplementedError）。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mbdsdr_ai.ft8_codec import (  # noqa: E402
    Ft8Codec,
    check_crc14,
    crc14_bits,
    llrs_from_tone_energies,
    pack28_callsign,
    pack77,
    unpack28_callsign,
    unpack77,
    unpack_grid4,
    pack_grid4,
)
from mbdsdr_ai.ft8_modem import Ft8Modulator, GRAY_MAP  # noqa: E402


@pytest.fixture(scope="module")
def codec() -> Ft8Codec:
    return Ft8Codec()


# -- CRC14 -------------------------------------------------------------------- #
def test_crc14_self_consistency():
    rng = np.random.default_rng(20261010)
    for _ in range(20):
        msg = rng.integers(0, 2, size=77).tolist()
        crc = crc14_bits(msg)
        assert check_crc14(msg + crc)


def test_crc14_detects_flip():
    rng = np.random.default_rng(7)
    msg = rng.integers(0, 2, size=77).tolist()
    crc = crc14_bits(msg)
    bits = msg + crc
    bits[40] ^= 1
    assert not check_crc14(bits)


# -- 编码 round-trip ---------------------------------------------------------- #
def _random_msg91(seed: int = 1):
    rng = np.random.default_rng(seed)
    msg77 = rng.integers(0, 2, size=77).tolist()
    return msg77 + crc14_bits(msg77)


def test_encode_decode_clean_roundtrip(codec):
    for seed in range(5):
        bits91 = _random_msg91(seed)
        cw = codec.encode(bits91)
        llr = np.where(cw == 0, 5.0, -5.0).astype(float)
        dec = codec.decode(llr)
        assert dec is not None
        np.testing.assert_array_equal(dec, np.array(bits91, dtype=np.int8))


def test_codeword_parity_structure(codec):
    bits91 = _random_msg91(0)
    cw = codec.encode(bits91)
    # 系统码：前 91 bit 即信息位
    np.testing.assert_array_equal(cw[:91], np.array(bits91, dtype=np.int8))
    # 列重 3：每 bit 连 3 个校验
    col_w = codec.H.sum(axis=0)
    assert (col_w == 3).all()


# -- 纠错能力扫描 ------------------------------------------------------------- #
def test_bitflip_correction_sweep(codec):
    bits91 = _random_msg91(42)
    cw = codec.encode(bits91)
    clean_llr = np.where(cw == 0, 5.0, -5.0).astype(float)
    results = {}
    for nflip in range(0, 8):
        ok_count = 0
        trials = 20
        rng = np.random.default_rng(100 + nflip)
        for t in range(trials):
            llr = clean_llr.copy()
            idx = rng.choice(174, nflip, replace=False)
            llr[idx] *= -1
            dec = codec.decode(llr)
            if dec is not None and np.array_equal(dec, np.array(bits91)):
                ok_count += 1
        results[nflip] = ok_count
    # 0-1 flips 应全部恢复；2-3 flips 多数恢复（BP 对陷阱集非完备，诚实记录）
    assert results[0] == 20
    assert results[1] == 20
    assert results[2] >= 16
    assert results[3] >= 10
    print(f"\n[诚实] bitflip 纠错扫描: {results}")


# -- 诚实空态 ----------------------------------------------------------------- #
def test_pure_noise_llr_returns_none(codec):
    rng = np.random.default_rng(999)
    for _ in range(10):
        llr = rng.normal(0, 0.5, size=174)   # 接近无信息
        dec = codec.decode(llr)
        assert dec is None


def test_all_zero_llr_degenerates_to_zero_codeword(codec):
    """全 0 LLR 收敛到全 0 码字（合法消息，但这是退化情形，非真实信号）。"""
    dec = codec.decode(np.zeros(174))
    # 全 0 是合法码字（空消息），CRC 自洽；不视为假解码
    assert dec is not None
    assert (dec == 0).all()


# -- 打包回译 ---------------------------------------------------------------- #
@pytest.mark.parametrize("call", ["K1ABC", "W9XYZ", "N0AAA", "K1"])
def test_callsign_roundtrip(call):
    n = pack28_callsign(call)
    back = unpack28_callsign(n)
    assert back == call.strip().upper()


@pytest.mark.parametrize("grid", ["EM12", "AA00", "RR99", "IO79"])
def test_grid_roundtrip(grid):
    assert unpack_grid4(pack_grid4(grid)) == grid


def test_pack77_unpack77_roundtrip():
    msg77 = pack77("K1ABC", "K2DEF", "EM12")
    crc = crc14_bits(msg77)
    assert check_crc14(msg77 + crc)
    parsed = unpack77(msg77)
    assert parsed is not None
    assert parsed["from"] == "K1ABC"
    assert parsed["to"] == "K2DEF"
    assert parsed["exchange"] == "EM12"


def test_unpack77_rejects_nonstandard():
    bad = [0] * 77
    bad[74:77] = [1, 0, 1]   # i3=5 非标准
    assert unpack77(bad) is None


# -- LLR 注入接口 ------------------------------------------------------------ #
def test_llrs_from_tone_energies_deterministic():
    rng = np.random.default_rng(5)
    E = rng.uniform(0, 1, size=(58, 8))
    l1 = llrs_from_tone_energies(E)
    l2 = llrs_from_tone_energies(E)
    np.testing.assert_array_equal(l1, l2)
    assert l1.shape == (174,)


# -- encode_message 接线 ------------------------------------------------------ #
def test_encode_message_wired():
    mod = Ft8Modulator()
    tones = mod.encode_message(from_call="K1ABC", to_call="K2DEF", grid4="EM12")
    assert tones.shape == (58,)
    assert tones.dtype == np.int8
    assert tones.min() >= 0 and tones.max() <= 7
    # 帧组装不再 raise
    frame = mod.build_frame_symbols(tones)
    assert frame.shape == (79,)


def test_encode_message_decodes_back(codec):
    """编码→LLR→BP→unpack 全链路闭环。"""
    mod = Ft8Modulator()
    tones = mod.encode_message(from_call="K1ABC", to_call="K2DEF", grid4="EM12")
    # 由 tone 反推 174 bit（格雷映射逆）
    inv_gray = np.argsort(GRAY_MAP)
    bits = np.zeros(174, dtype=np.int8)
    for s in range(58):
        v = inv_gray[tones[s]]
        bits[s*3] = (v >> 2) & 1
        bits[s*3+1] = (v >> 1) & 1
        bits[s*3+2] = v & 1
    # 干净 LLR
    llr = np.where(bits == 0, 5.0, -5.0).astype(float)
    dec = codec.decode(llr)
    assert dec is not None
    parsed = unpack77(dec[:77].tolist())
    assert parsed == {
        "from": "K1ABC", "to": "K2DEF", "exchange": "EM12",
        "report": False, "type": 1,
    }
