"""FST4 编码器：文本消息 → 160 个音调（与 WSJT-X genfst4.f90 位对位）。

链路：
  文本 → pack77 (77 bit) → rvec 加扰 → CRC24 (→101 bit)
       → LDPC(240,101) (→240 bit) → gray 2bit→tone (120 符号)
       → 插入 5 个 8 符号同步块 (→160 符号)

来源: WSJT-X lib/fst4/genfst4.f90
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

from mbdsdr_ai.fst4_ldpc import (
    KK, ND, NS, NN, M_ARY, SYNC_WORD1, SYNC_WORD2, GRAY_MAP, RVEC,
    _CRC24_P, fst4_crc24,
)
from mbdsdr_ai.ft8_encode import pack77_standard


@lru_cache(maxsize=1)
def _fst4_generator() -> list[list[int]]:
    """解析 g(139) 十六进制行 → 139×101 生成子矩阵。

    来源: encode240_101.f90:18-31。每行 26 hex，前 25 个各 4bit，
    最后 1 个 1bit，共 101 bit。
    """
    path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "repos",
        "wsjtx", "lib", "fst4", "ldpc_240_101_generator.f90"))
    txt = open(path, encoding="utf-8").read()
    rows = re.findall(r'"([0-9a-f]{26})"', txt)
    assert len(rows) == 139, f"期望 139 行，得到 {len(rows)}"
    gen = [[0] * 101 for _ in range(139)]
    for i, hexs in enumerate(rows):
        for j, ch in enumerate(hexs):
            nibble = int(ch, 16)
            ibmax = 1 if j == 25 else 4
            for jj in range(1, ibmax + 1):
                icol = j * 4 + jj - 1
                if (nibble >> (4 - jj)) & 1:
                    gen[i][icol] = 1
    return gen


def ldpc_encode_240_101(msg101: list[int]) -> list[int]:
    """101 bit → 240 bit 码字。来源: encode240_101.f90:33-38。"""
    gen = _fst4_generator()
    assert len(msg101) == 101
    cw = list(msg101) + [0] * 139
    for i in range(139):
        s = 0
        for j in range(101):
            s += msg101[j] * gen[i][j]
        cw[101 + i] = s & 1
    return cw


def add_crc24(msg77_scrambled: list[int]) -> list[int]:
    """77 加扰消息位 → 101 位 (77 + 24 CRC)。

    来源: genfst4.f90:64-66 —— 对加扰后的 77 位算 CRC24，追加到 78:101。
    """
    assert len(msg77_scrambled) == 77
    block = list(msg77_scrambled) + [0] * 24
    crc = fst4_crc24(block)
    crc_bits = [(crc >> k) & 1 for k in range(23, -1, -1)]
    return list(msg77_scrambled) + crc_bits


def encode_fst4_tones(msg77: list[int]) -> list[int]:
    """77 消息位 → 160 个音调。来源: genfst4.f90:62-107。"""
    # 加扰 —— genfst4.f90:63
    scrambled = [m ^ r for m, r in zip(msg77, RVEC)]
    msg101 = add_crc24(scrambled)
    cw = ldpc_encode_240_101(msg101)
    assert len(cw) == 240

    # 2bit→tone gray 映射 —— genfst4.f90:92-97
    # Fortran: is=codeword(2i)+2*codeword(2i-1)；0-indexed: is=cw[2i+1]+2*cw[2i]
    itmp = [0] * ND
    for i in range(ND):
        is_val = cw[2 * i + 1] + 2 * cw[2 * i]
        itmp[i] = GRAY_MAP[is_val]

    # 帧结构 s8 d30 s8 d30 s8 d30 s8 d30 s8 —— genfst4.f90:99-107
    i4tone = [0] * NN
    pos = 0
    # 5 个同步块，交替 isyncword1 / isyncword2；4 个数据块各 30
    sync_sequence = [SYNC_WORD1, SYNC_WORD2, SYNC_WORD1, SYNC_WORD2, SYNC_WORD1]
    di = 0
    for bi in range(5):
        for t in sync_sequence[bi]:
            i4tone[pos] = t
            pos += 1
        if bi < 4:
            for _ in range(30):
                i4tone[pos] = itmp[di]
                di += 1
                pos += 1
    return i4tone


def encode_fst4_text(text: str) -> list[int]:
    """标准文本消息 → 160 音调。"""
    parts = text.split()
    if parts[0].upper() == "CQ":
        call1, call2 = "CQ", parts[1]
        info = parts[2] if len(parts) >= 3 else None
    else:
        call1, call2 = parts[0], parts[1]
        info = parts[2] if len(parts) >= 3 else None
    msg77 = pack77_standard(call1, call2, info)
    return encode_fst4_tones(msg77)


if __name__ == "__main__":
    tones = encode_fst4_text("CQ BI4MIB OM74")
    print(f"FST4 音调数: {len(tones)}")
    # 校验同步块 (0-based): 0-7, 38-45, 76-83, 114-121, 152-159
    sync_starts = [0, 38, 76, 114, 152]
    expected = [SYNC_WORD1, SYNC_WORD2, SYNC_WORD1, SYNC_WORD2, SYNC_WORD1]
    for s, e in zip(sync_starts, expected):
        got = tones[s:s + 8]
        print(f"同步块@{s}: {got} {'OK' if got == e else 'FAIL exp'+str(e)}")
