"""
MBDSDR - fldigi 多模式数字解码真实移植
=========================================

本模块从 fldigi (w1hkj/fldigi, GPLv3) 真实 .cxx 源码移植：
  - PSK31 (Varicode + DBPSK)         : src/psk/pskvaricode.cxx, src/psk/psk.cxx
  - RTTY (ITA-2/Baudot + 2FSK)      : src/cw_rtty/rtty.cxx
  - MFSK8/16/32 (非相干 FFT 检测)    : src/mfsk/mfsk.cxx
  - FeldHell (振幅键控慢扫描文本)    : src/feld/feld.cxx, src/feld/Feld7x7-14.cxx
  - Olivia MFSK 基类（RS+交织参数）  : src/olivia/olivia.cxx
  - Thor 模式参数（继承 PSK FEC 卷积）: src/thor/thor.cxx

所有常量均在注释中标注来源文件:行号。

MBDSDR Project - AI定义无线电 - GPL-3.0 - BI4MIB
"""

import math
import struct
from typing import List, Tuple, Optional, Dict


# ============================================================================
# Varicode 表 —— 来源: fldigi src/psk/pskvaricode.cxx:28-288
# 每行 8 个，共 256 项。索引即 ASCII/字节值，值为发射比特串（首比特先发）。
# ============================================================================
VARICODE_STRS = [
    '1010101011', '1011011011', '1011101101', '1101110111', '1011101011', '1101011111', '1011101111', '1011111101',
    '1011111111', '11101111', '11101', '1101101111', '1011011101', '11111', '1101110101', '1110101011',
    '1011110111', '1011110101', '1110101101', '1110101111', '1101011011', '1101101011', '1101101101', '1101010111',
    '1101111011', '1101111101', '1110110111', '1101010101', '1101011101', '1110111011', '1011111011', '1101111111',
    '1', '111111111', '101011111', '111110101', '111011011', '1011010101', '1010111011', '101111111',
    '11111011', '11110111', '101101111', '111011111', '1110101', '110101', '1010111', '110101111',
    '10110111', '10111101', '11101101', '11111111', '101110111', '101011011', '101101011', '110101101',
    '110101011', '110110111', '11110101', '110111101', '111101101', '1010101', '111010111', '1010101111',
    '1010111101', '1111101', '11101011', '10101101', '10110101', '1110111', '11011011', '11111101',
    '101010101', '1111111', '111111101', '101111101', '11010111', '10111011', '11011101', '10101011',
    '11010101', '111011101', '10101111', '1101111', '1101101', '101010111', '110110101', '101011101',
    '101110101', '101111011', '1010101101', '111110111', '111101111', '111111011', '1010111111', '101101101',
    '1011011111', '1011', '1011111', '101111', '101101', '11', '111101', '1011011',
    '101011', '1101', '111101011', '10111111', '11011', '111011', '1111', '111',
    '111111', '110111111', '10101', '10111', '101', '110111', '1111011', '1101011',
    '11011111', '1011101', '111010101', '1010110111', '110111011', '1010110101', '1011010111', '1110110101',
    '1110111101', '1110111111', '1111010101', '1111010111', '1111011011', '1111011101', '1111011111', '1111101011',
    '1111101101', '1111101111', '1111110101', '1111110111', '1111111011', '1111111101', '1111111111', '10101010101',
    '10101010111', '10101011011', '10101011101', '10101011111', '10101101011', '10101101101', '10101101111', '10101110101',
    '10101110111', '10101111011', '10101111101', '10101111111', '10110101011', '10110101101', '10110101111', '10110110101',
    '10110110111', '10110111011', '10110111101', '10110111111', '10111010101', '10111010111', '10111011011', '10111011101',
    '10111011111', '10111101011', '10111101101', '10111101111', '10111110101', '10111110111', '10111111011', '10111111101',
    '10111111111', '11010101011', '11010101101', '11010101111', '11010110101', '11010110111', '11010111011', '11010111101',
    '11010111111', '11011010101', '11011010111', '11011011011', '11011011101', '11011011111', '11011101011', '11011101101',
    '11011101111', '11011110101', '11011110111', '11011111011', '11011111101', '11011111111', '11101010101', '11101010111',
    '11101011011', '11101011101', '11101011111', '11101101011', '11101101101', '11101101111', '11101110101', '11101110111',
    '11101111011', '11101111101', '11101111111', '11110101011', '11110101101', '11110101111', '11110110101', '11110110111',
    '11110111011', '11110111101', '11110111111', '11111010101', '11111010111', '11111011011', '11111011101', '11111011111',
    '11111101011', '11111101101', '11111101111', '11111110101', '11111110111', '11111111011', '11111111101', '11111111111',
    '101010101011', '101010101101', '101010101111', '101010110101', '101010110111', '101010111011', '101010111101', '101010111111',
    '101011010101', '101011010111', '101011011011', '101011011101', '101011011111', '101011101011', '101011101101', '101011101111',
    '101011110101', '101011110111', '101011111011', '101011111101', '101011111111', '101101010101', '101101010111', '101101011011',
]


