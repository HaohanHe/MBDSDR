"""FT8 完整软解码链：8FSK 软判决 → colorder 重排 → LDPC(174,91) BP。

帧结构（genft8.f90）：S7 D29 S7 D29 S7，共 79 符号。
同步 Costas 块在符号 0-6、36-42、72-78；58 个数据符号在 7-35、43-71。
每数据符号 3 bit（graymap 映射），58×3=174 = LDPC 码字长。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from mbdsdr_ai import ft8_ldpc

# 3 个 Costas 同步块（0-based）
SYNC_POS = set(range(0, 7)) | set(range(36, 43)) | set(range(72, 79))
# 58 个数据符号位置（0-based）
DATA_POS = [k for k in range(79) if k not in SYNC_POS]

# 标准 FT8 Costas7 序列（genft8.f90 icos7），三个同步块(0-6/36-42/72-78)共用
ICOS7 = [3, 1, 4, 0, 6, 5, 2]
_SYNC_BLOCKS = (range(0, 7), range(36, 43), range(72, 79))


def costas_correlation(tone_indices: Sequence[int]) -> float:
    """79 符号硬判决音调序列与标准 Costas7 序列的匹配度（同步质量）。

    在三个同步块位置（0-6、36-42、72-78）逐符号比对 ICOS7，
    返回匹配数 / 21（0.0~1.0）。越高表示位同步越准。
    """
    hits = 0
    total = 0
    for block in _SYNC_BLOCKS:
        for j, pos in enumerate(block):
            total += 1
            if pos < len(tone_indices) and int(tone_indices[pos]) == ICOS7[j]:
                hits += 1
    return hits / total if total else 0.0

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
    """传输 codeword 顺序 LLR → LDPC H 矩阵顺序。

    标准 FT8 (174,91) LDPC 码没有 colorder 置换（encode174_91.f90 直接
    codeword(1:K)=message, codeword(K+1:N)=pchecks）；colorder 只属于旧的
    (174,87) 码。因此这里是恒等映射，保留函数名/签名以备外部调用。
    """
    return list(llr_cw)


def decode_ft8_payload(tone_energies: Sequence[Sequence[float]],
                       max_iter: int = 30,
                       full_tones: Optional[Sequence[int]] = None) -> dict:
    """完整软解码：58 符号 8 路能量 → 91 信息位。

    返回 {info_bits(91), crc_bits(14), data_bits(77), iters, converged,
          costas_score}。
    CRC14 校验是否通过由调用方用 chkcrc 逻辑判定（本函数给出原始位）。
    full_tones 为 79 符号硬判决音调序列（可选）；提供时据此计算 costas_score，
    否则 costas_score 置 0.0（无 Costas 同步信息）。
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
        "crc_ok": check_crc14(info),
        "costas_score": costas_correlation(full_tones) if full_tones is not None else 0.0,
    }


def hard_tones_to_llr(tones: Sequence[int], confidence: float = 8.0) -> List[float]:
    """硬判决 tone 序列（58 个 0-7）转 LLR，便于接已有硬判决解调。"""
    energies = []
    for t in tones:
        e = [0.0] * 8
        e[t] = confidence
        energies.append(e)
    return soft_tones_to_llr(energies)


# CRC14 多项式 0x6757（wsjtx get_crc14.f90），15 bit
_CRC_P = [1, 1, 0, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1]


def crc14(msg_bits: Sequence[int]) -> int:
    """对 77 个消息位计算 14 bit CRC（多项式 0x6757）。

    WSJT-X 标准：77 bit 消息写入 12 字节(96bit)，尾部 19 bit 补零
    （5 bit 零填充 + 14 bit CRC 字段），CRC 在全部 96 bit 上做多项式除法。
    """
    mc = [int(b) for b in msg_bits] + [0] * 5 + [0] * 14  # 77+5+14=96
    r = mc[:15]
    for i in range(0, len(mc) - 14):
        r[14] = mc[i + 14] if i + 14 < len(mc) else 0
        if r[0]:
            r = [(r[k] ^ _CRC_P[k]) for k in range(15)]
        r = r[1:] + r[:1]  # cshift left by 1
    val = 0
    for b in r[:14]:
        val = (val << 1) | b
    return val


def check_crc14(info_bits: Sequence[int]) -> bool:
    """校验 91 位（77 消息 + 14 CRC）：组装成 96 bit 块后求余，余数为 0 即通过。"""
    info = [int(b) for b in info_bits]
    mc = info[:77] + [0] * 5 + info[77:91]  # 77+5+14=96
    r = mc[:15]
    for i in range(0, len(mc) - 14):
        r[14] = mc[i + 14] if i + 14 < len(mc) else 0
        if r[0]:
            r = [(r[k] ^ _CRC_P[k]) for k in range(15)]
        r = r[1:] + r[:1]
    return sum(r[:14]) == 0


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
    # CRC14 自洽：对随机 77 位算 CRC，拼成 91 位应通过；翻一位应失败
    import random
    random.seed(3)
    ok = bad = 0
    for _ in range(20):
        msg = [random.randint(0, 1) for _ in range(77)]
        c = crc14(msg)
        crc_bits = [(c >> (13 - b)) & 1 for b in range(14)]
        code91 = msg + crc_bits
        if check_crc14(code91):
            ok += 1
        code91[5] ^= 1  # 破坏一位
        if not check_crc14(code91):
            bad += 1
    print(f"CRC14: 正确码字通过 {ok}/20, 翻位后失败 {bad}/20")
