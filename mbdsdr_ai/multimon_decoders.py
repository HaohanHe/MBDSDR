# SPDX-License-Identifier: MIT
"""
常用数字模式解码器（独立实现）
================================

本模块依据公开的业余/广播协议规格独立实现以下解码器：

  * POCSAGDecoder   — POCSAG 寻呼解码（512/1200/2400bps），BCH(31,21,2) 纠错
                      （CCIR Radiopaging Code No.1 公开标准）
  * AFSK1200Demod   — Bell-202 AFSK 1200bps 解调（mark 1200 / space 2200 Hz）
  * DTMFDecoder     — DTMF 双音多频解码（Goertzel/正交能量法，ITU-T Q.23）
  * ZVEIDecoder     — ZVEI-1 selcall 选呼解码（16 音调序列，EBU 规格）

算法：POCSAG 用查表法 BCH(31,21)；AFSK 用正交相关（cos/sin 本地振荡 × 接收
信号，取 I/Q 平方差）；DTMF/ZVEI 用正交能量分块判决。各协议的同步字、
生成多项式、音调表均为公开标准事实；本模块 DSP、结构与命名自行编写。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

# ======================================================================
#  POCSAG 常量
# ======================================================================

# 同步码字与空闲码字
POCSAG_SYNC = 0x7CD215D8   #
POCSAG_IDLE = 0x7A89C197   #

# 消息/地址标志位：codeword bit31=1 表示消息码字
POCSAG_MESSAGE_DETECTION = 0x80000000  #

# BCH(31,21,2) 参数（
BCH_DATA_BITS = 21          #
BCH_PARITY_BITS = 10        #
BCH_CODE_LEN = 31           #
# POCSAG 生成多项式（八进制 03551）——
# 注：等价于传统 CCIR 记法 G(x)=x^10+x^8+x^6+x^5+x^4+x^2+1，即 0xED2000 = 0x769<<13
POCSAG_POLY = 0x769         #

# POCSAG 波特率（
POCSAG_BAUD = {"512": 512, "1200": 1200, "2400": 2400}

# 数字消息 BCD 转换表
# nibble 0..15 -> 字符；空用空格 ' '
_POCSAG_NUMERIC_TABLE = "084 2.6]195-3U7["  #


# ======================================================================
#  BCH(31,21,2) for POCSAG ——
# ======================================================================
class _POCSAGBCH:
    """POCSAG BCH(31,21,2) 编/纠错。

    32-bit 码字布局：
      bits 31..11 : 21 位数据
      bits 10..1  : 10 位 BCH 奇偶
      bit 0       : 整体偶校验

    可纠正最多 2 bit 错误。
    """

    def __init__(self) -> None:
        # 单数据位 -> 10 位 BCH 奇偶（
        self._parity_tbl = self._build_parity_table()
        # 单 bit 位置 -> 10 位 BCH 伴随式（
        self._syn_tbl = self._build_syndrome_table()
        # 伴随式 -> 错误图样（
        self._err_tbl = self._build_error_table()

    @staticmethod
    def _polynomial_div_parity(databit: int) -> int:
        """对单个数据位 databit 做多项式除法求 BCH 奇偶。


            shreg = 1u << (databit + BCH_PARITY_BITS);
            for i in 20..0:
                if shreg & (1u << (i + BCH_PARITY_BITS)):
                    shreg ^= (POCSAG_POLY << i);
            return shreg & 0x3FF;
        """
        shreg = 1 << (databit + BCH_PARITY_BITS)
        for i in range(BCH_DATA_BITS - 1, -1, -1):
            if shreg & (1 << (i + BCH_PARITY_BITS)):
                shreg ^= (POCSAG_POLY << i)
        return shreg & 0x3FF

    def _build_parity_table(self) -> List[int]:
        return [self._polynomial_div_parity(d) for d in range(BCH_DATA_BITS)]

    def _build_syndrome_table(self) -> List[int]:
        """31 个单 bit 位置各自的 BCH 伴随式。"""
        tbl = [0] * 32
        for bit in range(31):
            shreg = 1 << bit
            for i in range(BCH_DATA_BITS - 1, -1, -1):
                if shreg & (1 << (i + BCH_PARITY_BITS)):
                    shreg ^= (POCSAG_POLY << i)
            tbl[bit] = shreg & 0x3FF
        return tbl

    def _build_error_table(self) -> List[int]:
        """伴随式(11bit) -> 错误图样(32bit)；0 表示无错/不可纠。


          单 bit 错误（bit 1..31，不含 bit0 偶校验位）：
            syn = syn_tbl[i-1] | 0x400   # 单 bit 必致偶校验错
          双 bit 错误：两单 bit 伴随式异或（偶校验位抵消）
        """
        err = [0] * 2048  # 11-bit 伴随式空间
        # 单 bit
        for i in range(1, 32):
            syn = self._syn_tbl[i - 1] | 0x400
            err[syn] = 1 << i
        # 双 bit
        for i in range(1, 32):
            for j in range(i + 1, 32):
                syn = self._syn_tbl[i - 1] ^ self._syn_tbl[j - 1]
                if err[syn] == 0:
                    err[syn] = (1 << i) | (1 << j)
        return err

    @staticmethod
    def _even_parity(x: int) -> int:
        """32-bit 偶校验。"""
        x ^= x >> 16
        x ^= x >> 8
        x ^= x >> 4
        x ^= x >> 2
        x ^= x >> 1
        return x & 1

    def encode(self, data21: int) -> int:
        """21 位数据 -> 32 位 POCSAG 码字。"""
        d = data21 & 0x1FFFFF
        parity = 0
        tmp = d
        while tmp:
            bit = (tmp & -tmp).bit_length() - 1  # ctz
            parity ^= self._parity_tbl[bit]
            tmp &= tmp - 1
        cw = (d << (BCH_PARITY_BITS + 1)) | (parity << 1)
        cw |= self._even_parity(cw)
        return cw & 0xFFFFFFFF

    def _syndrome(self, cw: int) -> int:
        """计算 11-bit 伴随式。"""
        syn = 0
        bits = cw >> 1  # 去掉 bit0 偶校验位，对 bits1..31 求 BCH 伴随
        while bits:
            bit = (bits & -bits).bit_length() - 1
            syn ^= self._syn_tbl[bit]
            bits &= bits - 1
        if self._even_parity(cw):
            syn |= 0x400
        return syn

    def correct(self, cw: int) -> Tuple[int, int]:
        """纠错。返回 (纠错后码字, 纠正 bit 数)；纠正数 -1 表示不可纠。

