"""
OP25 P25 Phase1/2 解码移植（纯 numpy，无 GNU Radio 运行时）
=================================================================
本模块把 boatbod/op25 (repos/op25) 里 P25 帧同步/NID/DUID/IMBE 的关键结构
翻译成 Python，所有常量都在注释里标注「来源: op25 源文件:行号」。

移植自：
  * P25 帧同步字 (48 bit)          lib/frame_sync_magics.h:39
  * P25 帧同步反转位掩码             lib/frame_sync_magics.h:40
  * P25 Phase2 帧同步字 (40 bit)    lib/frame_sync_magics.h:47
  * NID 解码 (NAC 12bit + DUID 4bit) lib/p25_framer.cc:67-135
  * BCH(64,16) 生成多项式           lib/bch.cc:22-26
  * DUID 含义                       lib/op25_msg_types.h:39-45
  * IMBE 语音帧结构 (9×144 bit)     lib/op25_imbe_frame.h:61,114-233
  * IMBE 参数提取 (u0..u7)          lib/op25_imbe_frame.h:299-343
  * Phase2 TDMA DUID 编码           apps/tdma/duid.py:24-58

P25 空中接口关键参数：
  * 符号率 9600 sps (C4FM / 4-FSK dibit)
  * 帧同步 48 bit (Phase1) / 40 bit (Phase2)
  * NID 64 bit: NAC(12) + DUID(4) + BCH parity(48)
  * Phase1 语音帧 216ms = 9 个 IMBE 帧 × 20ms
"""
from __future__ import annotations

import json
from typing import Dict, List, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
#  P25 全局常量
# ═══════════════════════════════════════════════════════════════════════

#: 符号率 sps。来源: op25 C4FM 标准 TIA-102-BAAC
P25_SYMBOL_RATE = 9600.0

#: Phase1 帧同步字 (48 bit)。来源: frame_sync_magics.h:39
P25_FRAME_SYNC = 0x5575F5FF77FF
#: Phase1 帧同步反转位掩码（用于微分/翻转检测）。来源: frame_sync_magics.h:40
P25_FRAME_SYNC_REV = P25_FRAME_SYNC ^ 0xAAAAAAAAAAAA
#: Phase1 同步掩码。来源: frame_sync_magics.h:41
P25_FRAME_SYNC_MASK = 0xFFFFFFFFFFFF

#: Phase2 帧同步字 (40 bit)。来源: frame_sync_magics.h:47
P25P2_FRAME_SYNC = 0x575D57F7FF
P25P2_FRAME_SYNC_REV = P25P2_FRAME_SYNC ^ 0xAAAAAAAAAA
P25P2_FRAME_SYNC_MASK = 0xFFFFFFFFFF

#: DUID 枚举。来源: op25_msg_types.h:39-45
P25_DUID_HDU = 0       # 首片数据单元
P25_DUID_TDU = 3       # 终止数据单元
P25_DUID_LDU1 = 5      # 逻辑链路单元 1（语音 + 慢信令）
P25_DUID_TSBK = 7      # 时隙信令块（控制信道）
P25_DUID_LDU2 = 10     # 逻辑链路单元 2（语音 + 慢信令）
P25_DUID_PDU = 12      # 数据包单元
P25_DUID_TDULC = 15    # 终止数据单元（链路控制）

P25_DUID_NAMES: Dict[int, str] = {
    0: "HDU", 3: "TDU", 5: "LDU1", 7: "TSBK",
    10: "LDU2", 12: "PDU", 15: "TDULC",
}

#: BCH(64,16) 生成多项式系数（48 个，x^47 .. x^0）。来源: bch.cc:22-26
_P25_BCH_G = (
    1, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 1, 0, 0,
    1, 0, 1, 1, 1, 0, 1, 1, 1, 1, 0, 1, 0, 0, 0, 0,
    1, 1, 0, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 1, 1,
)
#: 生成多项式 g(x) = x^48 + 上面 48 项。打包成 49-bit 整数。
_P25_G_POLY = (1 << 48)
for i, c in enumerate(_P25_BCH_G):
    if c:
        _P25_G_POLY |= 1 << (47 - i)


