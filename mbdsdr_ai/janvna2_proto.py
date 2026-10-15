# SPDX-License-Identifier: MIT
"""JanVNA2（LibreVNA）二进制包协议编解码（公开协议事实的独立实现）。

协议事实来源：上游 JanVNA2_comms.h/.cpp 的包结构定义（协议结构属事实）：

- 帧格式：magic(0x5A, 1B) + total_length(2B LE，含头/载荷/CRC) +
  packet_type(1B) + payload + CRC32(4B LE，zlib IEEE 多项式 0xEDB88320，
  覆盖 [0, size−4))。
- 例外：Datapoint 包为吞吐跳过 CRC（载荷尾 4 字节写 0，解码端不校验）。
- 已支持载荷：Datapoint（4 复数 S 参数 float + u64 Hz + u16 点号）、
  SweepSettings（起/止/点数/IFBW/激励/端口激励位）。

诚实边界：ManualControl/DeviceInfo/SpectrumAnalyzer 等载荷本批未建模
（结构里有位域，需要时按同框架补）；流式分片器只按帧界切包，不做
USB 批量传输的时序假设。
"""
from __future__ import annotations

import enum
import struct
import zlib
from dataclasses import dataclass
from typing import List, Optional, Tuple

__all__ = [
    "PacketType",
    "JanPacket",
    "encode_packet",
    "decode_packet",
    "encode_datapoint",
    "decode_datapoint",
    "encode_sweep_settings",
    "decode_sweep_settings",
    "PacketStreamSplitter",
]

_MAGIC = 0x5A
_HEADER = struct.Struct("<BHB")          # magic, total_length, packet_type
_CRC_SKIP_TYPES = frozenset()


class PacketType(enum.IntEnum):
    NONE = 0
    DATAPOINT = 1
    SWEEP_SETTINGS = 2
    STATUS = 3
    MANUAL_CONTROL = 4
    DEVICE_INFO = 5
    FIRMWARE_PACKET = 6
    ACK = 7
    CLEAR_FLASH = 8
    PERFORM_FIRMWARE_UPDATE = 9
    NACK = 10
    REFERENCE = 11
    GENERATOR = 12
    SPECTRUM_ANALYZER_SETTINGS = 13
    SPECTRUM_ANALYZER_RESULT = 14
    REQUEST_DEVICE_INFO = 15


#: CRC 豁免包（上游吞吐优化口径）：载荷尾 4 字节置 0，解码不校验。
_CRC_SKIP_TYPES = frozenset({PacketType.DATAPOINT})


@dataclass(frozen=True)
class JanPacket:
    packet_type: PacketType
    payload: bytes
    crc_ok: bool               # 豁免类型恒 True（不校验，如实标注口径）


def encode_packet(packet_type: PacketType, payload: bytes = b"") -> bytes:
    """组帧；非豁免类型附 zlib.crc32（LE），豁免类型 CRC 字段写 0。"""
    total = _HEADER.size + len(payload) + 4
    if total > 0xFFFF:
        raise ValueError(f"包总长 {total} 超 uint16")
    head = _HEADER.pack(_MAGIC, total, int(packet_type))
    if packet_type in _CRC_SKIP_TYPES:
        crc = b"\x00" * 4
    else:
        crc = struct.pack("<I", zlib.crc32(head + payload))
    return head + payload + crc


def decode_packet(buf: bytes) -> JanPacket:
    """解析一帧完整包；magic/长度/CRC 不符显式 ValueError（不静默丢）。"""
    if len(buf) < _HEADER.size + 4:
        raise ValueError(f"包过短（{len(buf)} 字节）")
    magic, total, ptype = _HEADER.unpack_from(buf, 0)
    if magic != _MAGIC:
        raise ValueError(f"magic 错：{magic:#04x} != {_MAGIC:#04x}")
    if total != len(buf):
        raise ValueError(f"total_length {total} != 实际 {len(buf)}")
    try:
        packet_type = PacketType(ptype)
    except ValueError:
        raise ValueError(f"未知包类型 {ptype}") from None
    payload = buf[_HEADER.size:-4]
    if packet_type in _CRC_SKIP_TYPES:
        return JanPacket(packet_type, payload, crc_ok=True)
    (expected,) = struct.unpack_from("<I", buf, len(buf) - 4)
    actual = zlib.crc32(buf[:-4])
    if expected != actual:
        raise ValueError(
            f"CRC32 错：{expected:#010x} != {actual:#010x}（链路噪声）")
    return JanPacket(packet_type, payload, crc_ok=True)


