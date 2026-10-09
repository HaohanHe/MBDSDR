# SPDX-License-Identifier: MIT
"""FT8 LDPC(174,91) 编解码 + CRC14 + 77-bit unpack（干净室自写，第②步）。

本模块依据 ``docs/learn/phase63/ft8-mechanism-study.md`` §3-4 自述重写，
只依赖 NumPy。WSJT-X (GPL) 源码仅作机制/常量证据（笔记与本文件记 file:line），
未包含其代码文本。下列为 FT8 公开协议事实：LDPC(174,91)、列重 3 规则、
行重 6/7 不规则、CRC14 poly 0x6757、77-bit 消息布局（28+1+28+1+1+15+3）。

能力：
  - :func:`crc14_bits` / :func:`check_crc14`：14-bit CRC（poly 0x6757）；
  - :class:`Ft8Codec`：系统编码（H_parity GF(2) 求逆）+ log-domain tanh BP
    解码 + CRC14 早停；
  - :func:`pack77` / :func:`unpack77`：i5bit=0 标准消息（呼号 A/B + 4 字符网格）；
  - :func:`llrs_from_tone_energies`：8-FSK 符号谱 → 174 LLR（确定性注入接口）。

诚实空态：CRC 不过 / 迭代不收敛 / 消息字段非法 → 返回 None，绝不编造。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np

__all__ = [
    "LDPC_N",
    "LDPC_K",
    "LDPC_M",
    "CRC14_POLY",
    "NTOKENS",
    "MAX22",
    "MN_CHECKS",
    "crc14_bits",
    "check_crc14",
    "Ft8Codec",
    "pack28_callsign",
    "unpack28_callsign",
    "pack_grid4",
    "unpack_grid4",
    "pack77",
    "unpack77",
    "llrs_from_tone_energies",
]

# --------------------------------------------------------------------------- #
# 协议常量（FT8 公开标准；证据见机制笔记 §3-4）
# --------------------------------------------------------------------------- #
LDPC_N = 174   # 码字长（bpdecode174_91.f90:7）
LDPC_K = 91    # 信息位（77 消息 + 14 CRC）
LDPC_M = 83    # 校验位 = N-K

#: CRC14 多项式系数（15 位，省略最高位 x^14）：110011101010111 = 0x6757
#: （get_crc14.f90:12 data p/1,1,0,0,1,1,1,0,1,0,1,0,1,1,1/）
CRC14_POLY = (1, 1, 0, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1)

#: 28-bit 呼号打包的特殊令牌/哈希偏移（pack28.f90: NTOKENS=2063592, MAX22=4194304）
NTOKENS = 2_063_592
MAX22 = 4_194_304

#: LDPC(174,91) 校验矩阵 H 的列连接表（每变量节点连 3 个校验节点，1-indexed）。
#: 来源 wsjtx ``ldpc_174_91_c_parity.f90`` data Mn/.../（公开协议结构）。
MN_CHECKS: tuple = (
    (16, 45, 73), (25, 51, 62), (33, 58, 78), (1, 44, 45), (2, 7, 61),
    (3, 6, 54), (4, 35, 48), (5, 13, 21), (8, 56, 79), (9, 64, 69),
    (10, 19, 66), (11, 36, 60), (12, 37, 58), (14, 32, 43), (15, 63, 80),
    (17, 28, 77), (18, 74, 83), (22, 53, 81), (23, 30, 34), (24, 31, 40),
    (26, 41, 76), (27, 57, 70), (29, 49, 65), (3, 38, 78), (5, 39, 82),
    (46, 50, 73), (51, 52, 74), (55, 71, 72), (44, 67, 72), (43, 68, 78),
    (1, 32, 59), (2, 6, 71), (4, 16, 54), (7, 65, 67), (8, 30, 42),
    (9, 22, 31), (10, 18, 76), (11, 23, 82), (12, 28, 61), (13, 52, 79),
    (14, 50, 51), (15, 81, 83), (17, 29, 60), (19, 33, 64), (20, 26, 73),
    (21, 34, 40), (24, 27, 77), (25, 55, 58), (35, 53, 66), (36, 48, 68),
    (37, 46, 75), (38, 45, 47), (39, 57, 69), (41, 56, 62), (20, 49, 53),
    (46, 52, 63), (45, 70, 75), (27, 35, 80), (1, 15, 30), (2, 68, 80),
    (3, 36, 51), (4, 28, 51), (5, 31, 56), (6, 20, 37), (7, 40, 82),
    (8, 60, 69), (9, 10, 49), (11, 44, 57), (12, 39, 59), (13, 24, 55),
    (14, 21, 65), (16, 71, 78), (17, 30, 76), (18, 25, 80), (19, 61, 83),
    (22, 38, 77), (23, 41, 50), (7, 26, 58), (29, 32, 81), (33, 40, 73),
    (18, 34, 48), (13, 42, 64), (5, 26, 43), (47, 69, 72), (54, 55, 70),
    (45, 62, 68), (10, 63, 67), (14, 66, 72), (22, 60, 74), (35, 39, 79),
    (1, 46, 64), (1, 24, 66), (2, 5, 70), (3, 31, 65), (4, 49, 58),
    (1, 4, 5), (6, 60, 67), (7, 32, 75), (8, 48, 82), (9, 35, 41),
    (10, 39, 62), (11, 14, 61), (12, 71, 74), (13, 23, 78), (11, 35, 55),
    (15, 16, 79), (7, 9, 16), (17, 54, 63), (18, 50, 57), (19, 30, 47),
    (20, 64, 80), (21, 28, 69), (22, 25, 43), (13, 22, 37), (2, 47, 51),
    (23, 54, 74), (26, 34, 72), (27, 36, 37), (21, 36, 63), (29, 40, 44),
    (19, 26, 57), (3, 46, 82), (14, 15, 58), (33, 52, 53), (30, 43, 52),
    (6, 9, 52), (27, 33, 65), (25, 69, 73), (38, 55, 83), (20, 39, 77),
    (18, 29, 56), (32, 48, 71), (42, 51, 59), (28, 44, 79), (34, 60, 62),
    (31, 45, 61), (46, 68, 77), (6, 24, 76), (8, 10, 78), (40, 41, 70),
    (17, 50, 53), (42, 66, 68), (4, 22, 72), (36, 64, 81), (13, 29, 47),
    (2, 8, 81), (56, 67, 73), (5, 38, 50), (12, 38, 64), (59, 72, 80),
    (3, 26, 79), (45, 76, 81), (1, 65, 74), (7, 18, 77), (11, 56, 59),
    (14, 39, 54), (16, 37, 66), (10, 28, 55), (15, 60, 70), (17, 25, 82),
    (20, 30, 31), (12, 67, 68), (23, 75, 80), (27, 32, 62), (24, 69, 75),
    (19, 21, 71), (34, 53, 61), (35, 46, 47), (33, 59, 76), (40, 43, 83),
    (41, 42, 63), (49, 75, 83), (20, 44, 48), (42, 49, 57),
)
assert len(MN_CHECKS) == LDPC_N


# --------------------------------------------------------------------------- #
# CRC14（poly 0x6757；get_crc14.f90 位级算法自述重写）
# --------------------------------------------------------------------------- #
def crc14_bits(msg77: Sequence[int]) -> List[int]:
    """对 77-bit 消息计算 14-bit CRC（返回 14 个 0/1）。

    机制：消息后补 14 个零位，按 CRC14 多项式做 15 位移位寄存器，
    最终寄存器前 14 位即 CRC。与 get_crc14.f90:14-23 同构。
    """
    mc = [int(b) & 1 for b in msg77] + [0] * 14
    p = CRC14_POLY
    r = mc[0:15]
    for i in range(77):
        r[14] = mc[i + 14]
        fb = r[0]
        if fb:
            r = [(r[j] ^ p[j]) for j in range(15)]
        r = r[1:] + [r[0]]     # cshift left
    return r[0:14]


def check_crc14(bits91: Sequence[int]) -> bool:
    """校验 91-bit（77 消息 + 14 CRC）是否自洽。"""
    bits91 = [int(b) & 1 for b in bits91]
    if len(bits91) != LDPC_K:
        return False
    return bits91[77:91] == crc14_bits(bits91[0:77])


# --------------------------------------------------------------------------- #
# LDPC(174,91) 编码 + BP 解码
# --------------------------------------------------------------------------- #
def _gf2_invert(A: np.ndarray) -> np.ndarray:
    """GF(2) 方阵求逆（高斯-约当）。A: (n,n) 0/1。"""
    n = A.shape[0]
    M = np.hstack([A.astype(np.uint8), np.eye(n, dtype=np.uint8)])
    col = 0
    for row in range(n):
        piv = None
        for r in range(row, n):
            if M[r, col]:
                piv = r
                break
        if piv is None:
            raise ValueError("GF(2) 矩阵奇异，无法求逆")
        M[[row, piv]] = M[[piv, row]]
        for r in range(n):
            if r != row and M[r, col]:
                M[r] ^= M[row]
        col += 1
    return M[:, n:]


class Ft8Codec:
    """LDPC(174,91) 系统编码器 + log-domain tanh BP 解码器。

    机制（自述）：
      - 编码：H=[H_info(83×91) | H_parity(83×83)]，p = H_parity^{-1}·H_info·m
        （GF(2)），codeword = [m(91), p(83)]。
      - 解码：log-domain tanh BP（bpdecode174_91.f90:99-112），每轮硬判决查
        校验子；全 0 则验 CRC14，过即早停返回；ncheck 连续不降且 >15 放弃。
    """

    def __init__(self) -> None:
        H = np.zeros((LDPC_M, LDPC_N), dtype=np.uint8)
        for i in range(LDPC_N):
            for c in MN_CHECKS[i]:
                H[c - 1, i] = 1
        self.H = H
        self.H_info = H[:, 0:LDPC_K]
        self.H_parity = H[:, LDPC_K:LDPC_N]
        self.Hp_inv = _gf2_invert(self.H_parity)
        self.check_vars: List[np.ndarray] = [np.where(H[j])[0] for j in range(LDPC_M)]
        self.var_checks: List[np.ndarray] = [np.where(H[:, i])[0] for i in range(LDPC_N)]

    def encode(self, msg91: Sequence[int]) -> np.ndarray:
        """91-bit（77 消息 + 14 CRC）→ 174-bit 码字（int8 0/1）。"""
        m = np.array([int(b) & 1 for b in msg91], dtype=np.uint8)
        if m.size != LDPC_K:
            raise ValueError(f"信息位须为 {LDPC_K} bit")
        p = (self.Hp_inv @ (self.H_info @ m)) & 1
        cw = np.concatenate([m, p]).astype(np.int8)
        if not bool(((self.H @ cw) % 2 == 0).all()):
            raise ValueError("编码后校验子非零（H 矩阵构造有误）")
        return cw

    def decode(self, llr: Sequence[float], max_iter: int = 50
               ) -> Optional[np.ndarray]:
        """log-domain tanh BP 解码。成功返回 91-bit（int8），失败返回 None。"""
        llr = np.array(llr, dtype=np.float64)
        if llr.size != LDPC_N:
            raise ValueError(f"LLR 须为 {LDPC_N} 个")
        edge_idx: List[List[int]] = [[] for _ in range(LDPC_M)]
        toc: List[float] = []
        e = 0
        for j in range(LDPC_M):
            for vi in self.check_vars[j]:
                edge_idx[j].append(e)
                toc.append(float(llr[vi]))
                e += 1
        tov = np.zeros(LDPC_N)
        nclast = 0
        ncnt = 0
        for it in range(max_iter + 1):
            zn = llr + tov
            cw = (zn < 0).astype(np.uint8)     # 正 LLR -> bit0
            ncheck = int(np.count_nonzero((self.H @ cw) % 2))
            if ncheck == 0 and check_crc14(cw[0:LDPC_K].tolist()):
                return cw[0:LDPC_K].astype(np.int8)
            if it > 0:
                nd = ncheck - nclast
                ncnt = 0 if nd < 0 else ncnt + 1
                if ncnt >= 5 and it >= 10 and ncheck > 15:
                    return None
            nclast = ncheck
            for j in range(LDPC_M):
                for k, vi in enumerate(self.check_vars[j]):
                    toc[edge_idx[j][k]] = zn[vi] - tov[vi]
            tov[:] = 0.0
            for j in range(LDPC_M):
                edges = edge_idx[j]
                vals = np.array([toc[x] for x in edges])
                t = np.tanh(np.clip(vals / 2.0, -20.0, 20.0))
                for k, vi in enumerate(self.check_vars[j]):
                    prod = np.prod(np.delete(t, k))
                    tov[vi] += 2.0 * math.atanh(max(-0.999999, min(0.999999, prod)))
        return None


# --------------------------------------------------------------------------- #
# 28-bit 呼号打包（pack28.f90 标准呼号分支自述重写）
# --------------------------------------------------------------------------- #
_A1 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 36
_A2 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 36
_A3 = "0123456789"                              # 10
_A4 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"             # 27


def _normalize_callsign(call: str) -> str:
    """归一化到 6 字符：区位数字在第 3 位（iarea=2 时前补空格）。

    对应 pack28.f90: iarea=2 -> ' '//c13(1:5)；iarea=3 -> c13(1:6)。
    """
    c = call.strip().upper()
    digit_pos = next((i for i, ch in enumerate(c) if ch.isdigit()), -1)
    if digit_pos == 1:          # 数字在第 2 位：前补空格
        c = " " + c
    return (c + "      ")[0:6]


def pack28_callsign(call: str) -> int:
    """标准呼号（含一个区位数字）→ 28-bit 整数。"""
    c = _normalize_callsign(call)
    i1 = _A1.index(c[0]); i2 = _A2.index(c[1]); i3 = _A3.index(c[2])
    i4 = _A4.index(c[3]); i5 = _A4.index(c[4]); i6 = _A4.index(c[5])
    n = 36 * 10 * 27 ** 3 * i1 + 10 * 27 ** 3 * i2 + 27 ** 3 * i3 \
        + 27 ** 2 * i4 + 27 * i5 + i6
    return (n + NTOKENS + MAX22) & ((1 << 28) - 1)


def unpack28_callsign(n28: int) -> str:
    """28-bit 整数 → 标准呼号（特殊令牌/哈希返回 '???'）。"""
    n = n28 - NTOKENS - MAX22
    if n < 0:
        return "???"
    i1 = n // (36 * 10 * 27 ** 3); n %= 36 * 10 * 27 ** 3
    i2 = n // (10 * 27 ** 3); n %= 10 * 27 ** 3
    i3 = n // 27 ** 3; n %= 27 ** 3
    i4 = n // 27 ** 2; n %= 27 ** 2
    i5 = n // 27; i6 = n % 27
    s = _A1[i1] + _A2[i2] + _A3[i3] + _A4[i4] + _A4[i5] + _A4[i6]
    return s.strip()


def pack_grid4(grid: str) -> int:
    """4 字符 Maidenhead 网格（'EM12'）→ 15-bit 整数。"""
    g = grid.strip().upper()
    return (ord(g[0]) - 65) * 1800 + (ord(g[1]) - 65) * 100 \
        + int(g[2]) * 10 + int(g[3])


def unpack_grid4(n: int) -> str:
    """15-bit 整数 → 4 字符网格。"""
    j1 = n // 1800; n %= 1800
    j2 = n // 100; n %= 100
    return chr(65 + j1) + chr(65 + j2) + str(n // 10) + str(n % 10)


# --------------------------------------------------------------------------- #
# 77-bit 标准消息（i5bit=0）：28 +1 +28 +1 +1 +15 +3
# --------------------------------------------------------------------------- #
def pack77(from_call: str, to_call: str, grid4: str = "EM12",
           report: bool = False) -> List[int]:
    """组装 77-bit 标准消息：from(28)+/R(1)+to(28)+/R(1)+ir(1)+grid(15)+type(3)。"""
    n28a = pack28_callsign(from_call)
    n28b = pack28_callsign(to_call)
    igrid = pack_grid4(grid4)
    bits: List[int] = []
    for v, w in ((n28a, 28), (0, 1), (n28b, 28), (0, 1),
                 (1 if report else 0, 1), (igrid, 15), (1, 3)):
        bits += [(v >> (w - 1 - k)) & 1 for k in range(w)]
    assert len(bits) == 77
    return bits


def unpack77(bits77: Sequence[int]) -> Optional[Dict[str, object]]:
    """解析 77-bit 标准消息 → dict；非标准/非法 → None（诚实空态）。"""
    b = [int(x) & 1 for x in bits77]
    if len(b) != 77:
        return None

    def field(lo: int, width: int) -> int:
        v = 0
        for k in range(width):
            v = (v << 1) | b[lo + k]
        return v

    n28a = field(0, 28); n28b = field(29, 28)
    ir = field(58, 1); igrid = field(59, 15); i3 = field(74, 3)
    if i3 not in (1, 2):
        return None
    from_call = unpack28_callsign(n28a)
    to_call = unpack28_callsign(n28b)
    if "???" in (from_call, to_call):
        return None
    exch = unpack_grid4(igrid) if igrid <= 32400 else f"R{igrid - 32400}"
    return {"from": from_call, "to": to_call, "exchange": exch,
            "report": bool(ir), "type": i3}


# --------------------------------------------------------------------------- #
# 8-FSK 符号谱 → LLR（确定性软判决注入接口）
# --------------------------------------------------------------------------- #
def llrs_from_tone_energies(energies: np.ndarray,
                           gray_map: Sequence[int] = (0, 1, 3, 2, 5, 6, 4, 7)
                           ) -> np.ndarray:
    """58 符号 × 8 音能量 → 174 个 LLR（正=bit0 更可能）。确定性、无 RNG。

    tone t 承载的 3-bit 符号为 inv_gray[t]（tone=gray_map[bits3]），
    故按逆格雷分组统计 bit 概率。bit=2 为 MSB（对应码字 s*3）。
    """
    E = np.asarray(energies, dtype=np.float64).reshape(58, 8)
    gm = np.array(gray_map)
    inv = np.argsort(gm)          # inv[t] = tone t 承载的 3-bit 符号
    llr = np.zeros(174)
    for s in range(58):
        for bit in range(3):
            ones = [t for t in range(8) if (inv[t] >> bit) & 1]
            zeros = [t for t in range(8) if not ((inv[t] >> bit) & 1)]
            p1 = E[s, ones].sum() + 1e-12
            p0 = E[s, zeros].sum() + 1e-12
            # bit=0 为 LSB，对应码字 s*3+2；bit=2 为 MSB，对应 s*3
            llr[s * 3 + (2 - bit)] = math.log(p0 / p1)   # 正 = bit0 更可能
    return llr.astype(np.float64)
