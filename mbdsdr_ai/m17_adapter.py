"""
M17 数字语音协议栈移植（纯 numpy，无 GNU Radio / codec2 运行时）
=================================================================
本模块把 m17-cxx-demod (repos/m17) 里经过实践验证的 M17 物理层算法
原样翻译成 Python/numpy，所有关键常量都在注释里标注「来源: m17-cxx-demod 源文件:行号」。

移植自：
  * CRC16 (poly 0x5935, init 0xFFFF)       include/m17cxx/CRC16.h:12-70
  * 卷积码 (K=4 memory, rate 1/2,           include/m17cxx/Convolution.h:12-21
           polynomials 0o31/0o27)           include/m17cxx/M17Modulator.h:176-227
  * 删余矩阵 P1 (LSF) / P2 (voice)         include/m17cxx/Trellis.h:17-35
  * M17 帧结构 / 同步字 / LSF / Lich        include/m17cxx/M17Modulator.h:100-112, 277-333
  * 4FSK 符号映射 (+3/+1/-1/-3)             include/m17cxx/M17Modulator.h:137-147
  * 加扰 (DC sequence, 46 字节)             include/m17cxx/M17Randomizer.h:16-22, 61-76
  * Golay(24,12) (用于 Lich)                include/m17cxx/Golay24.h:87,184-201
  * 呼号 base-40 编码                       include/m17cxx/LinkSetupFrame.h:48-89

M17 空中接口关键参数（来源 m17project.org/specification + M17Modulator.h）：
  * 符号率 4800 sps (M17Modulator.h:94  1920 baseband / 10x upsample = 192 syms, 40ms)
  * 频偏 625 Hz（4FSK 四个频率间隔 625 Hz，即 +1875/+625/-625/-1875 Hz 相对中心）
  * 帧长 40 ms = 192 符号 = 384 bit（M17Framer.h:13  N=368 净荷 + 16 bit 同步 = 384）
  * LSF = 30 字节 (M17Modulator.h:100)
  * CRC16 多项式 0x5935，初值 0xFFFF (CRC16.h:12)
  * 卷积码约束长度 K=5 (memory=4)，速率 1/2，多项式 0o31 / 0o27 (M17Modulator.h:192-193)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
#  M17 全局常量
# ═══════════════════════════════════════════════════════════════════════

#: 符号率 sps。来源: M17Modulator.h:94  1920 baseband @ 48ksps / 10 = 4800 sps
M17_SYMBOL_RATE = 4800.0
#: 4FSK 频率偏移（Hz），符号间间隔 625 Hz。来源: M17 spec §3, M17Modulator.h:596-617 RRC 形状
M17_FREQ_DEVIATION = 625.0
#: 每帧符号数。来源: M17Modulator.h:93  symbols_t = array<int8_t, 192>
M17_SYMBOLS_PER_FRAME = 192
#: 每帧毫秒数。来源: M17Modulator.h:434  40ms 帧
M17_FRAME_MS = 40.0
#: 每帧净荷 bit 数（不含 16-bit 同步字）。来源: M17Framer.h:13  N=368
M17_PAYLOAD_BITS = 368
#: LSF 字节数。来源: M17Modulator.h:100  lsf_t = array<uint8_t,30>
M17_LSF_BYTES = 30

#: 同步字。来源: M17Modulator.h:109-111
M17_SYNC_STREAM = 0x3243   # 通用流同步
M17_SYNC_LSF = 0x55F7      # 链接设置帧同步
M17_SYNC_DATA = 0xFF5D     # 语音/数据包帧同步

#: CRC16 多项式与初值。来源: CRC16.h:12  CRC16<0x5935, 0xFFFF>
M17_CRC_POLY = 0x5935
M17_CRC_INIT = 0xFFFF

#: 卷积码生成多项式（八进制）。来源: M17Modulator.h:192-193
#:   g1 = 0o31 = 11001_2 = 0x19  (feedback taps on memory[0..4])
#:   g2 = 0o27 = 10111_2 = 0x17
M17_CONV_G1 = 0o31
M17_CONV_G2 = 0o27
M17_CONV_K = 5              # 约束长度（memory=4 + 1）

#: 删余矩阵。来源: Trellis.h:17-35
#:   P1: 61 位，在第 2,6,10,14,... 位删余（每 4 个删 1 个）
#:   P2: 12 位 [1,1,1,1,1,1, 1,1,1,1,1,0]，速率 6/11
M17_PUNCTURE_P1 = tuple(0 if (i - 2) % 4 == 0 and i >= 2 else 1 for i in range(61))
M17_PUNCTURE_P2 = (1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0)

#: M17 加扰序列（46 字节 DC）。来源: M17Randomizer.h:16-22
M17_DC_SEQUENCE = bytes([
    0xD6, 0xB5, 0xE2, 0x30, 0x82, 0xFF, 0x84, 0x62,
    0xBA, 0x4E, 0x96, 0x90, 0xD8, 0x98, 0xDD, 0x5D,
    0x0C, 0xC8, 0x52, 0x43, 0x91, 0x1D, 0xF8, 0x6E,
    0x68, 0x2F, 0x35, 0xDA, 0x14, 0xEA, 0xCD, 0x76,
    0x19, 0x8D, 0xD5, 0x80, 0xD1, 0x33, 0x87, 0x13,
    0x57, 0x18, 0x2D, 0x29, 0x78, 0xC3,
])

#: Golay(24,12) 多项式。来源: Golay24.h:87  POLY = 0xC75
M17_GOLAY_POLY = 0xC75


# ═══════════════════════════════════════════════════════════════════════
#  CRC16 —— 逐位移植自 CRC16.h
# ═══════════════════════════════════════════════════════════════════════
class M17CRC16:
    """M17 CRC-16 (poly=0x5935, init=0xFFFF, MSB-first).

    算法（来源 CRC16.h:12-70）：
      * reset(): reg=0xFFFF，再做 16 次 LSB-first 移位（预热）。
        对 0x5935 而言，这 16 步后 reg 仍是 0xFFFF（CRC16Test.cpp:24 验证）。
      * __call__(byte): 逐位 MSB-first 喂入 8 bit。
      * get(): 再做 16 次无输入移位，输出 FCS。
    """

    MASK = 0xFFFF
    LSB = 0x0001
    MSB = 0x8000

    def __init__(self, poly: int = M17_CRC_POLY, init: int = M17_CRC_INIT):
        self.poly = poly
        self.init = init
        self.reg = init
        self.reset()

    def reset(self) -> None:
        """来源 CRC16.h:21-34 —— 16 步 LSB-first 预热。"""
        reg = self.init
        for _ in range(16):
            bit = reg & self.LSB
            if bit:
                reg ^= self.poly
            reg >>= 1
            if bit:
                reg |= self.MSB
        self.reg = reg & self.MASK

    def _crc_bit(self, bit: int) -> None:
        """单 bit MSB-first 更新。来源 CRC16.h:43-48。"""
        msb = self.reg & self.MSB
        self.reg = ((self.reg << 1) & self.MASK) | (bit & 1)
        if msb:
            self.reg ^= self.poly

    def update(self, data: bytes) -> None:
        """喂入一串字节（MSB first）。来源 CRC16.h:41-50。"""
        for byte in data:
            for i in range(8):
                self._crc_bit((byte >> (7 - i)) & 1)

    def digest(self) -> int:
        """输出 16-bit FCS。来源 CRC16.h:52-62。"""
        reg = self.reg
        for _ in range(16):
            msb = reg & self.MSB
            reg = (reg << 1) & self.MASK
            if msb:
                reg ^= self.poly
        return reg & self.MASK

    def digest_bytes(self) -> bytes:
        """大端 2 字节。来源 CRC16.h:64-69。"""
        c = self.digest()
        return bytes([(c >> 8) & 0xFF, c & 0xFF])


def m17_crc16(data: bytes) -> int:
    """便捷函数：对 data 计算 M17 CRC16。"""
    c = M17CRC16()
    c.update(data)
    return c.digest()


# ═══════════════════════════════════════════════════════════════════════
#  卷积码 —— 移植自 Convolution.h + M17Modulator.h:176-227
# ═══════════════════════════════════════════════════════════════════════
def _convolve_bit(poly: int, memory: int) -> int:
    """popcount(poly & memory) & 1。来源 Convolution.h:12-15。"""
    return (poly & memory).bit_count() & 1


def _update_memory(memory: int, x: int, k: int = 4) -> int:
    """memory = (memory << 1 | x) & ((1<<(k+1))-1)。来源 Convolution.h:18-21。"""
    return ((memory << 1) | (x & 1)) & ((1 << (k + 1)) - 1)


def m17_conv_encode(data: bytes) -> bytes:
    """M17 卷积码编码（rate 1/2, K=5, g1=0o31, g2=0o27）。

    来源 M17Modulator.h:176-227：
      * MSB-first 读入每个数据 bit；
      * 每个输入 bit 输出 g1 再 g2（先 g1 后 g2）；
      * 末尾 flush 4 个 0 tail bits；
      * 输出打包成字节（MSB first）。
    """
    out_bits: List[int] = []
    memory = 0
    for byte in data:
        for i in range(8):
            x = (byte >> (7 - i)) & 1
            memory = _update_memory(memory, x, k=4)
            out_bits.append(_convolve_bit(M17_CONV_G1, memory))
            out_bits.append(_convolve_bit(M17_CONV_G2, memory))
    # flush 4 tail bits
    for _ in range(4):
        memory = _update_memory(memory, 0, k=4)
        out_bits.append(_convolve_bit(M17_CONV_G1, memory))
        out_bits.append(_convolve_bit(M17_CONV_G2, memory))
    # 打包成字节
    n_bytes = (len(out_bits) + 7) // 8
    out = bytearray(n_bytes)
    for i, b in enumerate(out_bits):
        if b:
            out[i // 8] |= 1 << (7 - (i % 8))
    return bytes(out)


def m17_viterbi_decode(coded_bits: List[int], info_bits: int) -> List[int]:
    """硬判决 Viterbi 译码（K=5, 32 状态, rate 1/2）。

    与 m17_conv_encode 对称：coded_bits 是 g1,g2,g1,g2,... 的硬判决序列
    （0/1），译码后返回 info_bits 个信息 bit（去掉 4 个 tail bit）。

    注意：卷积码 memory 是 5 bit（K=5, 约束长度 5，4 级移位寄存器），
    来源 Convolution.h:18-21 update_memory<4> 的掩码是 (1<<5)-1=31，
    多项式 0o31/0o27 都是 5-bit，所以状态数 = 2^5 = 32。
    """
    n_states = 32  # 2^5 (K=5, memory=4)
    # 累积路径度量
    pm = [float("inf")] * n_states
    pm[0] = 0.0
    # 回溯路径：每步每状态的前一状态 + 输入 bit
    history: List[List[Tuple[int, int]]] = []

    n_trellis = info_bits + 4  # 含 tail
    for step in range(n_trellis):
        next_pm = [float("inf")] * n_states
        next_prev: List[Tuple[int, int]] = [(-1, -1)] * n_states
        # 每个新状态 ns 由两个前状态 ps 到达：ns = (ps<<1 | x) & 31
        #   => ps = ns>>1，x = ns&1；ps 的 bit4 可为 0 或 1
        for ns in range(n_states):
            for ps in (ns >> 1, (ns >> 1) | 16):
                g1 = _convolve_bit(M17_CONV_G1, ns)
                g2 = _convolve_bit(M17_CONV_G2, ns)
                if 2 * step + 1 < len(coded_bits):
                    r1 = coded_bits[2 * step]
                    r2 = coded_bits[2 * step + 1]
                    cost = (g1 ^ r1) + (g2 ^ r2)
                else:
                    cost = 0
                cand = pm[ps] + cost
                if cand < next_pm[ns]:
                    next_pm[ns] = cand
                    next_prev[ns] = (ps, ns & 1)
        pm = next_pm
        history.append(next_prev)

    # 尾 bit 全 0，最终状态应为 0；取全局最小度量作为兜底
    best = int(np.argmin(pm))
    out_rev: List[int] = []
    state = best
    for step in range(n_trellis - 1, -1, -1):
        ps, x = history[step][state]
        out_rev.append(x)
        state = ps
    out_rev.reverse()
    return out_rev[:info_bits]


# ═══════════════════════════════════════════════════════════════════════
#  4FSK 调制 / 解调
# ═══════════════════════════════════════════════════════════════════════
#: dibit -> 符号值。来源 M17Modulator.h:137-147
#:   dibit 0 -> +1, 1 -> +3, 2 -> -1, 3 -> -3
_M17_DIBIT_TO_SYMBOL = (1, 3, -1, -3)
_M17_SYMBOL_TO_DIBIT = {1: 0, 3: 1, -1: 2, -3: 3}


def m17_bits_to_symbols(bits: List[int]) -> np.ndarray:
    """bit 串 -> 4FSK 符号串 (+/-1/+/-3)。

    每个符号传 2 bit (dibit)，先 bit1 后 bit0。
    来源 M17Modulator.h:150-159 bits_to_symbols。
    """
    assert len(bits) % 2 == 0, "bit 数必须为偶数"
    syms = np.empty(len(bits) // 2, dtype=np.int8)
    for i in range(0, len(bits), 2):
        dibit = (bits[i] << 1) | bits[i + 1]
        syms[i // 2] = _M17_DIBIT_TO_SYMBOL[dibit]
    return syms


def m17_symbols_to_bits(symbols: np.ndarray) -> List[int]:
    """4FSK 符号串 -> bit 串。来源 Util.h:115-126 from_4fsk。"""
    out: List[int] = []
    for s in symbols:
        dibit = _M17_SYMBOL_TO_DIBIT[int(np.clip(s, -3, 3))]
        out.append((dibit >> 1) & 1)
        out.append(dibit & 1)
    return out


def m17_fsk_modulate(symbols: np.ndarray, sample_rate: float = 48000.0,
                     symbol_rate: float = M17_SYMBOL_RATE,
                     deviation: float = M17_FREQ_DEVIATION,
                     center_freq: float = 0.0) -> np.ndarray:
    """符号串 -> 4FSK 复数基带（相位积分）。

    每个符号占用 sample_rate/symbol_rate 个采样；瞬时频偏 = deviation * symbol/3。
    """
    samples_per_symbol = sample_rate / symbol_rate
    n_sym = len(symbols)
    n_total = int(round(n_sym * samples_per_symbol))
    t = np.arange(n_total) / sample_rate
    # 每符号的频偏序列
    f_offset = deviation * symbols.astype(float) / 3.0  # +1875/+625/-625/-1875 Hz
    f_per_sample = np.repeat(f_offset, int(round(samples_per_symbol)))[:n_total]
    phase = 2 * np.pi * np.cumsum(f_per_sample) / sample_rate
    return np.exp(1j * (phase + 2 * np.pi * center_freq * t))


def m17_fsk_demodulate(iq: np.ndarray, sample_rate: float = 48000.0,
                       symbol_rate: float = M17_SYMBOL_RATE,
                       deviation: float = M17_FREQ_DEVIATION) -> np.ndarray:
    """4FSK 复数基带 -> 硬判决符号串 (+/-1/+/-3)。

    鉴频 (arg 差) -> 按符号周期抽样 -> 量化到最近的 {-3,-1,+1,+3}。
    """
    # 鉴频
    phase = np.angle(iq)
    dphase = np.diff(np.unwrap(phase))
    freq = dphase * sample_rate / (2 * np.pi)
    # 符号中心抽样：跳过半个符号
    sps = sample_rate / symbol_rate
    offset = int(round(sps / 2))
    idx = np.arange(offset, len(freq), int(round(sps)))
    samples = freq[idx]
    # 量化：频偏 / (deviation/3) -> [-3,3]
    norm = samples / (deviation / 3.0)
    syms = np.clip(np.round(norm), -3, 3).astype(np.int8)
    # 只保留奇数（+/-1/+/-3）
    syms = np.where(syms == 0, 1, syms)
    syms = np.where(syms == -2, -1, syms)
    syms = np.where(syms == 2, 1, syms)
    return syms


# ═══════════════════════════════════════════════════════════════════════
#  Golay(24,12) —— 用于 Lich
# ═══════════════════════════════════════════════════════════════════════
def golay24_encode(data12: int) -> int:
    """12 bit data -> 24 bit Golay 码字。来源 Golay24.h:184-201。"""
    reg = data12 & 0xFFF
    for _ in range(12):
        if reg & 1:
            reg ^= M17_GOLAY_POLY
        reg >>= 1
    codeword = reg | ((data12 & 0xFFF) << 11)
    # 整体奇偶校验位
    parity = codeword.bit_count() & 1
    return ((codeword << 1) | parity) & 0xFFFFFF


# ═══════════════════════════════════════════════════════════════════════
#  LSF / 呼号 base-40 编码
# ═══════════════════════════════════════════════════════════════════════
#: 呼号字符映射。来源 LinkSetupFrame.h:97  "xABC...Z0..9-/."
_M17_CALL_CHARMAP = "xABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/."


def m17_encode_callsign(callsign: str) -> bytes:
    """呼号字符串 -> 6 字节 base-40 大端。来源 LinkSetupFrame.h:48-89。"""
    if not callsign:
        return b"\xff\xff\xff\xff\xff\xff"
    value = 0
    for ch in reversed(callsign):
        value *= 40
        if "A" <= ch <= "Z":
            value += ord(ch) - ord("A") + 1
        elif "0" <= ch <= "9":
            value += ord(ch) - ord("0") + 27
        elif ch == "-":
            value += 37
        elif ch == "/":
            value += 38
        elif ch == ".":
            value += 39
        # 其他字符按 0 处理
    return value.to_bytes(6, "big")


def m17_decode_callsign(encoded: bytes) -> str:
    """6 字节 base-40 大端 -> 呼号字符串。来源 LinkSetupFrame.h:95-121。"""
    if encoded == b"\xff\xff\xff\xff\xff\xff":
        return "BROADCAST"
    value = int.from_bytes(encoded, "big")
    chars: List[str] = []
    while value:
        chars.append(_M17_CALL_CHARMAP[value % 40])
        value //= 40
    return "".join(chars)


def m17_build_lsf(src: str, dst: str, type_ls: int = 0x0005) -> bytes:
    """构造 30 字节 LSF。来源 M17Modulator.h:277-296。

    LSF = src(6) + dst(6) + type(2) + CRC16(2 over first 28 bytes)
    默认 type=0x0005 表示 VOICE/STREAM（M17Modulator.h:286-287 lsf[12]=0, lsf[13]=5）。
    """
    lsf = bytearray(M17_LSF_BYTES)
    lsf[0:6] = m17_encode_callsign(src)
    lsf[6:12] = m17_encode_callsign(dst)
    lsf[12] = (type_ls >> 8) & 0xFF
    lsf[13] = type_ls & 0xFF
    crc = M17CRC16()
    crc.update(bytes(lsf[:28]))
    lsf[28:30] = crc.digest_bytes()
    return bytes(lsf)


def m17_verify_lsf(lsf: bytes) -> Tuple[bool, str, str]:
    """校验 LSF 并解析 src/dst。返回 (ok, src, dst)。"""
    if len(lsf) != M17_LSF_BYTES:
        return False, "", ""
    expected = int.from_bytes(lsf[28:30], "big")
    crc = M17CRC16()
    crc.update(lsf[:28])
    ok = crc.digest() == expected
    src = m17_decode_callsign(lsf[0:6])
    dst = m17_decode_callsign(lsf[6:12])
    return ok, src, dst


# ═══════════════════════════════════════════════════════════════════════
#  字节加扰（XOR DC）
# ═══════════════════════════════════════════════════════════════════════
def m17_scramble(frame46: bytes) -> bytes:
    """对 46 字节帧做 M17 加扰（XOR DC）。来源 M17Randomizer.h:65-75。

    加扰与解扰是同一操作（XOR 对称）。
    """
    assert len(frame46) == 46
    return bytes(a ^ b for a, b in zip(frame46, M17_DC_SEQUENCE))


# ═══════════════════════════════════════════════════════════════════════
#  ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_m17_tools(registry) -> None:
    """把 M17 CRC / 卷积码 / 4FSK 调制解调 / LSF 构造 注册进 ToolRegistry。"""
    from mbdsdr_ai.tool_registry import ToolResult

    def _crc_roundtrip(args):
        """对一段 hex 数据计算 M17 CRC16，并验证追加 FCS 后二次校验为 0。"""
        try:
            data = bytes.fromhex(str(args.get("data", "41")))
            crc = M17CRC16()
            crc.update(data)
            fcs = crc.digest()
            # 校验：data + fcs 再算一次应为 0
            crc2 = M17CRC16()
            crc2.update(data + fcs.to_bytes(2, "big"))
            residual = crc2.digest()
            out = {
                "data_hex": data.hex(),
                "crc16_hex": f"{fcs:04X}",
                "crc16_dec": fcs,
                "residual_after_append": residual,
                "verify_ok": residual == 0,
                "source": "m17-cxx-demod CRC16.h:12-70",
            }
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"M17 CRC 失败: {e}")

    def _conv_roundtrip(args):
        """卷积码编码 -> Viterbi 硬判决译码往返。"""
        try:
            n_bits = int(args.get("info_bits", 64))
            rng = np.random.default_rng(int(args.get("seed", 0)))
            info = rng.integers(0, 2, size=n_bits).tolist()
            # bytes
            info_bytes = bytearray((n_bits + 7) // 8)
            for i, b in enumerate(info):
                if b:
                    info_bytes[i // 8] |= 1 << (7 - (i % 8))
            coded = m17_conv_encode(bytes(info_bytes))
            coded_bits = [(coded[i // 8] >> (7 - (i % 8))) & 1
                          for i in range(len(coded) * 8)]
            decoded = m17_viterbi_decode(coded_bits, n_bits)
            ok = decoded == info
            out = {
                "info_bits": n_bits,
                "coded_bytes": len(coded),
                "bit_errors": sum(a ^ b for a, b in zip(decoded, info)),
                "roundtrip_ok": ok,
                "polys": {"g1_oct": "0o31", "g2_oct": "0o27", "K": 5},
                "source": "m17-cxx-demod Convolution.h:12-21, M17Modulator.h:176-227",
            }
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"M17 卷积码往返失败: {e}")

    def _fsk_roundtrip(args):
        """4FSK 调制 -> 鉴频解调 -> 硬判决 bit 往返。"""
        try:
            n_bits = int(args.get("bits", 96))
            rng = np.random.default_rng(int(args.get("seed", 1)))
            bits = rng.integers(0, 2, size=n_bits).tolist()
            syms = m17_bits_to_symbols(bits)
            iq = m17_fsk_modulate(syms, sample_rate=48000.0)
            rx_syms = m17_fsk_demodulate(iq, sample_rate=48000.0)
            # 对齐长度
            m = min(len(syms), len(rx_syms))
            bit_errors = sum(a ^ b for a, b in zip(bits[:2 * m],
                                                  m17_symbols_to_bits(rx_syms[:m])))
            out = {
                "bits": n_bits,
                "symbols_tx": int(len(syms)),
                "symbols_rx": int(len(rx_syms)),
                "iq_samples": int(len(iq)),
                "symbol_rate_sps": M17_SYMBOL_RATE,
                "deviation_hz": M17_FREQ_DEVIATION,
                "bit_errors": int(bit_errors),
                "roundtrip_ok": bit_errors == 0,
                "source": "m17-cxx-demod M17Modulator.h:137-159,593-632",
            }
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"M17 4FSK 往返失败: {e}")

    def _lsf_build(args):
        """构造并校验一个 M17 LSF。"""
        try:
            src = str(args.get("src", "W9GL"))
            dst = str(args.get("dst", ""))
            lsf = m17_build_lsf(src, dst)
            ok, s, d = m17_verify_lsf(lsf)
            out = {
                "lsf_hex": lsf.hex(),
                "lsf_len": len(lsf),
                "src": s, "dst": d,
                "crc_ok": ok,
                "source": "m17-cxx-demod M17Modulator.h:277-296, LinkSetupFrame.h:48-89",
            }
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"M17 LSF 构造失败: {e}")

    registry.register(
        name="m17_crc16",
        description=("M17 CRC-16 (poly=0x5935, init=0xFFFF, MSB-first)。"
                     "对输入 hex 字符串计算 16-bit FCS，并验证 data+FCS 二次校验余数为 0。"
                     "已知向量：'A' -> 0x206E, '123456789' -> 0x772B。"),
        parameters={
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "hex 字符串", "default": "41"},
            },
            "required": [],
        },
        handler=_crc_roundtrip,
        category="digital_voice",
    )

    registry.register(
        name="m17_conv_roundtrip",
        description=("M17 卷积码 (K=5, rate 1/2, g1=0o31, g2=0o27) 编码 + Viterbi "
                     "硬判决译码往返。默认 64 bit 信息，无信道错误，应全对。"),
        parameters={
            "type": "object",
            "properties": {
                "info_bits": {"type": "integer", "default": 64},
                "seed": {"type": "integer", "default": 0},
            },
            "required": [],
        },
        handler=_conv_roundtrip,
        category="digital_voice",
    )

    registry.register(
        name="m17_4fsk_roundtrip",
        description=("M17 4FSK 调制解调往返：bit->dibit->符号(+3/+1/-1/-3)->"
                     "48ksps 复数 FSK(频偏 625Hz/级)->鉴频->硬判决->bit。"
                     "默认 96 bit，无噪应全对。"),
        parameters={
            "type": "object",
            "properties": {
                "bits": {"type": "integer", "default": 96},
                "seed": {"type": "integer", "default": 1},
            },
            "required": [],
        },
        handler=_fsk_roundtrip,
        category="digital_voice",
    )

    registry.register(
        name="m17_lsf_build",
        description=("构造 M17 Link Setup Frame (30 字节)：src(6)+dst(6)+type(2)+CRC16(2)。"
                     "呼号按 base-40 编码，dst 留空表示 BROADCAST(0xFFFFFF...)。"
                     "返回 hex 与 CRC 校验结果。"),
        parameters={
            "type": "object",
            "properties": {
                "src": {"type": "string", "default": "W9GL"},
                "dst": {"type": "string", "default": ""},
            },
            "required": [],
        },
        handler=_lsf_build,
        category="digital_voice",
    )


if __name__ == "__main__":
    # 自测：CRC 已知向量
    assert m17_crc16(b"A") == 0x206E, hex(m17_crc16(b"A"))
    assert m17_crc16(b"123456789") == 0x772B, hex(m17_crc16(b"123456789"))
    print("M17 CRC 已知向量 OK: 'A'=0x%04X, '123456789'=0x%04X" %
          (m17_crc16(b"A"), m17_crc16(b"123456789")))
    # 卷积码往返
    info = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1]
    info_bytes = bytearray(2)
    for i, b in enumerate(info):
        if b:
            info_bytes[i // 8] |= 1 << (7 - (i % 8))
    coded = m17_conv_encode(bytes(info_bytes))
    coded_bits = [(coded[i // 8] >> (7 - (i % 8))) & 1 for i in range(len(coded) * 8)]
    dec = m17_viterbi_decode(coded_bits, 16)
    assert dec == info, (dec, info)
    print("M17 卷积码 16-bit 往返 OK")
    # 4FSK 往返
    bits = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1,
            1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1]
    syms = m17_bits_to_symbols(bits)
    iq = m17_fsk_modulate(syms)
    rx = m17_fsk_demodulate(iq)
    print("M17 4FSK: tx syms=%d rx syms=%d" % (len(syms), len(rx)))
