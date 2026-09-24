"""FM 广播 RDS（Radio Data System，57kHz 副载波，1187.5 bit/s）lite 解码。

对标 SDR++/GQRX 的 RDS 插件与 redsea 的最小可用子集，纯 numpy/scipy、可离线复现：
  - 双相符号（biphase/Manchester）+ 57kHz BPSK 副载波的复合基带合成（自检信号源）；
  - (26,16) 循环码块：16bit 信息 + 10bit CRC，CRC 异或固定 offset 字标识 A/B/C/D 块；
  - 复下变频 + 静态载波相位恢复 + 半位匹配滤波 + 相位/极性搜索 + 块同步；
  - 解码 PI（台站识别码）、PTY（节目类型）、0A 组 PS（8 字符电台名）。

不做：RT 电台文本完整段重组、AF 频率表、时间/日期组、DI/MS 细分解读、Costas 动态
载波跟踪（静态相位恢复对 ppm 校正后的 RTL 棒足够，动态跟踪留后续）。

标准：EN 50067 / IEC 62108。位域参考 redsea。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

RDS_BIT_RATE = 1187.5
RDS_SUBCARRIER = 57000.0
RDS_INTERNAL_FS = 228000  # 1187.5*192，半位恰 96 样本，整数周期便于同步

# (26,16) 循环码生成多项式 g(x)=x10+x8+x7+x5+x4+x3+1 的低 10 位系数
RDS_POLY = 0x1B9
# 各块 10bit offset 字（标准 checkword 偏置）
OFFSET_WORDS = {"A": 0x0FC, "B": 0x198, "C": 0x168, "D": 0x1B4, "E": 0x350}
BLOCK_ORDER = ("A", "B", "C", "D")
# 完整 26bit 块在本长除法下的伴随式（=offset 经同一循环码的等价 syndrome），
# 接收端对 26bit 长除结果命中此表即完成块同步与类型识别。
SYNDROME = {"A": 0x17F, "B": 0x00E, "C": 0x12F, "D": 0x297, "E": 0x2EC}


# --------------------------------------------------------------------------- #
# CRC / 组帧（编码侧，供合成自检；与解码同一多项式保证自洽）
# --------------------------------------------------------------------------- #
def _bits_of(value: int, nbits: int) -> List[int]:
    return [(value >> (nbits - 1 - i)) & 1 for i in range(nbits)]


def crc10_remainder(bits: Sequence[int]) -> int:
    """RDS (26,16) GF(2) 多项式长除，10 位寄存器，返回余项（MSB-first）。

    对 16bit 信息得到标准 CRC；对完整 26bit 块得到该块类型的伴随式（见 SYNDROME）。
    """
    reg = 0
    mask = 0x3FF
    for b in bits:
        feedback = ((reg >> 9) & 1) ^ (b & 1)
        reg = (reg << 1) & mask
        if feedback:
            reg ^= RDS_POLY
    return reg


def make_block(data16: int, offset_name: str) -> List[int]:
    """生成 26bit 块：16bit 信息 + (CRC10 XOR offset)。"""
    info = _bits_of(data16 & 0xFFFF, 16)
    checksum = crc10_remainder(info) ^ OFFSET_WORDS[offset_name]
    return info + _bits_of(checksum, 10)


def build_b_block(pi_unused: int = 0, tp: int = 0, pty: int = 0,
                  group_type: int = 0, version_b: int = 0, low5: int = 0) -> int:
    """Block B 16bit 信息（EN 50067）：
    bit15..12=GroupType(4) bit11=VerB(1) bit10=TP(1) bit9..5=PTY(5) bit4..0=low5(5)。"""
    return (((group_type & 0xF) << 12) | ((version_b & 1) << 11) |
            ((tp & 1) << 10) | ((pty & 0x1F) << 5) | (low5 & 0x1F))


def build_0a_group(pi: int, seg: int, chars2: str, pty: int = 1,
                   tp: int = 0, ms: int = 0, di: int = 0) -> List[int]:
    """构造 0A 基本调谐组（104bit）：A=PI，B=组0/A，C=段地址+DI/MS，D=2字符PS。

    EN 50067：0A 组 block C 的 bit15..14 为 PS 段地址（0..3，每段 2 字符，
    4 段拼出 8 字符电台名），bit13..0 保留/DI 扩展。
    """
    b = build_b_block(pty=pty, tp=tp, group_type=0, version_b=0)
    c = ((seg & 0x3) << 14) | ((di & 0xF) << 5) | ((ms & 1) << 4)
    ch = (chars2 + "  ")[:2]
    d = (ord(ch[0]) << 8) | ord(ch[1])
    out = make_block(pi, "A") + make_block(b, "B") + make_block(c, "C") + make_block(d, "D")
    return out


def biphase_encode(bits: Sequence[int]) -> np.ndarray:
    """NRZ 位 → 双相半位电平：bit1=[+1,-1]，bit0=[-1,+1]（Manchester）。"""
    chips = np.empty(2 * len(bits), dtype=np.float64)
    for i, b in enumerate(bits):
        chips[2 * i] = 1.0 if b else -1.0
        chips[2 * i + 1] = -1.0 if b else 1.0
    return chips


def synthesize_rds_mpx(groups_bits: Sequence[Sequence[int]], fs: int = RDS_INTERNAL_FS,
                       amplitude: float = 0.35, noise_std: float = 0.01,
                       pilot: bool = True, rng: Optional[np.random.Generator] = None
                       ) -> np.ndarray:
    """把若干 104bit RDS 组合成为 FM 复合基带实信号（含 57k BPSK、可选 19k pilot）。

    返回鉴频后的 mpx 信号（实），采样率 fs。组间留少量空闲半位。
    """
    if rng is None:
        rng = np.random.default_rng(0)
    all_bits: List[int] = []
    for g in groups_bits:
        all_bits.extend(g)
    chips = biphase_encode(all_bits)
    half = fs / (2.0 * RDS_BIT_RATE)  # 半位样本数
    if abs(half - round(half)) > 1e-6:
        # 非整数倍采样率时最近样本量化（真机内部会先重采样到 228k）
        pass
    half = int(round(half))
    base = np.repeat(chips, half)
    # 前置/后置若干半位空载波，给滤波器建立时间
    pad = np.zeros(half * 40)
    base = np.concatenate([pad, base, pad])
    t = np.arange(len(base)) / fs
    mpx = amplitude * base * np.cos(2 * np.pi * RDS_SUBCARRIER * t)
    if pilot:
        mpx += 0.05 * np.cos(2 * np.pi * 19000.0 * t)
    mpx += noise_std * rng.standard_normal(len(mpx))
    return mpx.astype(np.float64)


# --------------------------------------------------------------------------- #
# 解码
# --------------------------------------------------------------------------- #
def _resample_to_internal(mpx: np.ndarray, fs: float):
    from math import gcd
    from scipy.signal import resample_poly
    if int(fs) == RDS_INTERNAL_FS:
        return mpx, RDS_INTERNAL_FS
    g = gcd(int(round(fs)), RDS_INTERNAL_FS)
    up = RDS_INTERNAL_FS // g
    down = int(round(fs)) // g
    return resample_poly(mpx, up, down), RDS_INTERNAL_FS


def _bits_to_int(bits: Sequence[int]) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | (b & 1)
    return v


def _recover_nrz(chips: np.ndarray, half_phase: int, polarity: int) -> np.ndarray:
    """双相半位序列 → NRZ 位。编码 1=[+1,-1]、0=[-1,+1]。"""
    n = (len(chips) - half_phase) // 2
    bits = np.zeros(n, dtype=np.uint8)
    for i in range(n):
        a = chips[half_phase + 2 * i]
        b = chips[half_phase + 2 * i + 1]
        bits[i] = 1 if polarity * (a - b) > 0 else 0
    return bits


def _find_blocks(bits: np.ndarray):
    """滑窗找所有伴随式命中的块，返回 {起始位置: 块类型}。"""
    found = {}
    for pos in range(0, len(bits) - 26 + 1):
        syn = crc10_remainder(bits[pos:pos + 26])
        for name, val in SYNDROME.items():
            if syn == val:
                found[pos] = name
                break
    return found


def _parse_group(bits: np.ndarray, a_pos: int) -> Optional[dict]:
    """解析从 A 块起点开始的 104bit 组，块序 A,B,C,D（C 允许为 E/C'）。"""
    if a_pos + 104 > len(bits):
        return None
    t = {i: crc10_remainder(bits[a_pos + i * 26:a_pos + (i + 1) * 26])
         for i in range(4)}
    if t[0] != SYNDROME["A"] or t[1] != SYNDROME["B"] or t[3] != SYNDROME["D"]:
        return None
    if t[2] not in (SYNDROME["C"], SYNDROME["E"]):
        return None
    info = {}
    for i, nm in ((0, "A"), (1, "B"), (2, "C"), (3, "D")):
        info[nm] = _bits_to_int(bits[a_pos + i * 26:a_pos + i * 26 + 16])
    b = info["B"]
    group_type = (b >> 12) & 0xF
    version_b = (b >> 11) & 1
    tp = (b >> 10) & 1
    pty = (b >> 5) & 0x1F
    out = {"pi": info["A"], "pty": pty, "tp": tp,
           "group_type": group_type, "version_b": version_b}
    if group_type == 0:  # 0A/0B 基本调谐，PS 电台名
        # EN 50067：block C bit15..14 = PS 段地址（0..3），block D 高/低字节各 1 字符
        seg = (info["C"] >> 14) & 0x3
        c1 = info["D"] >> 8
        c2 = info["D"] & 0xFF
        out["ps_seg"] = seg
        out["ps_chars"] = "".join(chr(c) if 32 <= c < 127 else " "
                                  for c in (c1, c2))
    elif group_type == 2:  # 2A/2B RadioText 滚动文本
        addr = b & 0xF              # 段地址 0..15
        ab = (b >> 4) & 1           # A/B 文本版标志（1=新文本版）
        c0 = info["C"] >> 8
        c1 = info["C"] & 0xFF
        c2 = info["D"] >> 8
        c3 = info["D"] & 0xFF
        out["rt_addr"] = addr
        out["rt_ab"] = ab
        out["rt_chars"] = bytes(c if 32 <= c < 127 else 32
                                for c in (c0, c1, c2, c3)).decode("ascii")
    elif group_type == 4 and version_b == 0:  # 4A 时钟时间 CT
        c = info["C"]; d = info["D"]
        # block C: b15=1, bits14..1 = MJD bits15..2；MJD 低 2 位放 block D bit5..4
        mjd = (((c & 0x7FFF) >> 1) << 2) | ((d >> 4) & 0x3)
        hour = (d >> 11) & 0x1F
        minute = (d >> 6) & 0x3F
        off_neg = (d >> 0) & 1       # 1=本地时间偏负
        out["ct_mjd"] = int(mjd)
        out["ct_hour"] = int(hour)
        out["ct_minute"] = int(minute)
        out["ct_offset_neg"] = int(off_neg)
    return out


