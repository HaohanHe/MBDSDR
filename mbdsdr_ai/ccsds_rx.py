# SPDX-License-Identifier: MIT
"""CCSDS 数字接收链（干净室 MIT 独立实现）。

本模块依据 CCSDS 公开标准（TM 同步与信道编码）与 fsphil SSDV 规格 ``docs/learn/
phase14/SSDV_SSTV_SPEC.md`` 独立实现，供 2026-10-08..10-10 卫星 SSDV 下传接收使用。

参考实现（GPL，**只学机制、未复制代码**）：
  - ``repos/by2hit_arcssd-go/engine/ccsds/viterbi27.c``（Phil Karn KA9Q K=7 r=1/2 Viterbi）
  - ``repos/by2hit_arcssd-go/engine/ccsds/ccsds.c``（ASM 32-bit 同步状态机）
  - ``repos/by2hit_arcssd-go/engine/ccsds/randomizer.c``（255 字节 PN 解扰）
  - ``repos/by2hit_arcssd-go/engine/ccsds/direwolf_demod_afsk.c``（AFSK 解调思路）

可复用本仓既有 MIT 模块：
  - :mod:`mbdsdr_ai.fec` —— ``ReedSolomon`` / ``Scrambler``
  - :mod:`mbdsdr_ai.ax25` —— ``AFSKModem``
  - :mod:`mbdsdr_ai.multimon_decoders` —— ``AFSK1200Demod``

链路（每一级都可独立喂入、可插拔）::

    AFSK 音频 ──► 0/1 比特 ──► ASM 帧同步 ──► [Viterbi] ──► 解扰 ──► [RS] ──► SSDV 256B 包

红线：
  - 物理层未确定的参数（采样率/波特率/mark/space/帧长）全部为具名参数/常量，
    不硬编码猜测；未拿到官方日程前只离线测试。
  - 不内置任何呼号；无数据时诚实空态（返回空列表）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .fec import ReedSolomon, Scrambler
from .multimon_decoders import AFSK1200Demod

# ===========================================================================
# 具名常量（CCSDS 公开标准；未确定的物理层参数一律不写死）
# ===========================================================================

#: CCSDS 附着同步字（Attached Sync Marker），32 bit，先发 MSB。
CCSDS_ASM_WORD: int = 0x1ACFFC1D

#: 卷积码约束长度 K=7（6 级移位寄存器 + 当前输入）。
CONV_K: int = 7
#: 码率 1/2 → 每个信息比特输出 2 个编码比特。
CONV_RATE: int = 2
#: 生成多项式 G1（八进制 133 = 0x5B = 101_1011）。
CONV_G1: int = 0o133
#: 生成多项式 G2（八进制 171 = 0x79 = 111_1001）。
CONV_G2: int = 0o171
#: 状态数 = 2^(K-1) = 64。
CONV_STATES: int = 1 << (CONV_K - 1)

#: ASM 同步允许的最大汉明距离（0 = 严格匹配；链路差时可调到 1~2）。
DEFAULT_ASM_MAX_HAMMING: int = 0

#: CCSDS RS(255,223)：32 校验字节，可纠 16 字节（复用 fec.ReedSolomon 默认）。
RS_NSYM: int = 32
#: SSDV 固定包长（fsphil 格式）。
SSDV_PACKET_LEN: int = 256

# 卫星 AFSK 物理层候选（规格 §2.2；官方日程未定，仅作参数默认，不改芯片/频率）。
#: 1200 baud 卫星 AFSK 候选：mark=1200 / space=2400 Hz。
SAT_AFSK_1200_MARK: float = 1200.0
SAT_AFSK_1200_SPACE: float = 2400.0
#: 2400 baud 卫星 AFSK 候选：mark=1200 / space=2400 Hz。
SAT_AFSK_2400_MARK: float = 1200.0
SAT_AFSK_2400_SPACE: float = 2400.0


# ===========================================================================
# 比特 <-> 字节工具（MSB-first，与 ccsds.c mask_bit_out=0x80 一致）
# ===========================================================================

def bits_to_bytes_msb(bits: Sequence[int]) -> bytes:
    """把 0/1 序列按 MSB-first 打包成字节；尾部不足 8 位补 0。"""
    out = bytearray()
    cur = 0
    n = 0
    for b in bits:
        cur = (cur << 1) | (1 if b else 0)
        n += 1
        if n == 8:
            out.append(cur)
            cur = 0
            n = 0
    if n:
        out.append(cur << (8 - n))
    return bytes(out)


def bytes_to_bits_msb(data: bytes) -> List[int]:
    """把字节按 MSB-first 拆成 0/1 序列。"""
    bits: List[int] = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def _popcount32(x: int) -> int:
    return bin(x).count("1")


def _parity7(x: int) -> int:
    """7-bit 值的奇偶（GF(2) 求和）。"""
    return bin(x & 0x7F).count("1") & 1


# ===========================================================================
# 卷积码 K=7 r=1/2 编码器
# ===========================================================================

class ConvolutionalEncoder:
    """CCSDS K=7 r=1/2 系统卷积编码器（干净室实现）。

    约定：6 级移位寄存器 ``reg``（bit5=最近一次输入…bit0=最早）。新输入比特 ``b``
    与 ``reg`` 拼成 7-bit 向量 ``full = (b<<6) | reg``（bit6=当前输入），两支路输出
    为 ``full`` 与 G1/G2 按位与后的奇偶。编码后 ``reg = ((reg<<1)|b) & 0x3F``。

    输出顺序：每个信息比特先发 G1 支路、再发 G2 支路。
    """

    def __init__(self, g1: int = CONV_G1, g2: int = CONV_G2):
        self.g1 = g1 & 0x7F
        self.g2 = g2 & 0x7F
        self.reset()

    def reset(self) -> None:
        self.reg: int = 0

    def _outputs(self, b: int) -> Tuple[int, int]:
        full = ((b & 1) << 6) | self.reg
        o1 = _parity7(full & self.g1)
        o2 = _parity7(full & self.g2)
        self.reg = ((self.reg << 1) | (b & 1)) & (CONV_STATES - 1)
        return o1, o2

    def encode_bits(self, bits: Sequence[int]) -> List[Tuple[int, int]]:
        """编码 0/1 序列，返回 (g1, g2) 符号对列表。"""
        return [self._outputs(int(b)) for b in bits]

    def encode_bytes(self, data: bytes, tail: int = 0) -> List[Tuple[int, int]]:
        """MSB-first 编码字节；``tail`` 个 0 比特收尾（把寄存器归零，便于 Viterbi
        从零状态收尾回溯）。"""
        pairs = self.encode_bits(bytes_to_bits_msb(data))
        if tail:
            pairs.extend(self.encode_bits([0] * tail))
        return pairs


# ===========================================================================
# Viterbi 解码器（硬判决，块式 ACS + 回溯）
# ===========================================================================

class ViterbiDecoder:
    """K=7 r=1/2 硬判决 Viterbi 解码器（干净室实现）。

    - 分支度量：接收符号对与期望符号对的汉明距离。
    - ACS：状态打包 ``s'=((s<<1)|u)&63``；对下一状态 ``ns``，信息位 ``u=ns&1``，
      两个前驱为 ``ns>>1`` 与 ``(ns>>1)|32``（仅相差被移出的最老比特），选度量小者。
    - 回溯：从最终最小度量状态出发，沿前驱链回推，信息位取路径状态的 LSB。

    与 :class:`ConvolutionalEncoder` 配对使用（同 g1/g2）。
    """

    def __init__(self, g1: int = CONV_G1, g2: int = CONV_G2):
        self.g1 = g1 & 0x7F
        self.g2 = g2 & 0x7F
        # expected[state][input] = (out1, out2)
        self._expected: List[List[Tuple[int, int]]] = [
            [(0, 0), (0, 0)] for _ in range(CONV_STATES)
        ]
        for s in range(CONV_STATES):
            for b in (0, 1):
                full = ((b & 1) << 6) | s
                self._expected[s][b] = (_parity7(full & self.g1),
                                        _parity7(full & self.g2))

    def decode(self, pairs: Sequence[Tuple[int, int]]) -> List[int]:
        """对 (g1,g2) 符号对序列做块式 Viterbi，返回信息 0/1 比特。

        本编码器状态打包为 ``s' = ((s<<1)|u)&63``（u 进 bit0），因此从下一状态
        ``ns`` 反推时：信息比特恒为 ``u = ns & 1``；两个前驱仅相差被移出的最老比特
        ``ns>>1`` 与 ``(ns>>1)|32``，ACS 在二者间择优并记录走向，回溯时信息位直接取
        路径状态的 LSB。
        """
        n = len(pairs)
        if n == 0:
            return []
        INF = 1 << 30
        metrics = [INF] * CONV_STATES
        metrics[0] = 0  # 已知从零状态出发（preamble/ASM 之后）

        decisions: List[List[int]] = []
        for rx in pairs:
            rx1, rx2 = int(rx[0]), int(rx[1])
            new_metrics = [INF] * CONV_STATES
            dec = [0] * CONV_STATES
            for ns in range(CONV_STATES):
                b = ns & 1  # 该阶段的信息比特
                p0 = ns >> 1
                p1 = p0 | 32
                e0 = self._expected[p0][b]
                e1 = self._expected[p1][b]
                d0 = metrics[p0] + abs(rx1 - e0[0]) + abs(rx2 - e0[1])
                d1 = metrics[p1] + abs(rx1 - e1[0]) + abs(rx2 - e1[1])
                if d0 <= d1:
                    new_metrics[ns] = d0
                    dec[ns] = 0  # 取前驱 p0
                else:
                    new_metrics[ns] = d1
                    dec[ns] = 1  # 取前驱 p1
            metrics = new_metrics
            decisions.append(dec)

        # 从最终最小度量状态回溯；信息位 = 路径状态的 LSB
        state = min(range(CONV_STATES), key=lambda s: metrics[s])
        bits: List[int] = []
        for stage in range(n - 1, -1, -1):
            bits.append(state & 1)
            d = decisions[stage][state]
            state = (state >> 1) if d == 0 else ((state >> 1) | 32)
        bits.reverse()
        return bits


# ===========================================================================
# CCSDS ASM 帧同步状态机
# ===========================================================================

@dataclass
class AsmFramer:
    """32-bit ASM 搜索 -> 定长帧收集状态机。

    参数
    ----------
    sync_word : int
        32-bit 同步字（默认 :data:`CCSDS_ASM_WORD`），先发 MSB。
    frame_bits : int
        ASM 之后需要收集的编码比特数（**整帧编码长度，含卷积符号**）。
        注意：若后级接 Viterbi，这里填卷积编码后的总比特数（= 信息比特×2）。
    max_hamming : int
        ASM 允许的汉明误比特数（默认 0 = 严格匹配）。
    """

    sync_word: int = CCSDS_ASM_WORD
    frame_bits: int = SSDV_PACKET_LEN * 8
    max_hamming: int = DEFAULT_ASM_MAX_HAMMING

    # 内部状态
    _det: int = field(default=0, repr=False)
    _collecting: bool = field(default=False, repr=False)
    _buf: int = field(default=0, repr=False)
    _nbuf: int = field(default=0, repr=False)
    _got: int = field(default=0, repr=False)
    _bytes: bytearray = field(default_factory=bytearray, repr=False)

    def reset(self) -> None:
        self._det = 0
        self._collecting = False
        self._buf = 0
        self._nbuf = 0
        self._got = 0
        self._bytes = bytearray()

    def feed_bit(self, b: int) -> Optional[bytes]:
        """喂入一个 0/1 比特；收集满一帧时返回帧字节，否则 None。"""
        b = 1 if b else 0
        if not self._collecting:
            self._det = ((self._det << 1) | b) & 0xFFFFFFFF
            if _popcount32(self._det ^ (self.sync_word & 0xFFFFFFFF)) <= self.max_hamming:
                self._collecting = True
                self._buf = 0
                self._nbuf = 0
                self._got = 0
                self._bytes = bytearray()
            return None

        # 收集帧比特（MSB-first 打包）
        self._buf = (self._buf << 1) | b
        self._nbuf += 1
        self._got += 1
        frame: Optional[bytes] = None
        if self._nbuf == 8:
            self._bytes.append(self._buf)
            self._buf = 0
            self._nbuf = 0
        if self._got >= self.frame_bits:
            # 尾部不足一字节的比特并入最后一字节
            if self._nbuf:
                self._bytes.append(self._buf << (8 - self._nbuf))
            frame = bytes(self._bytes)
            self.reset()
        return frame

    def feed_bits(self, bits: Sequence[int]) -> List[bytes]:
        out: List[bytes] = []
        for b in bits:
            f = self.feed_bit(int(b))
            if f is not None:
                out.append(f)
        return out

    def feed_bytes(self, data: bytes) -> List[bytes]:
        """按 MSB-first 喂入字节流。"""
        return self.feed_bits(bytes_to_bits_msb(data))


# ===========================================================================
# 卫星 AFSK 解调（复用 AFSK1200Demod，mark/space/baud 参数化）
# ===========================================================================

class SatelliteAFSKDemod(AFSK1200Demod):
    """卫星 AFSK 调制/解调：复用 multimon 正交相关解调，mark/space/baud 可配。

    参数
    ----------
    sample_rate : float
        音频采样率 Hz。
    baud : float
        波特率（1200 或 2400）。
    mark_freq, space_freq : float
        mark/space 频率 Hz（规格 §2.2 给出的候选见模块常量）。
    """

    def __init__(self, sample_rate: float = 22050.0, baud: float = 1200.0,
                 mark_freq: float = SAT_AFSK_1200_MARK,
                 space_freq: float = SAT_AFSK_1200_SPACE):
        # 先覆盖基类类属性（基类 __init__ 会用 self.BAUD 算 corlen）
        self.BAUD = baud
        self.MARK = int(mark_freq)
        self.SPACE = int(space_freq)
        super().__init__(sample_rate=sample_rate)
        self.corlen = max(1, int(round(self.fs / self.BAUD)))


# ===========================================================================
# 串联接收链（可插拔；字节/比特/包输入都能独立喂入）
# ===========================================================================

@dataclass
class CcsdsRxConfig:
    """接收链物理层/信道参数（未确定项留默认并由调用方覆盖，不硬编码猜测）。"""

    asm_word: int = CCSDS_ASM_WORD
    asm_max_hamming: int = DEFAULT_ASM_MAX_HAMMING
    #: ASM 之后的编码比特数（卷积开时 = 信息比特×2；否则 = 信息比特）。
    frame_bits_after_asm: int = SSDV_PACKET_LEN * 8
    use_viterbi: bool = False
    use_descramble: bool = True
    use_rs: bool = False
    rs_nsym: int = RS_NSYM
    g1: int = CONV_G1
    g2: int = CONV_G2


class CcsdsReceiveChain:
    """AFSK → 比特 → ASM → [Viterbi] → 解扰 → [RS] → SSDV 包。

    各级独立可插拔：
      - :meth:`process_audio`   从 AFSK 音频开始；
      - :meth:`process_bits`    从比特流开始（AFSK 已完成）；
      - :meth:`process_frames` 从已 ASM 同步的帧字节开始（ASM 已完成）；
      - :meth:`decode_frame`    对单帧字节做 Viterbi/解扰/RS。
    """

    def __init__(self, config: Optional[CcsdsRxConfig] = None,
                 afsk: Optional[SatelliteAFSKDemod] = None):
        self.cfg = config or CcsdsRxConfig()
        self.afsk = afsk  # 可选；process_audio 时若为 None 会新建默认
        self.framer = AsmFramer(sync_word=self.cfg.asm_word,
                                frame_bits=self.cfg.frame_bits_after_asm,
                                max_hamming=self.cfg.asm_max_hamming)
        self.viterbi = ViterbiDecoder(self.cfg.g1, self.cfg.g2) if self.cfg.use_viterbi else None
        self.scrambler = Scrambler() if self.cfg.use_descramble else None
        self.rs = ReedSolomon(nsym=self.cfg.rs_nsym) if self.cfg.use_rs else None

    # ------------------------------------------------------------------
    # 单帧后处理：帧字节（ASM 之后）→ 恢复出的信息字节
    # ------------------------------------------------------------------
    def decode_frame(self, frame_bytes: bytes) -> bytes:
        frame_bits = bytes_to_bits_msb(frame_bytes)
        if self.viterbi is not None:
            # 编码比特成对喂入 Viterbi
            if len(frame_bits) % 2 != 0:
                frame_bits = frame_bits[: len(frame_bits) - (len(frame_bits) % 2)]
            pairs = [(frame_bits[i], frame_bits[i + 1]) for i in range(0, len(frame_bits), 2)]
            info_bits = self.viterbi.decode(pairs)
        else:
            info_bits = frame_bits
        out = bits_to_bytes_msb(info_bits)
        if self.scrambler is not None:
            out = self.scrambler.descramble(out)
        if self.rs is not None:
            # RS 解 255 字节块；不足 255 时不强行 RS
            if len(out) >= 255:
                res = self.rs.decode(out[:255])
                out = res.data + out[255:]
        return out

    # ------------------------------------------------------------------
    def process_frames(self, frames: Sequence[bytes]) -> List[bytes]:
        return [self.decode_frame(f) for f in frames]

    def process_bits(self, bits: Sequence[int]) -> List[bytes]:
        frames = self.framer.feed_bits(bits)
        return self.process_frames(frames)

    def process_audio(self, audio: np.ndarray) -> List[bytes]:
        demod = self.afsk
        if demod is None:
            demod = SatelliteAFSKDemod()
        bits = demod.demodulate(np.asarray(audio, dtype=np.float64))
        return self.process_bits(bits.tolist())


__all__ = [
    "CCSDS_ASM_WORD",
    "CONV_K",
    "CONV_RATE",
    "CONV_G1",
    "CONV_G2",
    "CONV_STATES",
    "DEFAULT_ASM_MAX_HAMMING",
    "RS_NSYM",
    "SSDV_PACKET_LEN",
    "ConvolutionalEncoder",
    "ViterbiDecoder",
    "AsmFramer",
    "SatelliteAFSKDemod",
    "CcsdsRxConfig",
    "CcsdsReceiveChain",
    "bits_to_bytes_msb",
    "bytes_to_bits_msb",
]