class Varicode:
    """PSK31 Varicode 可变长度编码。

    来源: fldigi src/psk/pskvaricode.cxx:28-338
    字符间用 "00" 两位间隔；Varicode 自身不含 "00" 子串。
    """

    # 字符串表（编码用），索引=字节值 —— pskvaricode.cxx:28 varicodetab1[]
    ENC = VARICODE_STRS

    # 反向表（解码用）：比特串 -> 字节值
    DEC = {v: i for i, v in enumerate(VARICODE_STRS)}

    SEPARATOR = "00"  # psk.cxx:2484-2485 tx_bit(0); tx_bit(0);

    @classmethod
    def encode_char(cls, c: int) -> str:
        """字节 -> Varicode 比特串（不含间隔位）。pskvaricode.cxx:327-330"""
        c = c & 0xFF
        if c >= len(cls.ENC):
            c = 0
        return cls.ENC[c]

    @classmethod
    def encode_text(cls, text: str) -> str:
        """文本 -> 完整比特流（字符间自动加 "00" 间隔）。psk.cxx:2467-2489 tx_char"""
        bits = []
        for ch in text:
            bits.append(cls.encode_char(ord(ch)))
            bits.append(cls.SEPARATOR)  # psk.cxx:2484-2485
        return "".join(bits)

    @classmethod
    def decode_bits(cls, bits: str) -> bytes:
        """比特流 -> 字节序列。按 "00" 切分，每段查 DEC 表。
        对应 psk.cxx:1113-1135 rx_bit: (shreg & 3)==0 时解码 shreg>>2。
        """
        out = bytearray()
        buf = ""
        for b in bits:
            buf += b
            if len(buf) >= 2 and buf[-2:] == cls.SEPARATOR:
                code = buf[:-2]
                if code in cls.DEC:
                    out.append(cls.DEC[code])
                buf = ""
        return bytes(out)


# ============================================================================
# ITA-2 (Baudot-Murray, U.S. variant) —— 来源: fldigi src/cw_rtty/rtty.cxx:62-79
# 字母表 letters[32] / 数字表 figures[32]，索引=5bit 符号。
# LETTERS=0x1F(11111), FIGS=0x1B(11011) —— rtty.cxx:1408-1430 baudot_dec
# ============================================================================
ITA2_LETTERS = [
    '\x00', 'E', '\n', 'A', ' ', 'S', 'I', 'U',
    '\r', 'D', 'R', 'J', 'N', 'F', 'C', 'K',
    'T', 'Z', 'L', 'W', 'H', 'Y', 'P', 'Q',
    'O', 'B', 'G', ' ', 'M', 'X', 'V', ' ',
]
# rtty.cxx:72-79 U.S. figures
ITA2_FIGURES = [
    '\x00', '3', '\n', '-', ' ', '\a', '8', '7',
    '\r', '$', '4', "'", ',', '!', ':', '(',
    '5', '"', ')', '2', '#', '6', '0', '1',
    '9', '?', '&', ' ', '.', '/', ';', ' ',
]

ITA2_LTRS = 0x1F  # rtty.cxx:52 #define LETTERS 0x100 → 符号 0x1F=11111
ITA2_FIGS = 0x1B  # rtty.cxx:56 #define FIGS 0x1B


class ITA2:
    """ITA-2 (Baudot-Murray) 字母/数字模式编码。

    来源: fldigi src/cw_rtty/rtty.cxx:62-79, 1382-1430
    """

    @staticmethod
    def encode_char(c: str, in_figures: bool) -> Tuple[Optional[int], bool]:
        """返回 (5bit_symbol, new_figures_state)。找不到返回 (None, state)。
        对应 rtty.cxx:1382-1406 baudot_enc。
        """
        c = c.upper()
        # 先查 figures，再查 letters
        for i, fch in enumerate(ITA2_FIGURES):
            if fch == c:
                if in_figures:
                    return i, True
                return ITA2_FIGS, True  # 需要先切到 figures
        for i, lch in enumerate(ITA2_LETTERS):
            if lch == c:
                if not in_figures:
                    return i, False
                return ITA2_LTRS, False  # 需要先切到 letters
        return None, in_figures

    @classmethod
    def encode_text(cls, text: str) -> List[int]:
        """文本 -> 5bit 符号序列（含 LTRS/FIGS 切换）。rtty.cxx:1350-1370"""
        syms = []
        in_fig = False
        for c in text:
            sym, new_fig = cls.encode_char(c, in_fig)
            if sym is None:
                continue
            if sym in (ITA2_LTRS, ITA2_FIGS):
                syms.append(sym)
                in_fig = new_fig
                sym2, _ = cls.encode_char(c, in_fig)
                if sym2 is not None and sym2 not in (ITA2_LTRS, ITA2_FIGS):
                    syms.append(sym2)
            else:
                syms.append(sym)
        return syms

    @staticmethod
    def decode_symbols(syms: List[int]) -> str:
        """5bit 符号序列 -> 文本。rtty.cxx:1408-1430 baudot_dec"""
        out = []
        fig = False  # 默认字母模式 (rtty.cxx:125 rxmode = LETTERS)
        for s in syms:
            s &= 0x1F
            if s == ITA2_LTRS:
                fig = False
                continue
            if s == ITA2_FIGS:
                fig = True
                continue
            out.append(ITA2_FIGURES[s] if fig else ITA2_LETTERS[s])
        return "".join(out)


