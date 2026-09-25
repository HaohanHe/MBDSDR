#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rtcm3_decoder.py — RTCM3 消息解析器（移植自 RTKLIB src/rtcm3.c / rtcm.c / rtkcmn.c）

设计目标：
  - 帧同步(0xD3)、长度(10-bit)、CRC24Q 校验（多项式 0x1864CFB）
  - 解析 1005（基站 ECEF 坐标）
  - 解析 MSM7 系列：1077(GPS) / 1087(GLONASS) / 1097(Galileo) / 1127(BeiDou)
  - 输出结构化 dict，供 rtklib_adapter.SPPLocator / rtk_solver 直接消费

所有移植处均标注「来源: RTKLIB src/<file>.c:<line>」。
仅实现解算所需最小子集；其余消息类型返回 {"type":..., "supported":False}。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# ============================================================
# 常量 —— 逐字抄自 RTKLIB
# ============================================================

# rtcm.c:62  #define RTCM3PREAMB 0xD3
RTCM3_0_PREAMBLE = 0xD3

# rtcm3.c:38-46  MSM 物理单位常数
# rtcm3.c:38  #define PRUNIT_GPS  299792.458
PRUNIT_GPS = 299792.458
# rtcm3.c:39  #define PRUNIT_GLO  599584.916
PRUNIT_GLO = 599584.916
# rtcm3.c:40  #define RANGE_MS    (CLIGHT*0.001)
CLIGHT = 299792458.0
RANGE_MS = CLIGHT * 0.001
# rtcm3.c:42  #define P2_10       0.0009765625
P2_10 = 0.0009765625
# rtcm3.c:43  #define P2_34       5.820766091346740E-11
P2_34 = 5.820766091346740e-11
# rtcm3.c:44  #define P2_46       1.421085471520200E-14
P2_46 = 1.421085471520200e-14
# rtcm3.c:45  #define P2_59       1.734723475976810E-18
P2_59 = 1.734723475976810e-18
# rtcm3.c:46  #define P2_66       1.355252715606880E-20
P2_66 = 1.355252715606880e-20

# rtcm3.c:43 P2_34 = 2^-34；MSM6/7 伪距修正量用 P2_29 = 2^-29
P2_29 = 2.0 ** -29
# MSM6/7 载波相位修正量用 P2_31 = 2^-31
P2_31 = 2.0 ** -31
# MSM4/5 伪距修正量用 P2_24 = 2^-24
P2_24 = 2.0 ** -24
# MSM4/5 载波相位修正量用 P2_29 = 2^-29
# CNR: MSM7 10-bit * 0.0625  (rtcm3.c:2057)
# 半周歧义/lock 字段直接给位长

# ============================================================
# 信号 ID 表 —— rtcm3.c:64-99
# ============================================================
# rtcm3.c:64-69  msm_sig_gps[32]
MSM_SIG_GPS = [
    "", "1C", "1P", "1W", "1Y", "1M", "", "2C", "2P", "2W", "2Y", "2M",
    "", "", "2S", "2L", "2X", "", "", "", "", "5I", "5Q", "5X",
    "", "", "", "", "", "1S", "1L", "1X",
]
# rtcm3.c:70-75  msm_sig_glo[32]
MSM_SIG_GLO = [
    "", "1C", "1P", "", "", "", "", "2C", "2P", "", "3I", "3Q",
    "3X", "", "", "", "", "", "", "", "", "", "", "",
    "", "", "", "", "", "", "", "",
]
# rtcm3.c:76-81  msm_sig_gal[32]
MSM_SIG_GAL = [
    "", "1C", "1A", "1B", "1X", "1Z", "", "6C", "6A", "6B", "6X", "6Z",
    "", "7I", "7Q", "7X", "", "8I", "8Q", "8X", "", "5I", "5Q", "5X",
    "", "", "", "", "", "", "", "",
]
# rtcm3.c:94-99  msm_sig_cmp[32]
MSM_SIG_CMP = [
    "", "1I", "1Q", "1X", "", "", "", "6I", "6Q", "6X", "", "",
    "", "7I", "7Q", "7X", "", "", "", "", "", "", "", "",
    "", "", "", "", "", "", "", "",
]

# 星座字符串映射（rtklib.h: SYS_GPS=0, SYS_GLO=1, SYS_GAL=2, SYS_CMP=4）
SYS_TO_NAME = {0: "GPS", 1: "GLONASS", 2: "Galileo", 4: "BeiDou"}
NAME_TO_SIG_TABLE = {
    "GPS": MSM_SIG_GPS,
    "GLONASS": MSM_SIG_GLO,
    "Galileo": MSM_SIG_GAL,
    "BeiDou": MSM_SIG_CMP,
}