# ═══════════════════════════════════════════════════════════════════════
#  NID 编码 / 解码
# ═══════════════════════════════════════════════════════════════════════
def p25_encode_nid(nac: int, duid: int) -> int:
    """把 NAC(12) + DUID(4) 编码成 64-bit NID 字（含 48 bit BCH 校验）。

    布局（来源 p25_framer.cc:99-102）：
      acc[63:52] = NAC, acc[51:48] = DUID, acc[47:0] = BCH parity
    """
    info = ((nac & 0xFFF) << 4) | (duid & 0xF)
    # 系统 BCH 编码：reg = info << 48，然后模 g(x) 求余
    reg = info << 48
    for i in range(63, 47, -1):  # 16 个信息 bit，从 MSB 到 LSB
        if (reg >> i) & 1:
            reg ^= _P25_G_POLY << (i - 48)
    parity = reg & ((1 << 48) - 1)
    return (info << 48) | parity


def p25_decode_nid(nid_word: int) -> Tuple[int, int, bool]:
    """解码 64-bit NID 字。返回 (nac, duid, valid)。

    valid=True 表示 BCH 校验通过（余数为 0）。
    来源 p25_framer.cc:99-135。
    """
    nac = (nid_word >> 52) & 0xFFF
    duid = (nid_word >> 48) & 0xF
    # 重新计算校验：把 64-bit 字模 g(x)，余数应为 0
    reg = nid_word & ((1 << 64) - 1)
    for i in range(63, -1, -1):
        if (reg >> i) & 1:
            # 只有 i >= 48 时才需要 XOR g（低位不会触发）
            if i >= 48:
                reg ^= _P25_G_POLY << (i - 48)
            else:
                break
    valid = (reg & ((1 << 48) - 1)) == 0
    return nac, duid, valid


# ═══════════════════════════════════════════════════════════════════════
#  IMBE 语音帧结构
# ═══════════════════════════════════════════════════════════════════════
#: 每个 LDU 含 9 个 144-bit IMBE 码书帧。来源: op25_imbe_frame.h:61
P25_IMBE_FRAMES_PER_LDU = 9
#: 每个 IMBE 码书帧 bit 数。来源: op25_imbe_frame.h:61
P25_IMBE_FRAME_BITS = 144
#: Phase1 语音帧时长 (ms)。9 × 20ms = 180ms + 信令开销 ≈ 216ms。
P25_PHASE1_VOICE_FRAME_MS = 216

#: IMBE 参数位宽（来源 op25_imbe_frame.h:299-343）：
#:   u0:12 (Golay[23,12]), u1..u3:12 each (Golay + PN),
#:   u4..u6:11 each (Hamming[15,11] + PN), u7:7
P25_IMBE_PARAM_WIDTHS = (12, 12, 12, 12, 11, 11, 11, 7)


def p25_imbe_extract_params(cw144: List[int]) -> Dict[str, int]:
    """从 144-bit IMBE 码书帧提取 88 bit 原始参数 u0..u7（未纠错）。

    来源 op25_imbe_frame.h:299-343 imbe_header_decode：
      cw[0:23]   = u0 (Golay)
      cw[23:46]  = u1 (Golay ^ PN)
      cw[46:69]  = u2 (Golay ^ PN)
      cw[69:92]  = u3 (Golay ^ PN)
      cw[92:107] = u4 (Hamming[15,11] ^ PN)
      cw[107:122]= u5 (Hamming ^ PN)
      cw[122:137]= u6 (Hamming ^ PN)
      cw[137:144]= u7 (7 bit)
    """
    assert len(cw144) == P25_IMBE_FRAME_BITS
    fields = {}
    offsets = [0, 23, 46, 69, 92, 107, 122, 137]
    widths = P25_IMBE_PARAM_WIDTHS
    for i, (off, w) in enumerate(zip(offsets, widths)):
        v = 0
        for j in range(w):
            v = (v << 1) | cw144[off + j]
        fields[f"u{i}"] = v
    fields["total_bits"] = sum(widths)  # 88
    return fields


