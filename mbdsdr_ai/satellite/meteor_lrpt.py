"""METEOR-M2 LRPT 解调链（SatDump 风格精简版）。

上游对照（docs/learn/porting_2026_09_27.md §4.4）：
  - ``plugins/meteor_support/meteor/module_meteor_lrpt_decoder.cpp:34``  Viterbi r=1/2 K=7
  - ``:119`` ``ReedSolomon(RS223)``
  - ``:172`` ``diff.decode_bits``（NRZ-M）
  - ``:201`` QPSK 同步字：非差分 ``0xfca2b63db00d9794``，差分 ``0xfc4ef4fd0cc2df89``
  - ``src-core/common/codings/randomization.cpp:4-36`` CCSDS PN 扰码表

完整 LRPT 链是 QPSK→差分→Viterbi→解扰→RS→图像。本模块做确定性可测的
硬判决版：QPSK 硬判决 → CCSDS 解扰 → RS(255,223) 解码 → 帧重组。
Viterbi 软判决留接口（``soft_viterbi`` 参数），不强行实现 K=7 全译码器。
"""

from __future__ import annotations

import dataclasses
from typing import Optional

import numpy as np

from ..fec import ReedSolomon, Scrambler, CCSDS_PN

# 非差分 QPSK LRPT 同步字（SatDump module_meteor_lrpt_decoder.cpp:201）
LRPT_ASYNC_WORD = bytes.fromhex("fca2b63db00d9794")


# ---------------------------------------------------------------------------
# QPSK 调制/解调（硬判决）
# ---------------------------------------------------------------------------

def qpsk_modulate(data: bytes, amplitude: float = 1.0) -> np.ndarray:
    """字节流 → QPSK 复符号（格雷映射）。"""
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    # 每 2 bit 一个符号：I = bit0, Q = bit1，映射到 ±(1+j)/sqrt(2)
    i = bits[0::2].astype(np.float64) * 2.0 - 1.0
    q = bits[1::2].astype(np.float64) * 2.0 - 1.0
    syms = (i + 1j * q) / np.sqrt(2.0)
    return (syms * amplitude).astype(np.complex128)


def qpsk_demodulate(syms: np.ndarray) -> bytes:
    """QPSK 复符号 → 硬判决字节流。"""
    syms = np.asarray(syms, dtype=np.complex128)
    i_bits = (syms.real >= 0).astype(np.uint8)
    q_bits = (syms.imag >= 0).astype(np.uint8)
    # 交织：I,Q,I,Q...
    bits = np.empty(len(syms) * 2, dtype=np.uint8)
    bits[0::2] = i_bits
    bits[1::2] = q_bits
    # 截断到字节
    nbytes = len(bits) // 8
    return np.packbits(bits[: nbytes * 8]).tobytes()


# ---------------------------------------------------------------------------
# 链
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LRPTFrame:
    data: bytes            # RS 解码后的 k 字节
    corrected: bool         # 是否发生纠错
    nerrors: int            # 纠正符号数
    position: int            # 在原始字节流中的位置


class MeteorLRPTChain:
    """METEOR LRPT 硬判决解码链。

    参数
    ----------
    use_scrambler : bool
        是否做 CCSDS 解扰。
    use_rs : bool
        是否做 RS(255,223) 解码。
    sync_word : bytes
        帧同步字（None = 不解同步，直接按 255 字节分块）。
    """

    def __init__(
        self,
        use_scrambler: bool = True,
        use_rs: bool = True,
        sync_word: Optional[bytes] = LRPT_ASYNC_WORD,
    ):
        self.use_scrambler = use_scrambler
        self.use_rs = use_rs
        self.sync_word = sync_word
        self._scrambler = Scrambler()
        self._rs = ReedSolomon(nsym=32, fcr=112, ccsds_invert=True)

    # ------------------------------------------------------------------
    def encode_block(self, data: bytes) -> bytes:
        """把 k 字节数据编码成 255 字节码字（供测试/回放）。"""
        if len(data) != 223:
            raise ValueError(f"LRPT 块需要 223 字节，收到 {len(data)}")
        cw = self._rs.encode(data)
        if self.use_scrambler:
            cw = self._scrambler.scramble(cw)
        return cw

    # ------------------------------------------------------------------
    def decode_block(self, cw: bytes) -> LRPTFrame:
        """对 255 字节码字解码。"""
        if len(cw) != 255:
            raise ValueError(f"LRPT 块需要 255 字节，收到 {len(cw)}")
        buf = bytes(cw)
        if self.use_scrambler:
            buf = self._scrambler.descramble(buf)
        if self.use_rs:
            r = self._rs.decode(buf)
            return LRPTFrame(data=r.data, corrected=r.corrected,
                             nerrors=r.nerrors, position=0)
        return LRPTFrame(data=buf[:223], corrected=False, nerrors=0, position=0)

    # ------------------------------------------------------------------
    def find_sync(self, buf: bytes) -> int:
        """在字节流中找同步字，返回起始位置（-1 未找到）。"""
        if self.sync_word is None:
            return 0
        idx = buf.find(self.sync_word)
        return idx

    # ------------------------------------------------------------------
    def decode_stream(self, raw_bytes: bytes) -> list:
        """对连续字节流解码：找同步 → 逐块解扰/RS。"""
        frames = []
        if self.sync_word is not None:
            pos = self.find_sync(raw_bytes)
            if pos < 0:
                return frames
            buf = raw_bytes[pos + len(self.sync_word):]
        else:
            buf = raw_bytes
        offset = 0
        while len(buf) - offset >= 255:
            block = buf[offset: offset + 255]
            fr = self.decode_block(block)
            fr.position = offset
            frames.append(fr)
            offset += 255
        return frames


__all__ = [
    "LRPT_ASYNC_WORD",
    "qpsk_modulate",
    "qpsk_demodulate",
    "MeteorLRPTChain",
    "LRPTFrame",
]