# ============================================================
# 位读取器 —— 移植 rtkcmn.c:610 getbitu / 617 getbits
# ============================================================
class BitReader:
    """MSB-first 位流读取器。

    对应 rtkcmn.c:610 `getbitu(buff,pos,len)`：从 buff[pos] 起取 len 位无符号。
    对应 rtkcmn.c:617 `getbits(buff,pos,len)`：同前但按 2 的补码返回有符号。
    """

    def __init__(self, buff: bytes):
        self.buff = bytes(buff)
        self.pos = 0

    def getu(self, length: int) -> int:
        """无符号读取 length 位。来源: RTKLIB src/rtkcmn.c:610 getbitu()。"""
        val = 0
        for _ in range(length):
            byte_idx = self.pos >> 3
            bit_idx = 7 - (self.pos & 7)
            val = (val << 1) | ((self.buff[byte_idx] >> bit_idx) & 1)
            self.pos += 1
        return val

    def gets(self, length: int) -> int:
        """有符号（2's complement）读取 length 位。

        来源: RTKLIB src/rtkcmn.c:617 getbits()：
            value = getbitu(buff,pos,len);
            if (value>>(len-1)) value-=1<<len;
        """
        v = self.getu(length)
        if length > 0 and (v >> (length - 1)) & 1:
            v -= 1 << length
        return v

    def skip(self, length: int) -> None:
        self.pos += length


# ============================================================
# CRC24Q —— 移植 rtkcmn.c:674 rtk_crc24q
# ============================================================
# rtkcmn.c:130  #define POLYCRC24Q 0x1864CFBu
_POLY_CRC24Q = 0x1864CFB
_CRC24_MASK = 0xFFFFFF


def rtcm3_crc24(data: bytes) -> int:
    """RTCM3 CRC24Q。来源: RTKLIB src/rtkcmn.c:674 rtk_crc24q()。

    原实现用 256 项查表（rtkcmn.c:283 tbl_CRC24Q），等价于逐位计算：
        crc = 0
        for byte in data:
            crc = ((crc << 8) ^ table[((crc >> 16) ^ byte) & 0xFF]) & 0xFFFFFF
    这里直接用位驱动的等价写法（多项式 0x1864CFB，初值 0，无反射，无最终异或），
    与表驱动逐位一致。
    """
    crc = 0
    for b in data:
        crc ^= b << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= _POLY_CRC24Q
            crc &= _CRC24_MASK
    return crc


# ============================================================
# RTCM3Frame —— 一个已校验的物理帧
# ============================================================
@dataclass
class RTCM3Frame:
    """一个完整的 RTCM3 物理帧。

    布局（rtcm.c:255-258）：
        | preamble(8) | reserved(6) | length(10) | payload(length*8) | CRC24(24) |
    """
    raw: bytes                 # 完整字节（preamble..crc）
    payload: bytes             # 不含 preamble/length 头与 CRC 的消息体
    msg_type: int              # 消息号（payload 前 12 bit）
    crc_ok: bool

    @property
    def length(self) -> int:
        """payload 字节数（rtcm.c:274: len=getbitu(buff,14,10)+3 再减 3）。"""
        return len(self.payload)


