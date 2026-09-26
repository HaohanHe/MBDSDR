"""
MBDSDR - ACARS 协议编解码模块（自包含，不依赖 acars_decoder.py）
=================================================================

本模块与已存在的 ``mbdsdr_ai/acars_decoder.py`` 并存，互不 import、互不耦合。
它提供一对自包含的合成往返工具：

    acars_encode(mode, reg, label, text, baud) -> np.ndarray
    acars_decode(audio, sample_rate, baud) -> List[dict]

空中接口要点（参考 repos/ 下的 C 实现）：
  - 调制：MSK = 连续相位 FSK，mark=2400 Hz, space=1200 Hz, 中心=1800 Hz
      参考: repos/acarsdec/msk.c:81  VCO 中心 1800.0/INTRATE*2π
            repos/acarsdec/msk.c:25  FLEN=(INTRATE/1200)+1
  - 波特率：默认 2400 bps（空中标准），同时兼容 1200 bps
  - 比特顺序：LSB 先发
      参考: repos/acarsdec/msk.c:53-63  putbit() 右移把首比特放到 bit0
  - 帧结构（空中字节流）：
        SYN(0x16) SYN(0x16) SOH(0x01)
        mode(1) reg(7) ack(1) label(2) block_id(1)
        [STX(0x02)] text...
        ETX(0x83) / ETB(0x97)
        crc_lo crc_hi
        DEL(0x7f)
      参考: repos/acarsdec/acars.c:22-27  控制字符宏
            repos/acarsdec/acars.c:246-375  帧同步状态机
            repos/libacars/libacars/acars.c:272-385  字段解析
  - 偶校验：每个 7-bit 数据字节的 bit7 置位，使 8 位中 1 的个数为偶数；
      解码时与 0x7f 剥离
      参考: repos/libacars/libacars/acars.c:302-304  buf[i] & 0x7f
  - 块校验：CRC-16-CCITT（多项式 0x1021，初值 0x0000，右移查表），
      对 [SOH 之后..ETX/ETB] + crc_lo + crc_hi 求余数 == 0 即通过
      参考: repos/libacars/libacars/crc.c:73-115  查表
            repos/acarsdec/syndrom.h:49  update_crc 宏
            repos/libacars/libacars/acars.c:296-299  crc_ok = (crc == 0)

注意：
  - 本模块仅用于合成信号往返测试，不冒充任何真实空中接收数据。
  - 测试用的注册号/标签均为虚构值，不对应任何真实航空器。
"""

from __future__ import annotations

import math
from typing import List, Optional

import numpy as np

# ─────────────────────────────────────────────────────────────────────
# 协议常量（参考 repos/acarsdec/acars.c:22-27）
# ─────────────────────────────────────────────────────────────────────
SYN = 0x16   # 同步字（连续两个）           acars.c:22
SOH = 0x01   # 帧起始                       acars.c:23
STX = 0x02   # 文本起始                     acars.c:24
ETX = 0x83   # 文本结束（最终块）           acars.c:25
ETB = 0x97   # 传输结束（非最终块）          acars.c:26
DEL = 0x7F   # 帧尾 DEL                     acars.c:27

# 去校验位后看到的控制字符（参考 libacars/acars.c:30-35）
_LA_STX = 0x02
_LA_ETX = 0x03   # ETX & 0x7f
_LA_ETB = 0x17   # ETB & 0x7f
_LA_DEL = 0x7F

# 调制音调（参考 repos/acarsdec/msk.c:81 中心 1800 Hz，±600 Hz）
CENTER_FREQ_HZ = 1800.0
MARK_FREQ_HZ = 2400.0
SPACE_FREQ_HZ = 1200.0

# 默认采样率（合成音频用；真实接收链路可传入任意采样率）
DEFAULT_SAMPLE_RATE = 48000.0
DEFAULT_BAUD = 2400


# ─────────────────────────────────────────────────────────────────────
# CRC-16-CCITT 右移查表
#   参考 repos/libacars/libacars/crc.c:73-115
#   poly=0x1021, init=0x0000, crc=(crc>>8)^table[(crc^byte)&0xff]
# ─────────────────────────────────────────────────────────────────────
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
    """CRC-16-CCITT 右移版本。参考 repos/libacars/libacars/crc.c:110-114."""
    crc = crc_init & 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
        crc &= 0xFFFF
    return crc


# ─────────────────────────────────────────────────────────────────────
# 偶校验与字节/比特转换
# ─────────────────────────────────────────────────────────────────────
def _even_parity(byte7: int) -> int:
    """给 7-bit 数据字节加偶校验位 bit7，使 8 位中 1 的个数为偶数。

    解码时与 0x7f 剥离（参考 libacars/acars.c:302-304）。
    """
    b = byte7 & 0x7F
    if bin(b).count('1') & 1:
        b |= 0x80
    return b