# ---- Datapoint --------------------------------------------------------------

_DP = struct.Struct("<8fQB")            # 4 复数 + Hz + pointNum


@dataclass(frozen=True)
class Datapoint:
    s11: complex
    s21: complex
    s12: complex
    s22: complex
    freq_hz: int
    point_num: int


def encode_datapoint(dp: Datapoint) -> bytes:
    payload = _DP.pack(dp.s11.real, dp.s11.imag, dp.s21.real, dp.s21.imag,
                       dp.s12.real, dp.s12.imag, dp.s22.real, dp.s22.imag,
                       dp.freq_hz, dp.point_num)
    return encode_packet(PacketType.DATAPOINT, payload)


def decode_datapoint(packet: JanPacket) -> Datapoint:
    if packet.packet_type != PacketType.DATAPOINT:
        raise ValueError(f"包类型 {packet.packet_type} 非 Datapoint")
    if len(packet.payload) != _DP.size:
        raise ValueError(
            f"Datapoint 载荷 {len(packet.payload)} != {_DP.size}")
    v = _DP.unpack(packet.payload)
    return Datapoint(s11=complex(v[0], v[1]), s21=complex(v[2], v[3]),
                     s12=complex(v[4], v[5]), s22=complex(v[6], v[7]),
                     freq_hz=v[8], point_num=v[9])


# ---- SweepSettings ----------------------------------------------------------

_SS = struct.Struct("<QQHIhB")          # f_start f_stop points ifbw cdbm flags


@dataclass(frozen=True)
class SweepSettings:
    f_start_hz: int
    f_stop_hz: int
    points: int
    if_bandwidth_hz: int
    excitation_dbm_centi: int      # 1/100 dBm
    excite_port1: bool
    excite_port2: bool
    suppress_peaks: bool


def _int(v, name: str) -> int:
    """整数化校验：float 整数值接受（如 500e6），非整数显式拒绝。"""
    if isinstance(v, bool) or int(v) != v:
        raise ValueError(f"{name} 须为整数，收到 {v!r}")
    return int(v)


def encode_sweep_settings(ss: SweepSettings) -> bytes:
    flags = (int(ss.excite_port1)
             | (int(ss.excite_port2) << 1)
             | (int(ss.suppress_peaks) << 2))
    payload = _SS.pack(_int(ss.f_start_hz, "f_start_hz"),
                       _int(ss.f_stop_hz, "f_stop_hz"),
                       _int(ss.points, "points"),
                       _int(ss.if_bandwidth_hz, "if_bandwidth_hz"),
                       _int(ss.excitation_dbm_centi, "excitation_dbm_centi"),
                       flags)
    return encode_packet(PacketType.SWEEP_SETTINGS, payload)


def decode_sweep_settings(packet: JanPacket) -> SweepSettings:
    if packet.packet_type != PacketType.SWEEP_SETTINGS:
        raise ValueError(f"包类型 {packet.packet_type} 非 SweepSettings")
    if len(packet.payload) != _SS.size:
        raise ValueError(
            f"SweepSettings 载荷 {len(packet.payload)} != {_SS.size}")
    f0, f1, pts, ifbw, cdbm, flags = _SS.unpack(packet.payload)
    return SweepSettings(f0, f1, pts, ifbw, cdbm,
                         bool(flags & 1), bool(flags & 2), bool(flags & 4))


# ---- 流式分片 ---------------------------------------------------------------

class PacketStreamSplitter:
    """按 total_length 帧界从任意字节流切包；残片缓冲待下批数据。

    feed(data) 返回本次解析出的完整包字节串列表；incomplete() 返回
    缓冲的残片长度（调用方可判断链路停滞）。
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> List[bytes]:
        self._buf += data
        out: List[bytes] = []
        while True:
            if len(self._buf) < _HEADER.size:
                break
            _, total, _ = _HEADER.unpack_from(bytes(self._buf), 0)
            if total < _HEADER.size + 4:
                raise ValueError(f"帧声明长度 {total} 非法（< 头+CRC）")
            if len(self._buf) < total:
                break
            out.append(bytes(self._buf[:total]))
            del self._buf[:total]
        return out

    def incomplete(self) -> int:
        return len(self._buf)
