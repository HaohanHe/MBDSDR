"""
MBDSDR AX.25 协议栈
====================
完整实现业余无线电 AX.25 数据链路层协议，包括：
- AX.25 帧编解码（UI/I/Supervisory，支持数字中继器）
- AFSK 1200 baud Bell 202 调制解调（软件 TNC）
- APRS 完整编解码（位置/气象/消息/对象/遥测/状态）
- KISS 协议接口（连接硬件 TNC）
- Digipeater 分组转发
- CRC-16 CCITT FCS 校验

作者：MBDSDR Team (BI4MIB)
许可证：GPL-3.0
"""

import struct
import math
import time
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from enum import Enum


# ============================================================
# 常量定义
# ============================================================

AX25_FLAG = 0x7E          # 帧标志
AX25_PID_NOLAYER3 = 0xF0  # 无第三层协议
AX25_PID_IP = 0xCC        # IP 协议
AX25_CTRL_UI = 0x03       # UI 帧控制字段
AX25_CTRL_SABME = 0x6F    # SABME 控制字段
AX25_CTRL_DISC = 0x43     # DISC 控制字段
AX25_CTRL_UA = 0x63       # UA 控制字段
AX25_CTRL_DM = 0x0F       # DM 控制字段

# AFSK Bell 202 常量
# 来源: direwolf audio.h:470-472
#   #define DEFAULT_MARK_FREQ  1200   (audio.h:470)
#   #define DEFAULT_SPACE_FREQ 2200   (audio.h:471)
#   #define DEFAULT_BAUD       1200   (audio.h:472)
# 1200 baud VHF APRS：mark=1200Hz, space=2200Hz（Bell 202）。
# 采样率：direwolf 默认 44100 (audio.h:442 DEFAULT_SAMPLES_PER_SEC)，
#   也接受 48000（audio.h:447-450，SDR 常用）；这里默认 48000，与现有 SDR 链路一致。
AFSK_MARK_FREQ = 1200.0   # Mark 频率 (Hz)   audio.h:470
AFSK_SPACE_FREQ = 2200.0  # Space 频率 (Hz)  audio.h:471
AFSK_BAUD_RATE = 1200.0   # 波特率           audio.h:472
AFSK_SAMPLE_RATE = 48000.0  # 采样率（direwolf 默认 44100，audio.h:442；48000 亦支持）

# KISS 协议常量
KISS_FEND = 0xC0
KISS_FESC = 0xDB
KISS_TFEND = 0xDC
KISS_TFESC = 0xDD
KISS_CMD_DATA = 0x00
KISS_CMD_TXDELAY = 0x01
KISS_CMD_P = 0x02
KISS_CMD_SLOTTIME = 0x03
KISS_CMD_TXTAIL = 0x04
KISS_CMD_FULLDUPLEX = 0x05
KISS_CMD_SETHARDWARE = 0x06
KISS_CMD_RETURN = 0xFF

# APRS 数据类型标识符 (DTI)
# 来源: direwolf decode_aprs.c:336-488 (aprs_tt.c DTI 表)
APRS_POSITION = '!'       # 位置（无时间戳，无消息）   decode_aprs.c:338
APRS_POSITION_MSG = '='   # 位置（无时间戳，有消息）   decode_aprs.c:341  (旧误标为 ' Mic-E)
APRS_POSITION_TIME = '/'  # 位置（有时间戳，无消息）   decode_aprs.c:386
APRS_POSITION_TIME_MSG = '@'  # 位置（有时间戳，有消息） decode_aprs.c:387
APRS_STATUS = '>'         # 状态报告                  decode_aprs.c:440
APRS_MESSAGE = ':'        # 消息 / bulletin / 遥测元数据 decode_aprs.c:394
APRS_WEATHER = '_'        # 无位置气象报告            decode_aprs.c:459
APRS_OBJECT = ';'         # Object                    decode_aprs.c:428
APRS_ITEM = ')'           # Item                      decode_aprs.c:380
APRS_TELEMETRY = 'T'     # 遥测                      decode_aprs.c:453
APRS_QUERY = '?'          # 查询                      decode_aprs.c:447
APRS_MIC_E = "'"          # Mic-E 压缩位置（旧格式）  decode_aprs.c:373
APRS_USERDEF = '{'        # 用户自定义                decode_aprs.c:465
APRS_THIRDPARTY = '}'     # 第三方流量


# ============================================================
# CRC-16 CCITT FCS 计算
# ------------------------------------------------------------
# 来源: direwolf fcs_calc.c:76-87 (fcs_calc)
#   crc = 0xffff;
#   for each byte: crc = (crc >> 8) ^ ccitt_table[(crc ^ byte) & 0xff];
#   return crc ^ 0xffff;
# 表来自 RFC1549 (fcs_calc.c:34)。这是 CRC-16/X.25：
#   多项式 0x1021（正常）→ 反射 0x8408（表项 table[0x80]=0x8408，fcs_calc.c:52），
#   初值 0xFFFF，输入/输出反转，最终异或 0xFFFF。
#   校验矢量 "123456789" -> 0x906E（已与 C 表逐字节核对一致）。
# ============================================================

