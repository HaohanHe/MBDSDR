# SPDX-License-Identifier: MIT
"""Annex K.3 硬编码表测试（Meteor LRPT 真实路径第二块）。

规范定值钉死：表长、counts 和、与 DHT 生成表的构造一致性、分量选择口径。
"""
import pytest

from mbdsdr_ai.jpeg_k3_tables import (
    K3_AC_CHROMA_BITS,
    K3_AC_CHROMA_VALS,
    K3_AC_LUMA_VALS,
    K3_DC_LUMA_BITS,
    K3_DC_LUMA_VALS,
    build_k3_tables,
    k3_tables_for_component,
)
from mbdsdr_ai.lrpt_huffman import BitReader, HuffmanTable


def test_table_shapes():
    assert len(K3_DC_LUMA_BITS) == 16
    assert sum(K3_DC_LUMA_BITS) == len(K3_DC_LUMA_VALS) == 12
    assert len(K3_AC_LUMA_VALS) == 162
    assert len(K3_AC_CHROMA_VALS) == 162
    for b in (K3_AC_CHROMA_BITS, K3_DC_LUMA_BITS):
        assert len(b) == 16
    assert sum(K3_DC_LUMA_BITS) == 12


def test_build_k3_tables_constructs():
    tab = build_k3_tables()
    assert set(tab) == {("DC", 0), ("DC", 1), ("AC", 0), ("AC", 1)}
    for h in tab.values():
        assert isinstance(h, HuffmanTable)


def test_first_symbol_decodes():
    """用规范表解已知码流：DC luma 首符号 0x00。

    构造：DC luma 长度 2 的码空间：counts=(0,1,5,1,...) -> len1 无码、
    len2 从 0b00 起。写 2 bit '00' 应解出 0x00（LUMA_VALS[0]）。
    """
    tab = build_k3_tables()[("DC", 0)]
    r = BitReader(b"\x00\x00\x00\x00")
    assert tab.decode(r) == 0x00


def test_dc_chroma_len3_first_symbol():
    """DC chroma：counts=(0,3,1,1,...) -> len2 有 3 个码（00,01,10）。
    写 '100'：len2 的 10 命中 huffval[2]=0x02。"""
    tab = build_k3_tables()[("DC", 1)]
    # 位流 1 0 0 0 0 0 0 0 = 0x80 -> 首 2 bit '10'
    r = BitReader(b"\x80")
    assert tab.decode(r) == 0x02


def test_k3_tables_for_component_luma_vs_chroma():
    luma = k3_tables_for_component(0)
    chroma = k3_tables_for_component(1)
    assert luma[("DC", 0)].table_id == 0
    assert luma[("AC", 0)].table_class == "AC"
    assert chroma[("DC", 1)].table_id == 1
    # 分量 2 也用 chroma 表
    assert k3_tables_for_component(2)[("AC", 1)].table_id == 1


def test_ac_tables_differ_between_luma_chroma():
    tab = build_k3_tables()
    assert tab[("AC", 0)].huffval != tab[("AC", 1)].huffval
    assert tab[("DC", 0)].huffval == tab[("DC", 1)].huffval


def test_rejects_bad_counts_like_dht_path():
    """构造期校验与 DHT 路径同口径：counts 和 != HUFFVAL 数即拒。"""
    with pytest.raises(Exception):
        HuffmanTable([1] * 16, [0, 1], "DC", 0)