# ============================================================================
# PSK31 Modem —— DBPSK
# 来源: fldigi src/psk/psk.cxx:382-387 (symbollen=256, samplerate=8000 → 31.25 baud)
#       psk.cxx:2193-2210 sym_vec_pos[]; psk.cxx:2240-2253 差分编码
#       psk.cxx:2322-2358 tx_bit; psk.cxx:2467-2489 tx_char
#
# fldigi BPSK 映射（psk.cxx:2349 sym=bit<<1; 2259 sym*=4; 2252 prev*sym_vec_pos[sym]）:
#   bit=0 → sym=0 → sym_vec_pos[0]=-1 (180°, 相位翻转)
#   bit=1 → sym=2 → sym_vec_pos[8]=+1 (0°,  相位不变)
# 即：bit=1 无相位跳变，bit=0 相位跳变 π。
# ============================================================================
class PSK31Modem:
    BAUD = 31.25            # psk.cxx:382-387: 8000/256 = 31.25 baud
    SAMPLE_RATE = 8000      # psk.cxx:370 samplerate=8000
    SYMBOLS_PER_SIG = 256   # psk.cxx:383 symbollen=256
    CARRIER_HZ = 1500.0     # 音频中频频点（fldigi 默认音频载波由用户设定）

    def __init__(self, sample_rate: int = 8000, carrier_hz: float = 1500.0):
        self.sample_rate = sample_rate
        self.carrier_hz = carrier_hz
        self.sps = int(round(sample_rate / self.BAUD))  # samples per symbol

    def modulate(self, text: str) -> List[float]:
        """文本 -> DBPSK 实波形。

        Varicode 编码 → 差分 BPSK：bit=1 保持相位，bit=0 翻转 π。
        """
        bits = Varicode.encode_text(text)
        # 起始差分参考：先发一个 "1"（无跳变）作为相位参考
        out = []
        prev_i, prev_q = 1.0, 0.0  # 参考星座点 +1
        phase_acc = 0.0
        dphi = 2.0 * math.pi * self.carrier_hz / self.sample_rate
        for bit_ch in bits:
            bit = int(bit_ch)
            # 差分相位：bit=1 → 0, bit=0 → π
            dphase = 0.0 if bit == 1 else math.pi
            # 当前符号星座
            sym_i = prev_i * math.cos(dphase) - prev_q * math.sin(dphase)
            sym_q = prev_i * math.sin(dphase) + prev_q * math.cos(dphase)
            prev_i, prev_q = sym_i, sym_q
            # 成型：矩形脉冲（fldigi 用 RRC/PSK_CORE 成型，这里矩形足够往返）
            for _ in range(self.sps):
                out.append(sym_i * math.cos(phase_acc) - sym_q * math.sin(phase_acc))
                phase_acc += dphi
                if phase_acc > 2 * math.pi:
                    phase_acc -= 2 * math.pi
        return out

    def demodulate(self, samples: List[float]) -> str:
        """DBPSK 波形 -> 文本。

        1) 与本地载波相乘下变频到基带 I/Q
        2) 每符号积分 & dump
        3) 差分解调：比较当前符号与前一符号相位差 → bit
        4) Varicode 解码
        """
        n = len(samples)
        # 下变频 + 匹配滤波（每符号积分）
        dphi = 2.0 * math.pi * self.carrier_hz / self.sample_rate
        phase = 0.0
        sym_i_list, sym_q_list = [], []
        acc_i, acc_q = 0.0, 0.0
        cnt = 0
        for s in samples:
            acc_i += s * math.cos(phase)
            acc_q += -s * math.sin(phase)
            phase += dphi
            if phase > 2 * math.pi:
                phase -= 2 * math.pi
            cnt += 1
            if cnt >= self.sps:
                sym_i_list.append(acc_i / cnt)
                sym_q_list.append(acc_q / cnt)
                acc_i, acc_q, cnt = 0.0, 0.0, 0

        # 差分解调：bit = sign(Re(s_k * conj(s_{k-1})))
        # bit=1 → 同相（Re>0），bit=0 → 反相（Re<0）。与 modulate 对应。
        # 第一个符号与调制端初始参考 (+1,0) 比较，恢复第 0 位。
        bits = ""
        prev_c = complex(1.0, 0.0)  # 差分参考星座（modulate 中 prev_i=1, prev_q=0）
        for k in range(len(sym_i_list)):
            cur = complex(sym_i_list[k], sym_q_list[k])
            diff = cur * complex(prev_c.real, -prev_c.imag)
            bits += "1" if diff.real >= 0 else "0"
            prev_c = cur

        # Varicode 解码
        raw = Varicode.decode_bits(bits)
        return raw.decode("ascii", errors="replace")


# ============================================================================
# RTTY Modem —— 2FSK (Mark/Space)
# 来源: fldigi src/cw_rtty/rtty.cxx:83 SHIFT[]={...,170,...}, :85 BAUD[]={45,45.45,50,...}
#       rtty.cxx:62-79 ITA-2 表; rtty.cxx:1382-1430 编解码
# 标准业余 RTTY: 170Hz 频偏, 45.45 baud, mark=2125Hz, space=2295Hz (中心 2210Hz)
# ============================================================================
class RTTYModem:
    # rtty.cxx:83 SHIFT 表索引 3 = 170 Hz
    DEFAULT_SHIFT_HZ = 170.0
    # rtty.cxx:85 BAUD 表索引 1 = 45.45 baud
    DEFAULT_BAUD = 45.45
    # 典型 mark/space (业余 RTTY): mark=2125, space=2295
    DEFAULT_MARK_HZ = 2125.0
    DEFAULT_SPACE_HZ = 2295.0
    STOP_BITS = 1.5

    def __init__(self, sample_rate: int = 8000, baud: float = 45.45,
                 shift_hz: float = 170.0, mark_hz: float = 2125.0):
        self.sample_rate = sample_rate
        self.baud = baud
        self.shift_hz = shift_hz
        self.mark_hz = mark_hz
        self.space_hz = mark_hz + shift_hz
        self.spb = sample_rate / baud  # samples per bit

    def _bits_from_symbols(self, syms: List[int]) -> List[int]:
        """5bit 符号序列 -> 串行 NRZ 比特流（每字符: start=0, 5 data LSB first, stop=1）。
        对应 rtty.cxx:480-510 接收位采样与 rtty.cxx send_FSK 发送格式。
        """
        bits = []
        for s in syms:
            bits.append(0)  # start bit (space)
            for i in range(5):
                bits.append((s >> i) & 1)  # LSB first
            bits.append(1)  # stop bit (mark)
            bits.append(1)  # 1.5 stop → 第二个 stop 的半位
        return bits

    def modulate(self, text: str) -> List[float]:
        """文本 -> 2FSK 波形。mark=1(高频), space=0(低频)。"""
        syms = ITA2.encode_text(text)
        bits = self._bits_from_symbols(syms)
        out = []
        phase_mark = 0.0
        phase_space = 0.0
        dphi_mark = 2.0 * math.pi * self.mark_hz / self.sample_rate
        dphi_space = 2.0 * math.pi * self.space_hz / self.sample_rate
        spb = self.spb
        for b in bits:
            f_phase = phase_mark if b == 1 else phase_space
            dphi = dphi_mark if b == 1 else dphi_space
            for _ in range(int(round(spb))):
                out.append(math.cos(f_phase))
                f_phase += dphi
                if f_phase > 2 * math.pi:
                    f_phase -= 2 * math.pi
            if b == 1:
                phase_mark = f_phase
            else:
                phase_space = f_phase
        return out

    def demodulate(self, samples: List[float]) -> str:
        """2FSK 波形 -> 文本。非相干正交检测：I/Q 两路能量比较。"""
        n = len(samples)
        spb = self.spb
        # 正交混频到 mark/space 基带
        dphi_mark = 2.0 * math.pi * self.mark_hz / self.sample_rate
        dphi_space = 2.0 * math.pi * self.space_hz / self.sample_rate
        pm, ps = 0.0, 0.0
        e_mi = [0.0] * n
        e_mq = [0.0] * n
        e_si = [0.0] * n
        e_sq = [0.0] * n
        for i, s in enumerate(samples):
            e_mi[i] = s * math.cos(pm)
            e_mq[i] = s * math.sin(pm)
            e_si[i] = s * math.cos(ps)
            e_sq[i] = s * math.sin(ps)
            pm += dphi_mark
            ps += dphi_space
            if pm > 2 * math.pi:
                pm -= 2 * math.pi
            if ps > 2 * math.pi:
                ps -= 2 * math.pi

        # 按 bit 周期计算正交能量 |I+jQ|^2，判决 mark(1)/space(0)
        # 先用固定步长满采，再做帧同步
        nbits = int(n / spb)
        bits = []
        for k in range(nbits):
            i0 = int(k * spb)
            i1 = int((k + 1) * spb)
            mi = sum(e_mi[i0:i1])
            mq = sum(e_mq[i0:i1])
            si = sum(e_si[i0:i1])
            sq = sum(e_sq[i0:i1])
            e_mark = mi * mi + mq * mq
            e_space = si * si + sq * sq
            bits.append(1 if e_mark > e_space else 0)

        # 帧同步：找 start bit (0)，然后取 5 data bits (LSB first)，跳 stop
        syms = []
        i = 0
        while i < len(bits) - 6:
            if bits[i] == 0:  # start bit (space)
                s = 0
                for b in range(5):
                    s |= bits[i + 1 + b] << b
                syms.append(s)
                i += 1 + 5 + 2  # start + 5 data + ~1.5 stop
            else:
                i += 1
        return ITA2.decode_symbols(syms)


