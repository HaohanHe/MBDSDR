# SPDX-License-Identifier: MIT
"""LRPT 端到端 IQ 闭环管道（第③步，纯 Python）。

串联第①②步：
  payload → RS 编码 → 交织 → 加扰 → ASM → 卷积编码 → QPSK 调制 → IQ
  → QPSK 解调 → 帧同步 → Viterbi → 解扰 → RS 解码 → payload

机制证据见 weather-sat-digital-study.md；本文件只做串联，不重复实现算法。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from .lrpt_modem import (
    SPS,
    SYNC_BITS,
    LrptDemodulator,
    LrptModulator,
)
from .lrpt_fec import (
    CADU_SIZE,
    LrptCaduDecoder,
    convolve_encode,
    viterbi_decode,
)

__all__ = [
    "LrptE2EPipeline",
    "E2EResult",
]


@dataclass
class E2EResult:
    """端到端解码结果（诚实空态：失败时 payload=b''）。"""
    success: bool = False
    payload: bytes = b""
    sync_found: bool = False
    sync_symbol_index: int = -1
    sync_hamming: int = -1
    rs_errors: list = field(default_factory=list)
    decoded_cadu_len: int = 0


class LrptE2EPipeline:
    """LRPT 端到端 IQ 闭环（合成 → 传输 → 解调 → 解码）。

    用法::

        pipe = LrptE2EPipeline()
        iq = pipe.build_tx_iq(payload, freq_offset_hz=2000, awgn_snr_db=5)
        result = pipe.rx(iq)
        assert result.success
        assert result.payload == payload
    """

    def __init__(self) -> None:
        self.mod = LrptModulator()
        self.demod = LrptDemodulator(max_hamming=4)
        self.decoder = LrptCaduDecoder()

    # ------------------------------------------------------------------ #
    # 发射端：payload → CADU → 卷积编码 → QPSK → IQ
    # ------------------------------------------------------------------ #
    def build_cadu(self, payload: bytes) -> bytes:
        """payload (892B) → CADU (1024B)：RS 编码 + 交织 + 加扰 + ASM。"""
        if len(payload) != 892:
            raise ValueError(f"payload 须 892 字节（223×4），收到 {len(payload)}")
        blocks = [payload[i*223:(i+1)*223] for i in range(4)]
        encoded = [self.decoder.rs.encode(b) for b in blocks]
        data = self.decoder._interleave_4(encoded)
        scrambled = self.decoder.scrambler.scramble(data)
        asm = bytes([0x1A, 0xCF, 0xFC, 0x1D])
        return asm + scrambled

    def build_tx_iq(self, payload: bytes, *,
                    freq_offset_hz: float = 0.0,
                    phase_offset_rad: float = 0.0,
                    time_offset_samples: int = 0,
                    awgn_snr_db: Optional[float] = None,
                    noise_seed: int = 20261010) -> np.ndarray:
        """payload → 复基带 IQ（完整 LRPT 帧）。"""
        cadu = self.build_cadu(payload)
        cadu_bits = np.unpackbits(np.frombuffer(cadu, dtype=np.uint8))
        coded_bits = convolve_encode(cadu_bits)
        # QPSK 调制：coded_bits → 复符号 → IQ
        sym = self.mod.bits_to_symbols(coded_bits)
        iq = np.repeat(sym, SPS).astype(np.complex128)

        n = iq.size
        t = np.arange(n, dtype=np.float64) / self.mod.fs
        if phase_offset_rad:
            iq = iq * np.exp(1j * phase_offset_rad)
        if freq_offset_hz:
            iq = iq * np.exp(1j * 2 * np.pi * freq_offset_hz * t)
        if time_offset_samples > 0:
            iq = np.concatenate([np.zeros(time_offset_samples, dtype=complex), iq])
        if awgn_snr_db is not None:
            rng = np.random.default_rng(noise_seed)
            sig_pow = float(np.mean(np.abs(iq) ** 2))
            sigma = np.sqrt(sig_pow / (10 ** (awgn_snr_db / 10)))
            noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * (sigma / np.sqrt(2))
            iq = iq + noise
        return iq.astype(np.complex64)

    # ------------------------------------------------------------------ #
    # 接收端：IQ → 解调 → 帧同步 → Viterbi → 解扰 → RS → payload
    # ------------------------------------------------------------------ #
    def rx(self, iq: np.ndarray) -> E2EResult:
        """对一段 IQ 做完整端到端接收。"""
        result = E2EResult()
        iq = np.asarray(iq, dtype=np.complex128)
        if iq.size < SPS * 64:
            return result

        # 1. AGC + CFO 粗校 + 定时恢复（复用第①轮）
        iq = self.demod._agc(iq)
        cfo = self.demod._estimate_cfo_4thpower(iq)
        if abs(cfo) > 1.0:
            t = np.arange(iq.size, dtype=np.float64) / self.demod.fs
            iq = iq * np.exp(-1j * 2 * np.pi * cfo * t)

        # 2. 前馈定时恢复：得到全量硬判决 bits
        bits, _off = self.demod._recover_symbols(iq)
        if bits.size < SYNC_BITS + 100:
            return result

        # 3. 帧同步：找 64-bit 同步字位置
        frames = self.demod._find_frames(bits)
        if not frames:
            return result
        best = frames[0]
        result.sync_found = True
        result.sync_symbol_index = best.sync_symbol_index
        result.sync_hamming = best.hamming

        # 4. 从同步位置开始取软/硬 coded bits（硬判决 ±1.0）
        #    同步字 64 bit + 后续所需位
        start_bit = best.sync_symbol_index * 2
        needed_bits = SYNC_BITS + (CADU_SIZE * 8 + 6) * 2  # coded bits for CADU + tail
        end_bit = min(start_bit + needed_bits, bits.size)
        coded_bits_hard = bits[start_bit:end_bit]
        soft = coded_bits_hard.astype(np.float64) * 2 - 1  # 0→-1, 1→+1

        # 5. Viterbi 译码
        try:
            decoded_bits = viterbi_decode(soft)
        except Exception:
            return result

        # 6. 组回 CADU 字节
        nbytes = (decoded_bits.size + 7) // 8
        padded = np.zeros(nbytes * 8, dtype=np.uint8)
        padded[:decoded_bits.size] = decoded_bits
        decoded_cadu = np.packbits(padded).tobytes()
        result.decoded_cadu_len = len(decoded_cadu)

        # 7. 解扰 + RS 解码
        if len(decoded_cadu) < CADU_SIZE:
            return result
        payload, errors = self.decoder.decode_cadu(decoded_cadu)
        result.rs_errors = errors

        if all(e >= 0 for e in errors):
            result.success = True
            result.payload = payload
        return result
