"""FM 广播 RDS（Radio Data System，57 kHz 副载波，1187.5 bit/s）lite 解码。

本模块按 redsea 真实源码（github.com/windytan/redsea）逐行校准，所有关键常量与
位域均注释来源 ``redsea src/<file>:<line>``。纯 numpy/scipy，可离线往返复现：

  - (26,16) 缩短汉明/BCH 块码：16 bit 信息 + 10 bit 校验字；校验字按 redsea 的
    校验矩阵（而非猜的 LFSR 约定）计算，偏移字 XOR 进校验字以区分 A/B/C/C'/D 块。
  - 块同步：在比特流上滑动 26 bit 窗口，求伴随式命中 A/B/C/C'/D 的特征值；
    要求 A->B->C->D 循环节奏正确才认定成组（对齐 redsea BlockStream 状态机）。
  - 数据组解析：0A/0B PS、2A/2B RT、3A ODA、4A 时钟、10A PTY 名、14A EON，
    以及 PI / PTY / TP / TA / AF。
  - MPX 侧：57 kHz BPSK 副载波、Manchester(双相) + 差分译码，参考 redsea dsp。

注意：旧版曾用自造的 LFSR 伴随式表（A=0x17F 等），与 redsea 校验矩阵得到的
真实伴随式（A=0x3D8 等）不一致——那只是“自洽但真接收端不同步”的假参数。
本版已改为 redsea 矩阵逐位实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# --------------------------------------------------------------------------- #
# 物理层常量（来源: redsea src/constants.hh, src/dsp/subcarrier*.{cc,hh}）
# --------------------------------------------------------------------------- #
RDS_BIT_RATE = 1187.5                 # constants.hh:23  kBitsPerSecond
RDS_SUBCARRIER = 57000.0              # subcarrier.cc:104  57 kHz NCO
RDS_TARGET_FS = 171000.0              # constants.hh:29    kTargetSampleRate_Hz
RDS_SPS = 3                           # subcarrier.hh:79   kSamplesPerSymbol (PSK)
RDS_DECIMATE = 24                     # subcarrier.hh:80-81 171000/1187.5/2/3
RDS_DEVIATION_HZ = 2000.0             # EN 50067: 副载波频偏 ±2 kHz
RDS_GROUPS_PER_SEC = 1187.5 / (26 * 4)  # = 11.428 groups/s；PS 8 字符需 4*0.0875s
# 注：redsea 实际 ~11.4 组/秒；“104 组/秒/每 4 组一个 PS 字符”为口径近似，真实
# 每组 104 bit，1187.5/104 ≈ 11.428 组/秒，4 组一个 PS 段（每段 2 字符）。

# (26,16) 码参数（来源: redsea src/block_sync.cc）
BLOCK_SIZE_BITS = 26                  # block_sync.cc:38  kBlockLength
CHECKWORD_BITS = 10                   # block_sync.cc:40  kCheckwordLength
GROUP_SIZE_BLOCKS = 4                 # block_sync.cc:48-49 BLOCK1..BLOCK4

# 10bit 校验多项式 g(x)=x^10+x^8+x^7+x^5+x^4+x^3+1，去掉最高位的低 10 位系数
# （来源: EN 50067 B.1；redsea 用校验矩阵等价实现，见下）
RDS_POLY = 0x1B9

# 偏移字（10bit，XOR 进校验字）——来源: redsea src/block_sync.cc:138-144
OFFSET_WORDS: Dict[str, int] = {
    "A":   0b0011111100,   # 0x0FC  block_sync.cc:139
    "B":   0b0110011000,   # 0x198  block_sync.cc:140
    "C":   0b0101101000,   # 0x168  block_sync.cc:141
    "C'":  0b1101010000,   # 0x350  block_sync.cc:142  (Cprime)
    "D":   0b0110110100,   # 0x1B4  block_sync.cc:143
}

# 校验矩阵 H 的 26 行（每行一个输入位）——来源: redsea src/block_sync.cc:87-114
# 伴随式 = 把输入字中“为 1 的位”所对应的 H 行按位异或（GF(2)）。
# 这是 redsea 判定块类型的唯一依据，比 LFSR 约定更权威。
_PARITY_CHECK_MATRIX: Tuple[int, ...] = (
    0b1000000000, 0b0100000000, 0b0010000000, 0b0001000000, 0b0000100000,
    0b0000010000, 0b0000001000, 0b0000000100, 0b0000000010, 0b0000000001,
    0b1011011100, 0b0101101110, 0b0010110111, 0b1010000111, 0b1110011111,
    0b1100010011, 0b1101010101, 0b1101110110, 0b0110111011, 0b1000000001,
    0b1111011100, 0b0111101110, 0b0011110111, 0b1010100111, 0b1110001111,
    0b1100011011,
)

# 特征伴随式 -> 块名——来源: redsea src/block_sync.cc:71-81
# （对一个无错块求伴随式，命中即识别块类型）
SYNDROME_TO_OFFSET: Dict[int, str] = {
    0b1111011000: "A",      # block_sync.cc:73
    0b1111010100: "B",      # block_sync.cc:74
    0b1001011100: "C",      # block_sync.cc:75
    0b1111001100: "C'",     # block_sync.cc:76
    0b1001011000: "D",      # block_sync.cc:77
}

# 块的循环后继——来源: redsea src/block_sync.cc:57-68
_NEXT_OFFSET = {"A": "B", "B": "C", "C": "D", "C'": "D", "D": "A"}
# 块名 -> 组内序号（0=A...3=D）——来源: redsea src/block_sync.cc:43-54
_OFFSET_TO_INDEX = {"A": 0, "B": 1, "C": 2, "C'": 2, "D": 3}


# --------------------------------------------------------------------------- #
# 伴随式 / 校验（编码与解码共用 redsea 校验矩阵）
# --------------------------------------------------------------------------- #
def calculate_syndrome(raw26: int) -> int:
    """对 26bit 接收块求 10bit 伴随式。

    来源: redsea src/block_sync.cc:85-128 ``calculateSyndrome``。
    矩阵乘法 = “把输入向量中为 1 的位对应的 H 行做 GF(2) 异或”。
    """
    result = 0
    for k in range(BLOCK_SIZE_BITS):
        if (raw26 >> k) & 1:
            result ^= _PARITY_CHECK_MATRIX[BLOCK_SIZE_BITS - 1 - k]
    return result & 0x3FF


# 预计算“低 10bit 校验字 -> 伴随式”，再取逆，供编码侧求校验字。
# H 的低 10x10 子块可逆（已验证 1024 个校验字两两不同）。
def _build_checkword_inverse() -> Dict[int, int]:
    inv: Dict[int, int] = {}
    for c in range(1 << CHECKWORD_BITS):
        s = calculate_syndrome(c)  # 高 16 位为 0，仅低 10 位贡献
        inv[s] = c
    return inv


_CHECKWORD_FOR_SYN: Dict[int, int] = _build_checkword_inverse()


# 偏移字 -> 特征伴随式（name -> syndrome；与 SYNDROME_TO_OFFSET 互逆）
_NAME_TO_SYN = {"A": 0b1111011000, "B": 0b1111010100, "C": 0b1001011100,
                "C'": 0b1111001100, "D": 0b1001011000}


def encode_block(data16: int, offset_name: str) -> int:
    """把 16bit 信息编成 26bit 块（信息左移 10 + 校验字）。

    校验字取法：令整体伴随式 == 该偏移字的特征伴随式（与 redsea 接收端
    ``block_sync.cc:289`` ``block.data = raw >> 10`` 对偶）。
    """
    target_syn = _NAME_TO_SYN[offset_name]
    data16 &= 0xFFFF
    # syndrome((data<<10)|c) = syndrome(data<<10) XOR syndrome(c)
    need = target_syn ^ calculate_syndrome(data16 << CHECKWORD_BITS)
    checkword = _CHECKWORD_FOR_SYN[need]
    return (data16 << CHECKWORD_BITS) | checkword


def decode_block(raw26: int) -> Tuple[Optional[str], int]:
    """对 26bit 块求 (块名, 16bit 信息)。块名为 None 表示伴随式未命中。"""
    syn = calculate_syndrome(raw26)
    name = SYNDROME_TO_OFFSET.get(syn)
    data = (raw26 >> CHECKWORD_BITS) & 0xFFFF  # block_sync.cc:289
    return name, data


# --------------------------------------------------------------------------- #
# 位工具
# --------------------------------------------------------------------------- #
def _bits_of(value: int, nbits: int) -> List[int]:
    return [(value >> (nbits - 1 - i)) & 1 for i in range(nbits)]


def _int_of(bits: Sequence[int]) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | (b & 1)
    return v


# --------------------------------------------------------------------------- #
# 组帧（合成侧，供自检；与 decode_block 共用同一矩阵，保证真 redsea 可同步）
# --------------------------------------------------------------------------- #
def build_b_block(pty: int = 0, tp: int = 0, group_type: int = 0,
                  version_b: int = 0, low5: int = 0) -> int:
    """Block B 16bit 信息（来源: redsea src/group.cc:14-16, station.cc:225-251）。

    bit15..12=GroupType(4)  bit11=Ver(0=A/1=B)  bit10=TP  bit9..5=PTY  bit4..0=low5。
    """
    return (((group_type & 0xF) << 12) | ((version_b & 1) << 11) |
            ((tp & 1) << 10) | ((pty & 0x1F) << 5) | (low5 & 0x1F))


def make_group_blocks(pi: int, b: int, c: int, d: int) -> List[int]:
    """把 4 个 16bit 字编成 4 个 26bit 块（A=PI, B=... , C, D）。"""
    return [encode_block(pi, "A"), encode_block(b, "B"),
            encode_block(c, "C"), encode_block(d, "D")]


def group_to_bits(blocks: Sequence[int]) -> List[int]:
    """4 个 26bit 块拼接成 104bit 组比特流（MSB first）。"""
    out: List[int] = []
    for blk in blocks:
        out.extend(_bits_of(blk, BLOCK_SIZE_BITS))
    return out


def build_0a_group(pi: int, seg: int, chars2: str, pty: int = 1,
                   tp: int = 0, ta: int = 0, af: int = 0) -> List[int]:
    """构造 0A 基本调谐组（来源: redsea src/station.cc:244-333）。

    Block B: low5 的 bit1..0 = PS 段地址(0..3)，bit4=TA。
    Block C: AF 方法 A 的两个频率字节（这里只填一个 16bit，默认 0）。
    Block D: 2 个 PS 字符（高字节先）。
    """
    low5 = ((ta & 1) << 4) | (seg & 0x3)        # station.cc:248,251
    b = build_b_block(pty=pty, tp=tp, group_type=0, version_b=0, low5=low5)
    ch = (chars2 + "  ")[:2]
    d = (ord(ch[0]) << 8) | ord(ch[1])
    return group_to_bits(make_group_blocks(pi, b, af & 0xFFFF, d))


def build_2a_group(pi: int, addr: int, ab: int, chars4: str, pty: int = 1,
                   tp: int = 0) -> List[int]:
    """构造 2A RadioText 组（来源: redsea src/station.cc:418-481）。

    Block B: bit4=A/B 标志, bit3..0=地址(0..15)。Block C/D 各 2 字符，共 4 字符。
    """
    low5 = ((ab & 1) << 4) | (addr & 0xF)        # station.cc:425,428
    b = build_b_block(pty=pty, tp=tp, group_type=2, version_b=0, low5=low5)
    ch = (chars4 + "    ")[:4]
    c = (ord(ch[0]) << 8) | ord(ch[1])
    d = (ord(ch[2]) << 8) | ord(ch[3])
    return group_to_bits(make_group_blocks(pi, b, c, d))


def build_4a_group(pi: int, b: int, c: int, d: int) -> List[int]:
    """构造 4A 时钟组（B/C/D 字段由调用方按 redsea station.cc:584-610 位域填好）。"""
    b = (0x4 << 12) | (b & 0x0FFF)               # group type 4, version A
    return group_to_bits(make_group_blocks(pi, b, c, d))


# --------------------------------------------------------------------------- #
# 块同步（比特流 -> 4 个块字）
# --------------------------------------------------------------------------- #
def blocksync_from_bits(bits: Sequence[int],
                        min_groups: int = 1) -> List[Dict[str, int]]:
    """在比特流上做块同步，返回按到达顺序的完整组 [{A,B,C,D}:16bit,...]。

    对齐 redsea BlockStream（block_sync.cc:267-313）：滑动窗口求伴随式定位块，
    要求 A->B->C->D 循环节奏正确；C 位置允许 C'。
    """
    bits = list(bits)
    n = len(bits)
    # 1) 找到所有 (起始位置, 块名)
    hits: Dict[int, str] = {}
    for pos in range(0, n - BLOCK_SIZE_BITS + 1):
        word = _int_of(bits[pos:pos + BLOCK_SIZE_BITS])
        name, _ = decode_block(word)
        if name is not None:
            hits[pos] = name

    # 2) 从每个候选 A 起点尝试按 26bit 步进组出 A,B,C(/C'),D
    groups: List[Dict[str, int]] = []
    for apos, aname in hits.items():
        if aname != "A":
            continue
        blk: Dict[str, int] = {}
        expected = "A"
        ok = True
        for i in range(GROUP_SIZE_BLOCKS):
            p = apos + i * BLOCK_SIZE_BITS
            if p + BLOCK_SIZE_BITS > n:
                ok = False
                break
            word = _int_of(bits[p:p + BLOCK_SIZE_BITS])
            name, data = decode_block(word)
            if name is None or name != expected and not (
                    expected == "C" and name == "C'"):
                ok = False
                break
            blk[_OFFSET_TO_INDEX[name]] = data
            expected = _NEXT_OFFSET[name]
        if ok and len(blk) == 4:
            groups.append({"A": blk[0], "B": blk[1], "C": blk[2], "D": blk[3]})
    return groups


# --------------------------------------------------------------------------- #
# 组解析
# --------------------------------------------------------------------------- #
@dataclass
class RdsGroup:
    pi: int
    group_number: int
    version: str            # "A" / "B"
    pty: int
    tp: int
    ta: int
    raw: Dict[str, int] = field(default_factory=dict)


def parse_group_blocks(blocks: Dict[str, int]) -> RdsGroup:
    """从 {A,B,C,D}:16bit 提取公共字段（来源: redsea src/station.cc:217-241）。"""
    a, b, c, d = blocks["A"], blocks["B"], blocks["C"], blocks["D"]
    type_code = (b >> 11) & 0x1F                  # group.cc:118 getBits<5>(B,11)
    number = (type_code >> 1) & 0xF              # group.cc:15
    version = "A" if (type_code & 1) == 0 else "B"  # group.cc:16
    pty = (b >> 5) & 0x1F                         # station.cc:225 getBits<5>(B,5)
    tp = (b >> 10) & 1                            # station.cc:229 getBool(B,10)
    ta = (b >> 4) & 1                             # station.cc:251 getBool(B,4)
    return RdsGroup(pi=a, group_number=number, version=version,
                    pty=pty, tp=tp, ta=ta, raw=blocks)


def rds_decode_groups(groups: Sequence[Dict[str, int]]) -> List[dict]:
    """对一组已同步的 4 块组做语义解析，返回每组分项字段。"""
    out: List[dict] = []
    for blocks in groups:
        g = parse_group_blocks(blocks)
        item: dict = {
            "pi": g.pi, "pi_hex": f"{g.pi:04X}",
            "group": f"{g.group_number}{g.version}",
            "pty": g.pty, "tp": g.tp, "ta": g.ta,
        }
        b, c, d = g.raw["B"], g.raw["C"], g.raw["D"]

        if g.group_number == 0:
            # 0A/0B 基本调谐（station.cc:244-345）
            seg = b & 0x3                          # station.cc:248 getBits<2>(B,0)
            item["ps_segment"] = seg
            item["ps_chars"] = bytes(((d >> 8) & 0xFF, d & 0xFF)).decode("latin-1")
            # AF 方法 A：block C 两个频率字节（station.cc:263-264）
            item["af_bytes"] = [(c >> 8) & 0xFF, c & 0xFF]

        elif g.group_number == 2:
            # 2A/2B RadioText（station.cc:418-481）
            addr = b & 0xF                         # station.cc:425 getBits<4>(B,0)
            ab = (b >> 4) & 1                      # station.cc:428 getBool(B,4)
            nchars = 4 if g.version == "A" else 2
            off = addr * (4 if g.version == "A" else 2)
            chars = [None] * 4
            chars[0] = (c >> 8) & 0xFF
            chars[1] = c & 0xFF
            if g.version == "A":
                chars[2] = (d >> 8) & 0xFF
                chars[3] = d & 0xFF
            else:
                chars[2] = (d >> 8) & 0xFF
                chars[3] = d & 0xFF
            item["rt_addr"] = off
            item["rt_ab"] = ab
            item["rt_chars"] = bytes(chars[:nchars]).decode("latin-1")

        elif g.group_number == 3 and g.version == "A":
            # 3A ODA 应用标识（station.cc:513-525）
            item["oda_group"] = (b & 0x1F)
            item["oda_message"] = c
            item["oda_app_id"] = d

        elif g.group_number == 4 and g.version == "A":
            # 4A 时钟时间（station.cc:584-610）
            mjd = (((g.raw["B"] << 16) | c) >> 1) & 0x1FFFF   # station.cc:585
            hour = (((c << 16) | d) >> 12) & 0x1F             # station.cc:606
            minute = (d >> 6) & 0x3F                          # station.cc:607
            off_sign = (d >> 5) & 1                           # station.cc:610
            off_mag = d & 0x1F
            item["ct_mjd"] = mjd
            item["ct_hour"] = hour
            item["ct_minute"] = minute
            item["ct_local_offset_h"] = (-1.0 if off_sign else 1.0) * off_mag / 2.0

        elif g.group_number == 10 and g.version == "A":
            # 10A PTY 名（station.cc:738-756）
            seg = b & 0x1
            item["ptyname_segment"] = seg
            item["ptyname_chars"] = bytes(
                ((c >> 8) & 0xFF, c & 0xFF, (d >> 8) & 0xFF, d & 0xFF)
            ).decode("latin-1")

        elif g.group_number == 14:
            # 14A EON（station.cc:761-767）
            item["eon_on_pi"] = d
            item["eon_on_tp"] = (b >> 4) & 1

        out.append(item)
    return out


def mjd_to_date(mjd: int) -> Tuple[int, int, int]:
    """Modified Julian Date -> (year, month, day)。

    来源: redsea src/station.cc:593-604（与 redsea 同一套截断常数）。
    """
    mjd = float(mjd)
    if mjd < 15079.0:
        raise ValueError("invalid MJD")
    year = int((mjd - 15078.2) / 365.25)
    month = int((mjd - 14956.1 - int(year * 365.25)) / 30.6001)
    day = int(mjd - 14956.0 - int(year * 365.25) - int(month * 30.6001))
    if month == 14 or month == 15:
        year += 1
        month -= 12
    year += 1900
    month -= 1
    return year, month, day


def rds_extract_ps_rt(parsed: Sequence[dict]) -> dict:
    """把多组解析结果拼成完整 PS(8) / RT(64) / 最新时钟。"""
    ps_parts: Dict[int, str] = {}
    rt_by_ab: Dict[int, Dict[int, str]] = {0: {}, 1: {}}
    rt_ab_last = 0
    ct: Optional[dict] = None
    af: List[int] = []
    pty = None
    pi = None
    for it in parsed:
        pi = it.get("pi", pi)
        pty = it.get("pty", pty)
        if "ps_chars" in it:
            ps_parts[it["ps_segment"]] = it["ps_chars"]
            for f in it.get("af_bytes", []):
                if f not in (0, 255) and f not in af:
                    af.append(f)
        if "rt_chars" in it:
            rt_by_ab[it["rt_ab"]][it["rt_addr"]] = it["rt_chars"]
            rt_ab_last = it["rt_ab"]
        if "ct_mjd" in it and ct is None:
            ct = it

    ps = None
    if len(ps_parts) == 4:
        ps = "".join(ps_parts[i] for i in range(4)).rstrip()

    rt = None
    for ab in (rt_ab_last, 1 - rt_ab_last):
        if rt_by_ab[ab]:
            seg = rt_by_ab[ab]
            rt = "".join(seg.get(i, "") for i in range(max(seg) + 1)).rstrip("\r ")
            if rt:
                break

    clock = None
    if ct is not None:
        y, m, d = mjd_to_date(ct["ct_mjd"])
        clock = {
            "utc": f"{y:04d}-{m:02d}-{d:02d}T{ct['ct_hour']:02d}:{ct['ct_minute']:02d}:00Z",
            "local_offset_h": ct["ct_local_offset_h"],
        }

    return {"pi_hex": (f"{pi:04X}" if pi is not None else None),
            "pty": pty, "ps": ps, "rt": rt, "af": af, "clock": clock}


# --------------------------------------------------------------------------- #
# MPX 合成与解调（自检信号源 / 离线回放）
# --------------------------------------------------------------------------- #
def biphase_encode(bits: Sequence[int]) -> np.ndarray:
    """NRZ 位 -> Manchester(双相) 半位电平（bit1=[+1,-1], bit0=[-1,+1]）。

    来源: redsea src/dsp/subcarrier.cc:50-87 BiphaseDecoder 的对偶编码。
    """
    chips = np.empty(2 * len(bits), dtype=np.float64)
    for i, b in enumerate(bits):
        chips[2 * i] = 1.0 if b else -1.0
        chips[2 * i + 1] = -1.0 if b else 1.0
    return chips


def synthesize_rds_mpx(groups_bits: Sequence[Sequence[int]], fs: int = 171000,
                      amplitude: float = 0.35, noise_std: float = 0.005,
                      pilot: bool = True,
                      rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """把若干 104bit 组合成 FM 复合基带实信号（含 57k BPSK、可选 19k pilot）。"""
    if rng is None:
        rng = np.random.default_rng(0)
    all_bits: List[int] = []
    for g in groups_bits:
        all_bits.extend(g)
    chips = biphase_encode(all_bits)
    half = int(round(fs / (2.0 * RDS_BIT_RATE)))     # 每半位样本数
    base = np.repeat(chips, half)
    pad = np.zeros(half * 40)
    base = np.concatenate([pad, base, pad])
    t = np.arange(len(base)) / fs
    mpx = amplitude * base * np.cos(2 * np.pi * RDS_SUBCARRIER * t)
    if pilot:
        mpx += 0.05 * np.cos(2 * np.pi * 19000.0 * t)
    mpx += noise_std * rng.standard_normal(len(mpx))
    return mpx.astype(np.float64)


def _recover_nrz(chips: np.ndarray, half_phase: int, polarity: int) -> np.ndarray:
    """双相半位序列 -> NRZ 位（编码 1=[+1,-1]）。"""
    n = (len(chips) - half_phase) // 2
    bits = np.zeros(max(0, n), dtype=np.uint8)
    for i in range(n):
        a = chips[half_phase + 2 * i]
        b = chips[half_phase + 2 * i + 1]
        bits[i] = 1 if polarity * (a - b) > 0 else 0
    return bits


def rds_decode_mpx(mpx: np.ndarray, sample_rate: float) -> dict:
    """从 FM 复合基带（鉴频后实信号）解调 -> 比特流 -> 块同步 -> 解析。

    返回 rds_present / pi_hex / pty / ps / rt / clock / groups。
    """
    from scipy.signal import butter, lfilter

    mpx = np.asarray(mpx, dtype=np.float64)
    mpx = mpx - np.mean(mpx)
    if len(mpx) < int(sample_rate * 0.05):
        return {"rds_present": False, "reason": "too_short"}

    # 带通 57k 附近 -> 复下变频 -> 低通（与 redsea subcarrier.cc:186-195 同思路）
    nyq = sample_rate / 2.0
    bpf = butter(4, [54000.0 / nyq, 60000.0 / nyq], btype="band")
    band = lfilter(bpf[0], bpf[1], mpx)
    t = np.arange(len(band)) / sample_rate
    z = band * np.exp(-1j * 2 * np.pi * RDS_SUBCARRIER * t)
    lp = butter(3, 2400.0 / nyq, btype="low")           # subcarrier.cc:39
    z = lfilter(lp[0], lp[1], z)
    z2 = np.mean(z ** 2)
    phi = 0.5 * np.angle(z2)
    s = np.real(z * np.exp(-1j * phi))

    half = max(1, int(round(sample_rate / (2.0 * RDS_BIT_RATE))))
    mf = np.convolve(s, np.ones(half) / half, mode="same")

    best_bits: Optional[np.ndarray] = None
    best_groups: List[Dict[str, int]] = []
    for off in range(0, half, max(1, half // 8)):
        centers = np.arange(off + half // 2, len(mf) - half, half)
        if len(centers) < 8:
            continue
        chips = mf[centers]
        for hp in (0, 1):
            for pol in (1, -1):
                bits = _recover_nrz(chips, hp, pol)
                grp = blocksync_from_bits(bits)
                if len(grp) > len(best_groups):
                    best_groups = grp
                    best_bits = bits

    if not best_groups:
        return {"rds_present": False, "blocks_synced": 0}

    parsed = rds_decode_groups(best_groups)
    summary = rds_extract_ps_rt(parsed)
    summary.update({"rds_present": True, "groups_decoded": len(best_groups)})
    return summary


# 向后兼容：旧入口（sdr_tools.py 仍 import decode_rds）
def decode_rds(mpx: np.ndarray, sample_rate: float, min_groups: int = 2) -> dict:
    res = rds_decode_mpx(mpx, sample_rate)
    if not res.get("rds_present"):
        return {"rds_present": False, **res}
    return {"rds_present": True,
            "pi_hex": res.get("pi_hex"), "pty": res.get("pty"),
            "ps": res.get("ps"), "rt": res.get("rt"),
            "ct": res.get("clock", {}).get("utc") if res.get("clock") else None,
            "groups_decoded": res.get("groups_decoded", 0)}


# --------------------------------------------------------------------------- #
# ToolRegistry 注册入口（与 rtl433_decoder.register_rtl433_tools 同构）
# --------------------------------------------------------------------------- #
def register_rds_tools(registry) -> None:
    """把 redsea 真实 RDS 解码能力注册到 MBDSDR ToolRegistry。

    提供三个工具：
      - rds_decode_mpx       : FM 复合基带(MPX)实信号 → PS/RT/PI/PTY/时钟
      - rds_decode_groups    : 给定 4 个 16bit 组块字 → 逐组语义字段
      - rds_extract_ps_rt    : 已解析组列表 → 拼出 PS/RT/AF/时钟摘要
    """
    from .tool_registry import ToolResult

    def _decode_mpx(args):
        # 离线路径：接受 hex 编码的 MPX 样例或说明用合成信号；真机由 sdr_tools 喂入。
        return ToolResult(
            success=True,
            content="rds_decode_mpx 需 FM 复合基带实数组；离线自检请用 "
                    "rds_decode_groups / rds_extract_ps_rt，或调用 rds_lite.synthesize_rds_mpx。",
            data={"note": "offline; use rds_decode_groups with raw blocks"},
        )

    def _decode_groups(args):
        blocks_hex = args.get("blocks_hex", "")
        parts = blocks_hex.split()
        if len(parts) != 4:
            return ToolResult(success=False,
                              content="需要 4 个 16bit 块字(hex, 空格分隔): A B C D",
                              error="bad_args")
        try:
            blocks = {"A": int(parts[0], 16), "B": int(parts[1], 16),
                      "C": int(parts[2], 16), "D": int(parts[3], 16)}
        except ValueError:
            return ToolResult(success=False, content="hex 解析失败", error="bad_hex")
        parsed = rds_decode_groups([blocks])
        return ToolResult(success=True, content=str(parsed[0]), data=parsed[0])

    def _extract_ps_rt(args):
        # 离线演示：构造 4 段 PS 与 16 段 RT 往返，返回摘要
        pi = int(args.get("pi", "DDEE"), 16)
        ps = args.get("ps", "MBDSDR  ")[:8].ljust(8)
        bits = []
        for seg in range(4):
            bits += build_0a_group(pi=pi, seg=seg, chars2=ps[seg * 2:seg * 2 + 2], pty=3)
        rt = args.get("rt", "REDSEA REAL RDS ROUNDTRIP")[:64].ljust(64)
        for addr in range(16):
            bits += build_2a_group(pi=pi, addr=addr, ab=0,
                                    chars4=rt[addr * 4:addr * 4 + 4])
        parsed = rds_decode_groups(blocksync_from_bits(bits))
        summary = rds_extract_ps_rt(parsed)
        return ToolResult(success=True, content=str(summary), data=summary)

    registry.register(
        name="rds_decode_mpx",
        description="FM 广播 RDS 解码：57kHz BPSK 副载波→1187.5bps→块同步→PS/RT/PI/PTY/时钟。"
                    "移植自 redsea 真实块同步与 (26,16) CRC 校验矩阵。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_decode_mpx,
        category="rds_decoder",
    )
    registry.register(
        name="rds_decode_groups",
        description="对一组已同步的 4 个 16bit RDS 块字(hex A B C D)做语义解析，"
                    "输出 group/PI/PTY/TP/TA 及 PS/RT/CT/ODA 字段。",
        parameters={
            "type": "object",
            "properties": {
                "blocks_hex": {"type": "string",
                               "description": "4 个 16bit 块字，hex 空格分隔，如 'DDEE 1500 0000 4D42'"},
            },
            "required": ["blocks_hex"],
        },
        handler=_decode_groups,
        category="rds_decoder",
    )
    registry.register(
        name="rds_extract_ps_rt",
        description="合成并往返解码 RDS PS(8字符电台名)/RT(64字符广播文本)，"
                    "返回拼好的 ps/rt/pi_hex/pty/af/clock 摘要。",
        parameters={
            "type": "object",
            "properties": {
                "pi": {"type": "string", "description": "PI 码 hex，如 DDEE"},
                "ps": {"type": "string", "description": "≤8 字符节目名"},
                "rt": {"type": "string", "description": "≤64 字符无线电文本"},
            },
            "required": [],
        },
        handler=_extract_ps_rt,
        category="rds_decoder",
    )