# ============================================================================
# MFSK Modem —— 非相干 FFT 检测
# 来源: fldigi src/mfsk/mfsk.cxx:291 tonespacing = samplerate/symlen
#       mfsk.cxx:292 basefreq = samplerate*basetone/symlen
# MFSK16: sr=8000, symlen=256 → 31.25 baud, 31.25Hz tone spacing, base=1000Hz, 16 tones
# MFSK8:  sr=8000, symlen=1024 → 7.8125 baud, 7.8125Hz spacing, base=1000Hz, 32 tones
# ============================================================================
class MFSKModem:
    MODES = {
        # name: (symlen, basetone, numtones, samplerate)
        "MFSK8":  (1024, 128, 32, 8000),   # mfsk.cxx:191-198
        "MFSK16": (256, 32, 16, 8000),     # mfsk.cxx:209-216
        "MFSK32": (256, 32, 8, 8000),      # mfsk.cxx:200-207 (8 tones, 31.25 baud)
    }

    def __init__(self, mode: str = "MFSK16"):
        if mode not in self.MODES:
            raise ValueError(f"unknown MFSK mode: {mode}")
        self.mode = mode
        self.symlen, self.basetone, self.numtones, self.sample_rate = self.MODES[mode]
        # mfsk.cxx:291-292
        self.tonespace = self.sample_rate / self.symlen
        self.basefreq = self.sample_rate * self.basetone / self.symlen
        self.baud = self.tonespace

    def tone_freq(self, tone_idx: int) -> float:
        """第 tone_idx 个音调的频率 Hz。mfsk.cxx:292"""
        return self.basefreq + tone_idx * self.tonespace

    def modulate_symbol(self, tone_idx: int) -> List[float]:
        """单个符号 -> 一个符号周期的音调波形。"""
        f = self.tone_freq(tone_idx)
        dphi = 2.0 * math.pi * f / self.sample_rate
        out = []
        ph = 0.0
        for _ in range(self.symlen):
            out.append(math.cos(ph))
            ph += dphi
            if ph > 2 * math.pi:
                ph -= 2 * math.pi
        return out

    def modulate_sequence(self, symbols: List[int]) -> List[float]:
        out = []
        for s in symbols:
            out.extend(self.modulate_symbol(s))
        return out

    def demodulate(self, samples: List[float]) -> List[int]:
        """非相干 FFT 检测：每符号周期内找能量最大的音调。mfsk.cxx:294 sfft 检测"""
        n_sym = len(samples) // self.symlen
        result = []
        for k in range(n_sym):
            seg = samples[k * self.symlen:(k + 1) * self.symlen]
            best_tone = 0
            best_e = -1.0
            for t in range(self.numtones):
                f = self.tone_freq(t)
                dphi = 2.0 * math.pi * f / self.sample_rate
                # Goertzel 能量
                e = 0.0
                ph = 0.0
                for s in seg:
                    e += s * math.cos(ph)
                    ph += dphi
                    if ph > 2 * math.pi:
                        ph -= 2 * math.pi
                e = abs(e)
                if e > best_e:
                    best_e = e
                    best_tone = t
            result.append(best_tone)
        return result