def _bytes_to_bits_lsb(data: bytes) -> List[int]:
    """字节流展开为 LSB-first 比特序列（发送顺序）。

    参考 repos/acarsdec/msk.c:53-63 putbit()：先收到的 bit 落在 bit0。
    """
    bits: List[int] = []
    for b in data:
        for j in range(8):
            bits.append((b >> j) & 1)
    return bits


def _bits_to_bytes_lsb(bits: List[int]) -> List[int]:
    """LSB-first 比特序列按 8 位组字节。"""
    out: List[int] = []
    for i in range(0, len(bits) - 7, 8):
        b = 0
        for j in range(8):
            b |= (bits[i + j] & 1) << j
        out.append(b & 0xFF)
    return out


# ─────────────────────────────────────────────────────────────────────
# 帧构造（字节级）
# ─────────────────────────────────────────────────────────────────────
def build_frame_bytes(mode: str, reg: str, label: str, text: str,
                      ack: str = '_', block_id: str = '0',
                      final_block: bool = True) -> bytes:
    """构造完整空中字节流（含 SYN/SOH/STX/ETX/CRC/DEL）。

    字段顺序参考 repos/libacars/libacars/acars.c:323-385。
    数据字段加偶校验位；控制字符按线上原值发送。
    """
    body = bytearray()
    # mode
    body.append(_even_parity(ord(mode[0])))
    # reg 7 字符（不足补空格）
    reg7 = reg[:7].ljust(7)
    body.extend(_even_parity(ord(c)) for c in reg7)
    # ack
    body.append(_even_parity(ord(ack[0])))
    # label 2 字符
    lbl2 = label[:2].ljust(2)
    body.extend(_even_parity(ord(c)) for c in lbl2)
    # block_id
    body.append(_even_parity(ord(block_id[0])))
    # STX
    body.append(STX)
    # 文本
    body.extend(_even_parity(ord(c)) for c in text)
    # ETX/ETB
    body.append(ETX if final_block else ETB)
    # CRC：对 body（=SOH 之后到 ETX/ETB）求 CRC；低字节先发，高字节次发
    # 经验证：body + crc_lo + crc_hi 的 CRC 余数 == 0（右移 CCITT 查表）
    crc = crc16_ccitt(bytes(body), 0x0000)
    body.append(crc & 0xFF)         # crc_lo
    body.append((crc >> 8) & 0xFF)  # crc_hi
    # DEL 帧尾
    body.append(DEL)
    # 前导：SYN SYN SOH
    frame = bytes([SYN, SYN, SOH]) + bytes(body)
    return frame


# ─────────────────────────────────────────────────────────────────────
# MSK 调制：比特 -> 连续相位 FSK 音频
#   bit1 -> mark 2400 Hz, bit0 -> space 1200 Hz，相位连续
#   参考 repos/acarsdec/msk.c:81 中心 1800 Hz
# ─────────────────────────────────────────────────────────────────────
def _msk_modulate(bits: List[int], sample_rate: float, baud: int,
                  amplitude: float = 0.8) -> np.ndarray:
    spb = sample_rate / float(baud)
    n_total = int(round(len(bits) * spb))
    out = np.zeros(n_total, dtype=np.float64)
    phase = 0.0
    for i, b in enumerate(bits):
        f = MARK_FREQ_HZ if b else SPACE_FREQ_HZ
        start = int(round(i * spb))
        end = int(round((i + 1) * spb))
        n = end - start
        if n <= 0:
            continue
        t = np.arange(n, dtype=np.float64) / sample_rate
        out[start:end] = amplitude * np.cos(phase + 2.0 * math.pi * f * t)
        # 更新相位，保持连续
        phase = (phase + 2.0 * math.pi * f * n / sample_rate) % (2.0 * math.pi)
    return out


def acars_encode(mode: str, reg: str, label: str, text: str,
                 baud: int = DEFAULT_BAUD,
                 sample_rate: float = DEFAULT_SAMPLE_RATE,
                 ack: str = '_', block_id: str = '0',
                 final_block: bool = True,
                 amplitude: float = 0.8,
                 preamble_pad: float = 0.05) -> np.ndarray:
    """合成一段 ACARS MSK 音频。

    参数：
        mode: 1 字符模式（如 '2'）
        reg:  飞机注册号（7 字符，不足补空格；测试用虚构值）
        label: 2 字符标签（如 'H1'）
        text: 消息文本
        baud: 波特率，默认 2400，兼容 1200
        sample_rate: 采样率，默认 48000 Hz
        preamble_pad: 帧前后填充静音秒数（便于滤波器建立）

    返回：
        float64 一维 numpy 数组（归一化到约 ±amplitude）
    """
    frame = build_frame_bytes(mode, reg, label, text,
                             ack=ack, block_id=block_id,
                             final_block=final_block)
    bits = _bytes_to_bits_lsb(frame)
    sig = _msk_modulate(bits, sample_rate, baud, amplitude=amplitude)
    # 前后补静音
    pad = int(preamble_pad * sample_rate)
    out = np.concatenate([np.zeros(pad), sig, np.zeros(pad)])
    return out.astype(np.float64)