def p25_imbe_pack_params(u: List[int]) -> bytes:
    """把 88 bit IMBE 参数打包成 11 字节。来源 op25_imbe_frame.h:348-363。"""
    assert len(u) == 8
    cw = bytearray(11)
    cw[0] = (u[0] >> 4) & 0xFF
    cw[1] = ((u[0] & 0xF) << 4) | ((u[1] >> 8) & 0xF)
    cw[2] = u[1] & 0xFF
    cw[3] = (u[2] >> 4) & 0xFF
    cw[4] = ((u[2] & 0xF) << 4) | ((u[3] >> 8) & 0xF)
    cw[5] = u[3] & 0xFF
    cw[6] = (u[4] >> 3) & 0xFF
    cw[7] = ((u[4] & 0x7) << 5) | ((u[5] >> 6) & 0x1F)
    cw[8] = ((u[5] & 0x3F) << 2) | ((u[6] >> 9) & 0x3)
    cw[9] = (u[6] >> 1) & 0xFF
    cw[10] = ((u[6] & 0x1) << 7) | ((u[7] >> 1) & 0x7F)
    return bytes(cw)


# ═══════════════════════════════════════════════════════════════════════
#  Phase2 TDMA 时隙
# ═══════════════════════════════════════════════════════════════════════
def p25_phase2_slot_info() -> Dict[str, object]:
    """P25 Phase2 TDMA 时隙结构摘要。

    Phase2: 12.5 kHz 信道，2 个 6.25 kHz 时隙，每个时隙 30ms 发一个超帧。
    来源: op25 apps/tdma/duid.py + TIA-102-BCAH。
    """
    return {
        "slots_per_carrier": 2,
        "slot_duration_ms": 30,
        "symbol_rate_sps": P25_SYMBOL_RATE,
        "frame_sync_bits": 40,
        "duid_meaning": {
            0: "4v (4-ary voice)",
            3: "SACCH voice",
            6: "2v (2-slot voice)",
            9: "FACCH voice",
            12: "SACCH w/o",
            15: "FACCH w/o",
        },
        "source": "op25 apps/tdma/duid.py:41-58",
    }


