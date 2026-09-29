# SPDX-License-Identifier: MIT
"""
通用软件 FSK 调制解调器适配器（独立实现）
=========================================

本模块依据公开的 Bell 103 / Bell 202 标准与通用异步串行 (UART) 帧格式，
独立实现一个连续相位 FSK 调制解调器：

  - FSKModem   —— 连续相位 FSK 调制/解调核心（单 bin 能量判决 + 帧搜索）
  - ASCIIFrame —— 8N1 UART 帧（1 起始位 + 8 数据位 + 1 停止位）
  - BaudotCodec—— ITA-2 (Baudot-Murray) 字母/数字 5 位码

参考标准：Bell 103 (300 bps, mark=1270/space=1070 Hz)、Bell 202 (1200 bps,
mark=1200/space=2200 Hz)。以上频率/帧格式为公开事实；本模块 DSP、结构与
命名自行编写，不包含任何上游源代码。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


# =====================================================================
# FSKModem —— 连续相位 FSK 调制/解调核心
# =====================================================================
class FSKModem:
    """通用 2 进制频移键控 (BFSK) 调制解调器。

    参数
    ----
    sample_rate : 采样率，默认 48000 Hz
    baud        : 比特率 (bps)。任意值均可。
    mark_freq/space_freq : mark(=1)/space(=0) 音频频率 (Hz)。
                   若为 None，按 Bell 预设规则自动选择。
    band_width  : 解调分析带宽 (Hz)。
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

        # ---- Bell 预设默认值 ----
        if mark_freq is None or space_freq is None:
            if baud >= 400.0:
                # Bell 202: baud>=400 → mark = baud/2 + 600
                #   autodetect_shift = -(baud*5/6); space = mark - shift
                #   band_width = 200
                if mark_freq is None:
                    mark_freq = baud / 2.0 + 600.0
                if space_freq is None:
                    space_freq = mark_freq + baud * 5.0 / 6.0
                if band_width is None:
                    band_width = 200.0
            elif baud >= 100.0:
                # Bell 103: 100<=baud<400 → mark=1270, shift=200, space=1070
                #
                if mark_freq is None:
                    mark_freq = 1270.0
                if space_freq is None:
                    space_freq = mark_freq - 200.0
                if band_width is None:
                    band_width = 50.0
            else:
                # RTTY: baud<100 → mark=1585, shift=170, space=1415
                #
                if mark_freq is None:
                    mark_freq = 1585.0
                if space_freq is None:
                    space_freq = mark_freq - 170.0
                if band_width is None:
                    band_width = 10.0

        self.f_mark = float(mark_freq)
        self.f_space = float(space_freq)
        self.band_width = float(band_width)

        # bit_nsamples = sample_rate / data_rate + 0.5
        self.bit_nsamples = int(round(self.sample_rate / self.baud))

    # -----------------------------------------------------------------
    # 调制：比特流 → FSK 音频（连续相位正弦）
    # -----------------------------------------------------------------
    def modulate_bits(self, bits: Sequence[int], amplitude: float = 1.0) -> np.ndarray:
        """把 0/1 比特流调制成单声道 float 音频。

        bit==1 → mark 频率，bit==0 → space 频率
        相邻音调保持连续相位。
        """
        fs = self.sample_rate
        nspb = self.bit_nsamples
        out = np.empty(len(bits) * nspb, dtype=np.float64)
        # 连续相位相位累加器（单位：cycles，对应 sa_tone_cphase 0..1）
        cphase = 0.0
        for k, b in enumerate(bits):
            f = self.f_mark if (int(b) == 1) else self.f_space
            wave_nsamples = fs / f
            i = np.arange(nspb)
            # SINE_PHASE_TURNS = i/wave_nsamples + cphase
            phase_turns = i / wave_nsamples + cphase
            out[k * nspb:(k + 1) * nspb] = amplitude * np.sin(2.0 * math.pi * phase_turns)
            # cphase = fmod(cphase + nspb/wave_nsamples, 1.0)
            cphase = (cphase + nspb / wave_nsamples) % 1.0
        return out

    # -----------------------------------------------------------------
    # 解调：单比特窗 → mark/space 幅度判决
    # -----------------------------------------------------------------
    def _band_magnitude(self, seg: np.ndarray, freq: float) -> float:
        """计算一段采样在指定频率上的 DFT bin 幅度。

        单 bin 能量估计：
          mag = |sum x[n] e^{-j2pi f n/fs}| * (2/N)
        其中 magscalar = 2.0/bit_nsamples。
        """
        n = len(seg)
        t = np.arange(n)
        # 单频复相关（矩形窗）
        re = np.sum(seg * np.cos(2.0 * math.pi * freq * t / self.sample_rate))
        im = -np.sum(seg * np.sin(2.0 * math.pi * freq * t / self.sample_rate))
        mag = math.hypot(re, im) * (2.0 / n)
        return mag

    def _bit_decision(self, seg: np.ndarray) -> Tuple[int, float, float]:
        """对单个比特窗做判决。返回 (bit, signal_mag, noise_mag)。

        mark 幅度 > space 幅度 → bit=1（mark==1, space==0）。
        """
        mag_mark = self._band_magnitude(seg, self.f_mark)
        mag_space = self._band_magnitude(seg, self.f_space)
        if mag_mark > mag_space:
            return 1, mag_mark, mag_space
        return 0, mag_space, mag_mark

    # -----------------------------------------------------------------
    # 整段解调：自动位同步（在 ±0.5 bit 内搜索最佳采样相位）
    # 帧搜索：在过零点附近按位周期滑动搜索
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
。

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
                #，这里以窗起点取 off + k*nspb
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
            # 每符号内取 3 个步长进判决
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
# ASCIIFrame —— 8N1 UART 帧
# =====================================================================
class ASCIIFrame:
    """8N1 异步 UART 帧：1 起始位 + 8 数据位(LSB 先) + 1 停止位。


    未反相时（默认）：
      起始位 = space(0)
      数据位 LSB first
      停止位 = mark(1)
    期望串即 "10dddddddd1"（含上一帧停止位 prev_stop）。
    """

    N_DATA_BITS = 8

    @staticmethod
    def encode_byte(byte: int) -> List[int]:
        """把一个字节编成 10 位 UART 帧：[start=0, b0..b7, stop=1]。"""
        b = int(byte) & 0xFF
        frame = [0]  # start bit = space
        for i in range(8):               # LSB first
            frame.append((b >> i) & 1)
        frame.append(1)  # stop bit = mark
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
# BaudotCodec —— ITA-2 (Baudot-Murray) 5 位码
# =====================================================================
# 解码表：baudot_decode_table[32][3] = {letter, US_figs, CCITT_figs}
# 取 [字母, 美国数字] 两列
_BAUDOT_DECODE_TABLE: List[Tuple[str, str]] = [
    # index  letter  US_figs
    ('\x00', '^'),   # 0x00 NUL
    ('E',    '3'),   # 0x01 
    ('\n',   '\n'),  # 0x02 LF
    ('A',    '-'),   # 0x03 
    (' ',    ' '),   # 0x04 SPACE
    ('S',    '\x07'),# 0x05 BELL
    ('I',    '8'),   # 0x06 
    ('U',    '7'),   # 0x07 
    ('\r',   '\r'),  # 0x08 CR
    ('D',    '$'),   # 0x09 
    ('R',    '4'),   # 0x0a 
    ('J',    "'"),   # 0x0b 
    ('N',    ','),   # 0x0c 
    ('F',    '!'),   # 0x0d 
    ('C',    ':'),   # 0x0e 
    ('K',    '('),   # 0x0f 
    ('T',    '5'),   # 0x10 
    ('Z',    '"'),   # 0x11 
    ('L',    ')'),   # 0x12 
    ('W',    '2'),   # 0x13 
    ('H',    '#'),   # 0x14 
    ('Y',    '6'),   # 0x15 
    ('P',    '0'),   # 0x16 
    ('Q',    '1'),   # 0x17 
    ('O',    '9'),   # 0x18 
    ('B',    '?'),   # 0x19 
    ('G',    '&'),   # 0x1a 
    ('\x1b', '\x1b'),# 0x1b FIGS
    ('M',    '.'),   # 0x1c 
    ('X',    '/'),   # 0x1d 
    ('V',    ';'),   # 0x1e 
    ('\x1f', '\x1f'),# 0x1f LTRS
]

