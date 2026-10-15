# SPDX-License-Identifier: MIT
"""NanoVNA V2 二进制线协议编解码（公开协议事实的独立实现）。

协议事实来源：NanoVNA V2 固件公开文档与上游命令字/寄存器定义（协议
结构属事实，非受保护表达；实现为本仓独立编写）：

- 命令帧：[CMD, REG, payload(LE)]，读命令无 payload，写命令带 1/2/4/8
  字节小端值。
- FIFO 数据帧：32 字节 = 6×int32(LE)（ch0_fwd、ch0_rev、ch1_rev 的实/虚）
  + uint16 freq_index + 5 保留 + 1 校验和。校验和算法：seed 0x46，
  逐字节 checksum = (cs ^ ((cs<<1)|1)) ^ byte（uint8 截断）。
- S 参数为无量纲比值：S11 = ch0_rev/ch0_fwd，S21 = ch1_rev/ch0_fwd
  （无需 ADC 标定系数——比值自相除，上游同口径）。
- V2_USB_POINTS_MAX=1024（V2Plus4 65536），请求超上限显式拒绝。

诚实边界：本模块只做编解码，不做串口 I/O（串口链路在设备接入时另接）；
原始 int32 未经归一化，调用方如需电压量级须自行标定（docstring 登记）。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple

__all__ = [
    "CMD_V2_READ", "CMD_V2_WRITE", "REG_V2", "V2_USB_POINTS_MAX",
    "FifoFrame", "build_read", "build_write", "parse_fifo_frame",
    "sparams_from_frames",
]

#: 读/写命令字（按访问宽度索引：0->1字节, 1->2字节, 2->4字节, 3->8字节）
CMD_V2_READ = (0x10, 0x11, 0x12, 0x13)
CMD_V2_WRITE = (0x20, 0x21, 0x22, 0x23)
CMD_V2_INDICATE = 0x0D
CMD_V2_INDICATE_ACK = 0x32

#: 常用寄存器
REG_V2 = {
    "sweep_start_hz": 0x00,      # uint64
    "sweep_step_hz": 0x10,       # uint64
    "sweep_points": 0x20,        # uint16
    "values_per_freq": 0x22,     # uint16
    "values_fifo": 0x30,         # FIFO（写任意值清 FIFO）
    "average_setting": 0x40,     # uint8
    "unix_time": 0x58,           # uint32
    "device_variant": 0xF0,      # uint8，V2 恒 0x02
    "protocol_version": 0xF1,    # uint8，恒 0x01
    "hardware_revision": 0xF2,
    "firmware_major": 0xF3,
    "firmware_minor": 0xF4,
}

V2_USB_POINTS_MAX = 1024
V2PLUS4_USB_POINTS_MAX = 65536

_WIDTH_BYTES = (1, 2, 4, 8)
_FIFO_SIZE = 32
_CHECKSUM_SEED = 0x46


def build_read(reg: int, width: int = 1) -> bytes:
    """构造读寄存器命令帧。"""
    if width not in _WIDTH_BYTES:
        raise ValueError(f"width 须为 1/2/4/8，收到 {width}")
    return bytes((CMD_V2_READ[_WIDTH_BYTES.index(width)], reg))


def build_write(reg: int, value: int, width: int = 4) -> bytes:
    """构造写寄存器命令帧（值小端）。"""
    if width not in _WIDTH_BYTES:
        raise ValueError(f"width 须为 1/2/4/8，收到 {width}")
    if value < 0 or value >= (1 << (8 * width)):
        raise ValueError(f"值 {value} 超出 {width} 字节无符号范围")
    return bytes((CMD_V2_WRITE[_WIDTH_BYTES.index(width)], reg)) + \
        value.to_bytes(width, "little")


def _checksum(data: bytes) -> int:
    cs = _CHECKSUM_SEED
    for b in data:
        cs = (cs ^ ((cs << 1) | 1)) & 0xFF
        cs = (cs ^ b) & 0xFF
    return cs


@dataclass(frozen=True)
class FifoFrame:
    """一帧 32 字节 FIFO 数据（freq_index 用于重组频率网格）。"""
    ch0_fwd: complex          # 参考通道（前向）
    ch0_rev: complex          # 反射通道
    ch1_rev: complex          # 传输通道
    freq_index: int
    checksum_ok: bool


def parse_fifo_frame(buf: bytes) -> FifoFrame:
    """解析 32 字节 FIFO 帧；校验和错显式 ValueError（不静默丢帧）。"""
    if len(buf) != _FIFO_SIZE:
        raise ValueError(f"FIFO 帧须 {_FIFO_SIZE} 字节，收到 {len(buf)}")
    if _checksum(buf[:_FIFO_SIZE - 1]) != buf[_FIFO_SIZE - 1]:
        raise ValueError("FIFO 帧校验和错（链路噪声/帧错位）")
    r0f, i0f, r0r, i0r, r1r, i1r, idx = struct.unpack_from("<6iH", buf, 0)
    return FifoFrame(ch0_fwd=complex(r0f, i0f), ch0_rev=complex(r0r, i0r),
                     ch1_rev=complex(r1r, i1r), freq_index=idx,
                     checksum_ok=True)


def encode_fifo_frame(ch0_fwd: complex, ch0_rev: complex, ch1_rev: complex,
                      freq_index: int) -> bytes:
    """编码一帧（测试/仿真用；与 parse 互逆）。"""
    if not (0 <= freq_index <= 0xFFFF):
        raise ValueError(f"freq_index 超 uint16：{freq_index}")
    body = struct.pack("<6iH", int(ch0_fwd.real), int(ch0_fwd.imag),
                       int(ch0_rev.real), int(ch0_rev.imag),
                       int(ch1_rev.real), int(ch1_rev.imag), freq_index)
    full31 = body + b"\x00" * 5                  # reserved
    return full31 + bytes((_checksum(full31),))


def sparams_from_frames(frames, start_hz: int,
                        step_hz: int, points: int) -> Dict[str, list]:
    """FIFO 帧 -> 频率网格 + S11/S21。

    frames 接受 FifoFrame 或原始 32 字节（自动解析；校验和错显式
    ValueError 不静默丢帧）。频率网格 = start_hz + step_hz × freq_index；
    帧集必须恰好覆盖 freq_index 0..points-1 各一次，缺帧/重帧显式
    ValueError（诚实，不静默补零）。零前向功率（ch0_fwd=0）显式拒绝——
    无参考信号时 S 参数无定义。
    """
    if points > V2PLUS4_USB_POINTS_MAX:
        raise ValueError(f"points {points} 超过硬件上限")
    parsed = [parse_fifo_frame(f) if isinstance(f, (bytes, bytearray))
              else f for f in frames]
    seen: Dict[int, FifoFrame] = {}
    for fr in parsed:
        if fr.freq_index in seen:
            raise ValueError(f"freq_index {fr.freq_index} 重复帧")
        seen[fr.freq_index] = fr
    if set(seen) != set(range(points)):
        missing = sorted(set(range(points)) - set(seen))[:5]
        raise ValueError(f"缺帧（如 freq_index {missing}），不静默补零")
    freqs, s11, s21 = [], [], []
    for k in range(points):
        fr = seen[k]
        if fr.ch0_fwd == 0:
            raise ValueError(f"freq_index {k} 前向功率为零，S 参数无定义")
        freqs.append(start_hz + step_hz * k)
        s11.append(fr.ch0_rev / fr.ch0_fwd)
        s21.append(fr.ch1_rev / fr.ch0_fwd)
    return {"freq_hz": freqs, "s11": s11, "s21": s21}
