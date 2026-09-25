"""
mbdsdr_ai/js8call_adapter.py
==============================
JS8Call (js8call/js8call, WSJT-X/FT8 衍生) 消息模式的 Python 学习移植。

关键常量标注「来源: repos/js8call/<file>:<line>」：
  - 采样率 12000 Hz            commons.h:15
  - 每帧 79 个符号             commons.h:29  (JS8_NUM_SYMBOLS)
  - 子模式符号采样             commons.h:36-49
      JS8E(Slow)=3840, JS8A(Normal)=1920, JS8B(Fast)=1200, JS8C(Turbo)=600
  - 音调间隔 = fs/symbolSamples JS8Submode.cpp:73
  - 8-FSK 音调（FT8 衍生）     任务约定 + FT8 物理层

任务硬约束：默认 15.625 baud、8 个音调、77-bit 有效载荷。
（注：JS8Call 官方 Normal=6.25baud；任务采用 FT8 衍生的 15.625baud 学习模型。）

本移植纯 numpy，实现子模式参数表、呼号/网格编码、77-bit 消息打包。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# =====================================================================
# JS8 子模式参数表
# =====================================================================
# commons.h:36-49 给出每个子模式的 symbolSamples；
#   toneSpacing = JS8_RX_SAMPLE_RATE / symbolSamples (JS8Submode.cpp:73)
#   symbol rate = toneSpacing (MFSK 符号率 = 音调间隔)
# 任务约定 baud 档位：15.625 / 31.25 / 62.5。
JS8_SAMPLE_RATE = 12000.0          # commons.h:15
JS8_NUM_SYMBOLS = 79               # commons.h:29
JS8_N_TONES = 8                    # 8-FSK (FT8 衍生, 任务约定)
JS8_PAYLOAD_BITS = 77              # 任务约定 77-bit 有效载荷
JS8_TONE_SPACING = 6.25            # Hz (FT8 衍生)


@dataclass
class JS8Submode:
    name: str
    baud: float          # 符号率
    symbol_samples: int  # 每符号采样数
    tx_seconds: int


# 子模式表（任务约定 baud；symbol_samples = fs/baud）
#   Slow/Normal/Fast/Turbo 命名对应 JS8Submode.cpp:121-124
JS8_SUBMODES: Dict[str, JS8Submode] = {
    "slow":   JS8Submode("SLOW",   15.625, int(JS8_SAMPLE_RATE / 15.625), 30),
    "normal": JS8Submode("NORMAL", 15.625, int(JS8_SAMPLE_RATE / 15.625), 15),
    "fast":   JS8Submode("FAST",   31.25,  int(JS8_SAMPLE_RATE / 31.25),  10),
    "turbo":  JS8Submode("TURBO",  62.5,   int(JS8_SAMPLE_RATE / 62.5),    6),
}


def list_submodes() -> List[Dict[str, Any]]:
    out = []
    for k, s in JS8_SUBMODES.items():
        out.append({"key": k, "name": s.name, "baud": s.baud,
                    "symbol_samples": s.symbol_samples,
                    "tx_seconds": s.tx_seconds,
                    "tone_spacing_hz": JS8_TONE_SPACING,
                    "n_tones": JS8_N_TONES})
    return out


# =====================================================================
# 呼号 / 网格编码
# =====================================================================
# 呼号 -> 28bit 整数（学习模型，对齐 wsjtx unpack28.f90 的标准呼号段）：
#   C1: 36 字符 (空格+0-9+A-Z)
#   C2: 36 字符 (0-9+A-Z)
#   C3: 10 字符 (0-9)
#   C4: 27 字符 (空格+A-Z) x3
# 来源: mbdsdr_ai/ft8_callsign.py:14-22 (wsjtx unpack28.f90)
_C1 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_C2 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_C3 = "0123456789"
_C4 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def pack_callsign(call: str) -> int:
    """呼号 -> 28bit 整数（长度补 6）。"""
    s = call.upper().strip().ljust(6)[:6]
    i1 = _C1.index(s[0])
    i2 = _C2.index(s[1])
    i3 = _C3.index(s[2]) if s[2] in _C3 else 0
    i4 = _C4.index(s[3]) if s[3] in _C4 else 0
    i5 = _C4.index(s[4]) if s[4] in _C4 else 0
    i6 = _C4.index(s[5]) if s[5] in _C4 else 0
    n = i1 * (36 * 10 * 27 ** 3) + i2 * (10 * 27 ** 3) + i3 * (27 ** 3) \
        + i4 * (27 ** 2) + i5 * 27 + i6
    return n & 0x0FFFFFFF  # 28 bit


def unpack_callsign(n: int) -> str:
    n &= 0x0FFFFFFF
    i1 = n // (36 * 10 * 27 ** 3); n %= (36 * 10 * 27 ** 3)
    i2 = n // (10 * 27 ** 3);      n %= (10 * 27 ** 3)
    i3 = n // (27 ** 3);           n %= (27 ** 3)
    i4 = n // (27 ** 2);           n %= (27 ** 2)
    i5 = n // 27;                  i6 = n % 27
    return (_C1[i1] + _C2[i2] + _C3[i3] + _C4[i4] + _C4[i5] + _C4[i6]).strip()


def pack_grid(grid: str) -> int:
    """4 字符 Maidenhead 网格 -> 15bit 整数。

    前两位 A-R (18)，后两位 0-9 (10)。来源: wsjtx decode_grid.f90。
    """
    g = grid.upper().strip()[:4]
    n = (ord(g[0]) - ord('A')) * 18 * 10 * 10 \
        + (ord(g[1]) - ord('A')) * 10 * 10 \
        + (ord(g[2]) - ord('0')) * 10 \
        + (ord(g[3]) - ord('0'))
    return n & 0x7FFF  # 15 bit


def unpack_grid(n: int) -> str:
    n &= 0x7FFF
    c3 = n % 10; n //= 10
    c2 = n % 10; n //= 10
    c1 = n % 18; n //= 18
    c0 = n % 18
    return chr(ord('A') + c0) + chr(ord('A') + c1) + str(c2) + str(c3)


# =====================================================================
# 77-bit 消息打包 / 解包
# =====================================================================
# 布局（学习模型，共 77 bit）：
#   type   3 bit   (0=CQ, 1=呼叫, 2=私聊, 3=QRZ)
#   de     28 bit 发件呼号
#   to     28 bit 收件呼号
#   grid   15 bit 发件网格
#   spare   3 bit
@dataclass
class JS8Message:
    type: int            # 0..3
    de: str
    to: str
    grid: str = "AA00"


def pack_message(msg: JS8Message) -> int:
    """JS8Message -> 77-bit 整数。"""
    v = (int(msg.type) & 0x7)
    v = (v << 28) | (pack_callsign(msg.de) & 0x0FFFFFFF)
    v = (v << 28) | (pack_callsign(msg.to) & 0x0FFFFFFF)
    v = (v << 15) | (pack_grid(msg.grid) & 0x7FFF)
    v = (v << 3)
    return v & ((1 << JS8_PAYLOAD_BITS) - 1)


def unpack_message(v: int) -> JS8Message:
    v &= (1 << JS8_PAYLOAD_BITS) - 1
    v >>= 3                                   # spare
    grid = v & 0x7FFF; v >>= 15
    to = unpack_callsign(v & 0x0FFFFFFF); v >>= 28
    de = unpack_callsign(v & 0x0FFFFFFF); v >>= 28
    mtype = v & 0x7
    return JS8Message(type=mtype, de=de, to=to, grid=unpack_grid(grid))


# =====================================================================
# 8-FSK 音调序列（学习模型）
# =====================================================================
def message_to_tones(payload77: int) -> List[int]:
    """77-bit 载荷 -> 8-FSK 符号序列（每符号 3 bit，8 个音调）。

    返回长度 ceil(77/3)=26 的符号序列（0..7），对应 itone[] 数组。
    来源: mainwindow.cpp:91 itone[JS8_NUM_SYMBOLS]。
    """
    bits = [(payload77 >> i) & 1 for i in range(JS8_PAYLOAD_BITS)]
    bits = bits[::-1]  # MSB first
    tones: List[int] = []
    for i in range(0, len(bits) - 2, 3):
        sym = (bits[i] << 2) | (bits[i + 1] << 1) | bits[i + 2]
        tones.append(int(sym))
    return tones


def tones_to_audio(tones: List[int], submode: str = "normal",
                   f0: float = 1500.0) -> np.ndarray:
    """把 8-FSK 符号序列合成音频（中心 f0，音调间隔 6.25Hz）。"""
    sm = JS8_SUBMODES[submode]
    sps = sm.symbol_samples
    fs = JS8_SAMPLE_RATE
    out = np.empty(len(tones) * sps, dtype=np.float64)
    for k, sym in enumerate(tones):
        f = f0 + (sym - (JS8_N_TONES - 1) / 2.0) * JS8_TONE_SPACING
        t = np.arange(sps) / fs
        out[k * sps:(k + 1) * sps] = np.cos(2 * math.pi * f * t)
    return out


# =====================================================================
# 工具注册
# =====================================================================
def register_js8call_tools(registry) -> None:
    """注册 JS8 协议工具。"""
    from mbdsdr_ai.tool_registry import ToolResult

    def _submodes(args: Dict[str, Any]) -> ToolResult:
        try:
            data = list_submodes()
            return ToolResult(
                success=True,
                content=f"JS8 子模式: {[d['name'] for d in data]} "
                        f"({JS8_NUM_SYMBOLS}符号/帧, {JS8_N_TONES}-FSK)",
                data={"submodes": data, "n_symbols": JS8_NUM_SYMBOLS,
                      "payload_bits": JS8_PAYLOAD_BITS},
            )
        except Exception as e:
            return ToolResult(False, f"submodes 失败: {e}")

    def _encode(args: Dict[str, Any]) -> ToolResult:
        try:
            msg = JS8Message(
                type=int(args.get("type", 1)),
                de=args.get("de", "BI4MIB"),
                to=args.get("to", "SP1ABC"),
                grid=args.get("grid", "OL95"),
            )
            v = pack_message(msg)
            tones = message_to_tones(v)
            return ToolResult(
                success=True,
                content=f"JS8 编码: {msg.de}->{msg.to} grid={msg.grid} "
                        f"payload=0x{v:019X} ({JS8_PAYLOAD_BITS}bit, {len(tones)}符号)",
                data={"payload": v, "payload_hex": f"0x{v:019X}",
                      "tones": tones, "n_tones": len(tones)},
            )
        except Exception as e:
            return ToolResult(False, f"JS8 编码失败: {e}")

    def _decode(args: Dict[str, Any]) -> ToolResult:
        try:
            v = int(args["payload"]) & ((1 << JS8_PAYLOAD_BITS) - 1)
            msg = unpack_message(v)
            return ToolResult(
                success=True,
                content=f"JS8 解码: type={msg.type} de={msg.de} to={msg.to} grid={msg.grid}",
                data={"type": msg.type, "de": msg.de, "to": msg.to, "grid": msg.grid},
            )
        except Exception as e:
            return ToolResult(False, f"JS8 解码失败: {e}")

    registry.register(
        name="js8_submodes",
        description="列出 JS8Call 子模式参数 (Slow/Normal/Fast/Turbo: 15.625/15.625/31.25/62.5 baud, "
                    "8-FSK, 77bit 载荷)。来源: commons.h:15-49, JS8Submode.cpp:121-124。",
        parameters={"type": "object", "properties": {}},
        handler=_submodes,
        category="ham_modes",
    )
    registry.register(
        name="js8_encode_message",
        description="把 (type, de呼号, to呼号, 网格) 编码为 77-bit JS8 载荷与 8-FSK 符号序列。",
        parameters={"type": "object", "properties": {
            "type": {"type": "integer", "description": "0=CQ,1=呼叫,2=私聊,3=QRZ"},
            "de": {"type": "string"}, "to": {"type": "string"},
            "grid": {"type": "string", "description": "4位 Maidenhead"},
        }, "required": ["de", "to"]},
        handler=_encode,
        category="ham_modes",
    )
    registry.register(
        name="js8_decode_message",
        description="把 77-bit JS8 载荷解码回 (type, de, to, grid)。",
        parameters={"type": "object", "properties": {
            "payload": {"type": "integer", "description": "77bit 载荷整数"},
        }, "required": ["payload"]},
        handler=_decode,
        category="ham_modes",
    )
