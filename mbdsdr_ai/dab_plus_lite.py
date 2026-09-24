#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dab_plus_lite.py — 轻量级 DAB/DAB+ 接收链解析层（移植自 dablin 真实源码）

本模块是对 Opendigitalradio/dablin (v1.16.1) C++ 源码的忠实 Python 移植，
覆盖 dablin 中位于"OFDM 信道解码之后、音频解码器之前"的解析层：

    ETI 帧同步与层解析  (src/eti_player.cpp, src/eti_source.h)
    FIC / FIB / FIG 解码 (src/fic_decoder.cpp, src/fic_decoder.h)
    CRC-16/CCITT       (src/tools.cpp:218, src/tools.h:91-110)
    DAB+ 超帧格式推断   (src/dabplus_decoder.cpp/.h)

dablin 本身并不做 OFDM 解调/Viterbi/解扰——它通过 dab2eti/eti-cmdline 等外部
工具拿到 ETI(NI) 字节流后，从 ETI 帧开始解析（见 src/dablin.cpp:243,263-276）。
因此本 "lite" 模块与 dablin 一样：输入是已经分帧的 ETI 字节（或 FIC 字节），
输出是 ensemble/服务列表/子信道配置/音频参数。

红线：所有常量与位域均标注 dablin 源文件:行号。未读源码的部分不臆造。

来源仓库: https://github.com/Opendigitalradio/dablin  (clone 于 repos/dablin)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


# =============================================================================
# CRC-16/CCITT — 来源: repos/dablin/src/tools.cpp:218, tools.h:91-110
#
#   CalcCRC_CRC16_CCITT(true, true, 0x1021)
#     gen_polynom   = 0x1021   (x^16 + x^12 + x^5 + 1)   [tools.cpp:218]
#     initial_invert=true  -> crc 初值 = 0xFFFF          [tools.h:91-93]
#     无反射 (MSB-first, 左移 LUT)                        [tools.h:95-98]
#     final_invert=true    -> 末尾按位取反 (xorout=0xFFFF) [tools.h:107-110]
#
# 注意：任务书里写的"多项式 0x108"与上游源码不符；上游真实多项式是 0x1021。
# 初值 0xFFFF 与任务书一致。此处以上游 .cpp/.h 为准（红线：真读源码不造假）。
# =============================================================================

_CRC16_POLY = 0x1021  # tools.cpp:218


def _build_ccitt_lut() -> List[int]:
    """按 tools.cpp:232-244 FillLUT() 逐位生成查找表。"""
    lut = []
    for value in range(256):
        crc = value << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ _CRC16_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        lut.append(crc)
    return lut