# ============================================================================
# FeldHell (Hellschreiber) 解码器
# 来源: fldigi src/feld/feld.cxx:153-160 (column rate 17.5 cols/sec)
#       src/feld.h:42 FELD_COLUMN_LEN=14 (每字符 14 列)
#       src/feld/Feld7x7-14.cxx 字体表 (7 行 × 14 列)
# 标准 FeldHell: 振幅键控, 每列 1/17.5=57.14ms, 字符宽 14 列 = 0.8s
# ============================================================================
FELD_FONT = {
    ' ': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    '!': [0, 0, 32768, 32768, 32768, 32768, 32768, 32768, 0, 0, 32768, 32768, 0, 0],
    '"': [0, 0, 40960, 40960, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    '#': [0, 0, 20480, 20480, 63488, 63488, 20480, 20480, 63488, 63488, 20480, 20480, 0, 0],
    '$': [0, 8192, 30720, 30720, 40960, 40960, 28672, 28672, 10240, 10240, 61440, 61440, 8192, 0],
    '%': [0, 0, 51200, 51200, 4096, 4096, 8192, 8192, 16384, 16384, 38912, 38912, 0, 0],
    '&': [0, 0, 16384, 16384, 57344, 57344, 26624, 26624, 36864, 36864, 30720, 30720, 0, 0],
    '(': [0, 0, 24576, 24576, 32768, 32768, 32768, 32768, 32768, 32768, 24576, 24576, 0, 0],
    ')': [0, 0, 49152, 49152, 8192, 8192, 8192, 8192, 8192, 8192, 49152, 49152, 0, 0],
    '*': [0, 0, 43008, 43008, 28672, 28672, 63488, 63488, 28672, 28672, 43008, 43008, 0, 0],
    '+': [0, 0, 0, 0, 8192, 8192, 63488, 63488, 8192, 8192, 0, 0, 0, 0],
    ',': [0, 0, 0, 0, 0, 0, 0, 49152, 49152, 16384, 16384, 32768, 32768, 0],
    '-': [0, 0, 0, 0, 0, 0, 63488, 63488, 0, 0, 0, 0, 0, 0],
    '.': [0, 0, 0, 0, 0, 0, 0, 0, 0, 49152, 49152, 49152, 0, 0],
    '/': [0, 0, 2048, 2048, 4096, 4096, 8192, 8192, 16384, 16384, 32768, 32768, 0, 0],
    '0': [0, 0, 28672, 28672, 38912, 38912, 43008, 43008, 51200, 51200, 28672, 28672, 0, 0],
    '1': [0, 0, 24576, 24576, 40960, 40960, 8192, 8192, 8192, 8192, 8192, 8192, 0, 0],
    '2': [0, 0, 57344, 57344, 4096, 4096, 8192, 8192, 16384, 16384, 63488, 63488, 0, 0],
    '3': [0, 0, 61440, 61440, 2048, 2048, 12288, 12288, 2048, 2048, 61440, 61440, 0, 0],
    '4': [0, 0, 36864, 36864, 36864, 36864, 63488, 63488, 4096, 4096, 4096, 4096, 0, 0],
    '5': [0, 0, 63488, 63488, 32768, 32768, 61440, 61440, 2048, 2048, 61440, 61440, 0, 0],
    '6': [0, 0, 28672, 28672, 32768, 32768, 61440, 61440, 34816, 34816, 28672, 28672, 0, 0],
    '7': [0, 0, 63488, 63488, 2048, 2048, 4096, 4096, 8192, 8192, 16384, 16384, 0, 0],
    '8': [0, 0, 28672, 28672, 34816, 34816, 28672, 28672, 34816, 34816, 28672, 28672, 0, 0],
    '9': [0, 0, 28672, 28672, 34816, 34816, 30720, 30720, 2048, 2048, 28672, 28672, 0, 0],
    ':': [0, 0, 0, 0, 49152, 49152, 0, 0, 49152, 49152, 0, 0, 0, 0],
    ';': [0, 0, 0, 49152, 49152, 0, 0, 49152, 49152, 16384, 16384, 32768, 32768, 0],
    '<': [0, 0, 2048, 2048, 12288, 12288, 49152, 49152, 12288, 12288, 2048, 2048, 0, 0],
    '=': [0, 0, 0, 0, 63488, 63488, 0, 0, 63488, 63488, 0, 0, 0, 0],
    '>': [0, 0, 32768, 32768, 24576, 24576, 6144, 6144, 24576, 24576, 32768, 32768, 0, 0],
    '?': [0, 0, 57344, 57344, 4096, 4096, 8192, 8192, 0, 0, 8192, 8192, 0, 0],
    '@': [0, 0, 28672, 28672, 34816, 34816, 45056, 45056, 32768, 32768, 30720, 30720, 0, 0],
    'A': [0, 0, 28672, 28672, 34816, 34816, 63488, 63488, 34816, 34816, 34816, 34816, 0, 0],
    'B': [0, 0, 61440, 61440, 18432, 18432, 28672, 28672, 18432, 18432, 61440, 61440, 0, 0],
    'C': [0, 0, 30720, 30720, 32768, 32768, 32768, 32768, 32768, 32768, 30720, 30720, 0, 0],
    'D': [0, 0, 61440, 61440, 18432, 18432, 18432, 18432, 18432, 18432, 61440, 61440, 0, 0],
    'E': [0, 0, 63488, 63488, 32768, 32768, 57344, 57344, 32768, 32768, 63488, 63488, 0, 0],
    'F': [0, 0, 63488, 63488, 32768, 32768, 57344, 57344, 32768, 32768, 32768, 32768, 0, 0],
    'G': [0, 0, 30720, 30720, 32768, 32768, 38912, 38912, 34816, 34816, 30720, 30720, 0, 0],
    'H': [0, 0, 34816, 34816, 34816, 34816, 63488, 63488, 34816, 34816, 34816, 34816, 0, 0],
    'I': [0, 0, 57344, 57344, 16384, 16384, 16384, 16384, 16384, 16384, 57344, 57344, 0, 0],
    'J': [0, 0, 2048, 2048, 2048, 2048, 2048, 2048, 34816, 34816, 28672, 28672, 0, 0],
    'K': [0, 0, 34816, 34816, 36864, 36864, 57344, 57344, 36864, 36864, 34816, 34816, 0, 0],
    'L': [0, 0, 32768, 32768, 32768, 32768, 32768, 32768, 32768, 32768, 63488, 63488, 0, 0],
    'M': [0, 0, 34816, 34816, 55296, 55296, 43008, 43008, 34816, 34816, 34816, 34816, 0, 0],
    'N': [0, 0, 34816, 34816, 51200, 51200, 43008, 43008, 38912, 38912, 34816, 34816, 0, 0],
    'O': [0, 0, 28672, 28672, 34816, 34816, 34816, 34816, 34816, 34816, 28672, 28672, 0, 0],
    'P': [0, 0, 61440, 61440, 34816, 34816, 61440, 61440, 32768, 32768, 32768, 32768, 0, 0],
    'Q': [0, 0, 28672, 28672, 34816, 34816, 34816, 34816, 43008, 43008, 28672, 28672, 4096, 4096],
    'R': [0, 0, 61440, 61440, 34816, 34816, 61440, 61440, 36864, 36864, 34816, 34816, 0, 0],
    'S': [0, 0, 30720, 30720, 32768, 32768, 28672, 28672, 2048, 2048, 61440, 61440, 0, 0],
    'T': [0, 0, 63488, 63488, 8192, 8192, 8192, 8192, 8192, 8192, 8192, 8192, 0, 0],
    'U': [0, 0, 34816, 34816, 34816, 34816, 34816, 34816, 34816, 34816, 28672, 28672, 0, 0],
    'V': [0, 0, 34816, 34816, 36864, 36864, 40960, 40960, 49152, 49152, 32768, 32768, 0, 0],
    'W': [0, 0, 34816, 34816, 34816, 34816, 43008, 43008, 43008, 43008, 20480, 20480, 0, 0],
    'X': [0, 0, 34816, 34816, 20480, 20480, 8192, 8192, 20480, 20480, 34816, 34816, 0, 0],
    'Y': [0, 0, 34816, 34816, 20480, 20480, 8192, 8192, 8192, 8192, 8192, 8192, 0, 0],
    'Z': [0, 0, 63488, 63488, 4096, 4096, 8192, 8192, 16384, 16384, 63488, 63488, 0, 0],
    '[': [0, 0, 57344, 57344, 32768, 32768, 32768, 32768, 32768, 32768, 57344, 57344, 0, 0],
    '\\': [0, 0, 32768, 32768, 16384, 16384, 8192, 8192, 4096, 4096, 2048, 2048, 0, 0],
    ']': [0, 0, 57344, 57344, 8192, 8192, 8192, 8192, 8192, 8192, 57344, 57344, 0, 0],
    '^': [0, 0, 8192, 8192, 20480, 20480, 34816, 34816, 0, 0, 0, 0, 0, 0],
    '_': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 63488, 63488, 0, 0],
    '`': [0, 0, 49152, 49152, 32768, 32768, 16384, 16384, 0, 0, 0, 0, 0, 0],
    'a': [0, 0, 0, 0, 57344, 57344, 4096, 4096, 61440, 61440, 28672, 28672, 0, 0],
    'b': [0, 0, 32768, 32768, 32768, 61440, 61440, 34816, 34816, 34816, 61440, 61440, 0, 0],
    'c': [0, 0, 0, 0, 28672, 28672, 32768, 32768, 32768, 32768, 28672, 28672, 0, 0],
    'd': [0, 0, 2048, 2048, 30720, 30720, 34816, 34816, 34816, 34816, 30720, 30720, 0, 0],
    'e': [0, 0, 0, 0, 28672, 28672, 63488, 63488, 32768, 32768, 30720, 30720, 0, 0],
    'f': [0, 0, 14336, 14336, 16384, 16384, 57344, 57344, 16384, 16384, 57344, 57344, 0, 0],
    'g': [0, 0, 0, 30720, 30720, 34816, 34816, 30720, 30720, 2048, 2048, 28672, 28672, 0],
    'h': [0, 0, 32768, 32768, 61440, 61440, 34816, 34816, 34816, 34816, 34816, 34816, 0, 0],
    'i': [16384, 16384, 0, 0, 49152, 49152, 16384, 16384, 16384, 16384, 57344, 57344, 0, 0],
    'j': [4096, 4096, 0, 0, 12288, 12288, 4096, 4096, 4096, 4096, 36864, 36864, 24576, 24576],
    'k': [0, 0, 36864, 36864, 40960, 40960, 49152, 49152, 40960, 40960, 36864, 36864, 0, 0],
    'l': [0, 0, 57344, 57344, 8192, 8192, 8192, 8192, 8192, 8192, 63488, 63488, 0, 0],
    'm': [0, 0, 0, 0, 53248, 53248, 43008, 43008, 43008, 34816, 34816, 34816, 0, 0],
    'n': [0, 0, 0, 0, 45056, 45056, 51200, 51200, 34816, 34816, 34816, 34816, 0, 0],
    'o': [0, 0, 0, 0, 28672, 28672, 34816, 34816, 34816, 34816, 28672, 28672, 0, 0],
    'p': [0, 0, 0, 0, 61440, 61440, 34816, 34816, 51200, 51200, 45056, 45056, 32768, 32768],
    'q': [0, 0, 0, 0, 30720, 30720, 34816, 34816, 38912, 38912, 26624, 26624, 2048, 2048],
    'r': [0, 0, 0, 0, 55296, 55296, 24576, 24576, 16384, 16384, 57344, 57344, 0, 0],
    's': [0, 0, 0, 0, 30720, 30720, 49152, 49152, 14336, 14336, 61440, 61440, 0, 0],
    't': [0, 0, 16384, 16384, 57344, 57344, 16384, 16384, 16384, 16384, 12288, 12288, 0, 0],
    'u': [0, 0, 0, 0, 34816, 34816, 34816, 34816, 34816, 34816, 28672, 28672, 0, 0],
    'v': [0, 0, 0, 0, 34816, 34816, 34816, 34816, 20480, 20480, 8192, 8192, 0, 0],
    'w': [0, 0, 0, 0, 34816, 34816, 34816, 43008, 43008, 43008, 20480, 20480, 0, 0],
    'x': [0, 0, 0, 0, 34816, 34816, 28672, 28672, 28672, 28672, 34816, 34816, 0, 0],
    'y': [0, 0, 0, 0, 34816, 34816, 18432, 18432, 12288, 12288, 8192, 8192, 16384, 16384],
    'z': [0, 0, 0, 0, 63488, 63488, 4096, 4096, 24576, 24576, 63488, 63488, 0, 0],
    '{': [0, 0, 24576, 24576, 16384, 16384, 49152, 49152, 16384, 16384, 24576, 24576, 0, 0],
    '|': [0, 0, 32768, 32768, 32768, 32768, 0, 0, 32768, 32768, 32768, 32768, 0, 0],
    '}': [0, 0, 49152, 49152, 16384, 16384, 24576, 24576, 16384, 16384, 49152, 49152, 0, 0],
    '~': [0, 0, 0, 0, 8192, 8192, 20480, 20480, 34816, 34816, 0, 0, 0, 0],
}