# ─────────────────────────────────────────────────────────────────────
# MSK 解调：实数音频 -> 比特序列
#   参考 repos/acarsdec/msk.c:86-126：
#     mixer 下变频到中心 1800 Hz，匹配滤波后做 mark/space 判决。
#   这里用纯 numpy 实现非相干 FSK 能量检测：
#     分别与 mark(2400)/space(1200) 两个本振做相关，
#     在一个比特窗口上取模，能量大者判决。
#     等价于 msk.c:102-107 的匹配滤波（对 mark/space 各做一次相关）。
# ─────────────────────────────────────────────────────────────────────
def _fsk_discriminate(audio: np.ndarray, sample_rate: float,
                      baud: int) -> np.ndarray:
    """非相干 FSK 能量判决：返回逐采样的"mark 能量 - space 能量"。

    正值=mark(1)，负值=space(0)。用滑动相关实现，窗口约 1 个比特周期。
    """
    audio = np.asarray(audio, dtype=np.float64)
    n = len(audio)
    if n < 32:
        return np.zeros(0)
    t = np.arange(n, dtype=np.float64) / sample_rate
    # 与 mark/space 本振做复数相关
    z_mark = audio * np.exp(-1j * 2.0 * math.pi * MARK_FREQ_HZ * t)
    z_space = audio * np.exp(-1j * 2.0 * math.pi * SPACE_FREQ_HZ * t)
    # 滑动窗口能量（移动平均），窗口宽度 = 1 个比特周期
    spb = sample_rate / float(baud)
    k = max(2, int(spb))
    kernel = np.ones(k, dtype=np.float64) / float(k)
    e_mark = np.abs(np.convolve(z_mark, kernel, mode='same'))
    e_space = np.abs(np.convolve(z_space, kernel, mode='same'))
    return (e_mark - e_space).astype(np.float64)


def _sample_bits(dphi: np.ndarray, sample_rate: float, baud: int,
                 phase_offset: float) -> List[int]:
    """在能量判决序列上按 bit 周期采样，返回比特列表。

    phase_offset ∈ [0,1)：比特相位偏移，用于搜索最佳对齐。
    采样点落在 bit 中心（半周期处），参考 msk.c:96-100 的位时钟判决。
    """
    spb = sample_rate / float(baud)
    if len(dphi) < int(spb):
        return []
    bits: List[int] = []
    # 采样点：半周期中心 + phase_offset*spb，每 spb 采一个
    idx = int(round(spb / 2.0 + phase_offset * spb))
    while idx < len(dphi):
        # 在采样点附近取小窗口平均，抗噪
        lo = max(0, idx - 2)
        hi = min(len(dphi), idx + 3)
        s = float(np.mean(dphi[lo:hi]))
        bits.append(1 if s >= 0 else 0)
        idx += int(round(spb))
    return bits


# ─────────────────────────────────────────────────────────────────────
# 帧同步状态机（字节流）
#   参考 repos/acarsdec/acars.c:246-375
#   WSYN -> SYN2 -> SOH1 -> TXT -> CRC1 -> CRC2
# ─────────────────────────────────────────────────────────────────────
def _extract_frames(bytes_stream: List[int]) -> List[bytes]:
    """在字节流中扫描 SYN SYN SOH 前导，抽取完整帧（SOH 之后的 body）。

    返回每个帧的 body（含 ETX/ETB + crc + DEL，不含 SYN/SOH 本身）。
    """
    frames: List[bytes] = []
    i = 0
    n = len(bytes_stream)
    while i < n - 3:
        # 找 SYN SYN SOH
        if (bytes_stream[i] == SYN and
                bytes_stream[i + 1] == SYN and
                bytes_stream[i + 2] == SOH):
            j = i + 3
            body = bytearray()
            seen_etx = False
            crc_count = 0
            # 收集到 DEL（帧尾）为止
            while j < n:
                b = bytes_stream[j]
                body.append(b)
                if b == ETX or b == ETB:
                    seen_etx = True
                elif seen_etx and crc_count < 2:
                    crc_count += 1
                    if crc_count == 2:
                        # 接下来应是 DEL
                        if j + 1 < n and bytes_stream[j + 1] == DEL:
                            body.append(DEL)
                            j += 1
                        break
                elif len(body) > 300:
                    # 超时保护
                    break
                j += 1
            if seen_etx and crc_count == 2:
                frames.append(bytes(body))
            i = j + 1
        else:
            i += 1
    return frames