_CCITT_LUT = _build_ccitt_lut()


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT（dablin CalcCRC_CRC16_CCITT）。

    等价 C++: tools.cpp:247-256 Calc()
       crc = 0xFFFF                                  (Initialize, tools.h:91-93)
      for b in data: crc = (crc<<8) ^ lut[(crc>>8)^b]  (ProcessByte, tools.h:95-98)
      crc = ~crc & 0xFFFF                            (Finalize, tools.h:107-110)
    """
    crc = 0xFFFF  # tools.h:92
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CCITT_LUT[((crc >> 8) ^ b) & 0xFF]  # tools.h:97
    return (~crc) & 0xFFFF  # tools.h:109


# Fire-code（DAB+ 超帧同步）——来源: tools.cpp:220
#   CalcCRC_FIRE_CODE(false, false, 0x782F)，init=0x0000，不取反。
def crc_fire_code(data: bytes) -> int:
    """DAB+ 超帧 fire code，多项式 0x782F，初值 0，不取反 (tools.cpp:220)。"""
    crc = 0x0000
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x782F) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# =============================================================================
# DABParams — DAB 传输模式 I 物理层参数
#
# dablin 工作在 ETI 层，不再触及 OFDM；以下参数为 EN 300 401 规定的
# DAB 传输模式 I 常量。ETI 帧时长 = 24 ms 可由 dablin 源码证实：
#   ensemble_source.cpp:200  `ensemble_frames_count * 24` 作为毫秒时间戳
#   ensemble_source.cpp:236  MsToTimecode(ensemble_frames_count * 24)
# 一帧 ETI = 一个 CIF (Common Interleaved Frame) = 24 ms。
# =============================================================================
@dataclass(frozen=True)
class DABParams:
    """DAB 传输模式 I (Transmission Mode I) 物理层参数。"""

    fft_size: int = 2048            # 模式 I FFT/有效子载波总数 = 2048
    cp_us: int = 246                # 循环前缀 246 µs (CP=506 samples @2.048MHz)
    symbol_us: int = 1000           # 有用符号时长 1 ms
    null_us: int = 1246             # 零符号 (null symbol) 时长 ≈ 1.246 ms
    symbols_per_frame: int = 76      # 每传输帧 76 个 OFDM 符号(含零符号)
    eti_frame_ms: int = 24           # ETI/CIF 帧长 24 ms [ensemble_source.cpp:200]
    eti_frame_bytes: int = 6144      # ETI 帧固定 6144 字节 [eti_source.h:57]
    fic_long_words: int = 24         # mid!=3 时 FIC = 24 长字 [eti_player.cpp:57]

    # ETI 同步字（24 bit，位于字节偏移 1..3；偏移 0 为 ERR=0xFF）
    #   [eti_source.h:58-59]  AddSyncMagic(1, {0x07,0x3A,0xB6}, "FSYNC0")
    #   [eti_source.h:58-59]  AddSyncMagic(1, {0xF8,0xC5,0x49}, "FSYNC1")
    #   [eti_player.cpp:25-26] fsync = frame[1]<<16|frame[2]<<8|frame[3]
    fsync0: int = 0x073AB6
    fsync1: int = 0xF8C549          # fsync0 的按位取反(24bit)，奇偶帧交替
    err_ok: int = 0xFF              # [eti_player.cpp:33] ERR==0xFF 表示无传输错误

    sample_rate_hz: int = 2048000   # DAB 模式 I 采样率参考 2.048 MHz
    fic_rate_mbps: float = 2.048    # FIC 速率 2.048 Mbps 参考值


PARAMS = DABParams()


# =============================================================================
# FICDecoder — 来源: repos/dablin/src/fic_decoder.cpp / fic_decoder.h
# =============================================================================

# UEP 短表单 / 保护等级 / 比特率表 —— fic_decoder.cpp:822-839
_UEP_SIZES = [
    16, 21, 24, 29, 35, 24, 29, 35, 42, 52, 29, 35, 42, 52, 32, 42,
    48, 58, 70, 40, 52, 58, 70, 84, 48, 58, 70, 84, 104, 58, 70, 84,
    104, 64, 84, 96, 116, 140, 80, 104, 116, 140, 168, 96, 116, 140, 168, 208,
    116, 140, 168, 208, 232, 128, 168, 192, 232, 280, 160, 208, 280, 192, 280, 416,
]  # fic_decoder.cpp:822-827
_UEP_PLS = [
    5, 4, 3, 2, 1, 5, 4, 3, 2, 1, 5, 4, 3, 2, 5, 4,
    3, 2, 1, 5, 4, 3, 2, 1, 5, 4, 3, 2, 1, 5, 4, 3,
    2, 5, 4, 3, 2, 1, 5, 4, 3, 2, 1, 5, 4, 3, 2, 1,
    5, 4, 3, 2, 1, 5, 4, 3, 2, 1, 5, 4, 2, 5, 3, 1,
]  # fic_decoder.cpp:828-833
_UEP_BITRATES = [
    32, 32, 32, 32, 32, 48, 48, 48, 48, 48, 56, 56, 56, 56, 64, 64,
    64, 64, 64, 80, 80, 80, 80, 80, 96, 96, 96, 96, 96, 112, 112, 112,
    112, 128, 128, 128, 128, 128, 160, 160, 160, 160, 160, 192, 192, 192, 192, 192,
    224, 224, 224, 224, 224, 256, 256, 256, 256, 256, 320, 320, 320, 384, 384, 384,
]  # fic_decoder.cpp:834-839
# EEP 尺寸因子 —— fic_decoder.cpp:840-841
_EEP_A_FACTORS = [12, 8, 6, 4]   # fic_decoder.cpp:840
_EEP_B_FACTORS = [27, 21, 18, 15]  # fic_decoder.cpp:841


@dataclass
class FICSubchannel:
    """一个 MSC 子信道配置（FIG 0/1）。来源 fic_decoder.h:71-94。"""
    subchid: int = -1
    start_cu: int = 0        # 起始 CU 地址
    size_cu: int = 0         # 子信道长度(CU)
    protection: str = ""     # "UEP n" / "EEP n-A" / "EEP n-B"
    bitrate_kbps: int = -1


@dataclass
class FICService:
    """一个节目服务及其主音频组件。来源 fic_decoder.h:192-218。"""
    sid: int = -1
    subchid: int = -1        # 主组件子信道
    dab_plus: bool = False    # ascty==63 -> DAB+; ascty==0 -> DAB(MP2) [fic_decoder.cpp:233]
    label: str = ""


@dataclass
class FICEnsemble:
    """Ensemble 信息汇总。来源 fic_decoder.h:146-183。"""
    eid: int = -1            # FIG 0/0 [fic_decoder.cpp:136]
    alarm_flag: bool = False
    label: str = ""          # FIG 1/0 [fic_decoder.cpp:646-657]
    short_label: str = ""


class FICDecoder:
    """解析 FIC 字节流，提取 ensemble / 服务列表 / 子信道配置。

    用法:
        dec = FICDecoder()
        dec.process(fic_bytes)          # 长度应为 32 的倍数(4..N 个 FIB)
        print(dec.ensemble, dec.subchannels, dec.services)
    """

    FIB_LEN = 32  # fic_decoder.cpp:32,37 —— 每个 FIB 32 字节

    def __init__(self) -> None:
        self.ensemble = FICEnsemble()
        self.subchannels: Dict[int, FICSubchannel] = {}
        self.services: Dict[int, FICService] = {}
        self.discarded_fibs = 0

    # ---- 顶层入口: fic_decoder.cpp:30-39 Process() ----
    def process(self, data: bytes) -> None:
        # len % 32 != 0 则整体忽略 [fic_decoder.cpp:32-35]
        if len(data) % self.FIB_LEN:
            return
        for off in range(0, len(data), self.FIB_LEN):
            self._process_fib(data[off:off + self.FIB_LEN])

    # ---- 单个 FIB: fic_decoder.cpp:42-69 ProcessFIB() ----
    def _process_fib(self, fib: bytes) -> None:
        # CRC: 前 30 字节计算，CRC 大端存于 [30],[31] [fic_decoder.cpp:44-45]
        stored = (fib[30] << 8) | fib[31]
        if crc16_ccitt(fib[:30]) != stored:
            self.discarded_fibs += 1
            return

        # 遍历 FIG: type=字节>>5, len=字节&0x1F, 遇 0xFF 停止 [fic_decoder.cpp:52-55,67]
        offset = 0
        while offset < 30 and fib[offset] != 0xFF:
            ftype = fib[offset] >> 5
            flen = fib[offset] & 0x1F
            offset += 1
            payload = fib[offset:offset + flen]
            if ftype == 0:
                self._process_fig0(payload)
            elif ftype == 1:
                self._process_fig1(payload)
            offset += flen

    # ---- FIG 0 头: fic_decoder.h:34-41, fic_decoder.cpp:72-85 ----
    def _process_fig0(self, data: bytes) -> None:
        if not data:
            return
        cn = bool(data[0] & 0x80)   # fic_decoder.h:40
        oe = bool(data[0] & 0x40)
        pd = bool(data[0] & 0x20)
        ext = data[0] & 0x1F
        if cn or oe or pd:           # 忽略相邻/其他 ensemble/数据服务 [fic_decoder.cpp:84]
            return
        body = data[1:]
        if ext == 0:
            self._fig0_0(body)
        elif ext == 1:
            self._fig0_1(body)
        elif ext == 2:
            self._fig0_2(body)
        # 其余扩展(0/5 语言, 0/8, 0/9, 0/10 时间 ...)此处省略，保持 lite 聚焦

    # FIG 0/0 — Ensemble 信息: EId + 告警标志 [fic_decoder.cpp:128-147]
    def _fig0_0(self, data: bytes) -> None:
        if len(data) < 4:
            return
        self.ensemble.eid = (data[0] << 8) | data[1]       # fic_decoder.cpp:136
        self.ensemble.alarm_flag = bool(data[2] & 0x20)    # fic_decoder.cpp:137

    # FIG 0/1 — 基本子信道组织: 起始 CU / 长度 / 保护 / 比特率 [fic_decoder.cpp:149-206]
    def _fig0_1(self, data: bytes) -> None:
        off = 0
        while off + 2 <= len(data):
            subchid = data[off] >> 2                       # fic_decoder.cpp:154
            start = ((data[off] & 0x03) << 8) | data[off + 1]  # fic_decoder.cpp:155
            off += 2

            sc = FICSubchannel(subchid=subchid, start_cu=start)

            long_form = bool(data[off] & 0x80)             # fic_decoder.cpp:161
            if long_form:
                # 长格式 (EEP) [fic_decoder.cpp:162-180]
                option = (data[off] & 0x70) >> 4          # fic_decoder.cpp:164
                pl = (data[off] & 0x0C) >> 2              # fic_decoder.cpp:165
                size = ((data[off] & 0x03) << 8) | data[off + 1]  # fic_decoder.cpp:166
                sc.size_cu = size
                if option == 0b000:                       # EEP-A [fic_decoder.cpp:169-173]
                    sc.protection = f"EEP {pl + 1}-A"
                    sc.bitrate_kbps = size // _EEP_A_FACTORS[pl] * 8
                elif option == 0b001:                     # EEP-B [fic_decoder.cpp:174-178]
                    sc.protection = f"EEP {pl + 1}-B"
                    sc.bitrate_kbps = size // _EEP_B_FACTORS[pl] * 32
                off += 2
            else:
                # 短格式 (UEP) [fic_decoder.cpp:181-192]
                table_switch = bool(data[off] & 0x40)     # fic_decoder.cpp:184
                if not table_switch:
                    idx = data[off] & 0x3F                # fic_decoder.cpp:186
                    if 0 <= idx < len(_UEP_SIZES):
                        sc.size_cu = _UEP_SIZES[idx]
                        sc.protection = f"UEP {_UEP_PLS[idx]}"
                        sc.bitrate_kbps = _UEP_BITRATES[idx]
                off += 1

            self.subchannels[subchid] = sc

    # FIG 0/2 — 基本服务与组件定义: SId + 音频组件->子信道 [fic_decoder.cpp:208-258]
    def _fig0_2(self, data: bytes) -> None:
        off = 0
        while off + 2 <= len(data):
            sid = (data[off] << 8) | data[off + 1]         # fic_decoder.cpp:214
            off += 2
            num_comp = data[off] & 0x0F                    # fic_decoder.cpp:217
            off += 1
            for _ in range(num_comp):
                if off + 1 >= len(data):
                    return
                tmid = data[off] >> 6                      # fic_decoder.cpp:221
                if tmid == 0b00:                           # MSC stream audio [fic_decoder.cpp:224]
                    ascty = data[off] & 0x3F               # fic_decoder.cpp:225
                    subchid = data[off + 1] >> 2           # fic_decoder.cpp:226
                    ps = bool(data[off + 1] & 0x02)        # fic_decoder.cpp:227
                    ca = bool(data[off + 1] & 0x01)        # fic_decoder.cpp:228
                    if not ca and ascty in (0, 63):        # 0=DAB, 63=DAB+ [fic_decoder.cpp:232-234]
                        svc = self.services.get(sid) or FICService(sid=sid)
                        svc.subchid = subchid
                        svc.dab_plus = (ascty == 63)
                        if ps:
                            svc.sid = sid
                        self.services[sid] = svc
                off += 2                                   # fic_decoder.cpp:255

    # ---- FIG 1 头与标签: fic_decoder.cpp:581-644 ----
    def _process_fig1(self, data: bytes) -> None:
        if not data:
            return
        charset = data[0] >> 4     # fic_decoder.h:48
        oe = bool(data[0] & 0x08)
        ext = data[0] & 0x07
        if oe:                     # 其他 ensemble 忽略 [fic_decoder.cpp:593]
            return
        body = data[1:]

        if ext == 0:              # ensemble 标签, len_id=2 [fic_decoder.cpp:599-601]
            len_id = 2
        elif ext == 1:            # 节目服务标签, len_id=2
            len_id = 2
        else:
            return
        # 字段总长必须 = len_id + 16(标签) + 2(短标签掩码) [fic_decoder.cpp:615]
        if len(body) != len_id + 16 + 2:
            return

        eid_or_sid = (body[0] << 8) | body[1]
        raw_label = body[len_id:len_id + 16]
        short_mask = (body[len_id + 16] << 8) | body[len_id + 17]  # fic_decoder.cpp:625

        label = _decode_ebu_label(raw_label)
        short = _apply_short_mask(label, short_mask)

        if ext == 0:             # FIG 1/0 ensemble label [fic_decoder.cpp:630-632,646]
            self.ensemble.eid = self.ensemble.eid if self.ensemble.eid >= 0 else eid_or_sid
            self.ensemble.label = label
            self.ensemble.short_label = short
        elif ext == 1:           # FIG 1/1 节目服务 label [fic_decoder.cpp:634-636,659]
            svc = self.services.get(eid_or_sid) or FICService(sid=eid_or_sid)
            svc.label = label
            self.services[eid_or_sid] = svc


# EBU 字符集(charset 0)标签: 16 字节, 0x0D/0x0A/0x00 视为填充 -> 截断到空格
# 来源 fic_decoder.cpp:811-820 ConvertLabelToUTF8 (这里只做最常见的 EBU/ASCII 截断)
def _decode_ebu_label(raw: bytes) -> str:
    out = []
    for b in raw:
        if b in (0x00, 0x0A, 0x0D):
            break
        out.append(b)
    s = bytes(out).decode("ascii", errors="replace").rstrip(" ")
    return s


# 短标签掩码: short_label_mask 高位起, 置位的字符保留 [fic_decoder.cpp:970-978]
def _apply_short_mask(long_label: str, mask: int) -> str:
    return "".join(
        ch for i, ch in enumerate(long_label) if mask & (0x8000 >> i)
    )


# =============================================================================
# ETIParser — 来源: repos/dablin/src/eti_player.cpp, eti_source.h
# =============================================================================
@dataclass
class ETIFrameInfo:
    ok: bool = False
    fsync: int = 0
    nst: int = 0                 # 子信道个数 (eti_player.cpp:44)
    mid: int = 0                 # 传输模式 (eti_player.cpp:45)
    fl: int = 0                  # 帧长度字段 (eti_player.cpp:46)
    ficf: bool = False           # FIC 标志 (eti_player.cpp:43)
    ficl: int = 0                # FIC 长字数 (eti_player.cpp:57)
    fic_offset: int = -1
    fic_bytes: int = 0
    subchannels: Dict[int, int] = field(default_factory=dict)  # scid -> 字节数


class ETIParser:
    """解析 6144 字节 ETI(NI) 帧。与 dablin ETIPlayer::DecodeFrame 一致。"""

    FRAME_SIZE = PARAMS.eti_frame_bytes  # 6144 [eti_source.h:57]

    @staticmethod
    def find_sync(buf: bytes) -> int:
        """在缓冲区中扫描 FSYNC，返回同步偏移(应为 1)；未找到返回 -1。

        来源 eti_source.cpp:179-183 + eti_source.h:58-59。
        """
        for i in range(len(buf) - 3):
            word = (buf[i + 1] << 16) | (buf[i + 2] << 8) | buf[i + 3]
            if word in (PARAMS.fsync0, PARAMS.fsync1):
                return i
        return -1

    def parse(self, frame: bytes) -> ETIFrameInfo:
        info = ETIFrameInfo()
        if len(frame) < self.FRAME_SIZE:
            return info

        # FSYNC: frame[1..3] [eti_player.cpp:25-29]
        fsync = (frame[1] << 16) | (frame[2] << 8) | frame[3]
        if fsync not in (PARAMS.fsync0, PARAMS.fsync1):
            return info
        info.fsync = fsync

        # ERR 必须 0xFF [eti_player.cpp:33-36]
        if frame[0] != PARAMS.err_ok:
            return info

        # FICF / NST / MID / FL [eti_player.cpp:43-46]
        info.ficf = bool(frame[5] & 0x80)
        info.nst = frame[5] & 0x7F
        info.mid = (frame[6] & 0x18) >> 3
        info.fl = ((frame[6] & 0x07) << 8) | frame[7]

        # 头 CRC: 覆盖 frame[4 .. 4+hdr_len)，hdr_len = 4 + nst*4 + 2
        #   [eti_player.cpp:49-55]
        hdr_crc_len = 4 + info.nst * 4 + 2
        hdr_stored = (frame[4 + hdr_crc_len] << 8) | frame[4 + hdr_crc_len + 1]
        if crc16_ccitt(frame[4:4 + hdr_crc_len]) != hdr_stored:
            return info

        # FIC 长字数: ficf ? (mid==3 ? 32 : 24) : 0 [eti_player.cpp:57]
        if not info.ficf:
            info.ficl = 0
        elif info.mid == 3:
            info.ficl = 32
        else:
            info.ficl = 24

        # MST 区起始偏移 = 4(ERR+FSYNC) + 4(MNSC) + nst*4(STC) + 4 [eti_player.cpp:60]
        subch_offset = 4 + 4 + info.nst * 4 + 4

        # MST/MSC 数据区 CRC: 长度 (fl - nst - 1)*4 [eti_player.cpp:63-69]
        mst_crc_len = (info.fl - info.nst - 1) * 4
        mst_stored = (frame[subch_offset + mst_crc_len] << 8) | \
                     frame[subch_offset + mst_crc_len + 1]
        if crc16_ccitt(frame[subch_offset:subch_offset + mst_crc_len]) != mst_stored:
            return info

        # FIC 数据就在 MST 区开头, ficl*4 字节 [eti_player.cpp:71-73]
        if info.ficl:
            info.fic_offset = subch_offset
            info.fic_bytes = info.ficl * 4
            subch_offset += info.fic_bytes

        # 遍历子信道 STC: scid, stl->字节数 [eti_player.cpp:83-93]
        for i in range(info.nst):
            scid = (frame[8 + i * 4] & 0xFC) >> 2
            stl = ((frame[8 + i * 4 + 2] & 0x03) << 8) | frame[8 + i * 4 + 3]
            info.subchannels[scid] = stl * 8

        info.ok = True
        return info


# =============================================================================
# DABAudioInfo — 子信道音频参数
#   区分依据: FIG 0/2 中 ascty (fic_decoder.cpp:232-234)
#     ascty==0  -> DAB  (MPEG Audio Layer II, 源码 src/dab_decoder.h MP2Decoder)
#     ascty==63 -> DAB+ (HE-AAC, src/dabplus_decoder.h SuperframeFilter)
#   DAB+ 采样率由超帧格式字节推断 (dabplus_decoder.cpp:185-196, dabplus_decoder.h)
# =============================================================================
@dataclass
class DABAudioInfo:
    subchid: int
    codec: str            # "mpeg_layer2" 或 "heaac"
    dab_plus: bool
    sample_rate_khz: int = 48
    bitrate_kbps: int = -1
    channels: str = "stereo"


def audio_info_from_service(svc: FICService, sc: Optional[FICSubchannel]) -> DABAudioInfo:
    """根据 FIG 0/2 的音频类型 + FIG 0/1 的子信道比特率汇总音频参数。"""
    info = DABAudioInfo(
        subchid=svc.subchid,
        codec="heaac" if svc.dab_plus else "mpeg_layer2",
        dab_plus=svc.dab_plus,
    )
    if sc is not None:
        info.bitrate_kbps = sc.bitrate_kbps
    # DAB/DAB+ 核心采样率均为 48 kHz 基线（HE-AAC 经 SBR 上采样）
    info.sample_rate_khz = 48
    return info


# DAB+ 超帧格式字节 -> 核心采样率索引 (dabplus_decoder.h GetCoreSrIndex)
_DAC_RATE_SR = {  # (dac_rate, sbr) -> 核心采样率 kHz
    (True, True): 24, (True, False): 48,
    (False, True): 16, (False, False): 32,
}  # dabplus_decoder.h GetCoreSrIndex: 索引 6/3/8/5 -> 24/48/16/32 kHz


def dabplus_samplerate(sf_format_byte: int) -> int:
    """由 DAB+ 超帧格式字节推断核心采样率 (dabplus_decoder.cpp:185-189, dabplus_decoder.h)。"""
    dac_rate = bool(sf_format_byte & 0x40)
    sbr = bool(sf_format_byte & 0x20)
    return _DAC_RATE_SR[(dac_rate, sbr)]


# =============================================================================
# 高层便捷函数（供 ToolRegistry 调用）
# =============================================================================
def dab_fic_decode(fic_bytes: bytes) -> Dict:
    """解码一段 FIC 字节，返回 ensemble/服务/子信道摘要。"""
    dec = FICDecoder()
    dec.process(fic_bytes)
    return {
        "ensemble": {
            "eid": dec.ensemble.eid,
            "label": dec.ensemble.label,
            "short_label": dec.ensemble.short_label,
            "alarm": dec.ensemble.alarm_flag,
        },
        "services": [
            {"sid": hex(s.sid), "subchid": s.subchid, "dab_plus": s.dab_plus,
             "label": s.label}
            for s in dec.services.values()
        ],
        "subchannels": [
            {"subchid": sc.subchid, "start_cu": sc.start_cu, "size_cu": sc.size_cu,
             "protection": sc.protection, "bitrate_kbps": sc.bitrate_kbps}
            for sc in dec.subchannels.values()
        ],
        "discarded_fibs": dec.discarded_fibs,
    }


def dab_eti_parse(frame: bytes) -> Dict:
    """解析一个 6144 字节 ETI 帧，并顺带解码其中的 FIC。"""
    parser = ETIParser()
    info = parser.parse(frame)
    out = {
        "ok": info.ok,
        "fsync": hex(info.fsync),
        "nst": info.nst,
        "mid": info.mid,
        "fl": info.fl,
        "ficf": info.ficf,
        "ficl": info.ficl,
        "subchannels": {str(k): v for k, v in info.subchannels.items()},
    }
    if info.ok and info.fic_bytes:
        fic = frame[info.fic_offset:info.fic_offset + info.fic_bytes]
        out["fic"] = dab_fic_decode(fic)
    return out


def dab_decode_iq(eti_frame: bytes) -> Dict:
    """从一帧 ETI 字节完成"基带后"解码：同步 -> 层解析 -> FIC 服务表。

    注：与 dablin 一致，输入假定为已分帧 ETI(NI) 字节；真正的 OFDM 解调/
    Viterbi/解扰由上游(如 dab2eti)完成，本层不重复造轮子。
    """
    pos = ETIParser.find_sync(eti_frame)
    if pos < 0:
        return {"ok": False, "error": "no ETI FSYNC found"}
    aligned = eti_frame[pos:pos + ETIParser.FRAME_SIZE]
    return {"ok": True, "sync_offset": pos, "frame": dab_eti_parse(aligned)}


if __name__ == "__main__":
    # 自检：打印模式 I 参数
    p = DABParams()
    print(f"DAB Mode I: FFT={p.fft_size}, CP={p.cp_us}us, symbol={p.symbol_us}us, "
          f"symbols/frame={p.symbols_per_frame}, ETI={p.eti_frame_ms}ms/{p.eti_frame_bytes}B")