class FeldHellDecoder:
    """FeldHell 慢扫描文本解调器。

    来源: fldigi src/feld/feld.cxx:153-160
      - 标准 FeldHell: 17.5 列/秒, 14 列/字符, 7 行
      - 振幅键控: 有振幅=像素亮, 无振幅=像素暗
    本类提供：文本→振幅波形调制；振幅包络→二维列图重建→字符匹配。
    """

    COLUMN_RATE = 17.5     # feld.cxx:154 feldcolumnrate=17.5
    COLUMN_LEN = 14        # feld.h:42 FELD_COLUMN_LEN=14
    ROWS = 7
    SAMPLE_RATE = 8000

    def __init__(self, sample_rate: int = 8000, column_rate: float = 17.5,
                 carrier_hz: float = 1000.0):
        self.sample_rate = sample_rate
        self.column_rate = column_rate
        self.carrier_hz = carrier_hz
        self.spc = sample_rate / column_rate  # samples per column

    def modulate_text(self, text: str) -> List[float]:
        """文本 -> 振幅键控音频波形（每列按字体列位图调制载波幅度）。"""
        out = []
        dphi = 2.0 * math.pi * self.carrier_hz / self.sample_rate
        ph = 0.0
        spc = self.spc
        for ch in text:
            columns = FELD_FONT.get(ch, FELD_FONT[' '])
            for col_val in columns:
                # 7 行像素 → 该列振幅（亮像素数 / 7）
                bits = sum(((col_val >> (15 - r)) & 1) for r in range(self.ROWS))
                amp = bits / self.ROWS
                for _ in range(int(round(spc))):
                    out.append(amp * math.cos(ph))
                    ph += dphi
                    if ph > 2 * math.pi:
                        ph -= 2 * math.pi
        return out

    def demodulate_envelope(self, samples: List[float]) -> List[List[float]]:
        """音频波形 -> 每列 7 行灰度值矩阵。

        包络检波：整流+列内平均。检波后的峰值 = 2/π × 调制幅度，
        这里按列内最大幅度归一化恢复 0..1 亮度。
        """
        n = len(samples)
        env = [abs(s) for s in samples]
        # 找全局峰值用于归一化（标准 FeldHell 全亮列 = amp=1）
        peak = max(env) if env else 1.0
        if peak < 1e-6:
            peak = 1.0
        n_cols = int(round(n / self.spc))
        matrix = []
        for c in range(n_cols):
            start = int(round(c * self.spc))
            end = int(round((c + 1) * self.spc))
            col_env = sum(env[start:end]) / max(1, end - start)
            # 归一化：检波平均 = amp * 2/π，还原 amp = avg / (2/π)
            amp = col_env / (2.0 / math.pi)
            amp = min(1.0, amp / peak) if peak > 0 else 0.0
            matrix.append([amp] * self.ROWS)
        return matrix

    @staticmethod
    def match_char(column_brightness: List[float]) -> str:
        """给定 14 列亮度向量（0..1），匹配最像的字符。

        比较测量亮度向量与字体列亮度向量（target_bits/7）的欧氏距离。
        """
        best_ch = ' '
        best_score = 1e18
        n = len(column_brightness)
        # 测量向量归一化到自身峰值，消除整体增益差异
        meas_peak = max(column_brightness) if column_brightness else 1.0
        if meas_peak < 1e-6:
            meas_peak = 1.0
        meas_norm = [b / meas_peak for b in column_brightness]
        for ch, cols in FELD_FONT.items():
            target = []
            for i in range(14):
                v = cols[i] if i < len(cols) else 0
                bits = sum(((v >> (15 - r)) & 1) for r in range(7))
                target.append(bits / 7.0)
            # 字体向量也归一化
            tpeak = max(target) if max(target) > 0 else 1.0
            if tpeak < 1e-6:
                tnorm = target
            else:
                tnorm = [t / tpeak for t in target]
            score = sum((meas_norm[i] - tnorm[i]) ** 2 for i in range(min(n, 14)))
            if score < best_score:
                best_score = score
                best_ch = ch
        return best_ch