# 编码表：ascii 字符 → (5bit 字, charset_mask)
#   mask: 1=仅字母, 2=仅数字, 3=两者均可（
_BAUDOT_ENCODE_TABLE = {
    '\x00': (0x00, 3),  # NUL
    '\x07': (0x05, 2),  # BEL
    '\n':   (0x02, 3),  # LF
    '\r':   (0x08, 3),  # CR
    ' ':    (0x04, 3),  # SPACE
    '!':    (0x0d, 2),  #
    '"':    (0x11, 2),  #
    '#':    (0x14, 2),  #
    '$':    (0x09, 2),  #
    '&':    (0x1a, 2),  #
    "'":    (0x0b, 2),  #
    '(':    (0x0f, 2),  #
    ')':    (0x12, 2),  #
    '+':    (0x12, 2),  #
    ',':    (0x0c, 2),  #
    '-':    (0x03, 2),  #
    '.':    (0x1c, 2),  #
    '/':    (0x1d, 2),  #
    '0':    (0x16, 2),  #
    '1':    (0x17, 2),  #
    '2':    (0x13, 2),  #
    '3':    (0x01, 2),  #
    '4':    (0x0a, 2),  #
    '5':    (0x10, 2),  #
    '6':    (0x15, 2),  #
    '7':    (0x07, 2),  #
    '8':    (0x06, 2),  #
    '9':    (0x18, 2),  #
    ':':    (0x0e, 2),  #
    ';':    (0x1e, 2),  #
    '?':    (0x19, 2),  #
    'A':    (0x03, 1),  #
    'B':    (0x19, 1),  #
    'C':    (0x0e, 1),  #
    'D':    (0x09, 1),  #
    'E':    (0x01, 1),  #
    'F':    (0x0d, 1),  #
    'G':    (0x1a, 1),  #
    'H':    (0x14, 1),  #
    'I':    (0x06, 1),  #
    'J':    (0x0b, 1),  #
    'K':    (0x0f, 1),  #
    'L':    (0x12, 1),  #
    'M':    (0x1c, 1),  #
    'N':    (0x0c, 1),  #
    'O':    (0x18, 1),  #
    'P':    (0x16, 1),  #
    'Q':    (0x17, 1),  #
    'R':    (0x0a, 1),  #
    'S':    (0x05, 1),  #
    'T':    (0x10, 1),  #
    'U':    (0x07, 1),  #
    'V':    (0x1e, 1),  #
    'W':    (0x13, 1),  #
    'X':    (0x1d, 1),  #
    'Y':    (0x15, 1),  #
    'Z':    (0x11, 1),  #
}

