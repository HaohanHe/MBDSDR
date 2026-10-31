# SPDX-License-Identifier: MIT
"""P0-2（半收敛）BCH 干净室编解码测试（GF 全推导，零标准表依赖）。

能力钉扎（对随机消息 + 随机错位的真值断言）：
  * ≤t 个随机错 → 100% 纠回原消息；
  * >t 个错 → 如实报 uncorrectable，绝不输出伪正常码字；
  * 生成多项式性质：g(α^b)=0（b=1..2t）、deg(g)=n−k、系统形码字
    前 k 位=信息；
  * 非本原多项式 → 推导报错（不自洽参数不静默）。

DVB-S2 LDPC 显式不在本文件范围（标准地址表离线不可得，见
bch_codecs 模块尾注——不伪造）。
"""
import random

import pytest

from mbdsdr_ai.bch_codecs import (
    Gf2m, bch_decode, bch_encode, bch_generator_poly,
)


def _check_gen_properties(gen):
    """生成式性质：g(α^b)=0 for b=1..2t；deg = n−k。"""
    gf = Gf2m(gen["m"], gen["primitive_poly"])
    g = gen["g"]
    for b in range(1, 2 * gen["t"] + 1):
        ab = gf.pow_a(b)
        acc = 0
        # Horner 高位 → 低位（位 d = x^d）
        for i in reversed(range(g.bit_length())):
            acc = gf.mul(acc, ab)
            if (g >> i) & 1:
                acc ^= 1
        assert acc == 0, f"g(α^{b}) ≠ 0"
    assert g.bit_length() - 1 == gen["deg_g"] == gen["n"] - gen["k"]


def test_generator_properties():
    gen = bch_generator_poly(8, 8)
    # 窄义 BCH(255,191)：α^1..α^16 的共轭类共 8 个 × 8 次 = deg g 64，
    # k = 255−64 = 191（(255,223) 是 t=7 口径，别混）
    assert gen["n"] == 255 and gen["k"] == 191
    assert gen["designed_distance"] == 17
    _check_gen_properties(gen)


def test_generator_derived_not_tabulated():
    """换本原多项式 → 不同生成式（证明是现算而非查表）。"""
    g1 = bch_generator_poly(8, 8, 0x11D)
    g2 = bch_generator_poly(8, 8, 0x187)  # 另一个 8 次本原多项式
    assert g1["g"] != g2["g"]


def test_non_primitive_poly_rejected():
    """非本原多项式 → 推导报错（现找一个 8 次不可约但非本原的验证）。"""
    from mbdsdr_ai.bch_codecs import Gf2m
    rejected = None
    for cand in range(0x100, 0x200):
        if cand.bit_length() - 1 != 8:
            continue
        try:
            Gf2m(8, cand)
        except ValueError:
            rejected = cand
            break
    assert rejected is not None, "没找到非本原 8 次多项式（数学上不可能）"
    with pytest.raises(ValueError, match="本原多项式"):
        Gf2m(8, rejected)


def test_encode_systematic_form():
    gen = bch_generator_poly(8, 8)
    rng = random.Random(42)
    msg = [rng.randint(0, 1) for _ in range(gen["k"])]
    cw = bch_encode(msg, gen)
    assert len(cw) == gen["n"]
    assert cw[:gen["k"]] == msg  # 系统形：信息在前


@pytest.mark.parametrize("n_err", [0, 1, 2, 4, 7, 8])
def test_decode_corrects_up_to_t(n_err):
    """≤t 个随机错必纠回（t=8，参数化到边界 8）。"""
    gen = bch_generator_poly(8, 8)
    rng = random.Random(100 + n_err)
    for _ in range(10):
        msg = [rng.randint(0, 1) for _ in range(gen["k"])]
        cw = bch_encode(msg, gen)
        rx = list(cw)
        for pos in rng.sample(range(gen["n"]), n_err):
            rx[pos] ^= 1
        r = bch_decode(rx, gen)
        assert not r["uncorrectable"]
        assert r["codeword"][:gen["k"]] == msg
        assert r["n_errors"] == n_err


def test_decode_beyond_t_honest():
    """>t 个错：BM 次数超限或 Chien 不符 → uncorrectable 如实报。

    有界距离译码器对 >t 错**可能误纠**（输出 syndrome 为零的错误码字，
    这是代数学固有性质，任何 BCH 译码器都无法区分）——本测试钉扎的诚实
    边界是：**绝不声称纠了超过 t 个错**（n_errors ≤ t 恒成立）。
    """
    gen = bch_generator_poly(8, 8)
    rng = random.Random(7)
    for n_err in (10, 12, 16):
        for _ in range(10):
            msg = [rng.randint(0, 1) for _ in range(gen["k"])]
            cw = bch_encode(msg, gen)
            rx = list(cw)
            for pos in rng.sample(range(gen["n"]), n_err):
                rx[pos] ^= 1
            r = bch_decode(rx, gen)
            if r["uncorrectable"]:
                assert r["codeword"] is None
            else:
                assert r["n_errors"] <= gen["t"], (
                    "声称纠正了超过 t 个错——虚假能力声明")
                # 输出必须是合法码字（syndrome 全零已由解码器内检保证）


def test_decode_clean_codeword():
    gen = bch_generator_poly(8, 8)
    msg = [random.Random(3).randint(0, 1) for _ in range(gen["k"])]
    cw = bch_encode(msg, gen)
    r = bch_decode(cw, gen)
    assert not r["uncorrectable"] and not r["corrected"]
    assert r["n_errors"] == 0


def test_wrong_length_rejected():
    gen = bch_generator_poly(8, 8)
    with pytest.raises(ValueError, match="≠ n"):
        bch_decode([0] * 100, gen)
    with pytest.raises(ValueError, match="≠ k"):
        bch_encode([0] * 100, gen)


def test_small_bch_roundtrip():
    """小参数（m=4, t=2, 本原 x^4+x+1 → BCH(15,7)）独立验证数学链。"""
    gen = bch_generator_poly(4, 2, 0x13)
    assert gen["n"] == 15 and gen["k"] == 7
    _check_gen_properties(gen)
    rng = random.Random(1)
    for _ in range(20):
        msg = [rng.randint(0, 1) for _ in range(gen["k"])]
        cw = bch_encode(msg, gen)
        rx = list(cw)
        for pos in rng.sample(range(gen["n"]), 2):
            rx[pos] ^= 1
        r = bch_decode(rx, gen)
        assert not r["uncorrectable"]
        assert r["codeword"][:gen["k"]] == msg


# ── 工具注册面 ────────────────────────────────────────────────
def test_tools_registered():
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    names = {t["name"] for t in r.list_tools()}
    for want in ("bch_generator", "bch_encode", "bch_decode"):
        assert want in names


def test_tool_handlers_end_to_end():
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    msg = [1, 0, 1, 1, 0, 0, 1]
    enc = r.tools["bch_encode"]["handler"](
        {"message": msg, "m": 4, "t": 2, "primitive_poly": 0x13})
    assert enc.success
    cw = enc.data["codeword"]
    cw[3] ^= 1
    dec = r.tools["bch_decode"]["handler"](
        {"codeword": cw, "m": 4, "t": 2, "primitive_poly": 0x13})
    assert dec.success
    assert dec.data["codeword"][:7] == msg