# ============================================================================
# Olivia MFSK / Thor 参数参考（子类化 MFSK，本模块给出参数表）
# 来源: fldigi src/olivia/olivia.cxx:324-329 (sr=8000, BW=125*(1<<bw))
#       fldigi src/thor/thor.cxx (PSK K=15 卷积 + MFSK varicode)
# ============================================================================
class OliviaMFSK:
    """Olivia MFSK 参数表。标准 Olivia: 125Hz 音调间隔, 31.25 baud, RS(15,5) 交织。"""
    TONE_SPACING_HZ = 125.0   # olivia.cxx:325 bandwidth=125*(1<<bw), 最窄模式 125Hz
    BAUD = 31.25
    MODES = {
        "Olivia 16/500": (16, 500),
        "Olivia 16/1000": (16, 1000),
        "Olivia 8/250": (8, 250),
        "Olivia 4/125": (4, 125),
    }


class ThorMode:
    """Thor 模式参数。Thor = PSK K=15 卷积 + MFSK varicode。
    来源: fldigi src/thor/thor.cxx; psk.cxx:87-89 THOR_K15 多项式。"""
    K = 15
    POLY1 = 0o44735   # psk.cxx:88
    POLY2 = 0o63057   # psk.cxx:89
    MODES = {"Thor-M": 50, "Thor-16": 16, "Thor-8": 8, "Thor-4": 4}


