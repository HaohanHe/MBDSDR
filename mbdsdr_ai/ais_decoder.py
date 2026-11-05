# SPDX-License-Identifier: MIT
"""AIS（ITU-R M.1371）报文编解码 + GMSK 基带调制解调（纯 numpy）。

事实层（公开标准 ITU-R M.1371 / IEC 61993-2，不含任何厂商源码）：

  * 信道：AIS1 161.975 MHz（87B）/ AIS2 162.025 MHz（88B）
  * 调制：GMSK 9600 bit/s（BT=0.4，调制指数 0.5，峰值频偏 = 9600/4 = 2400 Hz）
  * 链路层（HDLC）：训练序列 0101… + 起始标志 0x7E + 数据（0-bit stuffing，
    连 5 个 1 插 0）+ FCS-16（CRC-16/X.25，与 AX.25 同一族）+ 结束标志 0x7E
  * 线路编码：NRZI（bit 0 = 电平翻转，bit 1 = 电平保持）
  * 报文：默认 168 bit（Type 1/2/3/18）；Type 5 静态 424 bit；Type 24A 160 bit
  * NMEA 0183 封装：``!AIVDM,<total>,<num>,<seq>,<channel>,<payload>,<fill>*<CS>``
    其中 payload 为 6-bit armoring 字符串，CS 为 ``!`` 与 ``*`` 之间字符的 XOR。

位序约定（诚实声明）：payload bits 按字段流 MSB-first 组织（与公开 AIS
解码器、NMEA 6-bit 打包口径一致）；帧层按该顺序逐位发射。真实物理层
「字节内 LSB 先发」属传输细节，本实现在帧层自洽——编码器产生的波形
可被本解码器逐位还原，字段值与 NMEA 封装和公开口径完全一致。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# 常量（标准事实）
# ---------------------------------------------------------------------------
AIS_BIT_RATE = 9600.0                 # bit/s
AIS_GMSK_BT = 0.4                     # GMSK 带宽时间积
AIS_DEVIATION_HZ = AIS_BIT_RATE / 4.0  # 调制指数 0.5 → 峰值频偏 2400 Hz
AIS_CHANNELS = {"A": 161.975e6, "B": 162.025e6}
HDLC_FLAG = 0x7E
AIS_DEFAULT_BITS = 168                # 单时隙默认报文长度
AIS_PREAMBLE_BITS = 24                # 训练序列长度（0101…）

# 特殊值（M.1371 不可用标记）
LON_NOT_AVAILABLE = 0x6791AC0         # 181 deg in 28 bit 补码语义
LAT_NOT_AVAILABLE = 0x3412140         # 91 deg
SOG_NOT_AVAILABLE = 1023
COG_NOT_AVAILABLE = 3600
HEADING_NOT_AVAILABLE = 511
ROT_NOT_AVAILABLE = -128


# ---------------------------------------------------------------------------
# 6-bit armoring（NMEA 层，M.1371 附录：v+48，v>40 再 +8）
# ---------------------------------------------------------------------------
def armor6(value: int) -> str:
    """0..63 → NMEA payload 字符。"""
    if not 0 <= int(value) <= 63:
        raise ValueError(f"armor6 值越界: {value}")
    c = int(value) + 48
    if c > 87:
        c += 8
    return chr(c)


def dearmor6(ch: str) -> int:
    """NMEA payload 字符 → 0..63；非法字符抛 ValueError。"""
    v = ord(ch) - 48
    if v > 40:
        v -= 8
    if not 0 <= v <= 63:
        raise ValueError(f"非法 AIS payload 字符: {ch!r}")
    return v


# ---------------------------------------------------------------------------
# 6-bit ASCII（报文文本字段层：v<32 → chr(v+64)，否则 chr(v)）
# ---------------------------------------------------------------------------
def text_to_sixbit(text: str, length: int) -> List[int]:
    """文本 → 定长 6-bit 值列表（不足补 '@'=0，超长截断，非法字符按 '@'）。"""
    vals = []
    for ch in text.upper()[:length]:
        o = ord(ch)
        vals.append(o - 64 if 64 <= o <= 95 else (o if 32 <= o <= 63 else 0))
    return vals + [0] * (length - len(vals))


def sixbit_to_text(vals: Sequence[int]) -> str:
    """6-bit 值列表 → 文本（去尾部 '@' 与空格）。"""
    chars = [chr(v + 64) if v < 32 else chr(min(v, 63)) for v in vals]
    return "".join(chars).rstrip("@ ").strip()


# ---------------------------------------------------------------------------
# 位打包（MSB-first）
# ---------------------------------------------------------------------------
def _uint(value: int, width: int) -> List[int]:
    if not 0 <= int(value) < (1 << width):
        raise ValueError(f"uint 溢出: value={value} width={width}")
    return [(int(value) >> (width - 1 - i)) & 1 for i in range(width)]


def _sint(value: int, width: int) -> List[int]:
    lo, hi = -(1 << (width - 1)), (1 << (width - 1)) - 1
    if not lo <= int(value) <= hi:
        raise ValueError(f"sint 溢出: value={value} width={width}")
    return _uint(int(value) & ((1 << width) - 1), width)


def _text6(text: str, n_chars: int) -> List[int]:
    bits: List[int] = []
    for v in text_to_sixbit(text, n_chars):
        bits += _uint(v, 6)
    return bits


def _read_uint(bits: Sequence[int], pos: int, width: int) -> int:
    v = 0
    for b in bits[pos:pos + width]:
        v = (v << 1) | (b & 1)
    return v


def _read_sint(bits: Sequence[int], pos: int, width: int) -> int:
    v = _read_uint(bits, pos, width)
    return v - (1 << width) if v >= (1 << (width - 1)) else v


def _read_text6(bits: Sequence[int], pos: int, n_chars: int) -> str:
    vals = [_read_uint(bits, pos + 6 * i, 6) for i in range(n_chars)]
    return sixbit_to_text(vals)


def bits_to_bytes(bits: Sequence[int]) -> bytes:
    """MSB-first 位流 → 字节串（尾部不足 8 位右侧补 0）。"""
    pad = (-len(bits)) % 8
    bb = list(bits) + [0] * pad
    return bytes(_read_uint(bb, i, 8) for i in range(0, len(bb), 8))


def bytes_to_bits(data: bytes) -> List[int]:
    """字节串 → MSB-first 位流。"""
    out: List[int] = []
    for byte in data:
        out += _uint(byte, 8)
    return out


# ---------------------------------------------------------------------------
# FCS-16（CRC-16/X.25）：复用 ax25 的同族实现，跨模块一致性由测试钉住
# ---------------------------------------------------------------------------
def fcs16(data: bytes) -> int:
    from mbdsdr_ai.ax25 import crc16_ccitt
    return crc16_ccitt(data)


# ---------------------------------------------------------------------------
# 报文构造（字段 → 168/424/160 bit）
# ---------------------------------------------------------------------------
def _lat_to_raw(lat_deg: float) -> int:
    return int(round(max(-90.0, min(90.0, lat_deg)) * 600000))


def _lon_to_raw(lon_deg: float) -> int:
    return int(round(max(-180.0, min(180.0, lon_deg)) * 600000))


def _rot_to_raw(rot_deg_min: float) -> int:
    sign = 1 if rot_deg_min >= 0 else -1
    val = int(round(4.733 * math.sqrt(abs(rot_deg_min))))
    return sign * min(126, val)


def _rot_from_raw(raw: int) -> float:
    if raw in (ROT_NOT_AVAILABLE,):
        return float("nan")
    sign = 1 if raw >= 0 else -1
    return sign * (raw / 4.733) ** 2


def build_type123(mmsi: int, lat_deg: float, lon_deg: float,
                  sog_knots: float, cog_deg: float, heading: int,
                  nav_status: int = 0, rot_deg_min: float = 0.0,
                  msg_type: int = 1, second: int = 0, accuracy: int = 0,
                  maneuver: int = 0, raim: int = 0, radio: int = 0,
                  repeat: int = 0) -> List[int]:
    """Type 1/2/3 位置报告（A 类），168 bit。"""
    if msg_type not in (1, 2, 3):
        raise ValueError("Type 1/2/3 位置报告 msg_type 须为 1/2/3")
    b: List[int] = []
    b += _uint(msg_type, 6)
    b += _uint(repeat, 2)
    b += _uint(mmsi, 30)
    b += _uint(nav_status, 4)
    b += _sint(_rot_to_raw(rot_deg_min), 8)
    b += _uint(int(round(sog_knots * 10)), 10)
    b += _uint(accuracy, 1)
    b += _sint(_lon_to_raw(lon_deg), 28)
    b += _sint(_lat_to_raw(lat_deg), 27)
    b += _uint(int(round(cog_deg * 10)) % 4096, 12)
    b += _uint(heading % 512, 9)
    b += _uint(second % 64, 6)
    b += _uint(maneuver, 2)
    b += _uint(0, 3)                       # spare
    b += _uint(raim, 1)
    b += _uint(radio, 19)
    assert len(b) == 168
    return b


def build_type5(mmsi: int, imo: int, callsign: str, shipname: str,
                ship_type: int = 0, dim_bow: int = 0, dim_stern: int = 0,
                dim_port: int = 0, dim_starboard: int = 0,
                fix_type: int = 1, eta_month: int = 0, eta_day: int = 0,
                eta_hour: int = 0, eta_minute: int = 0,
                draught_m: float = 0.0, destination: str = "",
                dte: int = 0, ais_version: int = 0, repeat: int = 0) -> List[int]:
    """Type 5 静态与航次数据（A 类），424 bit。"""
    b: List[int] = []
    b += _uint(5, 6)
    b += _uint(repeat, 2)
    b += _uint(mmsi, 30)
    b += _uint(ais_version, 2)
    b += _uint(imo, 30)
    b += _text6(callsign, 7)
    b += _text6(shipname, 20)
    b += _uint(ship_type, 8)
    b += _uint(dim_bow, 9)
    b += _uint(dim_stern, 9)
    b += _uint(dim_port, 6)
    b += _uint(dim_starboard, 6)
    b += _uint(fix_type, 4)
    b += _uint(eta_month, 4)
    b += _uint(eta_day, 5)
    b += _uint(eta_hour, 5)
    b += _uint(eta_minute, 6)
    b += _uint(int(round(draught_m * 10)), 8)
    b += _text6(destination, 20)
    b += _uint(dte, 1)
    b += _uint(0, 1)                       # spare
    assert len(b) == 424
    return b


def build_type18(mmsi: int, lat_deg: float, lon_deg: float,
                 sog_knots: float, cog_deg: float, heading: int,
                 second: int = 0, accuracy: int = 0, cs_unit: int = 1,
                 display: int = 0, dsc: int = 0, band: int = 0,
                 msg22: int = 0, assigned: int = 0, raim: int = 0,
                 radio: int = 0, repeat: int = 0) -> List[int]:
    """Type 18 标准 B 类位置报告，168 bit。"""
    b: List[int] = []
    b += _uint(18, 6)
    b += _uint(repeat, 2)
    b += _uint(mmsi, 30)
    b += _uint(0, 8)                       # reserved
    b += _uint(int(round(sog_knots * 10)), 10)
    b += _uint(accuracy, 1)
    b += _sint(_lon_to_raw(lon_deg), 28)
    b += _sint(_lat_to_raw(lat_deg), 27)
    b += _uint(int(round(cog_deg * 10)) % 4096, 12)
    b += _uint(heading % 512, 9)
    b += _uint(second % 64, 6)
    b += _uint(0, 2)                       # reserved
    b += _uint(cs_unit, 1)
    b += _uint(display, 1)
    b += _uint(dsc, 1)
    b += _uint(band, 1)
    b += _uint(msg22, 1)
    b += _uint(assigned, 1)
    b += _uint(raim, 1)
    b += _uint(radio, 20)
    assert len(b) == 168
    return b


def build_type24a(mmsi: int, shipname: str, repeat: int = 0) -> List[int]:
    """Type 24 part A：B 类船名，160 bit。"""
    b: List[int] = []
    b += _uint(24, 6)
    b += _uint(repeat, 2)
    b += _uint(mmsi, 30)
    b += _uint(0, 2)                       # part number = 0 (A)
    b += _text6(shipname, 20)
    assert len(b) == 160
    return b


# ---------------------------------------------------------------------------
# 报文解析
# ---------------------------------------------------------------------------
def parse_payload(bits: Sequence[int]) -> Dict:
    """按报文类型分派解析；未知类型诚实返回 type 与原始长度，不编造字段。"""
    if len(bits) < 38:
        return {"type": None, "error": f"报文过短（{len(bits)} bit）"}
    msg_type = _read_uint(bits, 0, 6)
    out: Dict = {"type": msg_type, "bit_length": len(bits)}
    if msg_type in (1, 2, 3) and len(bits) >= 168:
        lon_raw = _read_sint(bits, 61, 28)
        lat_raw = _read_sint(bits, 89, 27)
        rot_raw = _read_sint(bits, 42, 8)
        out.update({
            "mmsi": _read_uint(bits, 8, 30),
            "nav_status": _read_uint(bits, 38, 4),
            "rot_deg_min": None if rot_raw == ROT_NOT_AVAILABLE
            else _rot_from_raw(rot_raw),
            "sog_knots": _read_uint(bits, 50, 10) / 10.0,
            "accuracy": _read_uint(bits, 60, 1),
            "lon_deg": None if lon_raw == LON_NOT_AVAILABLE
            else lon_raw / 600000.0,
            "lat_deg": None if lat_raw == LAT_NOT_AVAILABLE
            else lat_raw / 600000.0,
            "cog_deg": _read_uint(bits, 116, 12) / 10.0,
            "heading": _read_uint(bits, 128, 9),
            "second": _read_uint(bits, 137, 6),
            "maneuver": _read_uint(bits, 143, 2),
            "raim": _read_uint(bits, 148, 1),
            "radio": _read_uint(bits, 149, 19),
        })
    elif msg_type == 5 and len(bits) >= 424:
        out.update({
            "mmsi": _read_uint(bits, 8, 30),
            "ais_version": _read_uint(bits, 38, 2),
            "imo": _read_uint(bits, 40, 30),
            "callsign": _read_text6(bits, 70, 7),
            "shipname": _read_text6(bits, 112, 20),
            "ship_type": _read_uint(bits, 232, 8),
            "dim_bow": _read_uint(bits, 240, 9),
            "dim_stern": _read_uint(bits, 249, 9),
            "dim_port": _read_uint(bits, 258, 6),
            "dim_starboard": _read_uint(bits, 264, 6),
            "fix_type": _read_uint(bits, 270, 4),
            "eta_month": _read_uint(bits, 274, 4),
            "eta_day": _read_uint(bits, 278, 5),
            "eta_hour": _read_uint(bits, 283, 5),
            "eta_minute": _read_uint(bits, 288, 6),
            "draught_m": _read_uint(bits, 294, 8) / 10.0,
            "destination": _read_text6(bits, 302, 20),
            "dte": _read_uint(bits, 422, 1),
        })
    elif msg_type == 18 and len(bits) >= 168:
        lon_raw = _read_sint(bits, 57, 28)
        lat_raw = _read_sint(bits, 85, 27)
        out.update({
            "mmsi": _read_uint(bits, 8, 30),
            "sog_knots": _read_uint(bits, 46, 10) / 10.0,
            "accuracy": _read_uint(bits, 56, 1),
            "lon_deg": None if lon_raw == LON_NOT_AVAILABLE
            else lon_raw / 600000.0,
            "lat_deg": None if lat_raw == LAT_NOT_AVAILABLE
            else lat_raw / 600000.0,
            "cog_deg": _read_uint(bits, 112, 12) / 10.0,
            "heading": _read_uint(bits, 124, 9),
            "second": _read_uint(bits, 133, 6),
            "cs_unit": _read_uint(bits, 141, 1),
            "display": _read_uint(bits, 142, 1),
            "dsc": _read_uint(bits, 143, 1),
            "band": _read_uint(bits, 144, 1),
            "msg22": _read_uint(bits, 145, 1),
            "assigned": _read_uint(bits, 146, 1),
            "raim": _read_uint(bits, 147, 1),
            "radio": _read_uint(bits, 148, 20),
        })
    elif msg_type == 24 and len(bits) >= 160:
        part = _read_uint(bits, 38, 2)
        out.update({
            "mmsi": _read_uint(bits, 8, 30),
            "part_number": part,
        })
        if part == 0:
            out["shipname"] = _read_text6(bits, 40, 20)
        else:
            out["note"] = "Type 24 part B 字段未解析（诚实声明）"
    else:
        out["note"] = f"Type {msg_type} 未实现字段解析（诚实声明，不编造）"
    return out


# ---------------------------------------------------------------------------
# HDLC 帧层（stuffing / FCS / 标志）
# ---------------------------------------------------------------------------
def stuff_bits(bits: Sequence[int]) -> List[int]:
    """0-bit stuffing：连 5 个 1 后插 0。"""
    out: List[int] = []
    run = 0
    for b in bits:
        out.append(b)
        run = run + 1 if b else 0
        if run == 5:
            out.append(0)
            run = 0
    return out


def unstuff_bits(bits: Sequence[int]) -> Optional[List[int]]:
    """去填充；发现非法（连 6 个 1）返回 None（帧级拒绝）。"""
    out: List[int] = []
    run = 0
    for b in bits:
        if run == 5:
            if b != 0:
                return None
            run = 0
            continue
        out.append(b)
        run = run + 1 if b else 0
    if run >= 5:
        return None
    return out


def _flag_bits() -> List[int]:
    return bytes_to_bits(bytes([HDLC_FLAG]))


def ais_frame_encode(payload_bits: Sequence[int]) -> List[int]:
    """payload → 完整线路位流：训练序列 + 标志 + stuffed(payload+FCS) + 标志。

    FCS-16 附加口径：16 位校验值按「低字节先行、字节内 MSB-first」
    附在数据之后（HDLC 低字节先发的语义在本位序约定下的等价）。
    """
    fcs = fcs16(bits_to_bytes(payload_bits))
    fcs_bits = bytes_to_bits(bytes([fcs & 0xFF, (fcs >> 8) & 0xFF]))
    body = stuff_bits(list(payload_bits) + fcs_bits)
    preamble = [i & 1 for i in range(AIS_PREAMBLE_BITS)]  # 0101…
    return preamble + _flag_bits() + body + _flag_bits()


def ais_frames_decode(wire_bits: Sequence[int]) -> Tuple[List[List[int]], int]:
    """在线路位流中找标志、切帧、去填充、校验 FCS。

    返回 (有效 payload 位流列表, 被丢弃的坏帧数)。坏帧（非法填充/
    长度非字节对齐/FCS 不匹配且长度像真帧 ≥40 bit）一律丢弃并计数，
    绝不带病上交；帧间训练序列片段（<40 bit）不算帧也不计入丢弃数。
    """
    flag = _flag_bits()
    frames: List[List[int]] = []
    dropped = 0
    i = 0
    n = len(wire_bits)
    starts = []
    while i + 8 <= n:
        if list(wire_bits[i:i + 8]) == flag:
            starts.append(i)
            i += 8
        else:
            i += 1
    for a, b in zip(starts, starts[1:]):
        body = unstuff_bits(wire_bits[a + 8:b])
        # 短段（<40 bit，帧间训练序列残段）不是帧：跳过且不计丢弃数。
        if body is None or len(body) < 40 or len(body) % 8 != 0:
            if body is None:
                dropped += 1
            continue
        data, fcs_bits = body[:-16], body[-16:]
        fcs = _read_uint(fcs_bits, 8, 8) << 8 | _read_uint(fcs_bits, 0, 8)
        if fcs16(bits_to_bytes(data)) != fcs:
            dropped += 1
            continue
        frames.append(data)
    return frames, dropped


# ---------------------------------------------------------------------------
# NRZI 与 GMSK 基带（复数 IQ）
# ---------------------------------------------------------------------------
def nrzi_encode(bits: Sequence[int], initial: int = 0) -> List[int]:
    """NRZI：bit 0 = 电平翻转，bit 1 = 电平保持。返回电平（0/1）序列。"""
    level = initial
    out = []
    for b in bits:
        if b == 0:
            level ^= 1
        out.append(level)
    return out


def nrzi_decode(levels: Sequence[int], initial: int = 0) -> List[int]:
    """NRZI 解码：相邻电平相同 → bit 1，不同 → bit 0。"""
    prev = initial
    out = []
    for lv in levels:
        out.append(1 if lv == prev else 0)
        prev = lv
    return out


def _gaussian_pulse(sps: int, bt: float = AIS_GMSK_BT,
                    span_symbols: int = 2) -> np.ndarray:
    """GMSK 高斯脉冲（截断 ±span_symbols 个符号，面积归一）。"""
    half = span_symbols * sps
    t = np.arange(-half, half + 1) / sps           # 单位：符号周期
    alpha = math.sqrt(math.log(2.0) / 2.0) / bt    # √ln2/(√2·BT)
    h = np.exp(-2.0 * (math.pi * alpha * t) ** 2)
    return h / h.sum()


def ais_modulate_iq(wire_bits: Sequence[int], fs: float) -> np.ndarray:
    """线路位流 → GMSK 复基带 IQ。fs 须 ≥ 2×9600（保证每符号 ≥2 样本）。"""
    sps = int(round(fs / AIS_BIT_RATE))
    if sps < 2 or abs(sps * AIS_BIT_RATE - fs) > 1e-6:
        raise ValueError(f"采样率须为 9600 的整数倍且 ≥19200 Hz，收到 {fs}")
    levels = nrzi_encode(wire_bits)                # NRZI 电平
    nrz = np.array([1.0 if lv else -1.0 for lv in levels])
    upsampled = np.repeat(nrz, sps)
    h = _gaussian_pulse(sps)
    shaped = np.convolve(upsampled, h, mode="same")
    phase = 2.0 * math.pi * AIS_DEVIATION_HZ * np.cumsum(shaped) / fs
    return np.exp(1j * phase).astype(np.complex128)


def ais_demodulate_iq(iq: np.ndarray, fs: float) -> List[int]:
    """GMSK 复基带 → 线路位流（1-bit 差分 + 位定时扫描 + NRZI 解码）。

    采样率必须是 9600 的整数倍且 ≥19200：否则 ``round(fs/9600)`` 会把
    fs=20000 静默取整成 sps=2，解出的位流与真实符号边界错位。这里显式
    拒绝，并把实际取整结果写进错误信息（不假装解出来了）。
    """
    sps = int(round(fs / AIS_BIT_RATE))
    if sps < 2 or abs(sps * AIS_BIT_RATE - fs) > 1e-6:
        raise ValueError(
            f"AIS 要求采样率为 9600 的整数倍且 ≥19200 Hz，收到 fs={fs:g} Hz"
            f"（按当前采样率取整为 sps={sps}，拒绝静默取整）"
        )
    if len(iq) < 4 * sps:
        raise ValueError(f"IQ 过短：至少需要 4 个符号（{4 * sps} 样本），实际 {len(iq)}")
    x = np.asarray(iq, dtype=np.complex128)
    dphi = np.angle(x[sps:] * np.conj(x[:-sps]))   # 1 符号延迟差分
    # 位定时：扫描符号相位 offset，取判决置信度（|dphi| 均值）最大者
    best_off, best_score = 0, -1.0
    usable = len(dphi) - (len(dphi) % sps)
    for off in range(sps):
        seg = dphi[off:off + usable:sps]
        if len(seg) == 0:
            continue
        score = float(np.mean(np.abs(seg)))
        if score > best_score:
            best_score, best_off = score, off
    symbols = dphi[best_off:best_off + usable:sps]
    levels = (symbols > 0).astype(int).tolist()    # 频偏正 → 高电平
    return nrzi_decode(levels)


def ais_decode_iq(iq: np.ndarray, fs: float) -> Dict:
    """复基带 → 帧解析 → 报文字段。返回 messages/dropped_frames/计数。"""
    wire = ais_demodulate_iq(iq, fs)
    payloads, dropped = ais_frames_decode(wire)
    messages = [parse_payload(p) for p in payloads]
    return {
        "messages": messages,
        "num_messages": len(messages),
        "dropped_frames": dropped,
        "recovered_bits": len(wire),
    }


# ---------------------------------------------------------------------------
# NMEA 0183 封装（!AIVDM）
# ---------------------------------------------------------------------------
def nmea_checksum(sentence_body: str) -> int:
    """``!`` 与 ``*`` 之间全部字符 XOR。"""
    cs = 0
    for ch in sentence_body:
        cs ^= ord(ch)
    return cs


def nmea_encode(payload_bits: Sequence[int], channel: str = "A") -> str:
    """payload 位流 → 单行 !AIVDM 语句（单 fragment）。"""
    fill = (-len(payload_bits)) % 6
    bb = list(payload_bits) + [0] * fill
    payload = "".join(armor6(_read_uint(bb, i, 6)) for i in range(0, len(bb), 6))
    body = f"AIVDM,1,1,,{channel},{payload},{fill}"
    return f"!{body}*{nmea_checksum(body):02X}"


def nmea_decode(line: str) -> List[int]:
    """单行 !AIVDM 语句 → payload 位流。校验失败/格式错误抛 ValueError。"""
    line = line.strip()
    if not line.startswith("!") or "*" not in line:
        raise ValueError("非 NMEA 0183 语句（缺 ! 或 *）")
    body, cs_text = line[1:].rsplit("*", 1)
    if nmea_checksum(body) != int(cs_text, 16):
        raise ValueError("NMEA 校验和不匹配")
    fields = body.split(",")
    if len(fields) != 7 or fields[0] != "AIVDM":
        raise ValueError("非 AIVDM 语句或字段数错误")
    total, num = int(fields[1]), int(fields[2])
    if total != 1 or num != 1:
        raise ValueError("多 fragment AIVDM 未在本函数处理（诚实拒绝）")
    payload, fill = fields[5], int(fields[6])
    bits: List[int] = []
    for ch in payload:
        bits += _uint(dearmor6(ch), 6)
    if fill:
        if fill > 5:
            raise ValueError("fill bit 越界")
        bits = bits[:-fill]
    return bits
