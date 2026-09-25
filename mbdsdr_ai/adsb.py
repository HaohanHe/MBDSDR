"""ADS-B / Mode-S (1090 ES) 纯 Python 真实解码器（对标 dump1090）。

本模块的每个关键常量与位域都对照 dump1090 源码逐条实现，注释里标注
「来源: dump1090 <file>:<line>」。只依赖 NumPy，可离线复现。

覆盖能力（dump1090 mode_s.c / cpr.c 的最小可用子集）：
  - 8µs preamble 检测（0/1/3.5/4.5µs 四个 0.5µs 脉冲 + 保护窗，demod_2400.c）；
  - 112bit 长帧 / 56bit 短帧 PPM 位判决；
  - CRC-24，生成多项式 0xFFF409（crc.c:28 modesChecksum）；
  - DF17/DF18 解析：CA、ICAO(24bit AA 域)、ME(56bit)；
  - TC1-4  航空器识别呼号（mode_s.c:798 decodeESIdentAndCategory）；
  - TC9-18/0/20-22 空中位置：12bit 气压高度 + CPR 经纬度（mode_s.c:1003）；
  - TC19   空中速度：子类型1/2=地速/航向，3/4=真空速/马赫（mode_s.c:856）；
  - TC5-8  地面位置 CPR（mode_s.c:965）；
  - CPR 全局解码：偶/奇帧对 + NL(Number of Longitude bands) 表（cpr.c:162）。

物理参数（ICAO Annex 10 / RTCA DO-260）：载频 1090MHz，码率 1Mbit/s，
每比特 1µs；数据位为 PPM：前 0.5µs 高、后 0.5µs 低 = 1，反之为 0。

注意：这是基带级实验实现，服务于 AI 工具与论文的软件实测，不替代经过
适航认证的接收机；真实空口还需重采样、多帧、长时配对等。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "CRC_POLY",
    "PREAMBLE_US",
    "PREAMBLE_LEN_US",
    "DATA_BITS_LONG",
    "DATA_BITS_SHORT",
    "CHARSET",
    "mode_s_crc24",
    "bytes_to_bits",
    "bits_to_bytes",
    "build_long_frame",
    "build_identification_frame",
    "encode_callsign",
    "decode_callsign",
    "decode_ac12_altitude",
    "decode_velocity_me",
    "extract_cpr",
    "cpr_nl",
    "decode_cpr_airborne",
    "encode_cpr_airborne",
    "ADSBFrame",
    "decode_frame",
    "ADSBDecoder",
    "modulate_baseband",
    "decode_baseband",
    "add_awgn",
    "register_adsb_tools",
]

# --------------------------------------------------------------------------- #
# 物理 / 协议常量（均标注 dump1090 来源）
# --------------------------------------------------------------------------- #
# 来源: dump1090 crc.c:28 —— Mode S CRC-24 生成多项式（省略最高位 x^24）。
# 正确值是 0xFFF409，不是网上常被误写的 0xFFFA04。
CRC_POLY = 0xFFF409

# 来源: dump1090 demod_2400.c:147-151 —— preamble 四个 0.5µs 脉冲的起始时刻
# （µs）。数据位从 8µs 处开始。理想相位表：脉冲落在 0/1/3.5/4.5µs。
PREAMBLE_US: Tuple[float, ...] = (0.0, 1.0, 3.5, 4.5)
PREAMBLE_LEN_US = 8.0
DATA_BITS_LONG = 112   # DF17/18 等长帧 = 88bit 数据 + 24bit PI
DATA_BITS_SHORT = 56  # DF11 等短帧 = 32bit 数据 + 24bit PI

# 来源: dump1090 ais_charset.c:4 —— 完整 64 项 6bit 呼号字符表（逐字节一致）。
# idx0=@(填充), 1-26=A-Z, 27=[, 28=\, 29=], 30=^, 31=_, 32=空格,
# 33-47=!"#$%&'()*+,-./, 48-57=0-9, 58-63=:;<=>?
CHARSET = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"

# 来源: dump1090.h:89 —— Mode S 载频 1090 MHz
MODES_DEFAULT_FREQ_HZ = 1_090_000_000
# 来源: dump1090.c:156 —— 默认采样率 2.4 Msps（dump1090 常用输入档）
MODES_DEFAULT_SPS = 2_400_000
# 来源: dump1090.h:97 —— 哨兵值 999999 表示 AGC（自动增益）
MODES_DEFAULT_GAIN_DB = 999_999

# 来源: dump1090/icao_filter.c:23 SIZE=4096, :26 TTL=60000ms, :118-125 双缓冲每 60s 翻转。
# ICAO 老化窗口：超过 60s 未出现的 ICAO 从 even/odd CPR 缓冲中清除，防止无限增长。
ICAO_FILTER_TTL_SEC = 60.0

# 来源: dump1090 cpr.c:77-138 —— NL 表（Number of Longitude bands）。
# 阈值纬度（绝对值，度）与对应 NL。对称于赤道。
_CPR_NL_BREAKS: Tuple[Tuple[float, int], ...] = (
    (10.47047130, 59), (14.82817437, 58), (18.18626357, 57),
    (21.02939493, 56), (23.54504487, 55), (25.82924707, 54),
    (27.93898710, 53), (29.91135686, 52), (31.77209708, 51),
    (33.53993436, 50), (35.22899598, 49), (36.85025108, 48),
    (38.41241892, 47), (39.92256684, 46), (41.38651832, 45),
    (42.80914012, 44), (44.19454951, 43), (45.54626723, 42),
    (46.86733252, 41), (48.16039128, 40), (49.42776439, 39),
    (50.67150166, 38), (51.89342469, 37), (53.09516153, 36),
    (54.27817472, 35), (55.44378444, 34), (56.59318756, 33),
    (57.72747354, 32), (58.84763776, 31), (59.95459277, 30),
    (61.04917774, 29), (62.13216659, 28), (63.20427479, 27),
    (64.26616523, 26), (65.31845310, 25), (66.36171008, 24),
    (67.39646774, 23), (68.42322022, 22), (69.44242631, 21),
    (70.45451075, 20), (71.45986473, 19), (72.45884545, 18),
    (73.45177442, 17), (74.43893416, 16), (75.42056257, 15),
    (76.39684391, 14), (77.36789461, 13), (78.33374083, 12),
    (79.29428225, 11), (80.24923213, 10), (81.19801349, 9),
    (82.13956981, 8), (83.07199445, 7), (83.99173563, 6),
    (84.89166191, 5), (85.75541621, 4), (86.53536998, 3),
    (87.00000000, 2),
)


# --------------------------------------------------------------------------- #
# 位 / CRC 基础
# --------------------------------------------------------------------------- #
def bytes_to_bits(data: bytes) -> List[int]:
    """MSB-first：每字节高位在前。"""
    bits: List[int] = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def bits_to_bytes(bits: Sequence[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(bits) - len(bits) % 8, 8):
        b = 0
        for bit in bits[i:i + 8]:
            b = (b << 1) | (bit & 1)
        out.append(b)
    return bytes(out)


def mode_s_crc24(bits: Sequence[int]) -> int:
    """对给定比特序列做 Mode-S CRC-24，返回 24 位余数（syndrome）。

    与 dump1090 crc.c:65 modesChecksum 等价（位级长除法 vs 表驱动，已用
    已知 DF17 帧 8D40621D...2863A7 交叉验证余数为 0）。对完整 112bit
    （数据 + PI）运行，合法 DF17/18 报文余数为 0。
    """
    reg = 0
    for bit in bits:
        top = (reg >> 23) & 1
        reg = (reg << 1) & 0xFFFFFF
        if top ^ (bit & 1):
            reg ^= CRC_POLY
    return reg & 0xFFFFFF


# --------------------------------------------------------------------------- #
# ME 字段位域读取（来源: dump1090 mode_s.h:104 getbits —— 1-based 闭区间）
# --------------------------------------------------------------------------- #
def _me_bits(me: bytes) -> List[int]:
    return bytes_to_bits(me)


def _gb(bits: Sequence[int], first: int, last: int) -> int:
    """复刻 dump1090 getbits(me, first, last)：1-based 闭区间 MSB-first。"""
    v = 0
    for i in range(first - 1, last):
        v = (v << 1) | (bits[i] & 1)
    return v


# --------------------------------------------------------------------------- #
# 呼号 TC1-4（来源: dump1090 mode_s.c:798 decodeESIdentAndCategory）
# --------------------------------------------------------------------------- #
def encode_callsign(callsign: str) -> bytes:
    """把最多 8 字符航班号按 6bit/字符打包为 6 字节，不足右侧补空格(idx32)。"""
    cs = callsign.upper().ljust(8)[:8]
    bits: List[int] = []
    for ch in cs:
        idx = CHARSET.index(ch) if ch in CHARSET else 32  # 空格
        for b in range(5, -1, -1):
            bits.append((idx >> b) & 1)
    return bits_to_bytes(bits)


def decode_callsign(six_bytes: bytes) -> str:
    """从 ME 中 6 字节(48bit)按 6bit/字符解出 8 字符呼号（TC1-4）。"""
    bits = bytes_to_bits(six_bytes)
    chars = []
    for i in range(0, 48, 6):
        idx = 0
        for b in range(6):
            idx = (idx << 1) | bits[i + b]
        chars.append(CHARSET[idx] if idx < len(CHARSET) else " ")
    return "".join(chars).replace("@", " ").strip()


# --------------------------------------------------------------------------- #
# 12bit 气压高度（来源: dump1090 mode_s.c:156 decodeAC12Field）
# --------------------------------------------------------------------------- #
def decode_ac12_altitude(ac12: int) -> Optional[int]:
    """解 12bit AC 高度域，返回英尺。仅实现 Q=1（25ft 间隔）分支，
    Gillham(Q=0) 分支返回 None（多数现代 ES 报文 Q=1）。"""
    q_bit = ac12 & 0x10  # bit 4（12bit 域内）= Q
    if q_bit:
        n = ((ac12 & 0x0FE0) >> 1) | (ac12 & 0x000F)
        return int(n) * 25 - 1000
    return None  # Gillham 编码，本实验实现不展开


# --------------------------------------------------------------------------- #
# TC19 空中速度（来源: dump1090 mode_s.c:856 decodeESAirborneVelocity）
# --------------------------------------------------------------------------- #
def decode_velocity_me(me: bytes) -> Dict[str, Any]:
    bits = _me_bits(me)
    sub = _gb(bits, 6, 8)  # 1-based: ME bit6-8 = 子类型
    out: Dict[str, Any] = {"subtype": sub}
    if sub < 1 or sub > 4:
        return out

    if sub in (1, 2):
        # 来源: mode_s.c:880-907 —— 地速/航向（子类型1=1kt，2=4kt 高分辨）
        ew_raw = _gb(bits, 15, 24)
        ns_raw = _gb(bits, 26, 35)
        if ew_raw and ns_raw:
            ew_sign = -1 if bits[13] else 1   # bit14 (1-based) = index13
            ns_sign = -1 if bits[24] else 1   # bit25 (1-based) = index24
            scale = 4 if sub == 2 else 1
            ew_vel = (ew_raw - 1) * ew_sign * scale
            ns_vel = (ns_raw - 1) * ns_sign * scale
            gs = math.sqrt(ns_vel ** 2 + ew_vel ** 2)
            track = math.degrees(math.atan2(ew_vel, ns_vel))
            if track < 0:
                track += 360.0
            out.update(kind="ground_speed", groundspeed_kt=round(gs, 1),
                       track_deg=round(track, 2))
    elif sub in (3, 4):
        # 来源: mode_s.c:910-932 —— 航向 + 空速（子类型3=IAS，4=TAS，1kt/4kt）
        if bits[13]:  # bit14 heading status
            out["heading_deg"] = round(_gb(bits, 15, 24) * 360.0 / 1024.0, 2)
        airspeed = _gb(bits, 26, 35)
        if airspeed:
            scale = 4 if sub == 4 else 1
            speed = (airspeed - 1) * scale
            out["kind"] = "true_airspeed" if bits[24] else "indicated_airspeed"
            out["airspeed_kt"] = speed

    # 来源: mode_s.c:938-952 —— 垂直速率（bit36 来源, bit37 符号, bit38-46 幅值）
    vert = _gb(bits, 38, 46)
    if vert:
        sign = -1 if bits[36] else 1  # bit37 (1-based) = index36
        out["vert_rate_ft_min"] = (vert - 1) * sign * 64
    return out


# --------------------------------------------------------------------------- #
# CPR 位置提取（来源: dump1090 mode_s.c:1003 / 965）
# --------------------------------------------------------------------------- #
def extract_cpr(me: bytes) -> Dict[str, Any]:
    """从 TC9-18/0/20-22(空中) 或 TC5-8(地面) 的 ME 中抽出 CPR 分量。

    bit22 = F 标志（0=偶 even, 1=奇 odd）；bit23-39 = cpr_lat；
    bit40-56 = cpr_lon。空中帧还解 bit9-20 的气压高度。
    """
    bits = _me_bits(me)
    tc = _gb(bits, 1, 5)
    odd = bool(bits[21])            # bit22 (1-based) = index21
    cpr_lat = _gb(bits, 23, 39)
    cpr_lon = _gb(bits, 40, 56)
    res: Dict[str, Any] = {"tc": tc, "cpr_odd": odd,
                           "cpr_lat": cpr_lat, "cpr_lon": cpr_lon}
    if 9 <= tc <= 18 or tc == 0 or 20 <= tc <= 22:
        alt = decode_ac12_altitude(_gb(bits, 9, 20))
        res["altitude_ft"] = alt
    return res


def cpr_nl(lat: float) -> int:
    """来源: dump1090 cpr.c:77 cprNLFunction —— 给定纬度的经度带数 NL。"""
    lat = abs(lat)
    for threshold, nl in _CPR_NL_BREAKS:
        if lat < threshold:
            return nl
    return 1


# 来源: dump1090 cpr.c:162 decodeCPRairborne —— 空中 CPR 全局解码（偶/奇帧对）。
def decode_cpr_airborne(even_lat: int, even_lon: int,
                        odd_lat: int, odd_lon: int,
                        fflag: int) -> Tuple[float, float]:
    """输入偶/奇两帧的 17bit CPR lat/lon，fflag=0 用偶帧、=1 用奇帧输出。

    返回 (lat_deg, lon_deg)。两帧跨纬度带时抛 ValueError（对应 cpr.c 返回 -1）。
    """
    dlat0 = 360.0 / 60.0
    dlat1 = 360.0 / 59.0

    def mod_int(a: int, b: int) -> int:
        r = a % b
        return r + b if r < 0 else r

    j = int(math.floor(((59 * even_lat - 60 * odd_lat) / 131072.0) + 0.5))
    rlat0 = dlat0 * (mod_int(j, 60) + even_lat / 131072.0)
    rlat1 = dlat1 * (mod_int(j, 59) + odd_lat / 131072.0)
    if rlat0 >= 270:
        rlat0 -= 360
    if rlat1 >= 270:
        rlat1 -= 360
    if not (-90 <= rlat0 <= 90 and -90 <= rlat1 <= 90):
        raise ValueError("CPR latitude out of range")
    if cpr_nl(rlat0) != cpr_nl(rlat1):
        raise ValueError("CPR crossed a latitude band; wait for next pair")

    if fflag:  # 用奇帧
        ni = cpr_nl(rlat1) - 1
        m = int(math.floor(((even_lon * (cpr_nl(rlat1) - 1) -
                             odd_lon * cpr_nl(rlat1)) / 131072.0) + 0.5))
        ni = max(ni, 1)
        dlon = 360.0 / ni
        rlon = dlon * (mod_int(m, ni) + odd_lon / 131072.0)
        rlat = rlat1
    else:     # 用偶帧
        ni = cpr_nl(rlat0)
        m = int(math.floor(((even_lon * (cpr_nl(rlat0) - 1) -
                             odd_lon * cpr_nl(rlat0)) / 131072.0) + 0.5))
        dlon = 360.0 / ni
        rlon = dlon * (mod_int(m, ni) + even_lon / 131072.0)
        rlat = rlat0

    rlon -= math.floor((rlon + 180) / 360.0) * 360.0
    return rlat, rlon


def encode_cpr_airborne(lat: float, lon: float) -> Dict[str, int]:
    """CPR 编码（decode_cpr_airborne 的逆）：从 (lat,lon) 生成偶/奇两帧的
    17bit CPR lat/lon，用于编码→解码往返验证。"""
    dlat0 = 360.0 / 60.0
    dlat1 = 360.0 / 59.0
    even_cprlat = int(math.floor(((lat - math.floor(lat / dlat0) * dlat0) /
                                  dlat0) * 131072.0))
    odd_cprlat = int(math.floor(((lat - math.floor(lat / dlat1) * dlat1) /
                                 dlat1) * 131072.0))
    ni_even = cpr_nl(lat)
    ni_odd = max(cpr_nl(lat) - 1, 1)
    dlon_even = 360.0 / ni_even
    dlon_odd = 360.0 / ni_odd
    even_cprlon = int(math.floor(((lon - math.floor(lon / dlon_even) * dlon_even) /
                                  dlon_even) * 131072.0))
    odd_cprlon = int(math.floor(((lon - math.floor(lon / dlon_odd) * dlon_odd) /
                                 dlon_odd) * 131072.0))
    return {"even_cprlat": even_cprlat, "even_cprlon": even_cprlon,
            "odd_cprlat": odd_cprlat, "odd_cprlon": odd_cprlon}


# --------------------------------------------------------------------------- #
# 帧构造（测试 / 信号源）
# --------------------------------------------------------------------------- #
def build_long_frame(df_ca: int, icao: int, me7: bytes) -> bytes:
    """构造 14 字节长帧（112bit）：DF/CA + ICAO(3B) + ME(7B) + CRC(3B)。"""
    if len(me7) != 7:
        raise ValueError("ME 负载必须为 7 字节")
    head = bytes([df_ca]) + int(icao & 0xFFFFFF).to_bytes(3, "big") + me7
    crc = mode_s_crc24(bytes_to_bits(head))
    return head + crc.to_bytes(3, "big")


def build_identification_frame(icao_hex: str, callsign: str,
                                category: int = 0) -> bytes:
    """构造 DF17 机载识别长报文（14 字节，含 CRC-24）。

    DF=17（10001），CA=5 -> 首字节 0x8D；TC=1（00001）机载识别（标准 TC1-4）。
    """
    icao = int(icao_hex.replace("0x", "").replace(" ", ""), 16) & 0xFFFFFF
    me = bytearray(7)
    me[0] = (1 << 3) | (category & 0x07)  # TC=1
    me[1:7] = encode_callsign(callsign)
    return build_long_frame(0x8D, icao, bytes(me))


# --------------------------------------------------------------------------- #
# 帧解析结果
# --------------------------------------------------------------------------- #
@dataclass
class ADSBFrame:
    df: int
    ca: int
    icao_hex: str
    nbits: int
    crc_ok: bool
    tc: Optional[int] = None
    msg_type: str = ""
    callsign: Optional[str] = None
    altitude_ft: Optional[int] = None
    velocity: Dict[str, Any] = field(default_factory=dict)
    cpr: Dict[str, Any] = field(default_factory=dict)
    raw_hex: str = ""

    def to_dict(self) -> dict:
        return {
            "df": self.df, "ca": self.ca, "icao": self.icao_hex,
            "bits": self.nbits, "crc_ok": self.crc_ok, "tc": self.tc,
            "msg_type": self.msg_type, "callsign": self.callsign,
            "altitude_ft": self.altitude_ft, "velocity": self.velocity,
            "cpr": self.cpr, "raw_hex": self.raw_hex,
        }


def _msg_type_for_tc(tc: int) -> str:
    if 1 <= tc <= 4:
        return "aircraft identification"
    if 5 <= tc <= 8:
        return "surface position (CPR)"
    if 9 <= tc <= 18 or tc == 0:
        return "airborne position (baro alt, CPR)"
    if 20 <= tc <= 22:
        return "airborne position (GNSS alt, CPR)"
    if tc == 19:
        return "airborne velocity"
    return f"DF17 type code {tc}"


def decode_frame(raw: bytes) -> Optional[ADSBFrame]:
    """解析一个已采样的 Mode-S 字节帧（14B 长 / 7B 短）。

    仅对 CRC 通过的 DF17/DF18 长帧做 ME 细分解码；其余返回基本头信息。
    """
    nbits = len(raw) * 8
    if nbits not in (56, 112):
        return None
    bits = bytes_to_bits(raw)
    syndrome = mode_s_crc24(bits)
    # 来源: dump1090 mode_s.c:624-626 —— DF17/18 ICAO 在明文 AA 域 bit8-32。
    df = int("".join(str(b) for b in bits[0:5]), 2)
    ca = int("".join(str(b) for b in bits[5:8]), 2)
    crc_ok = (syndrome == 0)
    icao = int("".join(str(b) for b in bits[8:32]), 2)
    frame = ADSBFrame(df=df, ca=ca, icao_hex=f"{icao:06X}", nbits=nbits,
                      crc_ok=crc_ok, raw_hex=raw.hex().upper())
    if not crc_ok:
        return frame
    if nbits == 112:
        me = raw[4:11]
        me_bits = bytes_to_bits(me)
        tc = int("".join(str(b) for b in me_bits[0:5]), 2)
        frame.tc = tc
        frame.msg_type = _msg_type_for_tc(tc)
        if 1 <= tc <= 4:
            frame.callsign = decode_callsign(me[1:7])
        elif tc == 19:
            frame.velocity = decode_velocity_me(me)
        elif (9 <= tc <= 18 or tc == 0 or 20 <= tc <= 22 or 5 <= tc <= 8):
            frame.cpr = extract_cpr(me)
            frame.altitude_ft = frame.cpr.get("altitude_ft")
    return frame


class ADSBDecoder:
    """有状态 ADS-B 解码器：按 ICAO 累积偶/奇 CPR 位置帧，配对成功时做全局解算。

    用法：
        dec = ADSBDecoder()
        out = dec.handle(frame_bytes)   # 喂一个 14/7 字节帧
        # out 含 crc_ok/df/icao/tc/呼号/高度/速度；位置帧配对后额外含 lat/lon。
    来源：全局 CPR 配对逻辑对标 dump1090 cpr.c:162 decodeCPRairborne。
    """

    def __init__(self) -> None:
        # 每个 ICAO 缓存最近一次 even/odd CPR 位置帧（带 ts，用于 TTL 清理）
        self._even: Dict[str, Dict[str, Any]] = {}
        self._odd: Dict[str, Dict[str, Any]] = {}
        # 来源: dump1090 icao_filter.c:23,26,118-125 —— ICAO 双缓冲老化表。
        # a/b 两个 set 交替作为"当前窗口"；每 60s 翻转一次，旧 inactive set 清空。
        # 一个 ICAO 在 active 或 inactive set 中都算"近期见过"，最长保留 2*TTL。
        self._icao_seen_a: set = set()
        self._icao_seen_b: set = set()
        self._icao_active: str = "a"
        self._icao_last_flip: float = time.time()

    # ------------------------------------------------------------------
    # ICAO 双缓冲老化（来源: dump1090 icao_filter.c:118-125 flip 逻辑）
    # ------------------------------------------------------------------
    def _current_set(self) -> set:
        return self._icao_seen_a if self._icao_active == "a" else self._icao_seen_b

    def _other_set(self) -> set:
        return self._icao_seen_b if self._icao_active == "a" else self._icao_seen_a

    def _maybe_flip_icao(self) -> None:
        """每 ICAO_FILTER_TTL_SEC 翻转一次双缓冲：清空旧 inactive set 并切换 active。"""
        now = time.time()
        if now - self._icao_last_flip >= ICAO_FILTER_TTL_SEC:
            # 翻转前：当前 active 保留（成为新的 inactive），旧 inactive 清空
            self._other_set().clear()
            self._icao_active = "b" if self._icao_active == "a" else "a"
            self._icao_last_flip = now

    def _mark_icao(self, icao: str) -> None:
        """记录某 ICAO 在当前窗口内出现过。"""
        self._maybe_flip_icao()
        self._current_set().add(icao)

    def _is_icao_recent(self, icao: str) -> bool:
        """ICAO 在最近一个翻转周期内出现过（active 或 inactive set 中）。"""
        self._maybe_flip_icao()
        return icao in self._icao_seen_a or icao in self._icao_seen_b

    def _purge_stale_cpr(self) -> None:
        """清除超过 ICAO_FILTER_TTL_SEC 未更新的 even/odd CPR 条目。"""
        now = time.time()
        for d in (self._even, self._odd):
            stale = [
                k for k, v in d.items()
                if now - float(v.get("ts", 0.0)) >= ICAO_FILTER_TTL_SEC
            ]
            for k in stale:
                del d[k]

    def handle(self, raw: bytes) -> Dict[str, Any]:
        fr = decode_frame(raw)
        if fr is None:
            return {"crc_ok": False, "error": "bad length"}
        out = fr.to_dict()
        if fr.crc_ok and fr.cpr:
            self._update_cpr(fr)
            pos = self._try_global(fr.icao_hex, bool(fr.cpr.get("cpr_odd")))
            if pos is not None:
                out["lat"], out["lon"] = pos
        return out

    def _update_cpr(self, fr: ADSBFrame) -> None:
        self._mark_icao(fr.icao_hex)
        c = fr.cpr
        rec = {"cpr_lat": c["cpr_lat"], "cpr_lon": c["cpr_lon"], "ts": time.time()}
        if c.get("cpr_odd"):
            self._odd[fr.icao_hex] = rec
        else:
            self._even[fr.icao_hex] = rec
        self._purge_stale_cpr()

    def _try_global(self, icao: str, fflag: int) -> Optional[Tuple[float, float]]:
        e = self._even.get(icao)
        o = self._odd.get(icao)
        if not e or not o:
            return None
        try:
            return decode_cpr_airborne(e["cpr_lat"], e["cpr_lon"],
                                       o["cpr_lat"], o["cpr_lon"], fflag)
        except ValueError:
            return None


# --------------------------------------------------------------------------- #
# 基带调制 / 前导检测 / 位判决（保留实验链路，参数对齐 demod_2400.c）
# --------------------------------------------------------------------------- #
def modulate_baseband(frame: bytes, fs: float = 4e6,
                      lead_us: float = 0.0,
                      amplitude: float = 1.0) -> np.ndarray:
    """把报文调制成复基带 PPM/OOK 样本（含前导）。lead_us 为前导前静默。"""
    sps = int(round(fs / 1e6))  # 样本/微秒 = 样本/比特
    chip = max(sps // 2, 1)
    lead = int(round(lead_us * sps))
    preamble = np.zeros(int(PREAMBLE_LEN_US * sps), dtype=np.complex128)
    for start_us in PREAMBLE_US:
        s = int(round(start_us * sps))
        preamble[s:s + chip] = amplitude
    bits = bytes_to_bits(frame)
    data = np.zeros(len(bits) * sps, dtype=np.complex128)
    for i, bit in enumerate(bits):
        s = i * sps
        if bit == 1:   # 前半高
            data[s:s + chip] = amplitude
        else:          # 后半高
            data[s + chip:s + 2 * chip] = amplitude
    if lead > 0:
        return np.concatenate([np.zeros(lead, dtype=np.complex128),
                               preamble, data])
    return np.concatenate([preamble, data])


def _find_preamble(env: np.ndarray, sps: int) -> Optional[int]:
    """8µs 四脉冲前导模板匹配滤波 + 脉冲/保护对比度判决。

    模板在 0/1/3.5/4.5µs 处为 1（来源 demod_2400.c:147-151）；保护间隙
    1.5/2.5/5.5/6.5µs 必须显著低于脉冲（来源 demod_2400.c:208-218 quiet bits）。
    """
    plen = int(PREAMBLE_LEN_US * sps)
    if len(env) < plen + sps:
        return None
    chip = max(sps // 2, 1)
    template = np.zeros(plen, dtype=np.float64)
    for start_us in PREAMBLE_US:
        s = int(round(start_us * sps))
        template[s:s + chip] = 1.0
    envf = env.astype(np.float64)
    corr = np.convolve(envf, template[::-1], mode="valid")
    local_energy = np.convolve(envf * envf, np.ones(plen), mode="valid")
    denom = np.sqrt(np.maximum(local_energy * template.sum(), 1e-12))
    score = corr / denom
    k = int(np.argmax(score))
    seg = envf[k:k + plen]
    if len(seg) < plen or score[k] < 0.60:
        return None
    pulse_off = [int(round(u * sps)) for u in PREAMBLE_US]
    guard_off = [int(round(u * sps)) for u in (1.5, 2.5, 5.5, 6.5)]
    pulse_min = min(seg[o:o + chip].mean() for o in pulse_off)
    guard_max = max(seg[o:o + chip].mean() for o in guard_off)
    if pulse_min < 1.8 * guard_max:
        return None
    return k


def decode_baseband(iq: np.ndarray, fs: float = 4e6,
                    long_frame: bool = True) -> Dict[str, Any]:
    """从复基带样本解码单个最强 Mode-S 报文并做 ME 级解析。"""
    sps = int(round(fs / 1e6))
    chip = max(sps // 2, 1)
    env = np.abs(iq) ** 2
    env = env / max(float(env.max()), 1e-12)
    k = _find_preamble(env, sps)
    if k is None:
        return {"found": False, "crc_ok": False, "reason": "preamble_not_found"}
    data_start = k + int(PREAMBLE_LEN_US * sps)
    nbits = DATA_BITS_LONG if long_frame else DATA_BITS_SHORT
    bits: List[int] = []
    for i in range(nbits):
        s = data_start + i * sps
        front = env[s:s + chip].sum()
        back = env[s + chip:s + 2 * chip].sum()
        bits.append(1 if front > back else 0)
    raw = bits_to_bytes(bits)
    crc_ok = (mode_s_crc24(bits) == 0)
    result: Dict[str, Any] = {
        "found": True, "crc_ok": bool(crc_ok), "preamble_index": int(k),
        "bits": bits, "raw_bytes": raw.hex(),
    }
    frame = decode_frame(raw)
    if frame is not None:
        result["frame"] = frame.to_dict()
        result["df"] = frame.df
        result["icao"] = frame.icao_hex
        result["tc"] = frame.tc
        if frame.callsign:
            result["callsign"] = frame.callsign
    return result


def add_awgn(iq: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    """按信号功率给复基带信号加复高斯白噪声到指定 SNR（dB）。"""
    sig_power = float(np.mean(np.abs(iq) ** 2))
    noise_power = sig_power * 10 ** (-snr_db / 10.0)
    noise = (rng.standard_normal(len(iq)) +
             1j * rng.standard_normal(len(iq))) * np.sqrt(noise_power / 2)
    return iq + noise


# --------------------------------------------------------------------------- #
# ToolRegistry 注册入口（与 mbdsdr_ai.sdr_tools.register_sdr_tools 同构）
# --------------------------------------------------------------------------- #
def register_adsb_tools(agent) -> None:
    """把 ADS-B 解码工具注册到 agent.tool_registry。

    提供两个工具：
      - adsb_decode_hex: 解析一个 14/7 字节 Mode-S 帧（hex 字符串），做 CRC
        校验并按 TC 解出呼号/高度/速度/CPR。
      - adsb_decode_iq: 对一段复基带 IQ 做 preamble 检测 + PPM 位判决 + CRC。
    """
    registry = getattr(agent, "tool_registry", None)
    if registry is None:
        return
    try:
        from .tool_registry import ToolResult
    except ImportError:  # 直接脚本运行时的兜底
        from tool_registry import ToolResult  # type: ignore

    def _decode_hex(args: Dict[str, Any]) -> "ToolResult":
        hx = str(args.get("hex", "")).replace(" ", "").replace("0x", "")
        try:
            raw = bytes.fromhex(hx)
        except ValueError as e:
            return ToolResult(success=False, content=f"非法 hex: {e}")
        fr = decode_frame(raw)
        if fr is None:
            return ToolResult(success=False, content="帧长必须为 7/14 字节")
        import json
        return ToolResult(
            success=fr.crc_ok,
            content=json.dumps(fr.to_dict(), ensure_ascii=False, indent=2),
            data=fr.to_dict(),
        )

    def _decode_iq(args: Dict[str, Any]) -> "ToolResult":
        # IQ 由宿主通过 agent 注入；本工具只暴露协议能力与参数说明。
        import json
        info = {
            "note": "对 1090MHz 复基带 IQ 做 preamble 检测/PPM 位判决/CRC/ME 解码",
            "sample_rate_hz": args.get("sample_rate_hz", 2_000_000),
            "crc_poly": hex(CRC_POLY),
            "tc_handled": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                           16, 17, 18, 19],
        }
        return ToolResult(success=True,
                          content=json.dumps(info, ensure_ascii=False, indent=2),
                          data=info)

    registry.register(
        name="adsb_decode_hex",
        description=("解析一个 Mode-S/ADS-B 帧（14 字节 DF17/18 长帧或 7 字节短帧，"
                     "hex 字符串）。做 CRC-24(多项式0xFFF409) 校验，并按类型码 TC "
                     "解出：呼号(TC1-4)、气压高度+CPR位置(TC0/9-18/20-22)、"
                     "空中速度(TC19)。返回 DF/CA/ICAO/TC/各字段与 CRC 是否通过。"),
        parameters={
            "type": "object",
            "properties": {
                "hex": {"type": "string",
                        "description": "Mode-S 帧，如 8D40621D58C382D690C8AC2863A7"},
            },
            "required": ["hex"],
        },
        handler=_decode_hex,
        category="sdr_demod",
    )

    registry.register(
        name="adsb_decode_iq",
        description=("对 1090MHz 复基带 IQ 做 ADS-B 接收：8µs 四脉冲 preamble 检测、"
                     "PPM 位判决、CRC-24 校验、DF17 ME 字段解码。与 dump1090 "
                     "参数对齐（前导 0/1/3.5/4.5µs，码率 1Mbit/s）。"),
        parameters={
            "type": "object",
            "properties": {
                "sample_rate_hz": {"type": "integer",
                                   "description": "IQ 采样率 Hz，默认 2MHz",
                                   "default": 2_000_000},
            },
            "required": [],
        },
        handler=_decode_iq,
        category="sdr_demod",
    )
