# SPDX-License-Identifier: MIT
"""CCSDS 数字接收链确定性测试（干净室；仅依赖本仓 MIT 模块 + numpy）。

覆盖：
  1. 卷积 K=7 r=1/2 → Viterbi 干净往返
  2. 注入误码后 Viterbi 纠正（能力内完全恢复）；超出能力时诚实失败
  3. ASM 帧同步（干净 preamble / 噪声 preamble / ASM 1-bit 误码容忍度）
  4. CCSDS 解扰往返、RS(255,223) 往返与纠错
  5. 卫星 AFSK 1200/2400 baud 调制解调往返
  6. 串联链：preamble+ASM+卷积(加扰帧) → process_bits → Viterbi→解扰 → 还原帧

无数据空态：未同步到任何 ASM 时返回空列表（不伪造帧）。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from mbdsdr_ai.ccsds_rx import (  # noqa: E402
    CCSDS_ASM_WORD,
    AsmFramer,
    ConvolutionalEncoder,
    CcsdsReceiveChain,
    CcsdsRxConfig,
    SatelliteAFSKDemod,
    ViterbiDecoder,
    bits_to_bytes_msb,
    bytes_to_bits_msb,
)
from mbdsdr_ai.fec import ReedSolomon, Scrambler  # noqa: E402


def _seed_bytes(seed: int, n: int) -> bytes:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=n, dtype=np.uint8).tobytes()


# ---------------------------------------------------------------------------
# 1/2. 卷积 + Viterbi
# ---------------------------------------------------------------------------

class TestConvolutionalViterbi:
    def test_clean_roundtrip(self):
        data = _seed_bytes(20261002, 256)
        enc = ConvolutionalEncoder()
        pairs = enc.encode_bytes(data, tail=6)
        dec = ViterbiDecoder()
        bits = dec.decode(pairs)
        out = bits_to_bytes_msb(bits)
        assert out[: len(data)] == data

    def test_corrects_injected_errors(self):
        data = _seed_bytes(11, 256)
        enc = ConvolutionalEncoder()
        pairs = enc.encode_bytes(data, tail=6)
        rng = np.random.default_rng(123)
        pairs2 = []
        for (a, b) in pairs:
            a ^= rng.random() < 0.03
            b ^= rng.random() < 0.03
            pairs2.append((int(a), int(b)))
        dec = ViterbiDecoder()
        out = bits_to_bytes_msb(dec.decode(pairs2))
        assert out[: len(data)] == data

    def test_beyond_capacity_is_honest(self):
        """高误码率下不应“还原”出错误数据：断言与原数据不一致（不造假）。"""
        data = _seed_bytes(21, 256)
        enc = ConvolutionalEncoder()
        pairs = enc.encode_bytes(data, tail=6)
        rng = np.random.default_rng(999)
        pairs2 = [(int(a ^ (rng.random() < 0.18)),
                   int(b ^ (rng.random() < 0.18))) for (a, b) in pairs]
        dec = ViterbiDecoder()
        out = bits_to_bytes_msb(dec.decode(pairs2))
        assert out[: len(data)] != data  # 超出 Viterbi 能力，诚实失败

    def test_all_zeros_and_all_ones(self):
        for data in (b"\x00" * 32, b"\xff" * 32):
            enc = ConvolutionalEncoder()
            pairs = enc.encode_bytes(data, tail=6)
            out = bits_to_bytes_msb(ViterbiDecoder().decode(pairs))
            assert out[: len(data)] == data


# ---------------------------------------------------------------------------
# 3. ASM 帧同步
# ---------------------------------------------------------------------------

def _build_stream(frame: bytes, preamble_bits: int, asm_bit_errors: int = 0,
                  seed: int = 5) -> list:
    asm = bytes_to_bits_msb(CCSDS_ASM_WORD.to_bytes(4, "big"))
    rng = np.random.default_rng(seed)
    pre = list(rng.integers(0, 2, size=preamble_bits).tolist())
    if asm_bit_errors:
        asm = list(asm)
        for i in range(asm_bit_errors):
            asm[i * 4] ^= 1
    return pre + asm + bytes_to_bits_msb(frame)


class TestAsmFramer:
    def test_clean_sync(self):
        frame = _seed_bytes(31, 64)
        stream = _build_stream(frame, preamble_bits=150)
        out = AsmFramer(frame_bits=len(frame) * 8).feed_bits(stream)
        assert out == [frame]

    def test_noisy_preamble(self):
        frame = _seed_bytes(32, 64)
        stream = _build_stream(frame, preamble_bits=200, seed=7)
        out = AsmFramer(frame_bits=len(frame) * 8).feed_bits(stream)
        assert out == [frame]

    def test_asm_one_bit_error_tolerated(self):
        frame = _seed_bytes(33, 48)
        stream = _build_stream(frame, preamble_bits=100, asm_bit_errors=1)
        ok = AsmFramer(frame_bits=len(frame) * 8, max_hamming=1).feed_bits(stream)
        assert ok == [frame]

    def test_asm_one_bit_error_rejected_when_strict(self):
        frame = _seed_bytes(34, 48)
        stream = _build_stream(frame, preamble_bits=100, asm_bit_errors=1)
        strict = AsmFramer(frame_bits=len(frame) * 8, max_hamming=0).feed_bits(stream)
        assert strict == []

    def test_empty_when_no_asm(self):
        noise = list(np.random.default_rng(0).integers(0, 2, size=500).tolist())
        assert AsmFramer(frame_bits=64 * 8).feed_bits(noise) == []

    def test_multiple_frames(self):
        frame = _seed_bytes(35, 32)
        one = _build_stream(frame, preamble_bits=80)
        out = AsmFramer(frame_bits=len(frame) * 8).feed_bits(one + one)
        assert out == [frame, frame]


# ---------------------------------------------------------------------------
# 4. 解扰 / RS
# ---------------------------------------------------------------------------

class TestScramblerRs:
    def test_descramble_roundtrip(self):
        msg = _seed_bytes(41, 223)
        sc = Scrambler()
        assert sc.descramble(sc.scramble(msg)) == msg

    def test_rs_clean_roundtrip(self):
        msg = _seed_bytes(42, 223)
        rs = ReedSolomon(nsym=32)
        cw = rs.encode(msg)
        res = rs.decode(cw)
        assert res.data == msg and res.nerrors == 0

    def test_rs_corrects_byte_errors(self):
        msg = _seed_bytes(43, 223)
        rs = ReedSolomon(nsym=32)
        cw = bytearray(rs.encode(msg))
        for i in (0, 60, 120, 200, 254):
            cw[i] ^= 0xA5
        res = rs.decode(bytes(cw))
        assert res.data == msg and res.nerrors == 5


# ---------------------------------------------------------------------------
# 5. 卫星 AFSK 往返
# ---------------------------------------------------------------------------

class TestSatelliteAfsk:
    @pytest.mark.parametrize("baud,mark,space", [
        (1200.0, 1200.0, 2400.0),
        (2400.0, 1200.0, 2400.0),
    ])
    def test_roundtrip(self, baud, mark, space):
        demod = SatelliteAFSKDemod(sample_rate=48000, baud=baud,
                                   mark_freq=mark, space_freq=space)
        payload = bytes([0xAA, 0x55, 0x12, 0x34, 0xDE, 0xAD,
                         0x55, 0xAA, 0x55, 0xAA, 0x01, 0x23])
        bits = np.array(
            bytes_to_bits_msb(CCSDS_ASM_WORD.to_bytes(4, "big"))
            + bytes_to_bits_msb(payload), dtype=np.int8)
        audio = demod.modulate(bits)
        rx = demod.demodulate(audio)
        best = None
        for off in range(-3, 4):
            a = rx[max(0, off):] if off >= 0 else rx[:off]
            b = bits[max(0, -off):] if off < 0 else bits[off:] if off > 0 else bits
            m = min(len(a), len(b))
            mism = int(np.sum(a[:m] != b[:m]))
            if best is None or mism < best[0]:
                best = (mism, off)
        assert best[0] <= 1, f"AFSK {baud} baud mismatches={best[0]}"


# ---------------------------------------------------------------------------
# 6. 串联链：AFSK 之后的比特流 → ASM → Viterbi → 解扰 → SSDV 帧
# ---------------------------------------------------------------------------

class TestCcsdsChain:
    def _build_tx_bits(self, frame: bytes) -> list:
        """preamble + ASM + 卷积(加扰帧)。"""
        sc = Scrambler()
        scrambled = sc.scramble(frame)
        enc = ConvolutionalEncoder()
        pairs = enc.encode_bytes(scrambled, tail=0)
        coded_bits = []
        for (a, b) in pairs:
            coded_bits.extend([a, b])
        asm = bytes_to_bits_msb(CCSDS_ASM_WORD.to_bytes(4, "big"))
        pre = [0] * 150
        return pre + asm + coded_bits

    def test_chain_bits_to_frame(self):
        frame = _seed_bytes(51, 64)
        tx = self._build_tx_bits(frame)
        cfg = CcsdsRxConfig(
            frame_bits_after_asm=64 * 8 * 2,  # 卷积后：信息字节×8×2
            use_viterbi=True,
            use_descramble=True,
            use_rs=False,
        )
        chain = CcsdsReceiveChain(cfg)
        out = chain.process_bits(tx)
        assert out == [frame]

    def test_chain_no_data_returns_empty(self):
        noise = list(np.random.default_rng(1).integers(0, 2, size=600).tolist())
        cfg = CcsdsRxConfig(use_viterbi=True, use_descramble=True)
        assert CcsdsReceiveChain(cfg).process_bits(noise) == []

    def test_chain_pluggable_byte_input(self):
        """已 ASM 同步的帧字节可直接喂 decode_frame（ASM 级被旁路）。"""
        frame = _seed_bytes(52, 64)
        sc = Scrambler()
        scrambled = sc.scramble(frame)
        enc = ConvolutionalEncoder()
        pairs = enc.encode_bytes(scrambled, tail=0)
        coded_bytes = bits_to_bytes_msb([b for p in pairs for b in p])
        cfg = CcsdsRxConfig(use_viterbi=True, use_descramble=True, use_rs=False)
        chain = CcsdsReceiveChain(cfg)
        out = chain.decode_frame(coded_bytes)
        assert out == frame
