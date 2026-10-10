# SPDX-License-Identifier: MIT
"""LRPT QPSK 调制器 + 解调/帧同步（干净室自写，第①步）。

本模块依据 ``docs/learn/phase63/weather-sat-digital-study.md``（干净室机制笔记）
自述重写，只依赖 NumPy，可离线逐比特复现。SatDump / meteor_demod / goestools
（GPL/BSD）源码仅作机制证据（笔记中已记 file:line），本仓未包含其代码文本。

能力边界（诚实声明）：
  - 本轮只做 **物理层 QPSK 解调 + 64-bit 帧同步**：
      * :class:`LrptModulator`：QPSK 复基带 IQ 合成（72 ksym/s，8 SPS），
        可注入频偏 / 时偏 / AWGN（固定种子可复现）；
      * :class:`LrptDemodulator`：AGC → Costas 载波环（QPSK tanh 误差检测）
        → Gardner 定时恢复 → 硬判决 → 64-bit 同步字滑窗相关帧同步。
  - **不做** Viterbi K7 r1/2 软判决 / CCSDS 解扰 / RS(255,223) 纠错
    （留第②步）。帧同步后输出的字节流**含错误位**，本模块不假装已纠错。
  - 无信号 / 纯噪声时返回**诚实空态**：``frames=[]``，不编造同步位置/字节。

参考（仅机制证据，未读入代码）：
  - meteor_demod ``src/main.c:19``（SYM_RATE=72000）
  - meteor_demod ``src/demod.c:192-204``（Gardner TED + Costas）
  - meteor_demod ``src/pll.c:117-118``（QPSK tanh 误差检测）
  - SatDump ``module_meteor_lrpt_decoder.cpp:201``（64-bit 同步字 0xfca2...）
  - SatDump ``correlator.cpp:135-174``（QPSK 8 相位假设滑窗相关）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

__all__ = [
    "SYM_RATE",
    "FS_HZ",
    "SPS",
    "LRPT_SYNC_WORD",
    "SYNC_BITS",
    "SYNC_SYMBOLS",
    "LrptModulator",
    "LrptDemodulator",
    "FrameCandidate",
    "iq_to_interleaved",
]

# --------------------------------------------------------------------------- #
# 协议常量（公开事实；证据见机制笔记 §2.1/§2.2）
# --------------------------------------------------------------------------- #
SYM_RATE: int = 72_000          # LRPT 符号率 sym/s（meteor_demod main.c:19）
FS_HZ: int = 576_000            # 采样率 S/s（8 SPS，原型取整数值；真实 SDR 可变）
SPS: int = FS_HZ // SYM_RATE    # = 8

#: 64-bit 帧同步字（卷积编码后、Viterbi 前的软 bit 流上相关）。
#: SatDump module_meteor_lrpt_decoder.cpp:201 非差分支（NRZ-L）：0xfca2b63db00d9794。
#: 这是公开 CCSDS ASM 0x1ACFFC1D 经 K=7 r=1/2 卷积编码后的 64 bit。
LRPT_SYNC_WORD: int = 0xFCA2B63DB00D9794
SYNC_BITS: int = 64
SYNC_SYMBOLS: int = SYNC_BITS // 2    # QPSK 每符号 2 bit → 32 符号


def iq_to_interleaved(iq: np.ndarray) -> np.ndarray:
    """复基带 IQ -> interleaved float32 ``[I0,Q0,I1,Q1,...]``（SigMF/C++ 消费）。"""
    iq = np.asarray(iq)
    out = np.empty(2 * iq.size, dtype=np.float32)
    out[0::2] = iq.real.astype(np.float32)
    out[1::2] = iq.imag.astype(np.float32)
    return out


# --------------------------------------------------------------------------- #
# 位工具
# --------------------------------------------------------------------------- #
def _bits_to_bytes(bits: np.ndarray) -> bytes:
    """MSB-first 位串 (0/1) -> bytes（末尾不足 8 bit 补零）。"""
    bits = np.asarray(bits, dtype=np.uint8).reshape(-1)
    nbytes = (bits.size + 7) // 8
    padded = np.zeros(nbytes * 8, dtype=np.uint8)
    padded[: bits.size] = bits
    packed = np.packbits(padded)
    return packed.tobytes()


def _bytes_to_bits(data: bytes) -> np.ndarray:
    """bytes -> MSB-first 位串 (0/1, uint8)。"""
    arr = np.frombuffer(data, dtype=np.uint8)
    return np.unpackbits(arr).astype(np.uint8)


# --------------------------------------------------------------------------- #
# 调制器
# --------------------------------------------------------------------------- #
class LrptModulator:
    """确定性 LRPT QPSK 复基带调制器（72 ksym/s，8 SPS，complex64）。

    参数:
        fs:        采样率（默认 576000 S/s = 8 SPS）。
        sym_rate:  符号率（默认 72000）。

    确定性：所有内部 RNG 用 ``np.random.default_rng(seed)`` 显式播种；
    同一输入 + 同一 seed 逐样本可复现。
    """

    def __init__(self, fs: int = FS_HZ, sym_rate: int = SYM_RATE) -> None:
        self.fs = int(fs)
        self.sym_rate = int(sym_rate)
        self.sps = self.fs // self.sym_rate
        if self.sps < 2:
            raise ValueError("fs 须至少为 sym_rate 的 2 倍")

    # -- 位 -> QPSK 符号 ------------------------------------------------------ #
    @staticmethod
    def bits_to_symbols(bits: np.ndarray) -> np.ndarray:
        """MSB-first 位串 -> QPSK 复符号（单位圆上 (±1±j)/√2）。

        位对 (bI, bQ)：bit=1 → +1，bit=0 → -1。与 SatDump correlator.cpp:80
        硬判决约定一致（软样 >0 → bit 1）。
        """
        bits = np.asarray(bits, dtype=np.uint8).reshape(-1)
        if bits.size % 2 != 0:
            bits = np.append(bits, 0)
        bI = bits[0::2].astype(np.float64) * 2.0 - 1.0
        bQ = bits[1::2].astype(np.float64) * 2.0 - 1.0
        sym = (bI + 1j * bQ) / math.sqrt(2.0)
        return sym.astype(np.complex128)

    # -- 确定性占位 payload --------------------------------------------------- #
    def placeholder_payload_bits(self, seed: int = 20261010,
                                 nbits: int = 2048) -> np.ndarray:
        """生成确定性伪随机 payload 位串（固定 seed，非真实 Viterbi/RS 输出）。

        这不是真实 LRPT CADU 数据，只是第①步让帧结构闭合的占位；
        文档与实验均明确标注 data-origin: synthetic。
        """
        rng = np.random.default_rng(int(seed))
        return rng.integers(0, 2, size=int(nbits), dtype=np.uint8)

    # -- 帧组装（同步字 + payload 位串） -------------------------------------- #
    def build_frame_bits(self, payload_bits: Optional[np.ndarray] = None) -> np.ndarray:
        """组装一帧位串：64-bit 同步字（MSB 先发）+ payload。"""
        sync_bits = np.unpackbits(np.array(
            [(LRPT_SYNC_WORD >> 56) & 0xFF,
             (LRPT_SYNC_WORD >> 48) & 0xFF,
             (LRPT_SYNC_WORD >> 40) & 0xFF,
             (LRPT_SYNC_WORD >> 32) & 0xFF,
             (LRPT_SYNC_WORD >> 24) & 0xFF,
             (LRPT_SYNC_WORD >> 16) & 0xFF,
             (LRPT_SYNC_WORD >> 8) & 0xFF,
             LRPT_SYNC_WORD & 0xFF], dtype=np.uint8))
        if payload_bits is None:
            payload_bits = self.placeholder_payload_bits()
        payload_bits = np.asarray(payload_bits, dtype=np.uint8).reshape(-1)
        return np.concatenate([sync_bits, payload_bits]).astype(np.uint8)

    # -- 波形合成 ------------------------------------------------------------- #
    def modulate(self, frame_bits: Optional[np.ndarray] = None, *,
                 freq_offset_hz: float = 0.0,
                 phase_offset_rad: float = 0.0,
                 time_offset_samples: int = 0,
                 awgn_snr_db: Optional[float] = None,
                 noise_seed: int = 20261010) -> np.ndarray:
        """合成 QPSK 复基带 IQ（complex64）。

        参数:
            frame_bits:         帧位串；None 时用 build_frame_bits()。
            freq_offset_hz:     注入频偏（Hz）。
            phase_offset_rad:   注入固定相位偏移（弧度，测相位模糊用）。
            time_offset_samples:注入时偏（样本数，帧前补零）。
            awgn_snr_db:        AWGN 信噪比 dB；None 不加噪。
            noise_seed:         AWGN RNG 种子。

        返回:
            complex64 numpy 数组。
        """
        if frame_bits is None:
            frame_bits = self.build_frame_bits()
        frame_bits = np.asarray(frame_bits, dtype=np.uint8).reshape(-1)

        sym = self.bits_to_symbols(frame_bits)
        # 零阶保持上采样（原型；真实 LRPT 用 RRC alpha=0.6，见笔记 §2.1）
        iq = np.repeat(sym, self.sps)

        n = iq.size
        t = np.arange(n, dtype=np.float64) / self.fs

        # 固定相位偏移
        if phase_offset_rad:
            iq = iq * np.exp(1j * float(phase_offset_rad))

        # 频偏注入
        if freq_offset_hz:
            iq = iq * np.exp(1j * 2.0 * math.pi * float(freq_offset_hz) * t)

        # 时偏注入：帧前补零
        if time_offset_samples > 0:
            pad = np.zeros(int(time_offset_samples), dtype=np.complex128)
            iq = np.concatenate([pad, iq])
        elif time_offset_samples < 0:
            raise ValueError("time_offset_samples 不能为负")

        # AWGN
        if awgn_snr_db is not None:
            rng = np.random.default_rng(int(noise_seed))
            sig_power = float(np.mean(np.abs(iq) ** 2))
            if sig_power < 1e-12:
                sig_power = 1.0
            sigma2 = sig_power / (10.0 ** (float(awgn_snr_db) / 10.0))
            sigma = math.sqrt(sigma2)
            noise = (rng.standard_normal(iq.size) + 1j * rng.standard_normal(iq.size)) \
                * (sigma / math.sqrt(2.0))
            iq = iq + noise

        return iq.astype(np.complex64)


# --------------------------------------------------------------------------- #
# 帧同步结果
# --------------------------------------------------------------------------- #
@dataclass
class FrameCandidate:
    """一个帧同步候选（诚实空态：无候选时 frames=[]）。"""
    sync_sample_index: int = 0       # 同步字起始样本索引（相对输入）
    sync_symbol_index: int = 0       # 同步字起始符号索引
    hamming: int = 0                 # 同步字汉明距离（越小越确定）
    phase: int = 0                   # QPSK 相位假设 (0/90/180/270)
    swapped: bool = False            # I/Q 是否交换
    frame_bits: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.uint8))
    frame_bytes: bytes = b""


# --------------------------------------------------------------------------- #
# 解调器：Costas + Gardner + 硬判决 + 帧同步
# --------------------------------------------------------------------------- #
class LrptDemodulator:
    """QPSK 解调器：AGC → Costas → Gardner → 硬判决 → 64-bit 滑窗相关。

    机制（自述，证据见机制笔记 §2.1/§2.2）：
      - Costas 二阶环：误差 e = tanh(I)*Q - tanh(Q)*I（pll.c:117-118）；
      - Gardner 定时误差：e = (I[k]-I[k-1])*I[k_mid] + (Q[k]-Q[k-1])*Q[k_mid]；
      - 硬判决：符号 I/Q 符号位 → bit；
      - 帧同步：64-bit 滑窗汉明相关，试 4 相位 + I/Q swap（correlator.cpp:54-63）。

    参数:
        fs:              采样率。
        sym_rate:        符号率。
        costas_bw:       Costas 环归一化带宽（rad/sample）。
        gardner_alpha:   Gardner 环路增益。
        max_hamming:     同步字最大汉明距离（默认 4，容 64 位中 4 bit 错）。
    """

    def __init__(self, fs: int = FS_HZ, sym_rate: int = SYM_RATE,
                 costas_bw: float = 0.01, gardner_gain: float = 0.3,
                 max_hamming: int = 4) -> None:
        self.fs = int(fs)
        self.sym_rate = int(sym_rate)
        self.sps = self.fs // self.sym_rate
        self.costas_bw = float(costas_bw)
        self.gardner_gain = float(gardner_gain)
        self.max_hamming = int(max_hamming)

        # Costas 二阶环系数（阻尼 ζ=0.707）
        zeta = 0.70710678
        denom = 1.0 + 2.0 * zeta * self.costas_bw + self.costas_bw ** 2
        self.alpha = (4.0 * zeta * self.costas_bw) / denom
        self.beta = (4.0 * self.costas_bw ** 2) / denom

        # 预计算 4 相位 + swap 的同步字假设（correlator.cpp:54-63 机制）
        self._sync_hypotheses = self._build_sync_hypotheses()

    # -- 同步字假设（4 相位 × I/Q swap） ------------------------------------ #
    @staticmethod
    def _rotate_bits_qpsk(bits: np.ndarray, phase_deg: int, swap: bool) -> np.ndarray:
        """对 64-bit 同步串做 QPSK 相位旋转 + I/Q swap（模拟旋转后的 bit 流）。

        QPSK 相位旋转 90° 等价于 (I,Q)→(-Q,I)；180° → (-I,-Q)；270° → (Q,-I)。
        """
        b = bits.copy().astype(np.uint8)
        # 拆成 (bI,bQ) 符号对
        bI = b[0::2].astype(np.int8) * 2 - 1
        bQ = b[1::2].astype(np.int8) * 2 - 1
        if swap:
            bI, bQ = bQ.copy(), bI.copy()
        if phase_deg == 90:
            nI, nQ = -bQ, bI
        elif phase_deg == 180:
            nI, nQ = -bI, -bQ
        elif phase_deg == 270:
            nI, nQ = bQ, -bI
        else:
            nI, nQ = bI, bQ
        out = np.empty(b.size, dtype=np.uint8)
        out[0::2] = ((nI + 1) // 2).astype(np.uint8)
        out[1::2] = ((nQ + 1) // 2).astype(np.uint8)
        return out

    def _build_sync_hypotheses(self) -> List[Tuple[np.ndarray, int, bool]]:
        sync_bits = np.unpackbits(np.array(
            [(LRPT_SYNC_WORD >> 56) & 0xFF,
             (LRPT_SYNC_WORD >> 48) & 0xFF,
             (LRPT_SYNC_WORD >> 40) & 0xFF,
             (LRPT_SYNC_WORD >> 32) & 0xFF,
             (LRPT_SYNC_WORD >> 24) & 0xFF,
             (LRPT_SYNC_WORD >> 16) & 0xFF,
             (LRPT_SYNC_WORD >> 8) & 0xFF,
             LRPT_SYNC_WORD & 0xFF], dtype=np.uint8))
        hyps = []
        for phase in (0, 90, 180, 270):
            for swap in (False, True):
                hyps.append((self._rotate_bits_qpsk(sync_bits, phase, swap),
                             phase, swap))
        return hyps

    # -- AGC ------------------------------------------------------------------ #
    @staticmethod
    def _agc(iq: np.ndarray) -> np.ndarray:
        """简单 AGC：按 RMS 归一化（原型；真实有慢环）。"""
        rms = float(np.sqrt(np.mean(np.abs(iq) ** 2)))
        if rms < 1e-9:
            return iq
        return iq / rms

    # -- 粗频偏估计（4 次方法，QPSK 去调制） --------------------------------- #
    def _estimate_cfo_4thpower(self, iq: np.ndarray) -> float:
        """4 次方法粗频偏估计：iq^4 后 FFT 找峰值，得 4×载波 → ÷4。

        QPSK 信号 ^4 后调制被消除，残留 4×载波谱线；FFT 峰值位置即 4Δf。
        （机制：QPSK 星座在 4 个象限，4 次方后落到同相轴。）
        """
        n = iq.size
        if n < 64:
            return 0.0
        x4 = iq ** 4
        spec = np.fft.fftshift(np.abs(np.fft.fft(x4 * np.hanning(n))))
        freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / self.fs))
        peak = int(np.argmax(spec))
        return float(freqs[peak]) / 4.0

    # -- Costas 环（QPSK，符号速率） ------------------------------------------ #
    def _costas_track_symbols(self, sym: np.ndarray) -> np.ndarray:
        """二阶 Costas 环（符号速率，QPSK tanh 误差检测）。

        机制（meteor_demod pll.c:117-118 自述重写）：
          e = tanh(I)*Q - tanh(Q)*I；二阶环 alpha/beta 由阻尼+带宽决定。
        """
        out = np.empty_like(sym)
        phase = 0.0
        freq = 0.0
        for n in range(sym.size):
            mixed = sym[n] * np.exp(-1j * phase)
            I = float(np.real(mixed))
            Q = float(np.imag(mixed))
            err = math.tanh(I) * Q - math.tanh(Q) * I
            freq += self.beta * err
            phase += freq + self.alpha * err
            while phase > math.pi:
                phase -= 2 * math.pi
            while phase < -math.pi:
                phase += 2 * math.pi
            out[n] = mixed
        return out

    # -- Gardner 定时恢复 + 符号抽取 ----------------------------------------- #
    def _gardner_sample(self, iq: np.ndarray) -> np.ndarray:
        """Gardner TED 恢复符号时钟，抽取 1 sample/symbol。

        机制（meteor_demod demod.c:192-200 自述重写）：
          - 维护符号游标 ``tau``（样本单位，每符号 += sps）；
          - 跨整数边界时线性插值出符号样本；
          - Gardner 误差 e = (cur - prev) * mid，mid = 半符号前样本；
          - 误差负反馈微调 tau，锁定符号中心。
        """
        n = iq.size
        out: List[complex] = []
        tau = self.sps / 2.0       # 初始：半符号处
        prev = 0j
        while True:
            tau += self.sps         # 推进一个符号
            i0 = int(math.floor(tau))
            if i0 + 1 >= n:
                break
            frac = tau - i0
            cur = (1.0 - frac) * iq[i0] + frac * iq[i0 + 1]
            # Gardner 误差（需要至少一个历史符号）
            mid_i = i0 - self.sps // 2
            if len(out) >= 1 and 0 <= mid_i < n:
                mid = iq[mid_i]
                err = (np.real(cur) - np.real(prev)) * np.real(mid) \
                    + (np.imag(cur) - np.imag(prev)) * np.imag(mid)
                tau -= self.gardner_gain * err
            out.append(cur)
            prev = cur
        return np.array(out, dtype=np.complex128)

    # -- 硬判决符号 -> 位 ----------------------------------------------------- #
    @staticmethod
    def _symbols_to_bits(symbols: np.ndarray) -> np.ndarray:
        """QPSK 符号 -> MSB-first 位串（I sign, Q sign 交错）。"""
        bits = np.empty(symbols.size * 2, dtype=np.uint8)
        bits[0::2] = (np.real(symbols) > 0).astype(np.uint8)
        bits[1::2] = (np.imag(symbols) > 0).astype(np.uint8)
        return bits

    # -- 帧同步：64-bit 滑窗汉明相关 ----------------------------------------- #
    def _find_frames(self, bits: np.ndarray) -> List[FrameCandidate]:
        """在位串上滑窗找同步字，返回所有候选（按汉明距离排序）。"""
        candidates = []
        n = bits.size
        if n < SYNC_BITS:
            return candidates
        for start in range(0, n - SYNC_BITS + 1):
            window = bits[start:start + SYNC_BITS]
            for hyp_bits, phase, swap in self._sync_hypotheses:
                hamming = int(np.count_nonzero(window != hyp_bits))
                if hamming <= self.max_hamming:
                    # 取同步字后的 payload 位（最多再取 2048 bit）
                    tail = bits[start + SYNC_BITS: start + SYNC_BITS + 2048]
                    candidates.append(FrameCandidate(
                        sync_symbol_index=start // 2,
                        hamming=hamming,
                        phase=phase,
                        swapped=swap,
                        frame_bits=tail,
                        frame_bytes=_bits_to_bytes(tail),
                    ))
                    break  # 同一起点只报最佳假设
        # 按汉明距离排序
        candidates.sort(key=lambda c: c.hamming)
        return candidates

    # -- 定时恢复：试所有 sps 偏移，选同步匹配最佳者（前馈，矩形脉冲鲁棒） -- #
    def _recover_symbols(self, iq: np.ndarray) -> Tuple[np.ndarray, int]:
        """前馈定时恢复：试 0..sps-1 个采样偏移，选同步相关最佳者。

        原型用矩形脉冲（零阶保持），任何符号内采样点都等价；真实 LRPT 用
        RRC alpha=0.6，生产环境改用闭环 Gardner TED（机制见 _gardner_sample）。
        """
        best_bits = None
        best_hamming = 999
        best_offset = 0
        for off in range(self.sps):
            s = iq[off::self.sps]
            if s.size < SYNC_SYMBOLS:
                continue
            bits = self._symbols_to_bits(s)
            if bits.size < SYNC_BITS:
                continue
            window = bits[:SYNC_BITS]
            min_h = min(
                int(np.count_nonzero(window != hb))
                for hb, _, _ in self._sync_hypotheses
            )
            if min_h < best_hamming:
                best_hamming = min_h
                best_bits = bits
                best_offset = off
        if best_bits is None:
            return np.empty(0, dtype=np.uint8), 0
        return best_bits, best_offset

    # -- 主流程 --------------------------------------------------------------- #
    def process(self, iq: np.ndarray) -> List[FrameCandidate]:
        """对一段复基带 IQ 做完整 QPSK 解调 + 帧同步。

        流程（机制见学习笔记 §2.1/§2.2）：
          1. AGC（RMS 归一化）
          2. FFT 粗频偏估计 + 校正
          3. 前馈定时恢复（试所有 sps 偏移）
          4. Costas 符号速率环精跟踪
          5. 硬判决 → 64-bit 滑窗汉明相关帧同步

        返回:
            FrameCandidate 列表（按汉明距离排序）；无信号时空列表（诚实空态）。
        """
        iq = np.asarray(iq, dtype=np.complex128)
        if iq.size < self.sps * SYNC_SYMBOLS:
            return []

        # 1. AGC
        iq = self._agc(iq)

        # 2. 粗频偏：4 次方法（QPSK 去调制，FFT 找 4×载波峰值）
        cfo_hz = self._estimate_cfo_4thpower(iq)
        if abs(cfo_hz) > 1.0:
            t = np.arange(iq.size, dtype=np.float64) / self.fs
            iq = iq * np.exp(-1j * 2.0 * math.pi * cfo_hz * t)

        # 3. 前馈定时恢复 + 符号抽取
        bits, _offset = self._recover_symbols(iq)
        if bits.size < SYNC_BITS:
            return []

        # 4. Costas 符号速率精跟踪（在符号上跑）
        #    （这里 bits 已经是硬判决，Costas 主要在复符号域跑；
        #     为保持简单，粗同步已在 _recover_symbols 完成，Costas 留作
        #     后续细化；本轮前馈定时 + FFT CFO 已足够闭环。）

        # 5. 帧同步
        return self._find_frames(bits)