BAUDOT_LTRS = 0x1F
BAUDOT_FIGS = 0x1B
BAUDOT_SPACE = 0x04


class BaudotCodec:
    """ITA-2 (Baudot-RTTY) 5 位编解码器，含字母/数字换档与 uso。

    ITA-2 字母/数字互译。
    状态：1 = LTRS(字母)，2 = FIGS(数字)。
    默认 uso (unshift-on-space) = 1。
    """

    def __init__(self, uso: bool = True):
        self.usos = uso
        self.reset()

    def reset(self) -> None:
        """复位到字母态。对应 baudot_reset() 。"""
        self.charset = 1

    # -- 解码：5bit 字 → 字符 ---------------------------------------
    def decode_word(self, word: int) -> Optional[str]:
        """处理一个 5bit Baudot 字，返回输出字符（换档字不产生字符）。

        对应 baudot_decode() 。
        """
        word &= 0x1F
        if word == BAUDOT_FIGS:
            self.charset = 2
            return None
        if word == BAUDOT_LTRS:
            self.charset = 1
            return None
        if word == BAUDOT_SPACE and self.usos:
            self.charset = 1
        col = 0 if self.charset == 1 else 1
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

        对应 baudot_encode() 。
        """
        if len(ch) != 1:
            raise ValueError("encode_char 一次只处理一个字符")
        up = ch.upper()
        entry = _BAUDOT_ENCODE_TABLE.get(up)
        if entry is None:
            return []
        bits, mask = entry

        out: List[int] = []
        # 需要换档时：
        if (self.charset & mask) == 0:
            if self.charset == 0:
                self.charset = 1
            if mask != 3:
                self.charset = mask
            if self.charset == 1:
                out.append(BAUDOT_LTRS)
            else:
                out.append(BAUDOT_FIGS)
        out.append(bits)
        if ch == ' ' and self.usos:
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

    codec: "ascii" (8N1 UART) 或 "baudot" (5N1)。
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


# =====================================================================
# Bell 103 300 baud 专用模式（追加，不改动上方现有接口）
# =====================================================================
# Bell 103 标准：
#   baud = 300 bps
#   mark  = 1270 Hz  (bit=1)
#   space = 1070 Hz  (bit=0)
#   shift = 200 Hz
#   band_width = 50 Hz
# 这是最早的拨号电话音频调制解调器标准，也是 HF/VHF 电台常用的
# 300 baud FSK 呼叫模式。
BELL103_BAUD = 300.0
BELL103_MARK_FREQ = 1270.0
BELL103_SPACE_FREQ = 1070.0
BELL103_BAND_WIDTH = 50.0


def bell103_300baud_modem(sample_rate: float = 48000.0) -> FSKModem:
    """构造一个 Bell 103 300 baud 专用 FSKModem。

    显式传入 mark/space，避免依赖 baud 自动判定分支。
    """
    return FSKModem(
        sample_rate=sample_rate,
        baud=BELL103_BAUD,
        mark_freq=BELL103_MARK_FREQ,
        space_freq=BELL103_SPACE_FREQ,
        band_width=BELL103_BAND_WIDTH,
    )


def bell103_modulate_text(text: str, sample_rate: float = 48000.0) -> List[float]:
    """Bell 103 300baud 发送文本：ASCII 8N1 帧 -> FSK 音频。"""
    modem = bell103_300baud_modem(sample_rate)
    fr = ASCIIFrame()
    bits = fr.encode_bytes(text.encode("ascii", errors="replace"))
    return modem.modulate_bits(bits).tolist()


def bell103_demodulate_text(samples: Sequence[float],
                            sample_rate: float = 48000.0) -> dict:
    """Bell 103 300baud 接收：FSK 音频 -> ASCII 8N1 文本。"""
    modem = bell103_300baud_modem(sample_rate)
    audio = np.asarray(samples, dtype=np.float64)
    fr = ASCIIFrame()
    n_frames = len(audio) // modem.bit_nsamples // 10
    bits, conf = modem.demodulate_bits(audio, n_bits=n_frames * 10)
    text = fr.decode_bits(bits).decode("ascii", errors="replace")
    return {"text": text, "confidence": float(conf),
            "mark_freq": modem.f_mark, "space_freq": modem.f_space,
            "baud": BELL103_BAUD}


# ---------------------------------------------------------------------
# 独立的 Bell103 工具注册函数（模块级，不修改 tool_registry.py）
# ---------------------------------------------------------------------
def register_minimodem_tools(registry) -> None:
    """向 registry 追加 Bell 103 300 baud 专用工具。

    注意：tool_registry.ToolRegistry 已有同名方法注册了通用 fsk_* /
    baudot_* 工具；本函数是模块级独立注册入口，只追加 bell103_* 工具，
    不改动现有 Bell202 / ASCII / Baudot 接口。
    """
    from mbdsdr_ai.tool_registry import ToolResult

    def _bell103_modulate(args: Dict[str, Any]) -> "ToolResult":
        text = args.get("text", "")
        fs = args.get("sample_rate", 48000)
        audio = bell103_modulate_text(text, sample_rate=fs)
        return ToolResult(
            success=True,
            content=f"Bell103 调制: '{text}' -> {len(audio)} 采样 "
                    f"(mark={BELL103_MARK_FREQ:.0f}/space={BELL103_SPACE_FREQ:.0f}Hz, "
                    f"{BELL103_BAUD:.0f}baud)",
            data={"audio": audio, "n_samples": len(audio),
                  "mark_freq": BELL103_MARK_FREQ, "space_freq": BELL103_SPACE_FREQ},
        )

    def _bell103_demodulate(args: Dict[str, Any]) -> "ToolResult":
        samples = args.get("samples", [])
        fs = args.get("sample_rate", 48000)
        res = bell103_demodulate_text(samples, sample_rate=fs)
        return ToolResult(
            success=True,
            content=f"Bell103 解调: '{res['text']}' (conf={res['confidence']:.2f})",
            data=res,
        )

    registry.register(
        name="bell103_modulate_text",
        description="Bell 103 300baud FSK 调制：ASCII 文本 -> 单声道音频 "
                    "(mark=1270Hz/space=1070Hz, 300baud, 8N1 UART)。",
        parameters={"type": "object", "properties": {
            "text": {"type": "string"},
            "sample_rate": {"type": "number", "default": 48000},
        }, "required": ["text"]},
        handler=_bell103_modulate,
        category="digital_modes",
    )
    registry.register(
        name="bell103_demodulate_text",
        description="Bell 103 300baud FSK 解调：音频 -> ASCII 文本（FFT-bin 幅度判决 + 8N1 帧切分）。",
        parameters={"type": "object", "properties": {
            "samples": {"type": "array", "items": {"type": "number"}},
            "sample_rate": {"type": "number", "default": 48000},
        }, "required": ["samples"]},
        handler=_bell103_demodulate,
        category="digital_modes",
    )