def decode_rds(mpx: np.ndarray, sample_rate: float, min_groups: int = 2) -> dict:
    """从 FM 复合基带（鉴频后实信号）解码 RDS。

    返回 rds_present / pi_hex / pty / ps（电台名）/ 同步组数与原始组。
    采样率任意（内部重采样到 228kHz）；建议输入采样率 >=140kHz 以保留 57k 副载波。
    """
    from scipy.signal import butter, lfilter, resample_poly  # noqa: F401
    mpx = np.asarray(mpx, dtype=np.float64)
    mpx = mpx - np.mean(mpx)
    if len(mpx) < int(sample_rate * 0.05):
        return {"rds_present": False, "reason": "too_short"}

    x, fs = _resample_to_internal(mpx, sample_rate)
    nyq = fs / 2.0
    bpf = butter(4, [54000.0 / nyq, 60000.0 / nyq], btype="band")
    band = lfilter(bpf[0], bpf[1], x)

    # 复下变频到基带
    t = np.arange(len(band)) / fs
    z = band * np.exp(-1j * 2 * np.pi * RDS_SUBCARRIER * t)
    lp = butter(3, 2400.0 / nyq, btype="low")
    z = lfilter(lp[0], lp[1], z)
    # 静态 BPSK 载波相位恢复：E[z^2] 相位 = 2φ
    z2 = np.mean(z ** 2)
    phi = 0.5 * np.angle(z2)
    s = np.real(z * np.exp(-1j * phi))

    half = int(round(fs / (2.0 * RDS_BIT_RATE)))  # 96
    # 半位矩形匹配滤波
    mf = np.convolve(s, np.ones(half) / half, mode="same")

    best = None
    for off in range(0, half, max(1, half // 8)):
        centers = np.arange(off + half // 2, len(mf) - half, half)
        if len(centers) < 8:
            continue
        chips = mf[centers]
        for hp in (0, 1):
            for pol in (1, -1):
                bits = _recover_nrz(chips, hp, pol)
                found = _find_blocks(bits)
                if len(found) > (best[0] if best else 0):
                    best = (len(found), bits, found)
    if best is None or best[0] < 2:
        return {"rds_present": False, "blocks_synced": 0 if best is None else best[0]}

    _, bits, found = best
    groups = []
    for pos, name in found.items():
        if name == "A":
            g = _parse_group(bits, pos)
            if g:
                groups.append(g)
    if len(groups) < min_groups:
        return {"rds_present": False, "blocks_synced": best[0],
                "groups_partial": len(groups)}

    pi = groups[0]["pi"]
    pty = groups[1]["pty"] if len(groups) > 1 else groups[0]["pty"]
    ps_buf: Dict[int, str] = {}
    rt_buf: Dict[int, Dict[int, str]] = {0: {}, 1: {}}  # ab -> addr -> 4字符
    ct: Dict[str, int] = {}
    for g in groups:
        if g["group_type"] == 0 and "ps_chars" in g:
            ps_buf[g["ps_seg"]] = g["ps_chars"]
        elif g["group_type"] == 2 and "rt_chars" in g:
            rt_buf[g.get("rt_ab", 0)][g["rt_addr"]] = g["rt_chars"]
        elif g["group_type"] == 4 and "ct_mjd" in g:
            ct = {"mjd": g["ct_mjd"], "hour": g["ct_hour"],
                  "minute": g["ct_minute"], "offset_neg": g["ct_offset_neg"]}
    ps = None
    if ps_buf:
        ps = "".join(ps_buf.get(i, "??") for i in range(max(ps_buf) + 1)).strip()
    rt = None
    for ab in (1, 0):  # 优先最新一版文本
        if rt_buf[ab]:
            seg = rt_buf[ab]
            rt = "".join(seg.get(i, "    ") for i in range(max(seg) + 1)).strip()
            if rt:
                rt_ab = ab
                break
    else:
        rt_ab = None
    # MJD -> YYYY-MM-DD
    ct_date = None
    if ct:
        try:
            from datetime import datetime, timedelta
            ct_date = (datetime(1858, 11, 17)
                       + timedelta(days=int(ct["mjd"]))).strftime("%Y-%m-%d")
        except Exception:
            ct_date = None
    out = {
        "rds_present": True,
        "pi_hex": f"{pi:04X}",
        "pty": int(pty),
        "ps": ps,
        "rt": rt,
        "ct": (f"{ct_date} {ct['hour']:02d}:{ct['minute']:02d}"
               if ct and ct_date else None),
        "blocks_synced": best[0],
        "groups_decoded": len(groups),
    }
    return out