# ─────────────────────────────────────────────────────────────────────
# 字段解析
#   参考 repos/libacars/libacars/acars.c:272-385
# ─────────────────────────────────────────────────────────────────────
def _parse_frame(body: bytes) -> Optional[dict]:
    """解析一帧 body（SOH 之后，含 ETX/ETB + crc + DEL）。

    步骤（参考 libacars/acars.c:290-385）：
      1) 去掉末尾 DEL
      2) 对剩余字节（含 2 字节 CRC）求 CRC16，余数应为 0
      3) 去掉 2 字节 CRC
      4) 逐字节 &0x7f 去偶校验位
      5) 末尾应是 ETX(0x03)/ETB(0x17)
      6) 依次取 mode(1) reg(7) ack(1) label(2) block_id(1) [STX] text
    """
    if len(body) < 16:
        return None
    buf = bytearray(body)
    # 1) 去末尾 DEL
    if buf[-1] == _LA_DEL:
        buf = buf[:-1]
    # 2) CRC 校验
    crc_residue = crc16_ccitt(bytes(buf), 0x0000)
    crc_ok = (crc_residue == 0)
    if len(buf) < 2:
        return None
    # 3) 去掉 2 字节 CRC
    payload = bytes(buf[:-2])
    # 4) 去偶校验位
    stripped = bytes(b & 0x7F for b in payload)
    # 5) 末尾 ETX/ETB
    if stripped[-1] == _LA_ETX:
        final = True
    elif stripped[-1] == _LA_ETB:
        final = False
    else:
        return None
    s = stripped[:-1]
    if len(s) < 12:
        return None
    # 6) 字段
    i = 0
    mode = chr(s[i]); i += 1
    reg = s[i:i + 7].decode('ascii', errors='replace'); i += 7
    ack = s[i]; i += 1
    ack_str = chr(ack)
    label = s[i:i + 2].decode('ascii', errors='replace'); i += 2
    block_id = chr(s[i]); i += 1
    # STX
    text = ""
    if i < len(s):
        if s[i] == _LA_STX:
            i += 1
        rest = s[i:]
        text = ''.join(chr(b) if 32 <= b < 127 else '.' for b in rest)
    return {
        "mode": mode,
        "reg": reg,
        "ack": ack_str,
        "label": label,
        "block_id": block_id,
        "text": text,
        "crc_ok": crc_ok,
        "final_block": final,
    }


# ─────────────────────────────────────────────────────────────────────
# 顶层解码入口
# ─────────────────────────────────────────────────────────────────────
def acars_decode(audio: np.ndarray,
                 sample_rate: float = DEFAULT_SAMPLE_RATE,
                 baud: int = DEFAULT_BAUD) -> List[dict]:
    """从一段实数音频中解码 ACARS 消息。

    参数：
        audio: 实数音频数组（FM 解调后的基带走带音频）
        sample_rate: 采样率
        baud: 波特率（2400 或 1200）

    返回：
        解析成功的消息字典列表；每条含 mode/reg/ack/label/block_id/text/
        crc_ok/final_block。
    """
    audio = np.asarray(audio, dtype=np.float64)
    dphi = _fsk_discriminate(audio, sample_rate, baud)
    if len(dphi) < 32:
        return []

    # 尝试多种比特相位偏移、极性与字节对齐，找能解出 CRC 通过帧的那种
    # （MSK 解调存在 180° 相位模糊，需同时尝试比特取反；
    #   比特到字节的分组对齐也需尝试 8 种偏移）
    candidates: List[dict] = []
    best_crc_ok_count = -1
    found = False
    for off in range(16):
        phase_off = off / 16.0
        for invert in (False, True):
            bits = _sample_bits(dphi, sample_rate, baud, phase_off)
            if invert:
                bits = [1 - b for b in bits]
            if len(bits) < 64:
                continue
            # 尝试 8 种字节分组对齐
            for byte_off in range(8):
                bs = bits[byte_off:]
                bytes_stream = _bits_to_bytes_lsb(bs)
                frame_bodies = _extract_frames(bytes_stream)
                parsed: List[dict] = []
                for body in frame_bodies:
                    m = _parse_frame(body)
                    if m is not None:
                        parsed.append(m)
                crc_ok_count = sum(1 for m in parsed if m["crc_ok"])
                if crc_ok_count > best_crc_ok_count:
                    best_crc_ok_count = crc_ok_count
                    candidates = parsed
                if crc_ok_count > 0:
                    found = True
                    break
            if found:
                break
        if found:
            break
    return candidates
