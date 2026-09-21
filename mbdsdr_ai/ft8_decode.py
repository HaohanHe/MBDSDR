"""FT8 完整软解码链：8FSK 软判决 → colorder 重排 → LDPC(174,91) BP。

帧结构（genft8.f90）：S7 D29 S7 D29 S7，共 79 符号。
同步 Costas 块在符号 0-6、36-42、72-78；58 个数据符号在 7-35、43-71。
每数据符号 3 bit（graymap 映射），58×3=174 = LDPC 码字长。
"""
from __future__ import annotations

import math
from typing import List, Sequence

from mbdsdr_ai import ft8_ldpc

# 3 个 Costas 同步块（0-based）
SYNC_POS = set(range(0, 7)) | set(range(36, 43)) | set(range(72, 79))
# 58 个数据符号位置（0-based）
DATA_POS = [k for k in range(79) if k not in SYNC_POS]

# genft8: graymap = [0,1,3,2,5,6,4,7]，index→tone；反映射 tone→index
_GRAYMAP = [0, 1, 3, 2, 5, 6, 4, 7]
_INV_GRAY = [0] * 8
for _idx, _tone in enumerate(_GRAYMAP):
    _INV_GRAY[_tone] = _idx


def data_symbol_positions() -> List[int]:
    """返回 58 个数据符号在 79 符号帧中的位置（0-based，按传输顺序）。"""
    return list(DATA_POS)


def soft_tones_to_llr(tone_energies: Sequence[Sequence[float]]) -> List[float]:
    """58 个数据符号的 8 路能量 → 174 个传输顺序 bit LLR。

    LLR 约定：正=偏向 bit 0，负=偏向 bit 1（与 ft8_ldpc 一致）。
    用 max-log 近似：LLR(b) = max E(b=0) - max E(b=1)。
    """
    assert len(tone_energies) == 58, f"需要 58 个数据符号，得到 {len(tone_energies)}"
    llr_cw: List[float] = []
    for energies in tone_energies:
        assert len(energies) == 8
        # tone → gray 反映射后的 index 能量
        e = [0.0] * 8
        for tone, val in enumerate(energies):
            e[_INV_GRAY[tone]] = val
        for bit in (4, 2, 1):  # MSB→LSB
            e0 = max(e[i] for i in range(8) if not (i & bit))
            e1 = max(e[i] for i in range(8) if i & bit)
            llr_cw.append(e0 - e1)
    return llr_cw  # 174，传输 codeword 顺序


def reorder_to_ldpc(llr_cw: Sequence[float]) -> List[float]:
    """传输 codeword 顺序 LLR → LDPC H 矩阵顺序（colorder 反映射）。"""
    colorder = ft8_ldpc.get_colorder()
    # encode: cw[colorder[j]] = itmp[j]  ⇒  itmp[j] = cw[colorder[j]]
    return [llr_cw[colorder[j]] for j in range(ft8_ldpc._N)]


def decode_ft8_payload(tone_energies: Sequence[Sequence[float]],
                       max_iter: int = 30) -> dict:
    """完整软解码：58 符号 8 路能量 → 91 信息位。

    返回 {info_bits(91), crc_bits(14), data_bits(77), iters, converged}。
    CRC14 校验是否通过由调用方用 chkcrc 逻辑判定（本函数给出原始位）。
    """
    llr_cw = soft_tones_to_llr(tone_energies)
    llr_ldpc = reorder_to_ldpc(llr_cw)
    cw, iters = ft8_ldpc.ldpc_bp_decode(llr_ldpc, max_iter=max_iter)
    info = cw[:91]
    return {
        "info_bits": info,           # 77 数据 + 14 CRC
        "data_bits": info[:77],
        "crc_bits": info[77:91],
        "codeword": cw,
        "iters": iters,
    }


def hard_tones_to_llr(tones: Sequence[int], confidence: float = 8.0) -> List[float]:
    """硬判决 tone 序列（58 个 0-7）转 LLR，便于接已有硬判决解调。"""
    energies = []
    for t in tones:
        e = [0.0] * 8
        e[t] = confidence
        energies.append(e)
    return soft_tones_to_llr(energies)


if __name__ == "__main__":
    # 闭环自测：构造全零数据符号（tone 全 0）→ 应解出全零信息位
    tones = [[0.0] * 8 for _ in range(58)]
    for e in tones:
        e[0] = 10.0
    r = decode_ft8_payload(tones)
    print(f"全零符号: 数据位全零={sum(r['data_bits'])==0}, 迭代={r['iters']}")
    # 数据符号位置校验
    pos = data_symbol_positions()
    print(f"数据符号数={len(pos)}, 块1={pos[0]}..{pos[28]}, 块2={pos[29]}..{pos[57]}")
    # gray 反映射校验
    print(f"invgray={_INV_GRAY}（应使 graymap 双射）")