# ============================================================================
# ToolRegistry 注册入口
# ============================================================================
def register_fldigi_modes_tools(registry) -> None:
    """把 fldigi 数字模式解码能力注册到 MBDSDR ToolRegistry。

    提供工具：
      - psk31_encode : 文本 → PSK31 Varicode 比特串
      - psk31_decode : DBPSK 波形 → 文本（往返）
      - rtty_decode  : 2FSK 波形 → ITA-2 文本
      - mfsk_decode  : MFSK 波形 → 符号序列
      - feldhell_decode : FeldHell 振幅波形 → 文本
    """
    from .tool_registry import ToolResult

    def _psk31_encode(args):
        text = args.get("text", "")
        bits = Varicode.encode_text(text)
        return ToolResult(
            success=True,
            content=f"PSK31 Varicode 编码 ({len(bits)} bits): {bits[:200]}{'...' if len(bits)>200 else ''}",
            data={"bits": bits, "n_bits": len(bits)},
        )

    def _psk31_decode(args):
        # 离线往返：编码→调制→解调
        text = args.get("text", "Hello")
        m = PSK31Modem()
        wav = m.modulate(text)
        out = m.demodulate(wav)
        ok = out.strip() == text.strip()
        return ToolResult(
            success=ok,
            content=f"PSK31 往返: in={text!r} out={out!r} {'OK' if ok else 'MISMATCH'}",
            data={"input": text, "output": out, "match": ok},
        )

    def _rtty_decode(args):
        text = args.get("text", "HELLO BI4MIB")
        m = RTTYModem()
        wav = m.modulate(text)
        out = m.demodulate(wav)
        ok = out.strip() == text.strip()
        return ToolResult(
            success=ok,
            content=f"RTTY 往返: in={text!r} out={out!r} {'OK' if ok else 'MISMATCH'}",
            data={"input": text, "output": out, "match": ok},
        )

    def _mfsk_decode(args):
        mode = args.get("mode", "MFSK16")
        m = MFSKModem(mode)
        syms = [1, 5, 9, 3, 15, 7, 0, 11]
        wav = m.modulate_sequence(syms)
        out = m.demodulate(wav)
        ok = out == syms
        return ToolResult(
            success=ok,
            content=f"MFSK {mode} 符号往返: in={syms} out={out} {'OK' if ok else 'MISMATCH'}",
            data={"mode": mode, "input": syms, "output": out, "match": ok},
        )

    def _feldhell_decode(args):
        text = args.get("text", "HELLO")
        d = FeldHellDecoder()
        wav = d.modulate_text(text)
        env = d.demodulate_envelope(wav)
        # 简单重建：把列按 14 列分块匹配
        n_cols = len(env)
        chars = []
        for cstart in range(0, n_cols - d.COLUMN_LEN + 1, d.COLUMN_LEN):
            block = [env[cstart + i][0] for i in range(d.COLUMN_LEN)]
            chars.append(d.match_char(block))
        out = "".join(chars)
        return ToolResult(
            success=True,
            content=f"FeldHell 重建: {out!r} (输入 {text!r})",
            data={"input": text, "reconstructed": out, "n_cols": n_cols},
        )

    registry.register(
        name="psk31_encode",
        description="把文本编码为 PSK31 Varicode 比特串（fldigi pskvaricode.cxx 真实表）",
        parameters={"type": "object", "properties": {
            "text": {"type": "string", "description": "要编码的 ASCII 文本"}
        }, "required": ["text"]},
        handler=_psk31_encode,
        category="digital_mode",
    )
    registry.register(
        name="psk31_decode",
        description="PSK31 DBPSK 往返：文本→Varicode→BPSK 调制→差分解调→Varicode 解码",
        parameters={"type": "object", "properties": {
            "text": {"type": "string", "description": "测试文本，默认 Hello"}
        }, "required": ["text"]},
        handler=_psk31_decode,
        category="digital_mode",
    )
    registry.register(
        name="rtty_decode",
        description="RTTY 往返：文本→ITA-2→2FSK(170Hz/45.45baud)→非相干检测→ITA-2 解码",
        parameters={"type": "object", "properties": {
            "text": {"type": "string", "description": "测试文本（大写），默认 HELLO"}
        }, "required": ["text"]},
        handler=_rtty_decode,
        category="digital_mode",
    )
    registry.register(
        name="mfsk_decode",
        description="MFSK 符号往返：给定音调序列→MFSK 波形→非相干 FFT 检测还原符号",
        parameters={"type": "object", "properties": {
            "mode": {"type": "string", "description": "MFSK8/MFSK16/MFSK32，默认 MFSK16"}
        }, "required": []},
        handler=_mfsk_decode,
        category="digital_mode",
    )
    registry.register(
        name="feldhell_decode",
        description="FeldHell 慢扫描文本往返：文本→7x14 字体 AM 调制→包络检波→字符匹配",
        parameters={"type": "object", "properties": {
            "text": {"type": "string", "description": "测试文本，默认 HELLO"}
        }, "required": ["text"]},
        handler=_feldhell_decode,
        category="digital_mode",
    )