# ═══════════════════════════════════════════════════════════════════════
#  ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_op25_tools(registry) -> None:
    """把 P25 NID 解码 / IMBE 帧解析 / Phase2 时隙 注册进 ToolRegistry。"""
    from mbdsdr_ai.tool_registry import ToolResult

    def _nid_decode(args):
        """解码一个 64-bit NID 字（输入 hex 或整数），返回 NAC/DUID/校验结果。"""
        try:
            raw = args.get("nid_word")
            if raw is None:
                # 默认构造一个已知 NID: NAC=0x3F5, DUID=LDU1(5)
                word = p25_encode_nid(0x3F5, P25_DUID_LDU1)
            elif isinstance(raw, str):
                word = int(raw, 16) if raw.startswith("0x") else int(raw, 16)
            else:
                word = int(raw)
            nac, duid, ok = p25_decode_nid(word)
            out = {
                "nid_hex": f"0x{word:016X}",
                "nac": nac,
                "nac_hex": f"0x{nac:03X}",
                "duid": duid,
                "duid_name": P25_DUID_NAMES.get(duid, "UNKNOWN"),
                "bch_valid": ok,
                "frame_sync_phase1": f"0x{P25_FRAME_SYNC:012X}",
                "source": "op25 p25_framer.cc:67-135, frame_sync_magics.h:39",
            }
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"NID 解码失败: {e}")

    def _nid_roundtrip(args):
        """构造一个 NID 并解码往返。"""
        try:
            nac = int(args.get("nac", 0x3F5))
            duid = int(args.get("duid", P25_DUID_LDU1))
            word = p25_encode_nid(nac, duid)
            nac2, duid2, ok = p25_decode_nid(word)
            out = {
                "nac_in": nac, "duid_in": duid,
                "nid_hex": f"0x{word:016X}",
                "nac_out": nac2, "duid_out": duid2,
                "roundtrip_ok": (nac2 == nac and duid2 == duid and ok),
                "source": "op25 p25_framer.cc:67-135, bch.cc:22-26",
            }
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"NID 往返失败: {e}")

    def _imbe_parse(args):
        """解析一个 144-bit IMBE 码书帧，提取 u0..u7 参数宽度。"""
        try:
            bits = args.get("bits")
            if bits is None:
                # 构造一个全 0 帧
                bits = [0] * P25_IMBE_FRAME_BITS
            fields = p25_imbe_extract_params(bits)
            out = {
                "frame_bits": P25_IMBE_FRAME_BITS,
                "params": fields,
                "frames_per_ldu": P25_IMBE_FRAMES_PER_LDU,
                "voice_frame_ms": P25_PHASE1_VOICE_FRAME_MS,
                "source": "op25 op25_imbe_frame.h:61,114-233,299-343",
            }
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"IMBE 解析失败: {e}")

    def _phase2_info(args):
        """返回 P25 Phase2 TDMA 时隙结构摘要。"""
        try:
            info = p25_phase2_slot_info()
            info["source"] = "op25 apps/tdma/duid.py, frame_sync_magics.h:47"
            return ToolResult(True, json.dumps(info, ensure_ascii=False), data=info)
        except Exception as e:
            return ToolResult(False, f"Phase2 信息失败: {e}")

    registry.register(
        name="p25_nid_decode",
        description=("P25 Phase1 NID 解码：输入 64-bit NID 字（hex），"
                     "BCH(64,16) 校验后提取 NAC(12bit) 与 DUID(4bit)。"
                     "不传 nid_word 时默认构造 NAC=0x3F5/DUID=LDU1(5) 的已知字。"),
        parameters={
            "type": "object",
            "properties": {
                "nid_word": {"type": "string", "description": "64-bit NID hex，如 0x03F5000..."},
            },
            "required": [],
        },
        handler=_nid_decode,
        category="digital_voice",
    )

    registry.register(
        name="p25_nid_roundtrip",
        description=("P25 NID 编码->解码往返：输入 NAC(0..0xFFF) 和 DUID(0..15)，"
                     "BCH 编码成 64-bit 字再解码，验证 NAC/DUID 还原且校验通过。"),
        parameters={
            "type": "object",
            "properties": {
                "nac": {"type": "integer", "default": 0x3F5},
                "duid": {"type": "integer", "default": 5},
            },
            "required": [],
        },
        handler=_nid_roundtrip,
        category="digital_voice",
    )

    registry.register(
        name="p25_imbe_parse",
        description=("P25 Phase1 IMBE 语音帧结构解析：把 144-bit 码书帧拆成 "
                     "u0..u7 八个参数（12/12/12/12/11/11/11/7 bit = 88 bit）。"
                     "9 个码书帧组成一个 LDU，216ms 语音。"),
        parameters={
            "type": "object",
            "properties": {
                "bits": {"type": "array", "items": {"type": "integer"}},
            },
            "required": [],
        },
        handler=_imbe_parse,
        category="digital_voice",
    )

    registry.register(
        name="p25_phase2_info",
        description=("P25 Phase2 TDMA 时隙结构：2 时隙/载波，30ms/时隙，"
                     "40-bit 帧同步，6 种 DUID（4v/2v/SACCH/FACCH w/wo）。"),
        parameters={"type": "object", "properties": {}},
        handler=_phase2_info,
        category="digital_voice",
    )


if __name__ == "__main__":
    # 自测：NID 往返
    word = p25_encode_nid(0x3F5, P25_DUID_LDU1)
    nac, duid, ok = p25_decode_nid(word)
    print(f"NID: 0x{word:016X}  NAC=0x{nac:03X} DUID={duid}({P25_DUID_NAMES.get(duid)}) valid={ok}")
    assert ok and nac == 0x3F5 and duid == P25_DUID_LDU1
    # IMBE 全 0 帧
    fields = p25_imbe_extract_params([0] * 144)
    print("IMBE 全0帧参数:", fields)
    assert fields["total_bits"] == 88
    print("op25_adapter 自测 OK")
