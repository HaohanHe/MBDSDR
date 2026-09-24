"""
mbdsdr_ai/minimodem_adapter.py
==============================
minimodem (kamalmostafa/minimodem) 通用软件 FSK 调制解调器的 Python 真实移植。

本模块逐类移植自 C 源码（repos/minimodem/src/），关键常量与算法均在注释中标注
「来源: minimodem src/<file>:<line>」。

移植内容：
  - FSKModem   —— 移植 fsk.c（调制解调核心）+ simple-tone-generator.c（正弦表）
  - ASCIIFrame —— 移植 databits_ascii.c + minimodem.c:build_expect_bits_string() 的 UART 帧
  - BaudotCodec—— 移植 baudot.c（ITA-2 字母/数字表）+ databits_baudot.c

参考标准：Bell 103 (300 bps, mark=1270/space=1070 Hz)、Bell 202 (1200 bps,
mark=1200/space=2200 Hz)，与 minimodem.c:900-921 完全一致。

Copyright 对应：上游 GPLv3 (C) 2011-2020 Kamal Mostafa <kamal@whence.com>。
本移植同样以 GPLv3 发布。
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np


# =====================================================================
# FSKModem —— 移植 fsk_plan / fsk_bit_analyze / fsk_transmit_frame
# =====================================================================
class FSKModem:
    """通用 2 进制频移键控 (BFSK) 调制解调器。

    移植自：
      - fsk_plan_new()        repos/minimodem/src/fsk.c:33
      - fsk_bit_analyze()     repos/minimodem/src/fsk.c:117
      - fsk_transmit_frame()  repos/minimodem/src/minimodem.c:81
      - simpleaudio_tone()    repos/minimodem/src/simple-tone-generator.c:107

    参数
    ----
    sample_rate : 采样率，上游默认 48000 Hz  (来源: minimodem.c:534)
    baud        : 比特率 (bps)。任意值均可。
    mark_freq/space_freq : mark(=1)/space(=0) 音频频率 (Hz)。
                   若为 None，按 minimodem.c:900-933 的规则自动选择 Bell 预设。
    band_width  : 解调分析带宽 (Hz)，对应 fskp->band_width (来源: fsk.c:50)。
    """

    def __init__(
        self,
        sample_rate: float = 48000.0,
        baud: float = 300.0,
        mark_freq: Optional[float] = None,
        space_freq: Optional[float] = None,
        band_width: Optional[float] = None,
    ):
        self.sample_rate = float(sample_rate)
        self.baud = float(baud)

        # ---- Bell 预设默认值 (来源: minimodem.c:900-933) ----
        if mark_freq is None or space_freq is None:
            if baud >= 400.0:
                # Bell 202: baud>=400 → mark = baud/2 + 600
                #   autodetect_shift = -(baud*5/6); space = mark - shift (minimodem.c:904-908)
                #   band_width = 200 (minimodem.c:910)
                if mark_freq is None:
                    mark_freq = baud / 2.0 + 600.0          # minimodem.c:906
                if space_freq is None:
                    space_freq = mark_freq + baud * 5.0 / 6.0  # minimodem.c:904,908
                if band_width is None:
                    band_width = 200.0                       # minimodem.c:910
            elif baud >= 100.0:
                # Bell 103: 100<=baud<400 → mark=1270, shift=200, space=1070
                #   (minimodem.c:911-921)
                if mark_freq is None:
                    mark_freq = 1270.0                       # minimodem.c:917
                if space_freq is None:
                    space_freq = mark_freq - 200.0           # minimodem.c:915,919
                if band_width is None:
                    band_width = 50.0                        # minimodem.c:921
            else:
                # RTTY: baud<100 → mark=1585, shift=170, space=1415
                #   (minimodem.c:922-933)
                if mark_freq is None:
                    mark_freq = 1585.0                       # minimodem.c:928
                if space_freq is None:
                    space_freq = mark_freq - 170.0           # minimodem.c:926,930
                if band_width is None:
                    band_width = 10.0                        # minimodem.c:932

        self.f_mark = float(mark_freq)
        self.f_space = float(space_freq)
        self.band_width = float(band_width)

        # bit_nsamples = sample_rate / data_rate + 0.5  (来源: minimodem.c:132)
        self.bit_nsamples = int(round(self.sample_rate / self.baud))

    # -----------------------------------------------------------------
    # 调制：比特流 → FSK 音频（连续相位正弦）
    # 移植 fsk_transmit_frame() minimodem.c:81-112 与
    #       simpleaudio_tone() simple-tone-generator.c:107-175
    # -----------------------------------------------------------------
    def modulate_bits(self, bits: Sequence[int], amplitude: float = 1.0) -> np.ndarray:
        """把 0/1 比特流调制成单声道 float 音频。

        bit==1 → mark 频率，bit==0 → space 频率  (来源: minimodem.c:106)
        相邻音调保持连续相位（sa_tone_cphase，simple-tone-generator.c:98,162）。
        """
        fs = self.sample_rate
        nspb = self.bit_nsamples
        out = np.empty(len(bits) * nspb, dtype=np.float64)
        # 连续相位相位累加器（单位：cycles，对应 sa_tone_cphase 0..1）
        cphase = 0.0
        for k, b in enumerate(bits):
            f = self.f_mark if (int(b) == 1) else self.f_space   # minimodem.c:106
            wave_nsamples = fs / f                               # simple-tone-generator.c:116
            i = np.arange(nspb)
            # SINE_PHASE_TURNS = i/wave_nsamples + cphase (simple-tone-generator.c:121)
            phase_turns = i / wave_nsamples + cphase
            out[k * nspb:(k + 1) * nspb] = amplitude * np.sin(2.0 * math.pi * phase_turns)
            # cphase = fmod(cphase + nspb/wave_nsamples, 1.0) (simple-tone-generator.c:162-163)
            cphase = (cphase + nspb / wave_nsamples) % 1.0
        return out

    # -----------------------------------------------------------------
    # 解调：单比特窗 → mark/space 幅度判决
    # 移植 fsk_bit_analyze() fsk.c:117-174
    # -----------------------------------------------------------------
    def _band_magnitude(self, seg: np.ndarray, freq: float) -> float:
        """计算一段采样在指定频率上的 DFT bin 幅度。

        等价于 fsk.c 中 fftw 后取 band_mag()：
          mag = |sum x[n] e^{-j2pi f n/fs}| * (2/N)
        其中 magscalar = 2.0/bit_nsamples  (来源: fsk.c:132)。
        """
        n = len(seg)
        t = np.arange(n)
        # 单频复相关（rectangular 窗，与 fsk.c:134-154 注释掉加窗后一致）
        re = np.sum(seg * np.cos(2.0 * math.pi * freq * t / self.sample_rate))
        im = -np.sum(seg * np.sin(2.0 * math.pi * freq * t / self.sample_rate))
        mag = math.hypot(re, im) * (2.0 / n)   # fsk.c:112,132
        return mag

    def _bit_decision(self, seg: np.ndarray) -> Tuple[int, float, float]:
        """对单个比特窗做判决。返回 (bit, signal_mag, noise_mag)。

        mark 幅度 > space 幅度 → bit=1（mark==1, space==0，来源: fsk.c:160-169）。
        """
        mag_mark = self._band_magnitude(seg, self.f_mark)
        mag_space = self._band_magnitude(seg, self.f_space)
        if mag_mark > mag_space:                # fsk.c:161
            return 1, mag_mark, mag_space
        return 0, mag_space, mag_mark

    # -----------------------------------------------------------------
    # 整段解调：自动位同步（在 ±0.5 bit 内搜索最佳采样相位）
    # 移植 fsk_find_frame() 的搜索思想 fsk.c:449-538
    # -----------------------------------------------------------------
    def demodulate_bits(
        self,
        audio: np.ndarray,
        n_bits: Optional[int] = None,
        start_offset: Optional[int] = None,
    ) -> Tuple[List[int], float]:
        """从音频解调比特流。

        参数
        ----
        audio : 单声道 float 采样。
        n_bits: 要解调的比特数；None 则解到音频末尾（按 bit_nsamples 整数截断）。
        start_offset: 已知的起始采样偏移；None 则自动搜索最佳位相位
                      （在 [0, bit_nsamples) 内取帧信噪比最大者，对应
                       fsk_find_frame 的 try_first_sample 搜索，fsk.c:477-502）。

        返回 (bits, confidence)。
        """
        audio = np.asarray(audio, dtype=np.float64)
        nspb = self.bit_nsamples
        if n_bits is None:
            n_bits = len(audio) // nspb

        def _score(off: int) -> Tuple[float, List[int]]:
            bits: List[int] = []
            sig = 0.0
            noise = 0.0
            for k in range(n_bits):
                # 比特窗中心：bit_begin = samples_per_bit*bitnum + 0.5
                #   (来源: fsk.c:204)，这里以窗起点取 off + k*nspb
                s0 = off + k * nspb
                s1 = s0 + nspb
                if s1 > len(audio):
                    break
                b, smag, nmag = self._bit_decision(audio[s0:s1])
                bits.append(b)
                sig += smag
                noise += nmag if nmag > 1e-9 else 0.0
            snr = sig / noise if noise > 0 else float("inf")
            return snr, bits

        if start_offset is None:
            # 在一个 bit 周期内逐样本搜索最佳相位（ coarse sync ）
            best_c = -1.0
            best_bits: List[int] = []
            # try_step_nsamples，类比 fsk.c:1248 FSK_ANALYZE_NSTEPS=3
            step = max(1, nspb // 8)
            for off in range(0, nspb, step):
                c, bits = _score(off)
                if c > best_c:
                    best_c = c
                    best_bits = bits
            return best_bits, best_c
        else:
            c, bits = _score(int(start_offset))
            return bits, c


# =====================================================================
# ASCIIFrame —— 移植 databits_ascii.c + build_expect_bits_string()
# =====================================================================
class ASCIIFrame:
    """8N1 异步 UART 帧：1 起始位 + 8 数据位(LSB 先) + 1 停止位。

    移植自：
      - databits_encode/decode_ascii8()  databits_ascii.c:27-44（数据位本身直通）
      - build_expect_bits_string()       minimodem.c:443-487
      - fsk_transmit_frame() 起止位极性   minimodem.c:95-111

    未反相时（invert_start_stop=0，默认 minimodem.c:510）：
      起始位 = space(0)  (minimodem.c:96-97)
      数据位 LSB first   (minimodem.c:103)
      停止位 = mark(1)   (minimodem.c:110)
    期望串即 "10dddddddd1"（含上一帧停止位 prev_stop，minimodem.c:455）。
    """

    N_DATA_BITS = 8

    @staticmethod
    def encode_byte(byte: int) -> List[int]:
        """把一个字节编成 10 位 UART 帧：[start=0, b0..b7, stop=1]。"""
        b = int(byte) & 0xFF
        frame = [0]  # start bit = space (minimodem.c:96)
        for i in range(8):               # LSB first (minimodem.c:103)
            frame.append((b >> i) & 1)
        frame.append(1)  # stop bit = mark (minimodem.c:110)
        return frame

    @staticmethod
    def decode_frame(bits: Sequence[int]) -> int:
        """从 10 位帧 [start,b0..b7,stop] 恢复字节。"""
        if len(bits) < 10:
            raise ValueError("ASCIIFrame 需要至少 10 位")
        byte = 0
        for i in range(8):
            byte |= (int(bits[1 + i]) & 1) << i   # LSB first
        return byte & 0xFF

    def encode_bytes(self, data: bytes) -> List[int]:
        out: List[int] = []
        for byte in data:
            out.extend(self.encode_byte(byte))
        return out

    def decode_bits(self, bits: Sequence[int]) -> bytes:
        """把连续的 10 倍数位流按 8N1 切成字节。"""
        out = bytearray()
        for i in range(0, len(bits) - 9, 10):
            out.append(self.decode_frame(bits[i:i + 10]))
        return bytes(out)


# =====================================================================
# BaudotCodec —— 移植 baudot.c（ITA-2 / RTTY 5 位码）
# =====================================================================
# 解码表：baudot_decode_table[32][3] = {letter, US_figs, CCITT_figs}
#   来源: minimodem/src/baudot.c:34-71
# 这里取 [字母, 美国数字] 两列（上游解码默认 t=1 用 US figs，baudot.c:236-239）
_BAUDOT_DECODE_TABLE: List[Tuple[str, str]] = [
    # index  letter  US_figs
    ('\x00', '^'),   # 0x00 NUL (baudot.c:36)
    ('E',    '3'),   # 0x01  (baudot.c:37)
    ('\n',   '\n'),  # 0x02 LF (baudot.c:38)
    ('A',    '-'),   # 0x03  (baudot.c:39)
    (' ',    ' '),   # 0x04 SPACE (baudot.c:40)
    ('S',    '\x07'),# 0x05 BELL (baudot.c:41)
    ('I',    '8'),   # 0x06  (baudot.c:42)
    ('U',    '7'),   # 0x07  (baudot.c:43)
    ('\r',   '\r'),  # 0x08 CR (baudot.c:45)
    ('D',    '$'),   # 0x09  (baudot.c:46)
    ('R',    '4'),   # 0x0a  (baudot.c:47)
    ('J',    "'"),   # 0x0b  (baudot.c:48)
    ('N',    ','),   # 0x0c  (baudot.c:49)
    ('F',    '!'),   # 0x0d  (baudot.c:50)
    ('C',    ':'),   # 0x0e  (baudot.c:51)
    ('K',    '('),   # 0x0f  (baudot.c:52)
    ('T',    '5'),   # 0x10  (baudot.c:54)
    ('Z',    '"'),   # 0x11  (baudot.c:55)
    ('L',    ')'),   # 0x12  (baudot.c:56)
    ('W',    '2'),   # 0x13  (baudot.c:57)
    ('H',    '#'),   # 0x14  (baudot.c:58)
    ('Y',    '6'),   # 0x15  (baudot.c:59)
    ('P',    '0'),   # 0x16  (baudot.c:60)
    ('Q',    '1'),   # 0x17  (baudot.c:61)
    ('O',    '9'),   # 0x18  (baudot.c:63)
    ('B',    '?'),   # 0x19  (baudot.c:64)
    ('G',    '&'),   # 0x1a  (baudot.c:65)
    ('\x1b', '\x1b'),# 0x1b FIGS (baudot.c:66,188)
    ('M',    '.'),   # 0x1c  (baudot.c:67)
    ('X',    '/'),   # 0x1d  (baudot.c:68)
    ('V',    ';'),   # 0x1e  (baudot.c:69)
    ('\x1f', '\x1f'),# 0x1f LTRS (baudot.c:70,187)
]

# 编码表：ascii 字符 → (5bit 字, charset_mask)
#   mask: 1=仅字母, 2=仅数字, 3=两者均可（baudot.c:74-75 注释）
#   来源: baudot.c:77-185
_BAUDOT_ENCODE_TABLE = {
    '\x00': (0x00, 3),  # NUL      baudot.c:78
    '\x07': (0x05, 2),  # BEL      baudot.c:85
    '\n':   (0x02, 3),  # LF       baudot.c:88
    '\r':   (0x08, 3),  # CR       baudot.c:91
    ' ':    (0x04, 3),  # SPACE    baudot.c:114
    '!':    (0x0d, 2),  #          baudot.c:115
    '"':    (0x11, 2),  #          baudot.c:116
    '#':    (0x14, 2),  #          baudot.c:117
    '$':    (0x09, 2),  #          baudot.c:118
    '&':    (0x1a, 2),  #          baudot.c:120
    "'":    (0x0b, 2),  #          baudot.c:121
    '(':    (0x0f, 2),  #          baudot.c:122
    ')':    (0x12, 2),  #          baudot.c:123
    '+':    (0x12, 2),  #          baudot.c:125
    ',':    (0x0c, 2),  #          baudot.c:126
    '-':    (0x03, 2),  #          baudot.c:127
    '.':    (0x1c, 2),  #          baudot.c:128
    '/':    (0x1d, 2),  #          baudot.c:129
    '0':    (0x16, 2),  #          baudot.c:132
    '1':    (0x17, 2),  #          baudot.c:133
    '2':    (0x13, 2),  #          baudot.c:134
    '3':    (0x01, 2),  #          baudot.c:135
    '4':    (0x0a, 2),  #          baudot.c:136
    '5':    (0x10, 2),  #          baudot.c:137
    '6':    (0x15, 2),  #          baudot.c:138
    '7':    (0x07, 2),  #          baudot.c:139
    '8':    (0x06, 2),  #          baudot.c:140
    '9':    (0x18, 2),  #          baudot.c:141
    ':':    (0x0e, 2),  #          baudot.c:142
    ';':    (0x1e, 2),  #          baudot.c:143
    '?':    (0x19, 2),  #          baudot.c:147
    'A':    (0x03, 1),  #          baudot.c:151
    'B':    (0x19, 1),  #          baudot.c:152
    'C':    (0x0e, 1),  #          baudot.c:153
    'D':    (0x09, 1),  #          baudot.c:154
    'E':    (0x01, 1),  #          baudot.c:155
    'F':    (0x0d, 1),  #          baudot.c:156
    'G':    (0x1a, 1),  #          baudot.c:157
    'H':    (0x14, 1),  #          baudot.c:158
    'I':    (0x06, 1),  #          baudot.c:159
    'J':    (0x0b, 1),  #          baudot.c:160
    'K':    (0x0f, 1),  #          baudot.c:161
    'L':    (0x12, 1),  #          baudot.c:162
    'M':    (0x1c, 1),  #          baudot.c:163
    'N':    (0x0c, 1),  #          baudot.c:164
    'O':    (0x18, 1),  #          baudot.c:165
    'P':    (0x16, 1),  #          baudot.c:168
    'Q':    (0x17, 1),  #          baudot.c:169
    'R':    (0x0a, 1),  #          baudot.c:170
    'S':    (0x05, 1),  #          baudot.c:171
    'T':    (0x10, 1),  #          baudot.c:172
    'U':    (0x07, 1),  #          baudot.c:173
    'V':    (0x1e, 1),  #          baudot.c:174
    'W':    (0x13, 1),  #          baudot.c:175
    'X':    (0x1d, 1),  #          baudot.c:176
    'Y':    (0x15, 1),  #          baudot.c:177
    'Z':    (0x11, 1),  #          baudot.c:178
}

BAUDOT_LTRS = 0x1F   # baudot.c:187
BAUDOT_FIGS = 0x1B   # baudot.c:188
BAUDOT_SPACE = 0x04  # baudot.c:189


class BaudotCodec:
    """ITA-2 (Baudot-RTTY) 5 位编解码器，含字母/数字换档与 uso。

    移植自 baudot.c:baudot_encode()/baudot_decode()。
    状态：1 = LTRS(字母)，2 = FIGS(数字)（baudot.c:192-197）。
    默认 uso (unshift-on-space) = 1（baudot.c:202）。
    """

    def __init__(self, uso: bool = True):
        self.usos = uso                       # baudot.c:202
        self.reset()

    def reset(self) -> None:
        """复位到字母态。对应 baudot_reset() baudot.c:206-209。"""
        self.charset = 1

    # -- 解码：5bit 字 → 字符 ---------------------------------------
    def decode_word(self, word: int) -> Optional[str]:
        """处理一个 5bit Baudot 字，返回输出字符（换档字不产生字符）。

        对应 baudot_decode() baudot.c:217-243。
        """
        word &= 0x1F
        if word == BAUDOT_FIGS:               # baudot.c:224
            self.charset = 2
            return None
        if word == BAUDOT_LTRS:               # baudot.c:227
            self.charset = 1
            return None
        if word == BAUDOT_SPACE and self.usos:  # baudot.c:230
            self.charset = 1
        col = 0 if self.charset == 1 else 1   # baudot.c:235-238（US figs）
        return _BAUDOT_DECODE_TABLE[word][col]

    def decode_words(self, words: Sequence[int]) -> str:
        out = []
        for w in words:
            ch = self.decode_word(int(w))
            if ch is not None:
                out.append(ch)
        return "".join(out)

    # -- 编码：字符 → 5bit 字序列 -----------------------------------
    def encode_char(self, ch: str) -> List[int]:
        """把一个字符编成 1~2 个 5bit 字（必要时插入换档字）。

        对应 baudot_encode() baudot.c:257-311。
        """
        if len(ch) != 1:
            raise ValueError("encode_char 一次只处理一个字符")
        up = ch.upper()                       # baudot.c:261
        entry = _BAUDOT_ENCODE_TABLE.get(up)
        if entry is None:                     # baudot.c:276-279 不可编码
            return []
        bits, mask = entry

        out: List[int] = []
        # 需要换档时：baudot.c:275-295
        if (self.charset & mask) == 0:
            if self.charset == 0:
                self.charset = 1
            if mask != 3:
                self.charset = mask
            if self.charset == 1:
                out.append(BAUDOT_LTRS)       # baudot.c:288
            else:
                out.append(BAUDOT_FIGS)       # baudot.c:290
        out.append(bits)                      # baudot.c:304
        if ch == ' ' and self.usos:           # baudot.c:307-308
            self.charset = 1
        return out

    def encode_string(self, text: str) -> List[int]:
        out: List[int] = []
        for ch in text:
            out.extend(self.encode_char(ch))
        return out


# =====================================================================
# 模块级便捷函数（供 ToolRegistry 注册）
# =====================================================================
def fsk_modulate(bits: Sequence[int], baud: float = 300.0,
                 mark_freq: Optional[float] = None,
                 space_freq: Optional[float] = None,
                 sample_rate: float = 48000.0) -> List[float]:
    """FSK 调制：比特流 → 音频采样。默认 Bell 103 (300bps)。"""
    modem = FSKModem(sample_rate=sample_rate, baud=baud,
                     mark_freq=mark_freq, space_freq=space_freq)
    return modem.modulate_bits(list(bits)).tolist()


def fsk_demodulate(samples: Sequence[float], baud: float = 300.0,
                   mark_freq: Optional[float] = None,
                   space_freq: Optional[float] = None,
                   n_bits: Optional[int] = None,
                   sample_rate: float = 48000.0) -> dict:
    """FSK 解调：音频采样 → 比特流 + 置信度。"""
    modem = FSKModem(sample_rate=sample_rate, baud=baud,
                     mark_freq=mark_freq, space_freq=space_freq)
    audio = np.asarray(samples, dtype=np.float64)
    bits, conf = modem.demodulate_bits(audio, n_bits=n_bits)
    return {"bits": bits, "confidence": conf,
            "mark_freq": modem.f_mark, "space_freq": modem.f_space}


def baudot_encode(text: str) -> List[int]:
    """Baudot/ITA-2 字符串编码为 5bit 字序列。"""
    return BaudotCodec().encode_string(text)


def baudot_decode(words: Sequence[int]) -> str:
    """Baudot/ITA-2 5bit 字序列解码为字符串。"""
    return BaudotCodec().decode_words(list(words))


def minimodem_decode_audio(samples: Sequence[float], baud: float = 300.0,
                           sample_rate: float = 48000.0,
                           codec: str = "ascii") -> dict:
    """完整收音频：FSK 解调 → 按帧(codec=ascii 用 8N1)还原文本。

    codec: "ascii" (8N1 UART, 来源 databits_ascii.c) 或 "baudot" (5N1)。
    """
    modem = FSKModem(sample_rate=sample_rate, baud=baud)
    audio = np.asarray(samples, dtype=np.float64)
    if codec == "ascii":
        # 8N1 帧长 = 10 bit；先按整段解调，再切帧
        frame = ASCIIFrame()
        n_bits = (len(audio) // modem.bit_nsamples // 10) * 10
        bits, conf = modem.demodulate_bits(audio, n_bits=n_bits)
        text = frame.decode_bits(bits).decode("ascii", errors="replace")
    else:
        words, conf = modem.demodulate_bits(audio)
        bc = BaudotCodec()
        text = bc.decode_words(words)
    return {"text": text, "confidence": float(conf),
            "mark_freq": modem.f_mark, "space_freq": modem.f_space}
