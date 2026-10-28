# SPDX-License-Identifier: MIT
"""
ft8_callsign.py（n28 呼号编解码）行为测试
=========================================

oracle 来源（都是公开 77-bit 报文格式事实，见模块 docstring）：
  - 特殊 token：0/1/2 = DE/QRZ/CQ；
  - CQ_nnn：3..1002；CQ_aaaa：1003..532443（27 进制 4 字母）；
  - 保留段 532444..NTOKENS-1 未分配——必须诚实失败，不得伪造成 hash；
  - 22bit hash 段无呼号表时返回 <hash:n> 且 ok=False；
  - 标准呼号 37×36×10×27×27×27 布局，28 位环回（与 ft8_codec/js8call
    的 28 位掩码口径一致），任何输入不得越界崩溃。
fixture 呼号按 AGENTS.md 约定用中性值 BI1AAA。

运行：python -m pytest mbdsdr_ai/tests/test_ft8_callsign_behavior.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.ft8_callsign import (  # noqa: E402
    MAX22, NTOKENS, unpack28,
)

# 标准呼号字符集（与模块一致；布局：c1 c2 c3 c4 c5 c6）
_C1 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_C2 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_C3 = "0123456789"
_C4 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _pack_std(call: str) -> int:
    """按公开布局独立实现 pack（不调用被测模块），作往返 oracle。"""
    s = call.upper().strip()
    if len(s) < 6 and s[0].isalpha() and s[1].isdigit():
        s = " " + s  # 单前缀呼号右移补空格（wsjtx 口径）
    s = s.ljust(6)
    i1 = _C1.index(s[0]); i2 = _C2.index(s[1]); i3 = _C3.index(s[2])
    i4 = _C4.index(s[3]); i5 = _C4.index(s[4]); i6 = _C4.index(s[5])
    n = (i1 * (36 * 10 * 27 ** 3) + i2 * (10 * 27 ** 3) + i3 * (27 ** 3)
         + i4 * (27 ** 2) + i5 * 27 + i6)
    return n + MAX22 + NTOKENS


# ─────────────────────────── 特殊 token / CQ 段 ───────────────────────────

def test_special_tokens_de_qrz_cq():
    assert unpack28(0) == ("DE", True)
    assert unpack28(1) == ("QRZ", True)
    assert unpack28(2) == ("CQ", True)


def test_cq_directed_numbers_boundaries():
    assert unpack28(3) == ("CQ_000", True)
    assert unpack28(1002) == ("CQ_999", True)


def test_cq_letter_tokens_known_vectors():
    # 布局独立计算：后缀 4 字母按 27 进制（_C4，空格补位）
    # "TEST": T=20, E=5, S=19, T=20 -> n = ((20*27+5)*27+19)*27+20
    n_test = 1003 + ((20 * 27 + 5) * 27 + 19) * 27 + 20
    assert unpack28(n_test) == ("CQ_TEST", True)
    # 全空格后缀（n=1003）剥空格后只留前缀
    assert unpack28(1003) == ("CQ_", True)


# ─────────────────────────── hash / 保留段 ───────────────────────────

def test_hash_segment_unresolvable_honest():
    assert unpack28(NTOKENS) == ("<hash:0>", False)
    assert unpack28(NTOKENS + MAX22 - 1) == (f"<hash:{MAX22 - 1}>", False)


def test_reserved_token_gap_fails_honestly():
    # 532444..NTOKENS-1 未分配：不得伪造成 hash（旧实现给出 <hash:负数>）
    for n in (532444, 600000, NTOKENS - 1):
        call, ok = unpack28(n)
        assert ok is False
        assert call == f"<unallocated:{n}>"
        assert not call.startswith("<hash")


# ─────────────────────────── 标准呼号 ───────────────────────────

def test_standard_callsign_roundtrip_fixture():
    n28 = _pack_std("BI1AAA")
    assert unpack28(n28) == ("BI1AAA", True)


def test_standard_callsign_roundtrip_variants():
    for call in ("W1AW", "4X5RI", "K1ABC"):
        assert unpack28(_pack_std(call)) == (call, True)


def test_standard_callsign_upper_boundary():
    # 28 位全 1 落在标准呼号段最大值
    assert unpack28(0x0FFFFFFF) == ("ZZ9ZZZ", True)


# ─────────────────────────── 输入边界 ───────────────────────────

def test_wide_input_wrapped_to_28bits_no_crash():
    # 旧实现掩 32 位，>=2^28 的输入越界 IndexError（_C1[i1] 越界）
    assert unpack28(2 ** 28) == unpack28(0) == ("DE", True)
    assert unpack28(0xFFFFFFFF) == unpack28(0x0FFFFFFF) == ("ZZ9ZZZ", True)


def test_agrees_with_ft8_codec_on_standard_segment():
    # 跨实现一致：ft8_codec.unpack28_callsign 是同一公开布局的独立实现
    from mbdsdr_ai.ft8_codec import unpack28_callsign
    for n in (_pack_std("BI1AAA"), _pack_std("BI1AAA") + 12345,
              NTOKENS + MAX22 + 987654):
        call, ok = unpack28(n)
        assert ok is True
        assert call == unpack28_callsign(n)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