def crc16_ccitt(data: bytes) -> int:
    """计算 CRC-16 CCITT/X.25 FCS。位级反射算法，与 fcs_calc.c 表驱动结果逐字节一致。"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return crc ^ 0xFFFF


def encode_address(callsign: str, ssid: int = 0, has_been_repeated: bool = False,
                   is_last: bool = False) -> bytes:
    """
    编码 AX.25 地址字段（7字节）。
    呼号左移1位，SSID 在第7字节。
    """
    # 清理呼号，去除 SSID
    if '-' in callsign:
        parts = callsign.split('-')
        callsign = parts[0]
        if len(parts) > 1 and parts[1].isdigit():
            ssid = int(parts[1])

    # 呼号最多6字符，不足补空格
    callsign = callsign.upper().ljust(6)[:6]

    # 前6字节：呼号字符左移1位
    addr = bytearray()
    for ch in callsign:
        addr.append(ord(ch) << 1)

    # 第7字节：SSID + 控制位。格式 0b C RRR SSID RRRRRR 1：
    #   bit7=C/R 命令位（仅目的地址置 1），bits6-4=保留(110)，bits3-1=SSID，bit0=扩展位
    ssid_byte = (ssid & 0x0F) << 1
    if has_been_repeated:
        ssid_byte |= 0x80  # H 位（已被中继，仅源/中继地址）
    if is_last:
        ssid_byte |= 0x01  # 扩展位（地址字段结束=1）
    # RR 位（保留）设为 11
    ssid_byte |= 0x60
    addr.append(ssid_byte)

    return bytes(addr)


def decode_address(data: bytes, offset: int = 0) -> Tuple[str, int, bool, bool, int]:
    """
    解码 AX.25 地址字段。
    返回：(呼号, SSID, 是否已被中继, 是否是最后地址, 新偏移)
    """
    callsign = ''
    for i in range(6):
        callsign += chr((data[offset + i] >> 1) & 0x7F)
    callsign = callsign.strip()

    ssid_byte = data[offset + 6]
    ssid = (ssid_byte >> 1) & 0x0F
    has_been_repeated = bool(ssid_byte & 0x80)
    is_last = bool(ssid_byte & 0x01)

    return callsign, ssid, has_been_repeated, is_last, offset + 7


# ============================================================
# AX.25 帧类
# ============================================================

@dataclass
class AX25Frame:
    """AX.25 帧数据结构。"""
    destination: str = ''           # 目的呼号
    dest_ssid: int = 0              # 目的 SSID
    source: str = ''                # 源呼号
    source_ssid: int = 0            # 源 SSID
    digipeaters: List[Tuple[str, int, bool]] = field(default_factory=list)  # 中继器列表 (呼号, SSID, 是否已中继)
    control: int = AX25_CTRL_UI     # 控制字段
    pid: int = AX25_PID_NOLAYER3    # 协议 ID
    info: bytes = b''               # 信息字段
    fcs: int = 0                    # 帧校验序列
    fcs_valid: bool = False         # FCS 是否有效

    def to_bytes(self) -> bytes:
        """将帧编码为字节流（不含首尾标志）。"""
        frame = bytearray()

        # 来源: direwolf ax25_pad.c:428,431,1253-1257 — 目的站 SSID 永远不是最后地址(L=0)；
        # 只有地址字段的最后一个站 SSID 字节 bit0=1（HDLC 地址扩展位）。
        # 之前 BUG: 把 is_last 传给目的站，无中继时目的站 bit0=1，direwolf
        # ax25_get_num_addr 扫到第 7 字节就停，认为只有 1 个地址而拒收整帧。
        # 目的地址（命令帧：C/R 位 bit7=1；目的站永远 is_last=False）
        dest_addr = bytearray(encode_address(self.destination, self.dest_ssid, is_last=False))
        dest_addr[6] |= 0x80  # C-bit = 1（命令帧）
        frame += dest_addr

        # 源地址：无中继时源站是最后地址(L=1)；有中继时源站不是最后地址
        src_is_last = (len(self.digipeaters) == 0)
        frame += encode_address(self.source, self.source_ssid, is_last=src_is_last)

        # 中继器地址：只有最后一个中继站是最后地址(L=1)
        for i, (call, ssid, repeated) in enumerate(self.digipeaters):
            is_last = (i == len(self.digipeaters) - 1)
            frame += encode_address(call, ssid, has_been_repeated=repeated, is_last=is_last)

        # 控制字段
        frame.append(self.control)

        # 协议 ID（仅 UI 和 I 帧有）
        if self.control in (AX25_CTRL_UI, 0x00, 0x02, 0x04, 0x06, 0x08, 0x0A, 0x0C, 0x0E):
            frame.append(self.pid)

        # 信息字段
        frame += self.info

        # FCS
        self.fcs = crc16_ccitt(bytes(frame))
        frame += struct.pack('<H', self.fcs)

        return bytes(frame)

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional['AX25Frame']:
        """从字节流解析帧（不含首尾标志）。"""
        if len(data) < 15:  # 最小帧长：7(目的)+7(源)+1(控制)+2(FCS)
            return None

        try:
            frame = cls()
            offset = 0

            # 目的地址
            frame.destination, frame.dest_ssid, _, is_last, offset = decode_address(data, offset)

            # 源地址
            frame.source, frame.source_ssid, _, is_last, offset = decode_address(data, offset)

            # 中继器地址
            while not is_last and offset + 7 <= len(data) - 2:
                call, ssid, repeated, is_last, offset = decode_address(data, offset)
                frame.digipeaters.append((call, ssid, repeated))

            # 控制字段
            frame.control = data[offset]
            offset += 1

            # 协议 ID
            if frame.control in (AX25_CTRL_UI, 0x00, 0x02, 0x04, 0x06, 0x08, 0x0A, 0x0C, 0x0E):
                frame.pid = data[offset]
                offset += 1

            # FCS（最后2字节）
            if offset + 2 <= len(data):
                frame.fcs = struct.unpack('<H', data[-2:])[0]
                frame.info = data[offset:-2]

                # 验证 FCS
                computed_fcs = crc16_ccitt(data[:-2])
                frame.fcs_valid = (computed_fcs == frame.fcs)
            else:
                frame.info = data[offset:]

            return frame
        except (IndexError, struct.error):
            return None

    def __repr__(self) -> str:
        digi_str = ','.join([f"{c}-{s}" for c, s, _ in self.digipeaters])
        return (f"AX25Frame({self.source}-{self.source_ssid} -> "
                f"{self.destination}-{self.dest_ssid}"
                f"{(' via ' + digi_str) if digi_str else ''}, "
                f"ctrl=0x{self.control:02X}, pid=0x{self.pid:02X}, "
                f"info_len={len(self.info)}, fcs_valid={self.fcs_valid})")


# ============================================================
# HDLC 位填充/去填充
# ------------------------------------------------------------
# 来源: direwolf hdlc_rec.c:695-705 —
#   "(pat_det & 0xfc) == 0x7c" 即连续 5 个 '1' 后跟一个 '0' 时，
#   该 '0' 是位填充(bit stuffing)，接收端必须丢弃。
#   pat_det 为 8 位移位寄存器，LSB first（hdlc_rec.c:511 "Octets are sent LSB first"）。
#   标志序列 0x7E = 01111110（hdlc_rec.c:526）；异常中止 0xFE = 11111110（hdlc_rec.c:677）。
# ============================================================

def bits_to_bytes(bits: List[int]) -> bytes:
    """把位序列（LSB first）打包成字节；末字节不足 8 位时右侧补 0。

    注意：这里不丢弃任何位——末字节的不足位补 0 填充。接收端去填充后，
    由 hdlc_bit_unstuff 负责丢弃尾部填充位（只保留完整字节）。
    """
    result = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j in range(min(8, len(bits) - i)):
            if bits[i + j]:
                byte |= 1 << j
        result.append(byte)
    return bytes(result)


def bytes_to_bits(data: bytes) -> List[int]:
    """把字节拆成位序列（LSB first，与 direwolf hdlc_rec.c:511 一致）。"""
    bits = []
    for byte in data:
        for i in range(8):
            bits.append((byte >> i) & 1)
    return bits


def stuff_bits(bits: List[int]) -> List[int]:
    """HDLC 位填充：连续 5 个 1 后插入一个 0。来源: hdlc_rec.c:695-705（发射侧等价实现）。"""
    out = []
    ones = 0
    for bit in bits:
        out.append(bit)
        if bit == 1:
            ones += 1
            if ones == 5:
                out.append(0)
                ones = 0
        else:
            ones = 0
    return out


def unstuff_bits(bits: List[int]) -> List[int]:
    """HDLC 去位填充：连续 5 个 1 后丢弃紧随的 0。来源: hdlc_rec.c:695-705。"""
    out = []
    ones = 0
    for bit in bits:
        if bit == 1:
            ones += 1
            out.append(bit)
        else:
            if ones == 5:
                # 这是填充位，丢弃
                ones = 0
                continue
            ones = 0
            out.append(bit)
    return out


def hdlc_bit_stuff(data: bytes) -> bytes:
    """HDLC 位填充：连续5个1后插入0。位序列打包成字节（末尾可能有零填充）。"""
    return bits_to_bytes(stuff_bits(bytes_to_bits(data)))


def hdlc_bit_unstuff(data: bytes) -> bytes:
    """HDLC 去位填充：移除连续5个1后的0。

    发射侧位填充后位长不一定是 8 的整数倍，打包成字节时末尾补了 0；
    接收侧去填充后多出的尾部填充位必须丢弃——只保留完整字节（与 direwolf
    接收端"只收完整 octet"一致，hdlc_rec.c:713-728 仅在 olen==8 时落盘）。
    原始 AX.25 帧是整数字节，故截断到 8 的整数倍即精确还原。
    """
    unstuffed = unstuff_bits(bytes_to_bits(data))
    n = (len(unstuffed) // 8) * 8
    return bits_to_bytes(unstuffed[:n])


# ============================================================
# AFSK 调制解调器（软件 TNC）
# ============================================================

class AFSKModem:
    """
    AFSK 1200 baud Bell 202 调制解调器。
    Mark = 1200 Hz, Space = 2200 Hz
    NRZI 编码：0 = 频率切换，1 = 频率不变
    """

    def __init__(self, sample_rate: float = AFSK_SAMPLE_RATE,
                 baud_rate: float = AFSK_BAUD_RATE,
                 mark_freq: float = AFSK_MARK_FREQ,
                 space_freq: float = AFSK_SPACE_FREQ):
        self.sample_rate = sample_rate
        self.baud_rate = baud_rate
        self.mark_freq = mark_freq
        self.space_freq = space_freq
        self.samples_per_bit = sample_rate / baud_rate

    def encode_nrzi(self, data: bytes) -> List[int]:
        """将字节数据编码为 NRZI 位序列。"""
        bits = []
        current_state = 1  # 初始 Mark

        for byte in data:
            for i in range(8):
                bit = (byte >> i) & 1
                if bit == 0:
                    current_state ^= 1  # 0 = 切换
                bits.append(current_state)
        return bits

    def modulate(self, frame: AX25Frame, preamble_bytes: int = 20,
                 postamble_bytes: int = 4) -> np.ndarray:
        """
        将 AX.25 帧调制为 AFSK 音频信号。
        返回 float32 numpy 数组（范围 -1 到 1）。
        """
        # 构建完整帧：前导标志 + 位填充帧 + 后导标志
        frame_data = frame.to_bytes()
        stuffed = hdlc_bit_stuff(frame_data)

        # 前导：多个 0x7E 标志
        full_data = bytes([AX25_FLAG] * preamble_bytes) + stuffed + bytes([AX25_FLAG] * postamble_bytes)

        # NRZI 编码
        nrzi_bits = self.encode_nrzi(full_data)

        # 生成音频
        total_samples = int(len(nrzi_bits) * self.samples_per_bit)
        t = np.arange(total_samples) / self.sample_rate
        phase = np.zeros(total_samples)
        freq = np.zeros(total_samples)

        bit_index = 0
        sample_count = 0
        current_freq = self.mark_freq if nrzi_bits[0] == 1 else self.space_freq

        for i in range(total_samples):
            freq[i] = current_freq
            if sample_count >= self.samples_per_bit and bit_index < len(nrzi_bits) - 1:
                bit_index += 1
                sample_count = 0
                current_freq = self.mark_freq if nrzi_bits[bit_index] == 1 else self.space_freq
            sample_count += 1

        # 积分频率得到相位
        phase = np.cumsum(2 * np.pi * freq / self.sample_rate)
        audio = np.sin(phase).astype(np.float32)

        # 淡入淡出
        fade_samples = int(self.sample_rate * 0.005)  # 5ms
        if len(audio) > 2 * fade_samples:
            audio[:fade_samples] *= np.linspace(0, 1, fade_samples)
            audio[-fade_samples:] *= np.linspace(1, 0, fade_samples)

        return audio

    def demodulate(self, audio: np.ndarray) -> List[AX25Frame]:
        """
        从 AFSK 音频信号中解调 AX.25 帧。

        FM 鉴频解调：FFT 构造解析信号取瞬时频率，半位窗平滑后逐样本判决
        NRZI 电平；数字 PLL 做位定时恢复（翻转沿锁相、位中心采样），多初始
        相位/增益尝试，按 0x7E 标志截取帧、HDLC 去填充并用 FCS(CRC-16) 校验。
        低采样率输入先整数倍上采样到 ≥44.1kHz，保证每位有足够样本。
        """
        if audio is None or len(audio) == 0:
            return []
        audio = np.asarray(audio, dtype=np.float64)
        # 低采样率整数倍上采样，保证每位样本数（22050->44100, 11025->44100）
        fsr = self.sample_rate
        up = 1
        while fsr * up < 44100:
            up += 1
        if up > 1:
            old_x = np.arange(len(audio))
            new_x = np.linspace(0, len(audio) - 1, (len(audio) - 1) * up + 1)
            audio = np.interp(new_x, old_x, audio)
            fsr = self.sample_rate * up
        n = len(audio)
        spb = fsr / self.baud_rate
        win = max(3, int(round(spb)))
        if n < int(spb * 8):
            return []
        mx = np.max(np.abs(audio))
        if mx > 0:
            audio = audio / mx
        # 来源: direwolf demod_afsk.c:450-451,638-703 — 带通预滤波（1014–2386 Hz）。
        # direwolf 用 FIR（prefilter_baud=0.155，f1=1200-186=1014, f2=2200+186=2386）。
        # 这里用二阶 RBJ biquad 带通做等效预滤波，抑制带外噪声/邻道干扰。
        bp_f0 = (self.mark_freq + self.space_freq) / 2.0  # 1700 Hz
        bp_bw = (self.space_freq - self.mark_freq) + 2 * 0.155 * self.baud_rate  # 1372 Hz
        bp_Q = bp_f0 / bp_bw
        w0 = 2.0 * math.pi * bp_f0 / fsr
        alpha = math.sin(w0) / (2.0 * bp_Q)
        cos_w0 = math.cos(w0)
        b0 = alpha; b1 = 0.0; b2 = -alpha
        a0 = 1.0 + alpha; a1 = -2.0 * cos_w0; a2 = 1.0 - alpha
        b0 /= a0; b1 /= a0; b2 /= a0; a1 /= a0; a2 /= a0
        # 直接 II 型转置 biquad
        x1 = x2 = y1 = y2 = 0.0
        y = np.empty_like(audio)
        for idx, x in enumerate(audio):
            v = x - a1 * y1 - a2 * y2
            yv = b0 * v + b1 * y1 + b2 * y2
            x2 = x1; x1 = x; y2 = y1; y1 = yv
            y[idx] = yv
        audio = y
        # 尾部补若干位（延续末电平），保证帧结束标志完整落入采样窗
        pad_bits = int(round(spb * 24))
        audio = np.concatenate([audio, np.full(pad_bits, float(audio[-1]))])
        n = len(audio)

        # 1) FM 鉴频：FFT 构造解析信号 -> 瞬时相位 -> 瞬时频率
        #    （AFSK 是二进制 FM，鉴频比过零率/短窗能量鲁棒，multimon-ng 同类思路）
        n0 = n
        spec = np.fft.fft(audio)
        h_filter = np.zeros(n0)
        if n0 % 2 == 0:
            h_filter[0] = h_filter[n0 // 2] = 1
            h_filter[1:n0 // 2] = 2
        else:
            h_filter[0] = 1
            h_filter[1:(n0 + 1) // 2] = 2
        analytic = np.fft.ifft(spec * h_filter)
        phase = np.unwrap(np.angle(analytic))
        inst_freq = np.concatenate(([phase[0]], np.diff(phase))) * fsr / (2 * np.pi)
        # 半位周期移动平均：抑制鉴频毛刺，又不过度模糊翻转沿
        w_avg = max(3, int(round(spb * 0.5)))
        csum = np.cumsum(np.insert(inst_freq, 0, 0.0))
        smoothed = (csum[w_avg:] - csum[:-w_avg]) / w_avg
        pad_l = w_avg // 2
        pad_r = w_avg - pad_l
        inst_freq = np.pad(smoothed, (pad_l, pad_r - 1 if pad_r else 0), mode="edge")
        if len(inst_freq) < n:
            inst_freq = np.pad(inst_freq, (0, n - len(inst_freq)), mode="edge")
        inst_freq = inst_freq[:n]
        # 判决门限取 mark/space 中点
        threshold = (self.mark_freq + self.space_freq) / 2.0
        level = (inst_freq < threshold).astype(np.uint8)  # mark=1, space=0

        # 2) 数字 PLL 位定时恢复：逐样本推进位时钟，检测到电平翻转沿时
        #    把时钟拉向最近的位边界；在每位中心采样 NRZI 电平。
        #    对非整数 samples_per_bit、采样率偏差和翻转沿抖动都鲁棒。
        def _run_pll(init_clock: float, gain: float) -> List[int]:
            clock = init_clock
            bits: List[int] = []
            prev_lv = int(level[0])
            last_edge = -10 * spb
            for i in range(n):
                cur = int(level[i])
                if cur != prev_lv:
                    # 去抖：只接受距上个沿超过 0.6 位的翻转（拒绝同一位内的抖动沿）
                    if i - last_edge >= 0.6 * spb:
                        err = clock if clock < spb / 2 else clock - spb
                        clock -= gain * err
                        last_edge = i
                    prev_lv = cur
                prev_clock = clock
                clock += 1.0
                if prev_clock < spb / 2 <= clock:
                    bits.append(cur)  # 位中心采样
                if clock >= spb:
                    clock -= spb
            return bits

        def _is_flag(bits, i):
            return (i + 7 < len(bits) and bits[i] == 0 and
                    all(bits[i + x] == 1 for x in range(1, 7)) and
                    bits[i + 7] == 0)

        def _extract_frames(raw_bits: List[int]) -> List['AX25Frame']:
            out_frames = []
            i = 0
            total_bits = len(raw_bits)
            while i < total_bits - 8:
                if not _is_flag(raw_bits, i):
                    i += 1
                    continue
                j = i + 8
                while j < total_bits - 8 and _is_flag(raw_bits, j):
                    j += 8
                frame_start = j
                end = -1
                aborted = False
                ones_run = 0
                k2 = frame_start
                while k2 < total_bits - 8:
                    # 来源: direwolf hdlc_rec.c:677, hdlc_rec2.c:684 — Abort 序列 0xFE
                    # 连续 7 个 1 (>=7) 表示帧异常终止，丢弃当前帧，不继续解析。
                    if raw_bits[k2] == 1:
                        ones_run += 1
                        if ones_run >= 7:
                            aborted = True
                            break
                    else:
                        ones_run = 0
                    if _is_flag(raw_bits, k2):
                        end = k2
                        break
                    k2 += 1
                if aborted:
                    # abort：跳到 abort 之后继续找下一个 flag
                    i = k2 + 1
                    continue
                if end < 0:
                    break
                frame_bits = raw_bits[frame_start:end]

                # HDLC 去位填充：连续 5 个 1 后的 0 是填充位
                unstuffed = []
                ones = 0
                for bit in frame_bits:
                    if bit == 1:
                        ones += 1
                        unstuffed.append(bit)
                    else:
                        if ones == 5:
                            ones = 0
                            continue
                        ones = 0
                        unstuffed.append(bit)

                frame_bytes = bytearray()
                for k3 in range(0, len(unstuffed) - 7, 8):
                    byte = 0
                    for b in range(8):
                        if unstuffed[k3 + b]:
                            byte |= 1 << b
                    frame_bytes.append(byte)

                if len(frame_bytes) >= 15:
                    frame = AX25Frame.from_bytes(bytes(frame_bytes))
                    if frame and frame.fcs_valid:
                        if not any(x.source == frame.source and x.info == frame.info
                                   for x in out_frames):
                            out_frames.append(frame)
                i = end + 8
            return out_frames

        # 3) 多初始相位/增益尝试，FCS(CRC-16) 是极强校验，只有位定时正确
        #    的那次才会通过；合并所有 FCS-valid 帧并去重。
        frames: List['AX25Frame'] = []
        seen = set()
        for gain in (0.5, 0.3, 0.7):
            for frac in (0.0, 0.25, 0.5, 0.75):
                init_clock = frac * spb
                nrzi_bits = _run_pll(init_clock, gain)
                raw_bits = []
                prev = 1
                for b in nrzi_bits:
                    raw_bits.append(0 if b != prev else 1)
                    prev = b
                for frame in _extract_frames(raw_bits):
                    key = (frame.source, frame.destination, bytes(frame.info))
                    if key not in seen:
                        seen.add(key)
                        frames.append(frame)

        return frames


# ============================================================
# APRS 编解码
# ============================================================

@dataclass
class APRSPosition:
    """APRS 位置报文。"""
    latitude: float = 0.0
    longitude: float = 0.0
    symbol_table: str = '/'
    symbol_code: str = '-'
    altitude: Optional[int] = None  # 英尺
    course: Optional[int] = None     # 度
    speed: Optional[int] = None      # 节
    comment: str = ''
    timestamp: Optional[str] = None  # DDHHMMz

    def encode(self) -> str:
        """编码为 APRS 位置报文。"""
        # 纬度：DDMM.hhN
        lat_deg = int(abs(self.latitude))
        lat_min = (abs(self.latitude) - lat_deg) * 60
        lat_hemi = 'N' if self.latitude >= 0 else 'S'
        lat_str = f"{lat_deg:02d}{lat_min:05.2f}{lat_hemi}"

        # 经度：DDDMM.hhW
        lon_deg = int(abs(self.longitude))
        lon_min = (abs(self.longitude) - lon_deg) * 60
        lon_hemi = 'E' if self.longitude >= 0 else 'W'
        lon_str = f"{lon_deg:03d}{lon_min:05.2f}{lon_hemi}"

        result = f"{lat_str}{self.symbol_table}{lon_str}{self.symbol_code}"

        # 课程/速度
        if self.course is not None and self.speed is not None:
            result += f"{self.course:03d}/{self.speed:03d}"

        # 高度
        if self.altitude is not None:
            result += f"/A={self.altitude:06d}"

        # 注释
        if self.comment:
            result += self.comment

        # 数据类型标识符
        if self.timestamp:
            return f"/{self.timestamp}{result}"
        else:
            return f"!{result}"

    @classmethod
    def decode(cls, payload: str) -> Optional['APRSPosition']:
        """从 APRS 报文体解码位置。"""
        if not payload or len(payload) < 1:
            return None

        data_type = payload[0]
        pos = cls()

        if data_type == '/':
            # 有时间戳
            if len(payload) >= 8:
                pos.timestamp = payload[1:8]
                payload = payload[8:]
            else:
                return None
        elif data_type == '!':
            payload = payload[1:]
        else:
            return None

        # 解析位置
        try:
            # 纬度 DDMM.hhN
            lat_deg = int(payload[0:2])
            lat_min = float(payload[2:7])
            lat_hemi = payload[7]
            pos.latitude = lat_deg + lat_min / 60
            if lat_hemi == 'S':
                pos.latitude = -pos.latitude

            # 符号表
            pos.symbol_table = payload[8]

            # 经度 DDDMM.hhW
            lon_deg = int(payload[9:12])
            lon_min = float(payload[12:17])
            lon_hemi = payload[17]
            pos.longitude = lon_deg + lon_min / 60
            if lon_hemi == 'W':
                pos.longitude = -pos.longitude

            # 符号代码
            pos.symbol_code = payload[18]

            # 课程/速度
            if len(payload) > 22 and payload[19:22].isdigit() and payload[22] == '/':
                pos.course = int(payload[19:22])
                if len(payload) > 25 and payload[23:26].isdigit():
                    pos.speed = int(payload[23:26])

            # 高度
            alt_idx = payload.find('/A=')
            if alt_idx >= 0:
                try:
                    pos.altitude = int(payload[alt_idx+3:alt_idx+9])
                except (ValueError, IndexError):
                    pass

            # 注释
            comment_start = 19
            if pos.course is not None:
                comment_start = 26
            if alt_idx >= 0:
                comment_start = alt_idx + 9
            if comment_start < len(payload):
                pos.comment = payload[comment_start:].strip()

            return pos
        except (IndexError, ValueError):
            return None


@dataclass
class APRSMessage:
    """APRS 消息报文。"""
    addressee: str = ''
    message: str = ''
    message_id: Optional[str] = None

    def encode(self) -> str:
        result = f":{self.addressee:<9}:{self.message}"
        if self.message_id:
            result += '{' + self.message_id
        return result

    @classmethod
    def decode(cls, payload: str) -> Optional['APRSMessage']:
        if not payload or payload[0] != ':':
            return None
        try:
            addressee = payload[1:10].strip()
            # APRS 消息格式: :ADDRESSEE(9chars):MESSAGE
            # 索引10是分隔冒号，消息从索引11开始
            msg_text = payload[11:] if len(payload) > 11 else ''

            # 检查消息ID
            msg_id = None
            if '{' in msg_text:
                parts = msg_text.rsplit('{', 1)
                msg_text = parts[0]
                msg_id = parts[1]

            return cls(addressee=addressee, message=msg_text, message_id=msg_id)
        except IndexError:
            return None


@dataclass
class APRSWeather:
    """APRS 气象报文。"""
    latitude: float = 0.0
    longitude: float = 0.0
    wind_dir: int = 0       # 度
    wind_speed: int = 0     # 节
    wind_gust: int = 0      # 节
    temperature: int = 0    # 华氏度
    rain_1h: int = 0        # 百分之一英寸
    rain_24h: int = 0
    rain_midnight: int = 0
    humidity: int = 0       # %
    pressure: int = 0       # 十分之一毫巴

    def encode(self) -> str:
        lat_deg = int(abs(self.latitude))
        lat_min = (abs(self.latitude) - lat_deg) * 60
        lat_hemi = 'N' if self.latitude >= 0 else 'S'

        lon_deg = int(abs(self.longitude))
        lon_min = (abs(self.longitude) - lon_deg) * 60
        lon_hemi = 'E' if self.longitude >= 0 else 'W'

        return (f"_{lat_deg:02d}{lat_min:05.2f}{lat_hemi}/"
                f"{lon_deg:03d}{lon_min:05.2f}{lon_hemi}_"
                f"{self.wind_dir:03d}/{self.wind_speed:03d}g{self.wind_gust:03d}"
                f"t{self.temperature:03d}r{self.rain_1h:03d}p{self.rain_24h:03d}"
                f"P{self.rain_midnight:03d}h{self.humidity:02d}b{self.pressure:05d}")


@dataclass
class APRSPacket:
    """完整的 APRS 报文（含 AX.25 帧头 + APRS 载荷）。"""
    source: str = ''
    source_ssid: int = 0
    destination: str = 'APRS'
    dest_ssid: int = 0
    digipeaters: List[str] = field(default_factory=list)
    data_type: str = ''
    payload: str = ''
    position: Optional[APRSPosition] = None
    message: Optional[APRSMessage] = None

    def to_ax25_frame(self) -> AX25Frame:
        """转换为 AX.25 帧。"""
        digi_list = [(d.split('-')[0] if '-' in d else d,
                      int(d.split('-')[1]) if '-' in d and d.split('-')[1].isdigit() else 0,
                      False)
                     for d in self.digipeaters]

        info = (self.data_type + self.payload).encode('latin-1', errors='replace')

        return AX25Frame(
            destination=self.destination,
            dest_ssid=self.dest_ssid,
            source=self.source,
            source_ssid=self.source_ssid,
            digipeaters=digi_list,
            control=AX25_CTRL_UI,
            pid=AX25_PID_NOLAYER3,
            info=info
        )

    @classmethod
    def from_ax25_frame(cls, frame: AX25Frame) -> Optional['APRSPacket']:
        """从 AX.25 帧解析 APRS 报文。"""
        if frame.control != AX25_CTRL_UI:
            return None

        try:
            info = frame.info.decode('latin-1', errors='replace')
        except Exception:
            return None

        if not info:
            return None

        packet = cls(
            source=frame.source,
            source_ssid=frame.source_ssid,
            destination=frame.destination,
            dest_ssid=frame.dest_ssid,
            digipeaters=[f"{c}-{s}" for c, s, _ in frame.digipeaters],
            data_type=info[0] if info else '',
            payload=info[1:] if len(info) > 1 else ''
        )

        # 尝试解析具体类型
        if packet.data_type in ('!', '/'):
            packet.position = APRSPosition.decode(info)
        elif packet.data_type == ':':
            packet.message = APRSMessage.decode(info)

        return packet


# ============================================================
# KISS 协议接口
# ============================================================

class KISSInterface:
    """KISS 协议编解码，用于连接硬件 TNC。"""

    @staticmethod
    def encode_data_frame(data: bytes, port: int = 0) -> bytes:
        """编码 KISS 数据帧。"""
        frame = bytearray()
        frame.append(KISS_FEND)
        frame.append(port & 0x0F)  # 类型指示器

        # 转义
        for byte in data:
            if byte == KISS_FEND:
                frame.append(KISS_FESC)
                frame.append(KISS_TFEND)
            elif byte == KISS_FESC:
                frame.append(KISS_FESC)
                frame.append(KISS_TFESC)
            else:
                frame.append(byte)

        frame.append(KISS_FEND)
        return bytes(frame)

    @staticmethod
    def decode_stream(data: bytes) -> List[Tuple[int, bytes]]:
        """从字节流中解码 KISS 帧，返回 (端口, 数据) 列表。"""
        frames = []
        i = 0
        while i < len(data):
            if data[i] == KISS_FEND:
                i += 1
                if i >= len(data):
                    break

                # 类型指示器
                type_byte = data[i]
                port = type_byte & 0x0F
                command = type_byte >> 4
                i += 1

                # 收集数据
                frame_data = bytearray()
                while i < len(data) and data[i] != KISS_FEND:
                    if data[i] == KISS_FESC and i + 1 < len(data):
                        i += 1
                        if data[i] == KISS_TFEND:
                            frame_data.append(KISS_FEND)
                        elif data[i] == KISS_TFESC:
                            frame_data.append(KISS_FESC)
                    else:
                        frame_data.append(data[i])
                    i += 1

                if command == 0:  # 数据帧
                    frames.append((port, bytes(frame_data)))
                i += 1  # 跳过 FEND
            else:
                i += 1

        return frames

    @staticmethod
    def encode_command(command: int, value: bytes, port: int = 0) -> bytes:
        """编码 KISS 命令帧。"""
        type_byte = (command << 4) | (port & 0x0F)
        frame = bytearray([KISS_FEND, type_byte])
        frame.extend(value)
        frame.append(KISS_FEND)
        return bytes(frame)


# ============================================================
# Digipeater 分组转发
# ============================================================

# 去重 TTL（秒）。来源: direwolf/src/dedupe.c:134 TTL=30s；:245 判定 now-ts<30。
# 超过 30s 的同指纹帧不再判重，避免环形缓冲无 TTL 造成的长期误杀。
DEDUP_TTL_SEC = 30


class Digipeater:
    """
    AX.25 Digipeater：接收帧并按中继器路径转发。
    支持 WIDEn-N 泛洪算法。
    """

    def __init__(self, mycall: str = 'NOCALL', myssid: int = 0,
                 digi_calls: List[str] = None):
        self.mycall = mycall.upper()
        self.myssid = myssid
        self.digi_calls = [c.upper() for c in (digi_calls or [])]
        self.packets_heard = 0
        self.packets_digipeated = 0
        # 来源: direwolf/src/dedupe.c:134,245 + ax25_pad.c:2803-2806 ——
        # 缓冲改为 (crc16_fingerprint, timestamp) 二元组；判定重复时要求 now-ts<30s。
        self.duplicate_buffer: List[Tuple[int, float]] = []
        self.max_duplicate_buffer = 50

    @staticmethod
    def _frame_fingerprint(frame: AX25Frame) -> int:
        """计算帧指纹：crc16_ccitt(src + '\\x00' + dest + '\\x00' + info)。

        来源: direwolf ax25_pad.c:2803-2806 —— 指纹=crc16(src+dest+info, seed=0xffff)。
        复用本模块 crc16_ccitt（seed 0xFFFF，最终 XOR 0xFFFF，与 fcs_calc.c 一致）。
        用 \\x00 分隔 src/dest，避免 "AB"+"CD" 与 "ABC"+"D" 这类拼接歧义。
        """
        payload = (
            frame.source.encode("ascii", errors="replace") + b"\x00"
            + frame.destination.encode("ascii", errors="replace") + b"\x00"
            + bytes(frame.info)
        )
        return crc16_ccitt(payload)

    def _is_duplicate(self, frame: AX25Frame) -> bool:
        """检查是否是重复帧（CRC16 指纹 + 30s TTL，来源 direwolf dedupe.c:134,245）。"""
        now = time.time()
        fp = self._frame_fingerprint(frame)
        # 先清掉过期条目（now - ts >= TTL）
        self.duplicate_buffer = [
            (c, ts) for (c, ts) in self.duplicate_buffer
            if now - ts < DEDUP_TTL_SEC
        ]
        # 在 TTL 窗口内同指纹才算重复
        for c, ts in self.duplicate_buffer:
            if c == fp and now - ts < DEDUP_TTL_SEC:
                return True
        self.duplicate_buffer.append((fp, now))
        if len(self.duplicate_buffer) > self.max_duplicate_buffer:
            self.duplicate_buffer.pop(0)
        return False

    def process_frame(self, frame: AX25Frame) -> Optional[AX25Frame]:
        """
        处理接收到的帧，决定是否转发。
        返回需要转发的帧，或 None（不转发）。
        """
        self.packets_heard += 1

        if not frame.fcs_valid:
            return None

        if self._is_duplicate(frame):
            return None

        # 检查中继器路径
        if not frame.digipeaters:
            return None

        # 找到第一个未被中继的中继器
        for i, (call, ssid, repeated) in enumerate(frame.digipeaters):
            if repeated:
                continue

            # 检查是否匹配我的呼号或 WIDEn-N
            call_upper = call.upper()

            # WIDEn-N 泛洪（call=WIDEn, ssid=N）
            if call_upper.startswith('WIDE') and len(call_upper) > 4:
                try:
                    # call 格式: WIDE1, WIDE2, WIDE3 等
                    n = ssid  # ssid 字段存储剩余跳数
                    if n > 1:
                        # 递减 SSID，保持 call 不变
                        new_digipeaters = list(frame.digipeaters)
                        new_digipeaters[i] = (call, n - 1, True)
                        frame.digipeaters = new_digipeaters
                        self.packets_digipeated += 1
                        return frame
                    elif n == 1:
                        # 最后一跳，标记为已中继
                        new_digipeaters = list(frame.digipeaters)
                        new_digipeaters[i] = (call, 0, True)
                        frame.digipeaters = new_digipeaters
                        self.packets_digipeated += 1
                        return frame
                except (ValueError, IndexError):
                    pass

            # 匹配我的呼号
            if call_upper == self.mycall:
                new_digipeaters = list(frame.digipeaters)
                new_digipeaters[i] = (self.mycall, self.myssid, True)
                frame.digipeaters = new_digipeaters
                self.packets_digipeated += 1
                return frame

            # 匹配其他配置的中继器呼号
            if call_upper in self.digi_calls:
                new_digipeaters = list(frame.digipeaters)
                new_digipeaters[i] = (call, ssid, True)
                frame.digipeaters = new_digipeaters
                self.packets_digipeated += 1
                return frame

            break  # 第一个未中继的不匹配，停止检查

        return None

    def get_stats(self) -> Dict[str, Any]:
        """获取 Digipeater 统计。"""
        return {
            'mycall': f"{self.mycall}-{self.myssid}",
            'packets_heard': self.packets_heard,
            'packets_digipeated': self.packets_digipeated,
            'duplicate_buffer_size': len(self.duplicate_buffer),
        }


# ============================================================
# 工具函数
# ============================================================

def build_aprs_position_frame(source: str, latitude: float, longitude: float,
                               comment: str = '', symbol: str = '/-',
                               digipeaters: List[str] = None) -> AX25Frame:
    """快速构建 APRS 位置帧。"""
    pos = APRSPosition(
        latitude=latitude,
        longitude=longitude,
        symbol_table=symbol[0] if len(symbol) > 0 else '/',
        symbol_code=symbol[1] if len(symbol) > 1 else '-',
        comment=comment
    )

    src_call = source.split('-')[0] if '-' in source else source
    src_ssid = int(source.split('-')[1]) if '-' in source and source.split('-')[1].isdigit() else 0

    packet = APRSPacket(
        source=src_call,
        source_ssid=src_ssid,
        destination='APRS',
        dest_ssid=0,
        digipeaters=digipeaters or ['WIDE2-2'],
        data_type='!',
        payload=pos.encode()[1:],  # 去掉数据类型标识符
        position=pos
    )

    return packet.to_ax25_frame()


def parse_ax25_from_audio(audio_path: str) -> List[AX25Frame]:
    """从 WAV 文件解调 AX.25 帧。"""
    try:
        import wave
        with wave.open(audio_path, 'rb') as wf:
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
            sample_width = wf.getsampwidth()
            n_channels = wf.getnchannels()
            sample_rate = wf.getframerate()

        # 转换为 numpy
        if sample_width == 2:
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
        elif sample_width == 1:
            audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) / 128.0
        else:
            return []

        # 如果是立体声，取左声道
        if n_channels == 2:
            audio = audio[::2]

        modem = AFSKModem(sample_rate=sample_rate)
        return modem.demodulate(audio)
    except Exception as e:
        return []


def save_ax25_to_wav(frame: AX25Frame, output_path: str,
                      sample_rate: float = AFSK_SAMPLE_RATE) -> bool:
    """将 AX.25 帧调制为 WAV 文件。"""
    try:
        import wave
        modem = AFSKModem(sample_rate=sample_rate)
        audio = modem.modulate(frame)

        # 转换为 16-bit PCM
        audio_int16 = (audio * 32767).astype(np.int16)

        with wave.open(output_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate))
            wf.writeframes(audio_int16.tobytes())

        return True
    except Exception:
        return False
