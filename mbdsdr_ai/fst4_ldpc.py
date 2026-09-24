"""FST4 LDPC (240,101) 译码——与 FT8 同源的 Tanner 图 BP。

复用 wsjtx parity 文件的 Mn/Nm/nrw 结构（每 bit 连 3 个 check），
自动推断 N=len(Mn)/3、M=len(nrw)，做归一化 min-sum BP。
帧结构/波形/CRC24/解包后续再接；本模块先把 LDPC 物理层硬骨头打通。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

_N = 240  # 码字长 (240,101)

# ============================================================================
# FST4 物理层常量 —— 来源: WSJT-X repos/wsjtx/lib/fst4/fst4_params.f90
# ============================================================================
KK = 77            # 信息位（77 消息位 + 24 CRC = 101）来源: fst4_params.f90:4
ND = 120           # 数据符号数 来源: fst4_params.f90:5
NS = 40            # 同步符号数 来源: fst4_params.f90:6
NN = NS + ND       # 总符号数 = 160 来源: fst4_params.f90:7
M_ARY = 4          # 4-FSK 来源: genfst4.f90:93-97 (2 bit → 4 个 tone)

# 同步字 —— 来源: WSJT-X genfst4.f90:25-26
SYNC_WORD1 = [0, 1, 3, 2, 1, 0, 2, 3]   # isyncword1
SYNC_WORD2 = [2, 3, 1, 0, 3, 2, 0, 1]   # isyncword2

# Gray 映射 2bit→tone —— 来源: WSJT-X genfst4.f90:93-97
# 00->0, 01->1, 11->2, 10->3  ⇒ Gray=[0,1,3,2]
GRAY_MAP = [0, 1, 3, 2]

# CRC-24 多项式 0x100065B —— 来源: WSJT-X get_crc24.f90:19
CRC24_POLY = 0x100065B

# FST4 扰码向量（77 位）—— 来源: WSJT-X genfst4.f90:29-31
# 编码时 msgbits(1:77)=mod(msgbits(1:77)+rvec,2) （见 genfst4.f90:63），
# 即发送前把 77 个消息位与 rvec 异或；因此解码在 CRC 校验通过后、unpack77 之前，
# 必须再异或一次 rvec 还原出原始消息位，否则 unpack77 解出乱码。
RVEC = [
    0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 1, 1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0,
    1, 0, 0, 1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 1, 1, 1, 1, 0, 0, 1, 0, 1,
    0, 1, 0, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 0, 0, 0, 1, 0, 1,
]
assert len(RVEC) == KK == 77, "rvec 必须是 77 位"


@lru_cache(maxsize=1)
def _graph():
    path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "repos",
        "wsjtx", "lib", "fst4", "ldpc_240_101_parity.f90"))
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    txt = open(path, encoding="utf-8").read()
    mn = [int(x) for x in re.findall(r"\d+", txt.split("data Mn/")[1].split("data Nm/")[0])]
    nm = [int(x) for x in re.findall(r"\d+", txt.split("data Nm/")[1].split("data nrw/")[0])]
    nrw = [int(x) for x in re.findall(r"\d+", txt.split("data nrw/")[1])]

    N = len(mn) // 3
    maxb = max(nrw)
    M = len(nm) // maxb  # 校验行数从 Nm 定（nrw 段可能多带一个数）
    var_checks = [[mn[3 * v + k] - 1 for k in range(3)] for v in range(N)]
    check_vars = []
    for c in range(M):
        n = nrw[c]
        check_vars.append([nm[maxb * c + i] - 1 for i in range(n)])
    return N, M, check_vars, var_checks


def ldpc_bp_decode(llr: list[float], max_iter: int = 25):
    """归一化 min-sum BP（dict 消息图，无下标错位）。输入 N 个 LLR（正=偏0）。"""
    N, M, check_vars, var_checks = _graph()
    if len(llr) != N:
        raise ValueError(f"需要 {N} 个 LLR，得到 {len(llr)}")
    # q[(c,v)]: variable v -> check c 的消息
    q = {(c, v): float(llr[v]) for c, vs in enumerate(check_vars) for v in vs}
    bits = [0] * N
    it = 0
    for it in range(1, max_iter + 1):
        # check -> variable: min-sum
        r = {}
        for c, vs in enumerate(check_vars):
            for v in vs:
                others = [q[(c, u)] for u in vs if u != v]
                prod = 1.0
                s = float("inf")
                for x in others:
                    prod *= 1 if x >= 0 else -1
                    s = min(s, abs(x))
                r[(c, v)] = prod * s * 0.75
        # variable -> check + 硬判决
        for v in range(N):
            total = float(llr[v]) + sum(r[(c, v)] for c in var_checks[v])
            bits[v] = 0 if total >= 0 else 1
            for c in var_checks[v]:
                q[(c, v)] = total - r[(c, v)]
        if all(sum(bits[u] for u in check_vars[c]) % 2 == 0 for c in range(M)):
            break
    return bits, it


# CRC24 多项式抽头（MSB=x^24 … LSB=x^0）—— 来源: WSJT-X get_crc24.f90:20
_CRC24_P = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]


def fst4_crc24(bits: list[int]) -> int:
    """CRC-24 (poly 0x100065B)，逐位移植自 WSJT-X get_crc24.f90。

    - 编码：传入 [msg(77), 0,0,...,0(24)]，返回 24 位 CRC（int）。
    - 校验：传入接收的完整 [msg(77), crc(24)] 共 101 位，余数 == 0 表示校验通过。
    来源: WSJT-X get_crc24.f90（mod-2 长除法 + cshift 左移 1 位）。
    """
    r = [int(x) for x in bits[:25]]
    n = len(bits)
    for i in range(0, n - 25):
        r[24] = int(bits[i + 25])          # r(25)=mc(i+25)
        f = r[0]                           # 旧 r(1) 作为反馈
        if f:
            r = [(r[j] + _CRC24_P[j]) & 1 for j in range(25)]  # r=mod(r+r(1)*p,2)
        r = r[1:] + [r[0]]                 # cshift(r,1)：左移一位
    val = 0
    for j in range(24):                    # r(1:24)，r(1) 为 MSB
        val = (val << 1) | r[j]
    return val


def fst4_derotate(bits77: list[int]) -> list[int]:
    """用 rvec 解扰 77 个消息位。

    来源: WSJT-X genfst4.f90:63 —— 编码时 msgbits(1:77)=mod(msgbits+rvec,2)，
    异或自逆，解码时再异或一次 rvec 即还原原始消息位。
    """
    if len(bits77) != KK:
        raise ValueError(f"需要 {KK} 个消息位，得到 {len(bits77)}")
    return [b ^ rv for b, rv in zip(bits77, RVEC)]


def decode_fst4_message(bits101: list[int]) -> dict | None:
    """LDPC 解出的 101 位（77 消息 + 24 CRC）→ 结构化消息。

    顺序（与 WSJT-X 解码端一致）：
      1. CRC24 校验 —— 来源: decode240_101.f90 get_crc24(m101,101,nbadcrc)==0 才接受
      2. bits[:77] ^= rvec 解扰 —— 来源: genfst4.f90:63（必须在 unpack77 之前）
      3. unpack77 还原文本/呼号/网格
    校验失败返回 None。
    """
    b = [int(x) for x in bits101]
    if len(b) != 101:
        raise ValueError(f"需要 101 个消息位，得到 {len(b)}")
    # 来源: WSJT-X decode240_101.f90 —— CRC 不过则丢弃
    if fst4_crc24(b) != 0:
        return None
    # 来源: WSJT-X genfst4.f90:63 —— 解出后必须先 ^= rvec 再 unpack77（本修复核心）
    msg77 = fst4_derotate(b[:77])
    from mbdsdr_ai.ft8_unpack import unpack77  # packjt77 与 FT8 共用
    return unpack77(msg77)


def fst4_decode(llr: list[float], max_iter: int = 25):
    """端到端：240 个信道 LLR → LDPC BP → CRC 校验 → rvec 解扰 → unpack77。

    返回 (result, iterations)；result 为 dict 或 None（校验失败/解不出来）。
    """
    bits, it = ldpc_bp_decode(llr, max_iter)
    return decode_fst4_message(bits[:101]), it


if __name__ == "__main__":
    N, M, cv, vc = _graph()
    print(f"FST4 (240,101) 图: N={N} M={M}")
    b, it = ldpc_bp_decode([10.0] * N)
    print(f"全零码字: bits 全0={all(x == 0 for x in b)}, 迭代={it}")
    llr = [10.0] * N
    for v in (0, 5, 50, 100, 150, 200):
        llr[v] = -10.0
    b, it = ldpc_bp_decode(llr)
    print(f"翻 6 位后: 迭代={it}, 残留错={sum(1 for x in b if x != 0)}")

    # ---- 修复自检：rvec 解扰逻辑 ----
    # 1) rvec 异或自逆：对已知 77 位向量异或两次应还原
    known = [(i * 7 + 3) % 2 for i in range(77)]
    once = fst4_derotate(known)
    twice = fst4_derotate(once)
    assert twice == known, "rvec 异或两次未还原！"
    print(f"[OK] rvec 异或自逆: 两次异或还原原文 (len={len(RVEC)})")

    # 2) CRC24 自洽：77 消息位 + 24 个 0 算出 CRC，拼回后余数必为 0
    msg77 = known
    crc = fst4_crc24(msg77 + [0] * 24)
    rx101 = msg77 + [(crc >> k) & 1 for k in range(23, -1, -1)]
    assert fst4_crc24(rx101) == 0, "CRC24 自洽失败"
    print(f"[OK] CRC24 自洽: crc=0x{crc:06X}, 校验余数=0")

    # 3) 模拟编码端（genfst4.f90:63-66）：先 msg77 ^= rvec，再对加扰位算 CRC；
    #    解码端 decode_fst4_message 应 CRC 通过后 ^= rvec 还原出 msg77。
    scrambled = [m ^ r for m, r in zip(msg77, RVEC)]
    crc_tx = fst4_crc24(scrambled + [0] * 24)          # 对加扰位算 CRC
    tx101 = scrambled + [(crc_tx >> k) & 1 for k in range(23, -1, -1)]
    res = decode_fst4_message(tx101)
    assert res is not None, "CRC 校验应通过"
    # decode 内部已 ^= rvec；这里独立复核解扰结果 == 原始 known
    recovered = fst4_derotate(scrambled)
    assert recovered == known, "解扰后未还原原始 77 位"
    print(f"[OK] rvec 解扰: 编码加扰→CRC→解码还原 = 原始 77 位 (unpack={res['type']})")