。
        """
        cw &= 0xFFFFFFFF
        syn = self._syndrome(cw)
        if syn == 0:
            return cw, 0
        error = self._err_tbl[syn]
        if error == 0:
            return cw, -1
        return (cw ^ error) & 0xFFFFFFFF, error.bit_count()


_BCH = _POCSAGBCH()


# ======================================================================
#  POCSAG 数据解码辅助 ——
# ======================================================================
def _rev7(b: int) -> int:
    """反转 7-bit 字的位序。"""
    return (
        (((b << 6) & 64) | ((b >> 6) & 1))
        | (((b << 4) & 32) | ((b >> 4) & 2))
        | (((b << 2) & 16) | ((b >> 2) & 4))
        | ((b & 8))
    ) & 0x7F


def _decode_numeric(buffer: bytes, numnibbles: int) -> str:
    """BCD 数字消息解码。

    只解 numnibbles 个 nibble。
    """
    out = []
    bp = 0
    n = numnibbles
    while n > 0:
        byte = buffer[bp]
        out.append(_POCSAG_NUMERIC_TABLE[(byte >> 4) & 0xF])
        n -= 1
        if n > 0:
            out.append(_POCSAG_NUMERIC_TABLE[byte & 0xF])
            n -= 1
        bp += 1
    return "".join(out)


def _decode_alpha(buffer: bytes, numnibbles: int) -> str:
    """ASCII 7-bit 文本消息解码。

    buffer 已按 5 nibbles/码字 累积；每 4 个 nibble(=20bit) 凑成 7-bit 字符。
    """
    # numnibbles*4/7 个 7-bit 字符
    nchars = numnibbles * 4 // 7
    out = []
    for n in range(nchars):
        # get7: 返回第 n 个 7-bit 字
        #   return ( buf[(n*7)/8]<<8 | buf[(n*7+6)/8] ) >> (n+1)%8
        byte0 = buffer[(n * 7) // 8]
        byte1 = buffer[(n * 7 + 6) // 8]
        word = ((byte0 << 8) | byte1) >> ((n + 1) % 8)
        c = _rev7(word & 0x7F)
        if 0x20 <= c < 0x7F:
            out.append(chr(c))
        elif c == 0x0D:
            out.append("\n")
    return "".join(out)


# ======================================================================
#  POCSAG 批构造器（编码器，便于往返测试）
# ======================================================================
@dataclass
class PocsagWord:
    """批内一个 32-bit 码字。"""
    codeword: int
    kind: str = "data"  # sync / data


def pocsag_encode_address_word(addr: int, func: int) -> int:
    """构造地址码字。


      function = (cw >> 11) & 3
      address  = ((cw >> 10) & 0x1FFFF8) | ((rxword >> 1) & 7)
    即数据字段 bits18:2 = address>>3（17bit），bits1:0 = func，bit20(R)=0。
    """
    data21 = ((addr >> 3) << 2) | (func & 3)
    return _BCH.encode(data21)


def pocsag_encode_message_word(payload20: int) -> int:
    """构造消息码字：R=1（bit31），后接 20 位数据。

) 与
    。
    """
    data21 = 0x100000 | (payload20 & 0xFFFFF)  # bit20=1, bits19:0=data
    return _BCH.encode(data21)


def pocsag_build_batch(words_after_sync: List[int]) -> List[int]:
    """组装一批：[SYNC, word1..word16]，不足补 idle。

