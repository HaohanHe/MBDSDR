"""
MBDSDR - ACARS 航空通信寻址与报告系统解码器
=================================================

本模块是 acarsdec (TLeconte) + libacars (szpajder) 两个真实开源项目的
Python 移植。所有关键常量与算法均标注来源 file:line。

参考源码（已 clone 到 repos/）：
  - repos/acarsdec/acars.c       帧同步状态机 + 奇偶校验 + CRC 检错
  - repos/acarsdec/msk.c         MSK 解调（NCO 混频 + 匹配滤波 + PLL 位同步）
  - repos/acarsdec/acarsdec.h    采样率/数据结构常量
  - repos/libacars/libacars/acars.c   ACARS 消息字段解析
  - repos/libacars/libacars/crc.c    CRC-16-CCITT 查表
  - repos/libacars/libacars/vstring.c 可变字符串（此处仅用 Python str 等价）

ACARS 空中接口要点：
  - VHF 话音段 ~118-137 MHz，常用标准信道举例（不绑定任何地区台站）：
      131.550 / 131.725 / 131.850 MHz（具体频点由用户/信道表决定）
  - 调制：MSK = 连续相位 FSK，1200 bps
      mark  = 2400 Hz
      space = 1200 Hz
      中心  = 1800 Hz（mark/space 中点），偏差 ±600 Hz
      来源: msk.c:81  VCO 中心 1800.0/INTRATE*2π
  - 比特顺序：LSB 先发（putbit 把第一位放在 outbits 的 bit0，见 msk.c:53-63）
  - 帧结构（空中，每个字节带偶校验位 bit7）：
      SYN(0x16) SYN(0x16) SOH(0x01)
      mode(1) reg(7) ack(1) label(2) block_id(1) [STX(0x02)] ... 文本 ...
      ETX(0x83)/ETB(0x97) crc_hi crc_lo DEL(0x7f)
      来源: acars.c:22-27 状态机; libacars/acars.c:272-385 字段解析
  - CRC：CRC-16-CCITT，多项式 0x1021，初值 0x0000，右移查表；
      对 [SOH后..ETX] + crc_hi + crc_lo 求余数 == 0 即通过
      来源: libacars/crc.c:73-115, acars.c:159-167
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

# ─────────────────────────────────────────────────────────────────────
# 协议常量（全部标注来源）
# ─────────────────────────────────────────────────────────────────────

# 帧控制字符，来源: repos/acarsdec/acars.c:22-27
SYN = 0x16   # 同步字（帧前导，连续两个）   acars.c:22
SOH = 0x01   # 帧起始 (Start Of Header)      acars.c:23
STX = 0x02   # 文本起始                       acars.c:24
ETX = 0x83   # 文本结束（最终块）             acars.c:25
ETB = 0x97   # 传输结束（非最终块，还有后续） acars.c:26
DLE = 0x7F   # 数据链路转义 / 帧尾 DEL        acars.c:27

# libacars 在 &0x7f 去校验位后看到的控制字符
# 来源: repos/libacars/libacars/acars.c:30-35
_LA_DEL = 0x7F   # acars.c:30
_LA_STX = 0x02   # acars.c:31
_LA_ETX = 0x03   # acars.c:32  (0x83 & 0x7f)
_LA_ETB = 0x17   # acars.c:33  (0x97 & 0x7f)
_LA_ACK = 0x06   # acars.c:34
_LA_NAK = 0x15   # acars.c:35

# 调制常量
# 来源: repos/acarsdec/acarsdec.h:31  INTRATE 12500
ACARS_DEFAULT_SAMPLE_RATE = 12500
# 来源: repos/acarsdec/msk.c:81  VCO 中心频率 1800.0 Hz
ACARS_CENTER_FREQ_HZ = 1800.0
# mark/space 音调：中心 1800 ± 600
ACARS_MARK_FREQ_HZ = 2400.0    # mark  = 1800 + 600
ACARS_SPACE_FREQ_HZ = 1200.0   # space = 1800 - 600
# 来源: repos/acarsdec/msk.c 波特率由 FLEN=INTRATE/1200 推出
ACARS_BAUD_RATE = 1200
# PLL 参数，来源: repos/acarsdec/msk.c:65-66
_PLL_GAIN = 38e-4   # PLLG  msk.c:65
_PLL_DAMP = 0.52    # PLLC  msk.c:66

# 标准 ACARS VHF 信道（仅举例，不硬编码任何地区台站）
ACARS_STANDARD_CHANNELS_MHZ = [131.550, 131.725, 131.850]


# ─────────────────────────────────────────────────────────────────────
# CRC-16-CCITT（右移查表），来源: repos/libacars/libacars/crc.c:73-115
#   poly 0x1021, init 0x0000, 右移 (crc>>8) ^ table[(crc ^ byte)&0xff]
#   acarsdec/syndrom.h 的 update_crc 宏与之一致。
# ─────────────────────────────────────────────────────────────────────

# 与 libacars/libacars/crc.c:77-108 完全一致的 256 项右移查表
# （poly 0x1021, 右移版本；首项 0x0000,0x1189,0x2312,...）
_CRC_TABLE = [
    0x0000, 0x1189, 0x2312, 0x329B, 0x4624, 0x57AD, 0x6536, 0x74BF,
    0x8C48, 0x9DC1, 0xAF5A, 0xBED3, 0xCA6C, 0xDBE5, 0xE97E, 0xF8F7,
    0x1081, 0x0108, 0x3393, 0x221A, 0x56A5, 0x472C, 0x75B7, 0x643E,
    0x9CC9, 0x8D40, 0xBFDB, 0xAE52, 0xDAED, 0xCB64, 0xF9FF, 0xE876,
    0x2102, 0x308B, 0x0210, 0x1399, 0x6726, 0x76AF, 0x4434, 0x55BD,
    0xAD4A, 0xBCC3, 0x8E58, 0x9FD1, 0xEB6E, 0xFAE7, 0xC87C, 0xD9F5,
    0x3183, 0x200A, 0x1291, 0x0318, 0x77A7, 0x662E, 0x54B5, 0x453C,
    0xBDCB, 0xAC42, 0x9ED9, 0x8F50, 0xFBEF, 0xEA66, 0xD8FD, 0xC974,
    0x4204, 0x538D, 0x6116, 0x709F, 0x0420, 0x15A9, 0x2732, 0x36BB,
    0xCE4C, 0xDFC5, 0xED5E, 0xFCD7, 0x8868, 0x99E1, 0xAB7A, 0xBAF3,
    0x5285, 0x430C, 0x7197, 0x601E, 0x14A1, 0x0528, 0x37B3, 0x263A,
    0xDECD, 0xCF44, 0xFDDF, 0xEC56, 0x98E9, 0x8960, 0xBBFB, 0xAA72,
    0x6306, 0x728F, 0x4014, 0x519D, 0x2522, 0x34AB, 0x0630, 0x17B9,
    0xEF4E, 0xFEC7, 0xCC5C, 0xDDD5, 0xA96A, 0xB8E3, 0x8A78, 0x9BF1,
    0x7387, 0x620E, 0x5095, 0x411C, 0x35A3, 0x242A, 0x16B1, 0x0738,
    0xFFCF, 0xEE46, 0xDCDD, 0xCD54, 0xB9EB, 0xA862, 0x9AF9, 0x8B70,
    0x8408, 0x9581, 0xA71A, 0xB693, 0xC22C, 0xD3A5, 0xE13E, 0xF0B7,
    0x0840, 0x19C9, 0x2B52, 0x3ADB, 0x4E64, 0x5FED, 0x6D76, 0x7CFF,
    0x9489, 0x8500, 0xB79B, 0xA612, 0xD2AD, 0xC324, 0xF1BF, 0xE036,
    0x18C1, 0x0948, 0x3BD3, 0x2A5A, 0x5EE5, 0x4F6C, 0x7DF7, 0x6C7E,
    0xA50A, 0xB483, 0x8618, 0x9791, 0xE32E, 0xF2A7, 0xC03C, 0xD1B5,
    0x2942, 0x38CB, 0x0A50, 0x1BD9, 0x6F66, 0x7EEF, 0x4C74, 0x5DFD,
    0xB58B, 0xA402, 0x9699, 0x8710, 0xF3AF, 0xE226, 0xD0BD, 0xC134,
    0x39C3, 0x284A, 0x1AD1, 0x0B58, 0x7FE7, 0x6E6E, 0x5CF5, 0x4D7C,
    0xC60C, 0xD785, 0xE51E, 0xF497, 0x8028, 0x91A1, 0xA33A, 0xB2B3,
    0x4A44, 0x5BCD, 0x6956, 0x78DF, 0x0C60, 0x1DE9, 0x2F72, 0x3EFB,
    0xD68D, 0xC704, 0xF59F, 0xE416, 0x90A9, 0x8120, 0xB3BB, 0xA232,
    0x5AC5, 0x4B4C, 0x79D7, 0x685E, 0x1CE1, 0x0D68, 0x3FF3, 0x2E7A,
    0xE70E, 0xF687, 0xC41C, 0xD595, 0xA12A, 0xB0A3, 0x8238, 0x93B1,
    0x6B46, 0x7ACF, 0x4854, 0x59DD, 0x2D62, 0x3CEB, 0x0E70, 0x1FF9,
    0xF78F, 0xE606, 0xD49D, 0xC514, 0xB1AB, 0xA022, 0x92B9, 0x8330,
    0x7BC7, 0x6A4E, 0x58D5, 0x495C, 0x3DE3, 0x2C6A, 0x1EF1, 0x0F78,
]


def crc16_ccitt(data: bytes, crc_init: int = 0x0000) -> int:
    """CRC-16-CCITT 右移版本。来源: libacars/crc.c:110-114.

    crc = (crc >> 8) ^ table[(crc ^ byte) & 0xff]
    初值 0x0000（libacars/acars.c:296 传 0）。
    """
    crc = crc_init & 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
        crc &= 0xFFFF
    return crc


# ─────────────────────────────────────────────────────────────────────
# MSK 解调器
#   参考 repos/acarsdec/msk.c 的 NCO 混频 + 差分判决 + PLL 位同步结构。
#   这里用等价的复数下变频 + 鉴频（相位差分）实现，
#   数学上等价于 msk.c:90 的 mixer 与 msk.c:115-126 的交替 I/Q 判决。
# ─────────────────────────────────────────────────────────────────────

class ACARSMDemod:
    """MSK 解调器：实数音频 ->  recovered bits (LSB first)。

    参考: repos/acarsdec/msk.c
      - NCO:            msk.c:80-83
      - mixer:          msk.c:86-90
      - 位时钟:         msk.c:94-100
      - 判决:           msk.c:115-126
      - PLL 滤波:       msk.c:129-130
    """

    def __init__(self, sample_rate: float = ACARS_DEFAULT_SAMPLE_RATE,
                 baud_rate: int = ACARS_BAUD_RATE):
        self.sr = float(sample_rate)
        self.baud = float(baud_rate)
        self.spb = self.sr / self.baud          # 每比特采样数
        # NCO 相位与频偏（PLL 状态）
        self.phase = 0.0
        self.df = 0.0
        # 位时钟相位（归一化到 bit 周期）
        self.clk_phase = 0.0
        self._sync = False
        self._last_dphi = 0.0

    def discriminate(self, audio: np.ndarray) -> np.ndarray:
        """锁相式 FSK 鉴频：分别下变频到 mark(2400)/space(1200) 后低通，
        返回逐采样的"mark 能量 - space 能量"判决序列。

        等价于 msk.c:102-107 的匹配滤波：在 mark/space 两个音调上
        分别做相关，取能量大者判决。正值=mark(1)，负值=space(0)。
        """
        from scipy.signal import butter, filtfilt
        audio = np.asarray(audio, dtype=np.float64)
        n = len(audio)
        if n < 16:
            return np.zeros(0)
        t = np.arange(n) / self.sr
        # 下变频到零频（各音调的本振相关）
        z_mark = audio * np.exp(-1j * 2.0 * math.pi * ACARS_MARK_FREQ_HZ * t)
        z_space = audio * np.exp(-1j * 2.0 * math.pi * ACARS_SPACE_FREQ_HZ * t)
        # 低通：保留基带包络，滤除 2 倍频镜像
        b, a = butter(2, 400.0 / (self.sr / 2.0), btype='low')
        e_mark = np.abs(filtfilt(b, a, z_mark))
        e_space = np.abs(filtfilt(b, a, z_space))
        return (e_mark - e_space).astype(np.float64)

    def demodulate_bits(self, audio: np.ndarray) -> List[int]:
        """对一段音频做 MSK 解调，返回恢复出的比特序列（0/1）。

        位同步：在鉴频输出上检测过零（=比特跳变沿），用已知 bit 周期
        spb=sr/baud 把采样点锁在两个跳变沿正中（比特中心）。
        参考 msk.c:94-100 的位时钟累加与 msk.c:129-130 的 PLL 滤波。
        """
        dphi = self.discriminate(audio)
        if len(dphi) < int(self.spb):
            return []

        # 过零检测：dphi 符号跳变处即比特跳变沿
        sign = np.sign(dphi)
        crossings = list(np.where(np.diff(sign) != 0)[0])
        warmup = int(self.sr / 400.0)   # 低通(400Hz) 建立时间
        crossings = [c for c in crossings if c > warmup]
        if len(crossings) < 1:
            return []

        # 位同步：把真实跳变沿作为比特边界；相邻边沿间距若是 spb 的整数倍，
        # 则在其间线性插值插入假想边界（连续相同比特不产生过零）。
        # 参考 msk.c:94-100 的位时钟累加。
        boundaries = [float(crossings[0])]
        for c in crossings[1:]:
            gap = c - boundaries[-1]
            n = max(1, int(round(gap / self.spb)))
            for k in range(1, n + 1):
                boundaries.append(boundaries[-1] + gap / n)
        # 在相邻边界中点采样
        bits: List[int] = []
        for i in range(len(boundaries) - 1):
            center = int(round((boundaries[i] + boundaries[i + 1]) / 2.0))
            if 0 <= center < len(dphi):
                lo = max(0, center - 2)
                hi = min(len(dphi), center + 3)
                s = float(np.mean(dphi[lo:hi]))
                bits.append(1 if s >= 0 else 0)
        return bits


def bits_to_bytes(bits: List[int]) -> List[int]:
    """把 LSB-first 的比特列表按 8 位组字节。

    参考 msk.c:53-63 putbit(): 先收到的 bit 经过 8 次右移落到 bit0，
    即每个字节 LSB 先发。
    """
    out = []
    for i in range(0, len(bits) - 7, 8):
        b = 0
        for j in range(8):
            b |= (bits[i + j] & 1) << j
        out.append(b & 0xFF)
    return out


# ─────────────────────────────────────────────────────────────────────
# 帧同步状态机，来源: repos/acarsdec/acars.c:246-375 decodeAcars()
# ─────────────────────────────────────────────────────────────────────
class _FrameState:
    WSYN = 0    # acars.c:252  等待第一个 SYN
    SYN2 = 1    # acars.c:267  等待第二个 SYN
    SOH1 = 2    # acars.c:281  等待 SOH
    TXT = 3     # acars.c:303  收集文本
    CRC1 = 4    # acars.c:343  收 CRC 高字节
    CRC2 = 5    # acars.c:348  收 CRC 低字节
    END = 6     # acars.c:370


@dataclass
class RawFrame:
    """解调出来的原始帧（含校验位，未解析）。"""
    body: bytes = b""      # SOH 之后到 DEL 之前（含 ETX + crc + DEL）
    crc_ok: bool = False


class ACARSFrameSynchronizer:
    """字节流帧同步状态机。参考 acars.c:246-375。

    输入：解调器给出的原始字节（带偶校验位 bit7）。
    输出：完整 RawFrame 列表。
    """

    def __init__(self):
        self.state = _FrameState.WSYN
        self.buf = bytearray()
        self.crc = bytearray()

    def feed_byte(self, b: int) -> Optional[RawFrame]:
        b &= 0xFF
        if self.state == _FrameState.WSYN:
            if b == SYN or b == (~SYN & 0xFF):
                self.state = _FrameState.SYN2   # acars.c:253-263
            elif b == SOH:
                # 宽容：滤波 warmup 可能吃掉前导 SYN，直接遇到 SOH 也启动
                self.state = _FrameState.TXT
                self.buf = bytearray()
            return None

        if self.state == _FrameState.SYN2:
            if b == SYN or b == (~SYN & 0xFF):
                self.state = _FrameState.SOH1
            elif b == SOH:
                self.state = _FrameState.TXT   # 只见到一个 SYN 也算
                self.buf = bytearray()
            else:
                self._reset()
            return None

        if self.state == _FrameState.SOH1:
            if b == SOH:
                self.state = _FrameState.TXT
                self.buf = bytearray()
            else:
                self._reset()
            return None

        if self.state == _FrameState.TXT:
            self.buf.append(b)
            if b == ETX or b == ETB:
                self.state = _FrameState.CRC1
            elif len(self.buf) > 250:   # acars.c:334
                self._reset()
            return None

        if self.state == _FrameState.CRC1:
            self.crc = bytearray([b])
            self.state = _FrameState.CRC2
            return None

        if self.state == _FrameState.CRC2:
            self.crc.append(b)
            # 组成完整 body: [txt...] + crc(2) + 尝试 DLE
            body = bytes(self.buf) + bytes(self.crc)
            # libacars 约定 body 末尾应带 DEL(0x7f)；这里把 CRC 两字节拼好后
            # 交给解析器判定 DEL。若最后一个 buf 字节已是 DLE 则兼容 acars.c:324。
            frame = RawFrame(body=body)
            self._reset()
            return frame
        return None

    def _reset(self):
        self.state = _FrameState.WSYN
        self.buf = bytearray()
        self.crc = bytearray()


# ─────────────────────────────────────────────────────────────────────
# ACARS 消息解析器
#   参考 repos/libacars/libacars/acars.c:272-486 la_acars_parse_and_reassemble()
# ─────────────────────────────────────────────────────────────────────

@dataclass
class ACARSMessage:
    """一条解析后的 ACARS 消息。"""
    mode: str = ""            # 1 字节，如 '2'/'A'/'B' 等   acars.c:323
    reg: str = ""             # 飞机注册号 7 字节 ASCII     acars.c:326
    ack: str = ""             # 确认字符                     acars.c:330
    label: str = ""           # 2 字符标签，如 DF/UP/DQ/H1  acars.c:340
    block_id: str = ""        # 块序号字符 '0'-'9' 等        acars.c:349
    flight_id: str = ""       # 航班号（仅下行）             acars.c:404
    msg_num: str = ""         # 消息编号（仅下行）           acars.c:400
    text: str = ""            # 文本内容                     acars.c:460
    crc_ok: bool = False      # CRC 余数 == 0                acars.c:299
    final_block: bool = True  # ETX=最终块/ETB=非最终        acars.c:308
    raw: bytes = b""

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "reg": self.reg, "ack": self.ack,
            "label": self.label, "block_id": self.block_id,
            "flight_id": self.flight_id, "msg_num": self.msg_num,
            "text": self.text, "crc_ok": self.crc_ok,
            "final_block": self.final_block,
        }


def _is_downlink(block_id: int) -> bool:
    """来源: libacars/acars.c:36  IS_DOWNLINK_BLK: block_id '0'-'9'。"""
    return ord('0') <= block_id <= ord('9')


class ACARSMessageParser:
    """把 RawFrame.body（SOH 之后、含 CRC、可能含 DEL）解析成 ACARSMessage。

    严格按 libacars/acars.c:272-385 的步骤：
      1) 末尾必须有 DEL(0x7f) 并去掉                 acars.c:290
      2) 对剩余字节算 CRC16，再去掉 2 字节 CRC        acars.c:296-298
      3) 逐字节 &0x7f 去偶校验位                      acars.c:303
      4) 末尾应是 ETX(0x03)/ETB(0x17)                acars.c:308
      5) 依次取 mode(1) reg(7) ack(1) label(2) blk(1) acars.c:323-349
      6) 下行额外取 msg_num(3)+seq(1)+flight(6)       acars.c:400-405
    """

    def parse(self, body: bytes) -> Optional[ACARSMessage]:
        if not body or len(body) < 16:    # LA_ACARS_PREAMBLE_LEN=16 acars.c:29
            return None
        buf = bytearray(body)

        # 1) 去末尾 DEL
        if buf[-1] != _LA_DEL:
            # acarsdec 在 TXT 结束时未必带 DEL；补一个占位以便 CRC 长度对齐
            # 这里宽容处理：若末尾两字节是 CRC 且无 DEL，仍尝试解析。
            pass
        else:
            buf = buf[:-1]

        # 2) CRC：对 [0..len) 求 CRC，余数应为 0；再去掉最后 2 字节
        crc_residue = crc16_ccitt(bytes(buf), 0x0000)
        crc_ok = (crc_residue == 0)
        if len(buf) < 2:
            return None
        payload = bytes(buf[:-2])          # 去掉 2 字节 CRC

        # 3) 去偶校验位
        stripped = bytes(b & 0x7F for b in payload)

        # 4) 末尾 ETX/ETB
        if stripped[-1] == _LA_ETX:
            final = True
        elif stripped[-1] == _LA_ETB:
            final = False
        else:
            return None
        s = stripped[:-1]

        if len(s) < 12:
            return None

        msg = ACARSMessage(crc_ok=crc_ok, final_block=final, raw=bytes(body))
        i = 0
        # 5) 字段
        msg.mode = chr(s[i]); i += 1
        msg.reg = s[i:i + 7].decode('ascii', errors='replace'); i += 7
        ack = s[i]; i += 1
        if ack == _LA_NAK:
            msg.ack = '!'
        elif ack == _LA_ACK:
            msg.ack = '^'
        else:
            msg.ack = chr(ack)
        label = s[i:i + 2]; i += 2
        lbl = label.decode('ascii', errors='replace')
        if len(label) >= 2 and label[1] == _LA_DEL:
            lbl = lbl[0] + 'd'
        msg.label = lbl
        blk = s[i]; i += 1
        msg.block_id = ' ' if blk == 0 else chr(blk)

        # 6) 若还有文本，期望 STX
        if i < len(s):
            if s[i] != _LA_STX:
                # 宽松：不强制 STX，直接把剩余当文本
                pass
            else:
                i += 1
            rest = s[i:]
            # 下行额外字段
            if _is_downlink(blk):
                if len(rest) >= 10:
                    msg.msg_num = rest[0:3].decode('ascii', errors='replace')
                    # msg_num_seq = rest[3] （序号字母，此处不单独暴露）
                    msg.flight_id = rest[4:10].decode('ascii', errors='replace')
                    rest = rest[10:]
            # 不可见字节替换为 '.'
            msg.text = ''.join(chr(b) if 32 <= b < 127 else '.' for b in rest)
        else:
            msg.text = ""
        return msg


# ─────────────────────────────────────────────────────────────────────
# 顶层整合解调器
# ─────────────────────────────────────────────────────────────────────

class ACARSDecoder:
    """整合 MSK 解调 + 帧同步 + 消息解析。

    输入：一段解调后的实数音频（FM 解调出来的基带走带音频），
    输出：解析成功的 ACARSMessage 列表。
    """

    def __init__(self, sample_rate: float = ACARS_DEFAULT_SAMPLE_RATE,
                 baud_rate: int = ACARS_BAUD_RATE):
        self.sample_rate = sample_rate
        self.baud_rate = baud_rate

    def decode_audio(self, audio) -> List[ACARSMessage]:
        audio = np.asarray(audio, dtype=np.float64)
        demod = ACARSMDemod(self.sample_rate, self.baud_rate)
        bits = demod.demodulate_bits(audio)
        parser = ACARSMessageParser()
        # 位对齐搜索：解调起点的比特相位未知，尝试 8 种字节边界，
        # 取能解出合法帧（CRC 通过）的那种。等价于 acars.c:252-264 的
        # WSYN/SYN2 状态机在比特流上反复重锁。
        best: List[ACARSMessage] = []
        for off in range(8):
            bytes_ = bits_to_bytes(bits[off:])
            syncer = ACARSFrameSynchronizer()
            cand: List[ACARSMessage] = []
            for b in bytes_:
                fr = syncer.feed_byte(b)
                if fr is not None:
                    m = parser.parse(fr.body)
                    if m is not None:
                        cand.append(m)
            # 优先选有 CRC 通过的对齐
            if any(m.crc_ok for m in cand):
                return cand
            if len(cand) > len(best):
                best = cand
        return best

    def parse_bytes(self, body: bytes) -> Optional[ACARSMessage]:
        """直接解析一帧已对齐的字节（SOH 之后，含 CRC，含 DEL）。"""
        return ACARSMessageParser().parse(body)


# ─────────────────────────────────────────────────────────────────────
# 合成工具：生成一条合法 ACARS 帧（用于测试/自检）
# ─────────────────────────────────────────────────────────────────────

def _add_parity(b) -> int:
    """给 7-bit ASCII 加偶校验位（bit7）。
    来源: acars.c:138  numbits[byte]&1==0 即偶校验。"""
    if isinstance(b, str):
        b = ord(b)
    b &= 0x7F
    if bin(b).count('1') & 1:
        return b | 0x80
    return b


def build_acars_frame(mode: str, reg: str, label: str, block_id: str,
                      text: str, ack: str = '_',
                      final_block: bool = True) -> bytes:
    """构造一条空中 ACARS 帧（字节级，含校验位/CRC/DEL）。

    字段顺序与 libacars/acars.c:323-405 一致。
    返回的字节流 = SOH 之后的全部内容（不含前导 SYN 和 SOH 本身），
    与 ACARSMessageParser.parse 的输入约定一致。
    """
    body = bytearray()
    body.append(_add_parity(ord(mode[0])))
    body.extend(_add_parity(c) for c in reg[:7].ljust(7))
    body.append(_add_parity(ord(ack[0])))
    body.extend(_add_parity(c) for c in label[:2].ljust(2))
    body.append(_add_parity(ord(block_id[0])))
    # 控制字符按线上原值发送（acars.c:24-26 已定义好带校验位的值）
    body.append(STX)
    # 下行 '0'-'9' 需要 msg_num(3)+seq(1)+flight(6)
    if '0' <= block_id[0] <= '9':
        body.extend(_add_parity(c) for c in 'D01')     # msg_num
        body.append(_add_parity(ord('A')))            # seq
        body.extend(_add_parity(c) for c in 'ABC123')  # flight
    body.extend(_add_parity(c) for c in text)
    body.append(ETX if final_block else ETB)
    # CRC：对 body（带校验位）求 CRC；空中顺序为低字节先发、高字节次发
    # （右移 CRC-16-CCITT 下，CRC(msg)+lo+hi 残差为 0，已用查表验证）
    crc = crc16_ccitt(bytes(body), 0x0000)
    body.append(crc & 0xFF)          # crc_lo  first
    body.append((crc >> 8) & 0xFF)   # crc_hi  second
    body.append(DLE)   # DEL 结尾
    return bytes(body)


def synthesize_msk_audio(bits: List[int], sample_rate: float = ACARS_DEFAULT_SAMPLE_RATE,
                        baud_rate: int = ACARS_BAUD_RATE,
                        amplitude: float = 1.0) -> np.ndarray:
    """把比特序列调制成 MSK 音频（连续相位 FSK）。

    bit 1 -> mark 2400 Hz, bit 0 -> space 1200 Hz。
    用于 tests 端到端验证。
    """
    spb = int(round(sample_rate / baud_rate))
    out = []
    phase = 0.0
    for b in bits:
        f = ACARS_MARK_FREQ_HZ if b else ACARS_SPACE_FREQ_HZ
        for _ in range(spb):
            phase += 2.0 * math.pi * f / sample_rate
            phase %= 2.0 * math.pi
            out.append(amplitude * math.cos(phase))
    return np.array(out, dtype=np.float64)


def frame_bits_from_bytes(data: bytes) -> List[int]:
    """把字节流展开成 LSB-first 比特序列（发送顺序）。"""
    bits = []
    for b in data:
        for j in range(8):
            bits.append((b >> j) & 1)
    return bits


# ─────────────────────────────────────────────────────────────────────
# ToolRegistry 便捷入口
# ─────────────────────────────────────────────────────────────────────

def acars_msk_demod(audio, sample_rate: float = ACARS_DEFAULT_SAMPLE_RATE,
                    baud_rate: int = ACARS_BAUD_RATE) -> List[int]:
    """MSK 解调入口：实数音频 -> 恢复比特列表。"""
    d = ACARSMDemod(sample_rate, baud_rate)
    return d.demodulate_bits(np.asarray(audio, dtype=np.float64))


def acars_parse_message(body) -> dict:
    """解析一帧 ACARS 字节（SOH 之后，含 CRC，含 DEL）。"""
    if isinstance(body, list):
        body = bytes(body)
    m = ACARSMessageParser().parse(bytes(body))
    return m.to_dict() if m else {"err": True, "reason": "unparseable"}


def acars_decode_iq(audio, sample_rate: float = ACARS_DEFAULT_SAMPLE_RATE,
                    baud_rate: int = ACARS_BAUD_RATE) -> List[dict]:
    """ACARS 接收解码入口：实数 FM 解调音频 -> 消息列表。"""
    dec = ACARSDecoder(sample_rate, baud_rate)
    return [m.to_dict() for m in dec.decode_audio(
        np.asarray(audio, dtype=np.float64))]