# ============================================================
# RTCM3Decoder —— 字节流 -> 解析后消息 dict
# ============================================================
class RTCM3Decoder:
    """增量解析 RTCM3 字节流。

    使用方式：
        dec = RTCM3Decoder()
        for msg in dec.feed(bytes_chunk):
            ...  # msg 是 dict
        # 或一次性：
        msgs = dec.feed_all(bytes_blob)

    帧同步逻辑移植 rtcm.c:261 input_rtcm3()：
      - nbyte==0 时找 0xD3；
      - 收齐 3 字节后从 bit 14 读 10-bit 长度；
      - 收齐 payload+3 字节 CRC 后校验并解码。
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    # ----------------------------------------------------------
    # 字节流喂入
    # ----------------------------------------------------------
    def feed(self, data: bytes) -> List[Dict[str, Any]]:
        """喂入一段字节，返回所有已完整解码出的消息 dict。"""
        self._buf.extend(data)
        out: List[Dict[str, Any]] = []
        while True:
            frame = self._try_pop_frame()
            if frame is None:
                break
            try:
                msg = self._decode_frame(frame)
            except Exception as exc:  # pragma: no cover - 容错
                msg = {"type": frame.msg_type, "supported": False,
                       "error": f"{type(exc).__name__}: {exc}"}
            out.append(msg)
        return out

    def feed_all(self, data: bytes) -> List[Dict[str, Any]]:
        return self.feed(data)

    # ----------------------------------------------------------
    # 帧拼装（rtcm.c:261 input_rtcm3）
    # ----------------------------------------------------------
    def _try_pop_frame(self) -> Optional[RTCM3Frame]:
        """从缓冲区解析一个完整帧；不足则返回 None。

        来源: RTKLIB src/rtcm.c:261 input_rtcm3()。
        """
        # 找 preamble 0xD3
        # rtcm.c:267: if (data!=RTCM3PREAMB) return 0;
        while self._buf and self._buf[0] != RTCM3_0_PREAMBLE:
            del self._buf[0]
        if len(self._buf) < 3:
            return None
        # rtcm.c:274: rtcm->len = getbitu(buff,14,10)+3;
        # bit 14 = byte 1 bit 6；10-bit length
        length_field = ((self._buf[1] & 0x03) << 8) | self._buf[2]
        payload_len = length_field  # rtcm.c:274 中 +3 是含头总长；这里只要 payload
        total = 3 + payload_len + 3  # header(3) + payload + crc(3)
        if len(self._buf) < total:
            return None
        raw = bytes(self._buf[:total])
        del self._buf[:total]
        # rtcm.c:280: check parity over buff[0..len) (len = 3+payload)
        body_for_crc = raw[: 3 + payload_len]
        crc_expected = (raw[3 + payload_len] << 16) | \
                       (raw[4 + payload_len] << 8) | raw[5 + payload_len]
        crc_got = rtcm3_crc24(body_for_crc)
        # 消息号：payload 前 12 bit（rtcm3.c:2079: getbitu(buff,24,12)）
        msg_type = ((raw[3] << 4) | (raw[4] >> 4)) & 0xFFF
        return RTCM3Frame(
            raw=raw,
            payload=raw[3: 3 + payload_len],
            msg_type=msg_type,
            crc_ok=(crc_got == crc_expected),
        )

    # ----------------------------------------------------------
    # 消息分发（rtcm3.c:2076 decode_rtcm3 switch）
    # ----------------------------------------------------------
    def _decode_frame(self, frame: RTCM3Frame) -> Dict[str, Any]:
        if not frame.crc_ok:
            return {"type": frame.msg_type, "crc_ok": False, "supported": False}
        t = frame.msg_type
        if t == 1005:
            return self._decode_1005(frame.payload)
        if t == 1006:
            return self._decode_1006(frame.payload)
        if t in (1077, 1087, 1097, 1127):
            sys_map = {1077: 0, 1087: 1, 1097: 2, 1127: 4}
            return self._decode_msm7(frame.payload, t, sys_map[t])
        return {"type": t, "crc_ok": True, "supported": False}

    # ----------------------------------------------------------
    # 1005: 静止参考站 ARP ECEF 坐标
    # 来源: RTKLIB src/rtcm3.c:374 decode_type1005()
    # ----------------------------------------------------------
    @staticmethod
    def _decode_1005(payload: bytes) -> Dict[str, Any]:
        """decode_type1005 (rtcm3.c:374-406)。

        位布局（payload 内）：
          i=24 起（即 preamble+len 头之后）：
            staid    12 bit
            reserved 6 bit + 4 bit
            rr[0]    38 bit signed (0.1 mm)
            rr[1]    38 bit signed (0.1 mm)
            rr[2]    38 bit signed (0.1 mm)
        缩放：rtcm3.c:400 rtcm->sta.pos[j] = rr[j]*0.0001;
        """
        br = BitReader(payload)
        br.skip(12)                       # 前 12 bit 消息号（payload 起）
        station_id = br.getu(12)          # rtcm3.c:381
        br.skip(6 + 4)                    # rtcm3.c:382: itrf(6) + reserved(4)
        x = br.gets(38) * 0.0001          # rtcm3.c:383,400
        y = br.gets(38) * 0.0001          # rtcm3.c:384,400
        z = br.gets(38) * 0.0001          # rtcm3.c:385,400
        return {
            "type": 1005,
            "crc_ok": True,
            "supported": True,
            "station_id": station_id,
            "antenna_ecef": [x, y, z],
            "antenna_height": 0.0,        # 1005 无天线高；1006 才有
        }

    # ----------------------------------------------------------
    # 1006: 1005 + 天线高
    # 来源: RTKLIB src/rtcm3.c:408 decode_type1006()
    # ----------------------------------------------------------
    @staticmethod
    def _decode_1006(payload: bytes) -> Dict[str, Any]:
        br = BitReader(payload)
        br.skip(12)
        station_id = br.getu(12)          # rtcm3.c:415
        br.skip(6 + 4)                    # rtcm3.c:416
        x = br.gets(38) * 0.0001          # rtcm3.c:417,435
        y = br.gets(38) * 0.0001          # rtcm3.c:418,435
        z = br.gets(38) * 0.0001          # rtcm3.c:419,435
        anth = br.getu(16) * 0.0001       # rtcm3.c:420,438
        return {
            "type": 1006,
            "crc_ok": True,
            "supported": True,
            "station_id": station_id,
            "antenna_ecef": [x, y, z],
            "antenna_height": anth,
        }

    # ----------------------------------------------------------
    # MSM7 头部 + 卫星/信号/单元格掩码
    # 来源: RTKLIB src/rtcm3.c:1743 decode_msm_head()
    # ----------------------------------------------------------
    @staticmethod
    def _decode_msm_head(payload: bytes, sys_code: int
                         ) -> Tuple[BitReader, int, int, int, List[int], List[int], List[int]]:
        """返回 (br, station_id, gps_tow_ms, nsat, sats, sigs, cellmask)。

        tow 单位毫秒（rtcm3.c:1759/1763/1768: *0.001）。
        BeiDou 时间需 +14s 转到 GPST（rtcm3.c:1764）。
        GLONASS 用 day-of-week + tod，这里只提取 tod 毫秒（与 tow 同尺度）。
        """
        br = BitReader(payload)
        msg_type = br.getu(12)            # rtcm3.c:1751
        station_id = br.getu(12)          # rtcm3.c:1755

        gps_tow_ms = 0
        if sys_code == 1:  # SYS_GLO: rtcm3.c:1757-1760
            _dow = br.getu(3)
            gps_tow_ms = br.getu(27)
        elif sys_code == 4:  # SYS_CMP: rtcm3.c:1762-1766
            gps_tow_ms = br.getu(30)
            # BDT -> GPST +14 s（rtcm3.c:1764），转毫秒
            gps_tow_ms += 14000
        else:  # GPS / Galileo: rtcm3.c:1767-1770
            gps_tow_ms = br.getu(30)

        br.skip(1)   # rtcm3.c:1771  sync
        br.skip(3)   # rtcm3.c:1772  iod
        br.skip(7)   # rtcm3.c:1773  time_s
        br.skip(2)   # rtcm3.c:1774  clk_str
        br.skip(2)   # rtcm3.c:1775  clk_ext
        br.skip(1)   # rtcm3.c:1776  smooth
        br.skip(3)   # rtcm3.c:1777  tint_s

        sats: List[int] = []              # rtcm3.c:1778-1781
        for j in range(1, 65):
            if br.getu(1):
                sats.append(j)
        sigs: List[int] = []              # rtcm3.c:1782-1785
        for j in range(1, 33):
            if br.getu(1):
                sigs.append(j)

        cellmask: List[int] = []          # rtcm3.c:1804-1807
        for _ in range(len(sats) * len(sigs)):
            cellmask.append(br.getu(1))
        return br, station_id, gps_tow_ms, msg_type, sats, sigs, cellmask

    # ----------------------------------------------------------
    # MSM7 主体 —— 来源: RTKLIB src/rtcm3.c:2003 decode_msm7()
    # ----------------------------------------------------------
    def _decode_msm7(self, payload: bytes, msg_type: int, sys_code: int
                     ) -> Dict[str, Any]:
        sys_name = SYS_TO_NAME[sys_code]
        sig_table = NAME_TO_SIG_TABLE[sys_name]

        br, station_id, gps_tow_ms, _, sats, sigs, cellmask = \
            self._decode_msm_head(payload, sys_code)

        nsat = len(sats)
        nsig = len(sigs)

        # ---- 卫星级数据（rtcm3.c:2026-2040）----
        rng: List[float] = [0.0] * nsat
        ext: List[int] = [15] * nsat
        phaserate_sat: List[float] = [0.0] * nsat

        for j in range(nsat):            # rtcm3.c:2026-2029 range 8-bit ms
            v = br.getu(8)
            if v != 255:
                rng[j] = v * RANGE_MS
        for j in range(nsat):            # rtcm3.c:2030-2032 extended info 4-bit
            ext[j] = br.getu(4)
        for j in range(nsat):            # rtcm3.c:2033-2036 range modulo 10-bit
            v = br.getu(10)
            if rng[j] != 0.0:
                rng[j] += v * P2_10 * RANGE_MS
        for j in range(nsat):            # rtcm3.c:2037-2040 phaserate (sat-level) 14-bit
            v = br.gets(14)
            if v != -8192:
                phaserate_sat[j] = float(v)

        # ---- 单元格级数据（rtcm3.c:2042-2062）----
        # 单元格按 row-major 枚举：cellmask[k + i*nsig]，非零单元格 j 顺序消费
        ncell = sum(cellmask)
        pr_corr: List[float] = []
        cp_corr: List[float] = []
        lock: List[int] = []
        half: List[int] = []
        cnr: List[float] = []
        doppler_cell: List[float] = []

        for _ in range(ncell):           # rtcm3.c:2042-2045 pseudorange corr 20-bit
            v = br.gets(20)
            pr_corr.append(0.0 if v == -524288 else v * P2_29 * RANGE_MS)
        for _ in range(ncell):           # rtcm3.c:2046-2049 phaserange corr 24-bit
            v = br.gets(24)
            cp_corr.append(0.0 if v == -8388608 else v * P2_31 * RANGE_MS)
        for _ in range(ncell):           # rtcm3.c:2050-2052 lock time 10-bit
            lock.append(br.getu(10))
        for _ in range(ncell):           # rtcm3.c:2053-2055 half-cycle 1-bit
            half.append(br.getu(1))
        for _ in range(ncell):           # rtcm3.c:2056-2058 cnr 10-bit * 0.0625
            cnr.append(br.getu(10) * 0.0625)
        for _ in range(ncell):           # rtcm3.c:2059-2062 phaserate cell 15-bit
            v = br.gets(15)
            doppler_cell.append(0.0 if v == -16384 else v * 0.0001)

        # ---- 组装观测列表（save_msm_obs rtcm3.c:1691-1739）----
        observations: List[Dict[str, Any]] = []
        j = 0
        for i in range(nsat):
            prn = sats[i]
            for k in range(nsig):
                if not cellmask[k + i * nsig]:
                    continue
                sig_label = sig_table[sigs[k] - 1] if sigs[k] - 1 < len(sig_table) else ""
                # rtcm3.c:1722-1723  pseudorange = r[i] + pr[j]
                pseudorange = rng[i] + pr_corr[j] if rng[i] != 0.0 else None
                # rtcm3.c:1726-1727  carrier phase (cycles) = (r[i]+cp[j])/wl
                # 这里直接输出「相位距离(m)」= r[i]+cp[j]，波长由解算端按信号选择
                carrier_range_m = rng[i] + cp_corr[j] if rng[i] != 0.0 else None
                # rtcm3.c:1731  doppler = -(rr[i]+rrf[j])/wl
                doppler_hz = -(phaserate_sat[i] + doppler_cell[j])
                observations.append({
                    "prn": prn,
                    "constellation": sys_name,
                    "signal": sig_label,
                    "pseudorange": pseudorange,           # m
                    "carrier_range_m": carrier_range_m,    # m (phase range, 未除波长)
                    "carrier_phase": carrier_range_m,      # 兼容字段：相位距离(m)
                    "doppler": doppler_hz,                  # Hz (近似，未除波长)
                    "cnr": cnr[j],                         # dB-Hz
                    "half_cycle_ambiguity": half[j],
                    "lock_time": lock[j],
                })
                j += 1

        return {
            "type": msg_type,
            "crc_ok": True,
            "supported": True,
            "station_id": station_id,
            "constellation": sys_name,
            "gps_tow": gps_tow_ms * 0.001,           # s
            "gps_tow_ms": gps_tow_ms,
            "nsat": nsat,
            "nsig": nsig,
            "ncell": ncell,
            "observations": observations,
        }


# ============================================================
# 便捷：构造一个合法 RTCM3 帧（测试用）
# ============================================================
def build_rtcm3_frame(payload: bytes) -> bytes:
    """把消息体打包成完整 RTCM3 帧（preamble+len+payload+crc24）。

    对应 rtcm.c:349 gen_rtcm3() 的打包逻辑。
    """
    hdr = bytes([RTCM3_0_PREAMBLE,
                 0x00 | ((len(payload) >> 8) & 0x03),
                 len(payload) & 0xFF])
    body = hdr + payload
    crc = rtcm3_crc24(body)
    return body + bytes([(crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF])
