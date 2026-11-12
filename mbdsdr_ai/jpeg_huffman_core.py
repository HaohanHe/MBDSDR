# SPDX-License-Identifier: MIT
"""JPEG Huffman 表的叶子定义。

本模块不 import 任何 ``mbdsdr_ai`` 模块——它是 ``lrpt_huffman`` 与
``jpeg_k3_tables`` 的共同下游，后者原先反向 import ``lrpt_huffman`` 的
``HuffmanTable``，再叠上 ``lrpt_huffman`` 对 K.3 表的延迟 import，形成
2 模块环。

``LrptJpegError`` / ``LrptJpegHuffmanError`` 随表一起下沉：``HuffmanTable``
会抛坏码异常，异常层级必须与 ``lrpt_huffman`` 的其它异常同源，不能各定义一份。
``lrpt_huffman`` 原处再导出全部名字，调用方不变。
"""
from __future__ import annotations

from typing import List, Protocol


class LrptJpegError(Exception):
    """LRPT JPEG 压缩域解码所有错误的基类。"""


class LrptJpegHuffmanError(LrptJpegError):
    """坏 Huffman 码（16 bit 内无匹配）或 AC/DC 表缺失。"""


class BitReaderLike(Protocol):
    """``HuffmanTable.decode`` 只需要这两件事，避免反向依赖 ``lrpt_huffman``。"""

    pos: int

    def read_bit(self) -> int: ...


class HuffmanTable:
    """DC/AC Huffman 解码表（JPEG Annex F mincode/maxcode/valptr）。

    由 DHT 的 16 个“各长度的码数”与符号序列（HUFFVAL）构造。``table_class``
    仅用于报错上下文（'DC'/'AC'），``table_id`` 为 DHT 高位半字节 Th。
    """

    def __init__(self, counts: List[int], huffval: List[int],
                 table_class: str, table_id: int) -> None:
        if len(counts) != 16:
            raise LrptJpegHuffmanError(
                f"{table_class} 表 {table_id} counts 须 16 项，收到 {len(counts)}")
        if sum(counts) != len(huffval):
            raise LrptJpegHuffmanError(
                f"{table_class} 表 {table_id} counts 和 {sum(counts)} != HUFFVAL 数 {len(huffval)}")
        self.table_class = table_class
        self.table_id = table_id
        self.huffval = list(huffval)
        self.mincode = [0] * 17
        self.maxcode = [-1] * 17
        self.valptr = [-1] * 17
        code = 0
        k = 0
        for length in range(1, 17):
            cnt = counts[length - 1]
            if cnt > 0:
                if code + cnt - 1 >= (1 << length):
                    raise LrptJpegHuffmanError(
                        f"{table_class} 表 {table_id} 长度 {length} 码溢出（非法 DHT）")
                self.valptr[length] = k
                self.mincode[length] = code
                code += cnt
                self.maxcode[length] = code - 1
                k += cnt
            code <<= 1

    def decode(self, reader: BitReaderLike) -> int:
        """逐位读入，命中即返回符号；16 bit 内无匹配则抛坏码异常。"""
        code = 0
        for length in range(1, 17):
            code = (code << 1) | reader.read_bit()
            mc = self.maxcode[length]
            if mc >= 0 and code <= mc:
                idx = self.valptr[length] + (code - self.mincode[length])
                return self.huffval[idx]
        raise LrptJpegHuffmanError(
            f"坏 Huffman 码：16 bit 内在 {self.table_class} 表 {self.table_id} 无匹配"
            f" (offset={reader.pos})")


__all__ = [
    "LrptJpegError",
    "LrptJpegHuffmanError",
    "BitReaderLike",
    "HuffmanTable",
]