。
    """
    batch = [POCSAG_SYNC]
    batch.extend(words_after_sync[:16])
    while len(batch) < 17:
        batch.append(POCSAG_IDLE)
    return batch


# ======================================================================
#  POCSAGDecoder —— 帧同步字状态机
# ======================================================================
@dataclass
class PocsagMessage:
    address: int
    function: int
    numeric: str = ""
    alpha: str = ""


class POCSAGDecoder:
    """POCSAG 批/码字解码器。

    输入一批 32-bit 码字（已位同步），运行与 pocsag.c do_one_bit 相同的
    状态机（NO_SYNC/SYNC/ADDRESS/MESSAGE/END_OF_MESSAGE），输出消息列表。
    也接受原始 bit 流做同步搜索。
    """

    # 状态常量
    NO_SYNC = 0
    SYNC = 64
    LOSING_SYNC = 65
    LOST_SYNC = 66
    ADDRESS = 67
    MESSAGE = 68
    END_OF_MESSAGE = 69

    def __init__(self, error_correction: int = 2) -> None:
        self.error_correction = error_correction  #
        self.reset()

    def reset(self) -> None:
        self._state = self.NO_SYNC
        self._rx_word = 0
        self._inverted = 0
        self.address = -1
        self.function = -1
        self._buf = bytearray(512)  #
        self._numnibbles = 0
        self.messages: List[PocsagMessage] = []
        self.corrected_1bit = 0
        self.corrected_2bit = 0
        self.uncorrectable = 0

    # -- 批/码字级解码 --
    def feed_word(self, rxword_idx: int, cw: int) -> None:
        """喂入批内第 rxword_idx 个字（0=sync），cw=32bit。

) switch 语义：
          ADDRESS: 地址字 -> 记录 address/function -> MESSAGE；消息字 -> 部分解码。
          MESSAGE: 消息字 -> 累积 nibbles；地址/空闲字 -> 结束并输出本消息。
        为避免递归，结束消息后把本地址字内联作为新地址处理（不重复喂）。
        """
        cw, nerr = _BCH.correct(cw)
        if nerr == 1:
            self.corrected_1bit += 1
        elif nerr == 2:
            self.corrected_2bit += 1
        elif nerr < 0:
            self.uncorrectable += 1

        # 同步检测（
        if cw == POCSAG_SYNC:
            self._state = self.ADDRESS
            self._rx_word = 0
            return
        if self._state not in (self.ADDRESS, self.MESSAGE):
            return

        # 空闲字：若正在收消息则结束；空闲字本身不携带地址
        if cw == POCSAG_IDLE:
            if self._state == self.MESSAGE:
                self._emit()
                self._state = self.ADDRESS
            return

        is_msg = bool(cw & POCSAG_MESSAGE_DETECTION)  #

        # —— ADDRESS 状态 ——
        if self._state == self.ADDRESS:
            if is_msg:
                # 无前导地址的消息
                self.function = -2
                self.address = -2
            else:
                self.function = (cw >> 11) & 3          #
                self.address = ((cw >> 10) & 0x1FFFF8) | ((rxword_idx >> 1) & 7)
            self._state = self.MESSAGE
            # 落到 MESSAGE 分支处理本字
        # —— MESSAGE 状态 ——
        if self._state == self.MESSAGE:
            if is_msg:
                data = (cw >> 11) & 0x1FFFFF              #
                bp = self._numnibbles >> 1
                if self._numnibbles & 1:
                    self._buf[bp] = (self._buf[bp] & 0xF0) | ((data >> 16) & 0xF)
                    self._buf[bp + 1] = (data >> 8) & 0xFF
                    self._buf[bp + 2] = data & 0xFF
                else:
                    self._buf[bp] = (data >> 12) & 0xFF
                    self._buf[bp + 1] = (data >> 4) & 0xFF
                    self._buf[bp + 2] = (data << 4) & 0xFF
                self._numnibbles += 5
                return
            else:
                # 地址字结束上一条消息（
                self._emit()
                self._state = self.ADDRESS
                # 本地址字内联作为新地址开始下一条消息
                self.function = (cw >> 11) & 3
                self.address = ((cw >> 10) & 0x1FFFF8) | ((rxword_idx >> 1) & 7)
                self._state = self.MESSAGE
                return

    def _emit(self) -> None:
        if self._numnibbles == 0:
            return
        numeric = _decode_numeric(bytes(self._buf), self._numnibbles)
        alpha = _decode_alpha(bytes(self._buf), self._numnibbles)
        self.messages.append(PocsagMessage(
            address=self.address, function=self.function,
            numeric=numeric, alpha=alpha.strip(),
        ))
        self._numnibbles = 0
        self.address = -1
        self.function = -1

    def decode_batch(self, batch: List[int]) -> List[PocsagMessage]:
        """解码一整批（17 字）。"""
        self.reset()
        self._state = self.ADDRESS  # 批以 SYNC 开头，跳过同步搜索
        for idx, cw in enumerate(batch):
            self.feed_word(idx, cw)
        self._emit()
        return self.messages

    # -- bit 流同步搜索 --
    def sync_search(self, bits: np.ndarray) -> List[int]:
        """在 0/1 bit 流中找同步码字起始位置（MSB 先入）。


        """
        positions = []
        reg = 0
        for i, b in enumerate(bits):
            reg = ((reg << 1) | (1 if b == 0 else 0)) & 0xFFFFFFFF
            if reg == POCSAG_SYNC:
                positions.append(i - 31)
        return positions


# ======================================================================
#  AFSK 1200 (Bell-202) 解调
# ======================================================================
class AFSK1200Demod:
    """Bell-202 AFSK 1200bps 正交相关解调。

    常量：
      FREQ_MARK  = 1200 Hz
      FREQ_SPACE = 2200 Hz
      BAUD       = 1200
    相关窗长 CORRLEN = FREQ_SAMP/BAUD。
    """

    MARK = 1200    #
    SPACE = 2200   #
    BAUD = 1200    #

    def __init__(self, sample_rate: float = 22050.0) -> None:
        self.fs = sample_rate
        self.corlen = max(1, int(round(self.fs / self.BAUD)))  #

    def modulate(self, bits: np.ndarray) -> np.ndarray:
        """把 0/1 比特流调制成 AFSK 音频（bit1=mark, bit0=space）。"""
        spb = self.fs / self.BAUD  # samples per bit
        n = int(len(bits) * spb)
        t = np.arange(n) / self.fs
        freq = np.where(bits == 1, self.MARK, self.SPACE)
        # 每个 bit 一段恒定频率相位
        seg = np.repeat(freq, int(round(spb)))
        if len(seg) < n:
            seg = np.pad(seg, (0, n - len(seg)), constant_values=self.SPACE)
        seg = seg[:n]
        phase = 2 * np.pi * np.cumsum(seg) / self.fs
        return np.cos(phase).astype(np.float32)

    def demodulate(self, audio: np.ndarray) -> np.ndarray:
        """正交相关解调，恢复 0/1 比特序列。

        位同步照搬 C 版 sphase 机制：在 dcd 跳变处微调相位，
        sphase 溢出时采一个比特。
        """
        n = len(audio)
        k = np.arange(self.corlen)
        wm = 2 * np.pi * self.MARK / self.fs
        ws = 2 * np.pi * self.SPACE / self.fs
        mark_i = np.cos(wm * k)
        mark_q = np.sin(wm * k)
        space_i = np.cos(ws * k)
        space_q = np.sin(ws * k)

        # f = |corr_mark|^2 - |corr_space|^2（
        cm_i = np.convolve(audio, mark_i[::-1], mode="valid")
        cm_q = np.convolve(audio, mark_q[::-1], mode="valid")
        cs_i = np.convolve(audio, space_i[::-1], mode="valid")
        cs_q = np.convolve(audio, space_q[::-1], mode="valid")
        f = (cm_i ** 2 + cm_q ** 2) - (cs_i ** 2 + cs_q ** 2)
        dcd = (f > 0).astype(np.int32)

        # 跳变沿时钟恢复（
        spb = self.fs / self.BAUD
        sphase_inc = 0x10000 / spb
        sphase = 0
        bits: List[int] = []
        prev = int(dcd[0]) if len(dcd) else 0
        for i in range(1, len(dcd)):
            cur = int(dcd[i])
            # 跳变处微调时钟（±1/8 步长）
            if (cur ^ prev) & 1:
                if sphase < (0x8000 - sphase_inc / 2):
                    sphase += sphase_inc / 8
                else:
                    sphase -= sphase_inc / 8
            sphase += sphase_inc
            if sphase >= 0x10000:
                sphase -= 0x10000
                bits.append(cur)
            prev = cur
        return np.array(bits, dtype=np.uint8)


# ======================================================================
#  DTMF 解码
# ======================================================================
class DTMFDecoder:
    """DTMF 双音多频解码。

    频率表：
      高频组: 1209, 1336, 1477, 1633
      低频组:  697,  770,  852,  941
    字符表 "123A456B789C*0#D"（
    """

    # 顺序：高4 + 低4（
    FREQS = [1209, 1336, 1477, 1633, 697, 770, 852, 941]
    TRANSL = "123A456B789C*0#D"  #

    def __init__(self, sample_rate: float = 22050.0) -> None:
        self.fs = sample_rate

    def encode(self, digit: str, dur: float = 0.1) -> np.ndarray:
        """合成单个 DTMF 双音。"""
        idx = self.TRANSL.index(digit)
        hi = self.FREQS[idx & 3]            # 高组索引（
        lo = self.FREQS[4 + (idx >> 2)]     # 低组索引
        n = int(self.fs * dur)
        t = np.arange(n) / self.fs
        return (np.sin(2 * np.pi * hi * t) + np.sin(2 * np.pi * lo * t)).astype(np.float32)

    def _energy(self, audio: np.ndarray, freq: float) -> float:
        """正交相关能量 = I^2+Q^2。"""
        n = len(audio)
        t = np.arange(n) / self.fs
        i = np.sum(audio * np.cos(2 * np.pi * freq * t))
        q = np.sum(audio * np.sin(2 * np.pi * freq * t))
        return float(i * i + q * q)

    def decode(self, audio: np.ndarray) -> Optional[str]:
        """对一段音频判决一个 DTMF 按键。"""
        e = [self._energy(audio, f) for f in self.FREQS]
        hi = e[0:4]
        lo = e[4:8]

        def _best(g: List[float]) -> int:
            best = int(np.argmax(g))
            if g[best] < 0.1 * max(g):  # 二次谐波抑制（
                return -1
            return best

        i = _best(hi)
        j = _best(lo)
        if i < 0 or j < 0:
            return None
        idx = i | (j << 2)  #
        return self.TRANSL[idx]


# ======================================================================
#  ZVEI-1 selcall 解码
# ======================================================================
class ZVEIDecoder:
    """ZVEI-1 选呼解码（16 音调，hex 0..F）。

    频率表：
      索引:  0    1    2    3    4    5    6    7    8    9    10   11  12   13   14   15
      Hz:  2400,1060,1160,1270,1400,1530,1670,1830,2000,2200,2800,810,970,885,2600,680
    选呼通常 5 个连续音调。
    """

    FREQS = [2400, 1060, 1160, 1270, 1400, 1530, 1670, 1830,
             2000, 2200, 2800, 810, 970, 885, 2600, 680]  #

    def __init__(self, sample_rate: float = 22050.0, tone_dur: float = 0.06) -> None:
        self.fs = sample_rate
        self.tone_dur = tone_dur  # ZVEI 单音 ~ 40-70ms

    def encode(self, code: str) -> np.ndarray:
        """把 5 位 hex 选呼码合成音调序列。"""
        segs = []
        for ch in code.upper():
            idx = int(ch, 16)
            n = int(self.fs * self.tone_dur)
            t = np.arange(n) / self.fs
            segs.append(np.sin(2 * np.pi * self.FREQS[idx] * t))
        return np.concatenate(segs).astype(np.float32)

    def _energy(self, audio: np.ndarray, freq: float) -> float:
        n = len(audio)
        t = np.arange(n) / self.fs
        i = np.sum(audio * np.cos(2 * np.pi * freq * t))
        q = np.sum(audio * np.sin(2 * np.pi * freq * t))
        return float(i * i + q * q)

    def decode(self, audio: np.ndarray, n_tones: int = 5) -> str:
        """把音频按 tone_dur 切 n_tones 段，逐段判决 hex 音调。"""
        per = int(self.fs * self.tone_dur)
        out = []
        for k in range(n_tones):
            seg = audio[k * per:(k + 1) * per]
            if len(seg) < per // 2:
                break
            e = [self._energy(seg, f) for f in self.FREQS]
            best = int(np.argmax(e))
            # 主音能量需远大于次音（
            srt = sorted(e, reverse=True)
            if srt[0] < 0.1 * (srt[1] + 1e-9) and srt[0] < srt[1] * 2:
                out.append("?")
            else:
                out.append(f"{best:X}")
        return "".join(out)


# ======================================================================
#  注册进 ToolRegistry
# ======================================================================
def register_multimon_tools(registry) -> None:
    """把本模块数字模式解码器注册成 AI 可调用工具。


    """
    bch = _POCSAGBCH()

    def _pocsag_decode(args):
        words = args.get("codewords", [])
        if not words:
            return {"success": False, "content": "需要 codewords 数组（32-bit 整数）"}
        dec = POCSAGDecoder()
        msgs = dec.decode_batch([int(w) & 0xFFFFFFFF for w in words])
        return {
            "success": True,
            "content": f"POCSAG 解码 {len(msgs)} 条消息",
            "messages": [
                {"address": m.address, "function": m.function,
                 "numeric": m.numeric, "alpha": m.alpha}
                for m in msgs
            ],
            "stats": {"corrected_1bit": dec.corrected_1bit,
                      "corrected_2bit": dec.corrected_2bit,
                      "uncorrectable": dec.uncorrectable},
        }

    def _pocsag_bch_correct(args):
        cw = int(args.get("codeword", 0)) & 0xFFFFFFFF
        fixed, n = bch.correct(cw)
        return {"success": True, "codeword_in": f"0x{cw:08X}",
                "codeword_out": f"0x{fixed:08X}", "errors_corrected": n}

    registry.register(
        name="pocsag_decode",
        description="POCSAG 寻呼解码：BCH(31,21)纠错，"
                    "同步字0x7CD215D8，17字/批，地址+数字(BCD)/文本(ASCII7bit)。",
        parameters={
            "type": "object",
            "properties": {
                "codewords": {"type": "array", "items": {"type": "integer"},
                              "description": "一批 17 个 32-bit 码字（0=sync）"},
            },
            "required": ["codewords"],
        },
        handler=_pocsag_decode,
        category="multimon",
    )
    registry.register(
        name="pocsag_bch_correct",
        description="POCSAG BCH(31,21,2) 单码字纠错（生成多项式0x769/八进制03551）。",
        parameters={
            "type": "object",
            "properties": {"codeword": {"type": "integer", "description": "32-bit 码字"}},
            "required": ["codeword"],
        },
        handler=_pocsag_bch_correct,
        category="multimon",
    )

    def _dtmf_decode(args):
        import numpy as _np
        audio = _np.array(args.get("samples", []), dtype=_np.float32)
        if audio.size == 0:
            return {"success": False, "content": "需要 samples 数组"}
        dec = DTMFDecoder(sample_rate=args.get("sample_rate", 22050))
        digit = dec.decode(audio)
        return {"success": digit is not None, "digit": digit}

    registry.register(
        name="dtmf_decode",
        description="DTMF 双音解码：8频(697/770/852/941 + 1209/1336/1477/1633)，正交能量法。",
        parameters={
            "type": "object",
            "properties": {
                "samples": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number", "default": 22050},
            },
            "required": ["samples"],
        },
        handler=_dtmf_decode,
        category="multimon",
    )

    def _zvei_decode(args):
        import numpy as _np
        audio = _np.array(args.get("samples", []), dtype=_np.float32)
        if audio.size == 0:
            return {"success": False, "content": "需要 samples 数组"}
        dec = ZVEIDecoder(sample_rate=args.get("sample_rate", 22050))
        code = dec.decode(audio, n_tones=args.get("n_tones", 5))
        return {"success": True, "code": code}

    registry.register(
        name="zvei_decode",
        description="ZVEI-1 选呼解码：16音调序列(5位hex)。",
        parameters={
            "type": "object",
            "properties": {
                "samples": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number", "default": 22050},
                "n_tones": {"type": "integer", "default": 5},
            },
            "required": ["samples"],
        },
        handler=_zvei_decode,
        category="multimon",
    )

    def _afsk1200_demod(args):
        import numpy as _np
        audio = _np.array(args.get("samples", []), dtype=_np.float32)
        if audio.size == 0:
            return {"success": False, "content": "需要 samples 数组"}
        dec = AFSK1200Demod(sample_rate=args.get("sample_rate", 22050))
        bits = dec.demodulate(audio)
        return {"success": True, "bits": bits.tolist()}

    registry.register(
        name="afsk1200_demod",
        description="Bell-202 AFSK1200 解调：mark1200/space2200, 1200bps, 正交相关。",
        parameters={
            "type": "object",
            "properties": {
                "samples": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number", "default": 22050},
            },
            "required": ["samples"],
        },
        handler=_afsk1200_demod,
        category="multimon",
    )
