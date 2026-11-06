# SPDX-License-Identifier: MIT
"""LRPT QPSK 调制器 + 解调/帧同步（独立实现，第①步）。

本模块依据 ``docs/learn/phase63/weather-sat-digital-study.md``（独立实现机制笔记）
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


def _root_raised_cosine(alpha: float, sps: int, span: int = 10) -> np.ndarray:
    """RRC 成型/匹配滤波器抽头（单位能量，span 符号对称 FIR）。

    公式为公开标准（3GPP TS 25.104 / CCSDS 414.0 同型）；Meteor-M2 LRPT
    下行 RRC α=0.6（机制笔记 §2.2）。真录必须匹配滤波——无匹配滤波时
    定时漂移直接把采样点推出符号中心（ISI → 同步汉明超标，2026-11-03 实测）。
    """
    n_taps = span * sps + 1
    t = (np.arange(n_taps) - (n_taps - 1) / 2.0) / float(sps)
    taps = np.zeros(n_taps, dtype=np.float64)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-12:
            taps[i] = 1.0 - alpha + 4.0 * alpha / math.pi
        elif alpha > 0 and abs(abs(ti) - 1.0 / (4.0 * alpha)) < 1e-9:
            taps[i] = (alpha / math.sqrt(2.0)) * (
                (1.0 + 2.0 / math.pi) * math.sin(math.pi / (4.0 * alpha))
                + (1.0 - 2.0 / math.pi) * math.cos(math.pi / (4.0 * alpha)))
        else:
            num = (math.sin(math.pi * ti * (1.0 - alpha))
                   + 4.0 * alpha * ti * math.cos(math.pi * ti * (1.0 + alpha)))
            den = math.pi * ti * (1.0 - (4.0 * alpha * ti) ** 2)
            taps[i] = num / den
    return taps / math.sqrt(float(np.sum(taps ** 2)))


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
        rrc_alpha:       RRC 匹配滤波器滚降系数；None 关闭（合成矩形脉冲链
                         不需要；真录 Meteor-M2 用 0.6）。
        use_gardner:     True 时用 Gardner TED 闭环定时（真录推荐）；
                         False 用前馈 sps 偏移扫描（合成链兼容）。
    """

    def __init__(self, fs: int = FS_HZ, sym_rate: int = SYM_RATE,
                 costas_bw: float = 0.01, gardner_gain: float = 0.3,
                 max_hamming: int = 4,
                 rrc_alpha: Optional[float] = None,
                 use_gardner: bool = False) -> None:
        self.fs = int(fs)
        self.sym_rate = int(sym_rate)
        self.sps = self.fs // self.sym_rate
        self.costas_bw = float(costas_bw)
        self.gardner_gain = float(gardner_gain)
        self.max_hamming = int(max_hamming)
        self.rrc_alpha = None if rrc_alpha is None else float(rrc_alpha)
        self.use_gardner = bool(use_gardner)
        self._rrc_taps: Optional[np.ndarray] = None
        if self.rrc_alpha is not None:
            self._rrc_taps = _root_raised_cosine(self.rrc_alpha, self.sps)

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
    def _gardner_sample(self, iq: np.ndarray,
                        tau0: Optional[float] = None) -> np.ndarray:
        """Gardner TED 恢复符号时钟，抽取 1 sample/symbol。

        机制（meteor_demod demod.c:192-200 自述重写；2026-11-03 三处
        结构修复后可收敛）：
          - 维护符号游标 ``tau``（样本单位，每符号 += sps）；
          - 跨整数边界时线性插值出符号样本；
          - Gardner 误差 e = Re((cur - prev) · conj(mid))，
            **mid 在 tau-sps/2 处线性插值**（旧版取整丢半样本，漂移后
            误差信号被 ISI 污染）；
          - 误差按符号功率归一（旧版裸误差与幅度耦合）；
          - 负反馈**钳制在 ±0.5 样本/符号**（旧版无钳制，发散时一路跑飞
            ——τ 发散 1.36M 样本的元凶之一）；
          - ``tau0`` 热启动（前馈扫描的最优偏移）；缺省 sps/2。

        仍属实验路径（use_gardner 默认关）：真录 2sps 前馈已够用，
        4sps 下闭环才可能反超（见 capability-map 连帧节）。
        """
        n = iq.size
        out: List[complex] = []
        tau = float(tau0) if tau0 is not None else self.sps / 2.0
        prev = 0j
        k = 0
        while True:
            if k > 0:
                tau += self.sps         # 推进一个符号（首符号用 tau0）
            i0 = int(math.floor(tau))
            if i0 + 1 >= n:
                break
            frac = tau - i0
            cur = (1.0 - frac) * iq[i0] + frac * iq[i0 + 1]
            # Gardner 误差（需要至少一个历史符号）
            if k >= 1:
                mt = tau - self.sps / 2.0
                mi = int(math.floor(mt))
                if 0 <= mi < n - 1:
                    mf = mt - mi
                    mid = (1.0 - mf) * iq[mi] + mf * iq[mi + 1]
                    err = np.real((cur - prev) * np.conj(mid))
                    power = (abs(prev) ** 2 + abs(cur) ** 2) * 0.5 + 1e-12
                    step = self.gardner_gain * err / power
                    tau -= max(-0.5, min(0.5, step))
            out.append(cur)
            prev = cur
            k += 1
        return np.array(out, dtype=np.complex128)

    # -- 硬判决符号 -> 位 ----------------------------------------------------- #
    @staticmethod
    def _symbols_to_bits(symbols: np.ndarray) -> np.ndarray:
        """QPSK 符号 -> MSB-first 位串（I sign, Q sign 交错）。"""
        bits = np.empty(symbols.size * 2, dtype=np.uint8)
        bits[0::2] = (np.real(symbols) > 0).astype(np.uint8)
        bits[1::2] = (np.imag(symbols) > 0).astype(np.uint8)
        return bits

    # -- 帧同步：64-bit 滑窗汉明相关（分块 FFT 向量化） --------------------- #
    @staticmethod
    def _fft_corr(x: np.ndarray, h: np.ndarray) -> np.ndarray:
        """线性相关 corr[i] = Σ_k x[i+k]·h[k]（分块 FFT，常数内存）。

        全流 15M 位约 2s/hypothesis——旧逐位 Python 滑窗全录约 225s，
        流扫描（连帧）跑不动，这里是向量化的动机。
        """
        n = x.size
        L = h.size
        npos = n - L + 1
        out = np.empty(npos, dtype=np.float64)
        chunk = 1 << 20
        fft_size = 1
        want = min(chunk, npos) + L
        while fft_size < want:
            fft_size <<= 1
        hr = np.fft.rfft(h[::-1], fft_size)
        pos = 0
        while pos < npos:
            take = min(chunk, npos - pos)
            seg = x[pos:pos + take + L - 1]
            conv = np.fft.irfft(np.fft.rfft(seg, fft_size) * hr, fft_size)
            out[pos:pos + take] = conv[L - 1:L - 1 + take]
            pos += take
        return out

    def _find_frames(self, bits: np.ndarray,
                     max_hamming: Optional[int] = None) -> List[FrameCandidate]:
        """在位串上滑窗找同步字，返回所有候选（按汉明距离排序）。

        机制不变（64-bit 窗 × 8 假设汉明相关）；实现为 numpy FFT 相关。
        同起点多假设命中时取**汉明最小**假设（原实现 break 在首个 ≤max 假设，
        是遍历序而非最优——这里对齐「同一起点只报最佳假设」注释的语义）。
        候选起点集合与旧实现一致（∃hyp ham≤max）。

        max_hamming 覆盖：松阈值捕获（如 12）+ 下游 Viterbi/ASM 验证是
        真解码器的标准姿势——硬判决同步丢卷积编码增益，ham≤4 会漏掉
        Viterbi 仍可纠的帧（2026-11-03 连帧实测）。松阈值的假接受由
        ASM（0x1ACFFC1D）验证归零。
        """
        limit = self.max_hamming if max_hamming is None else int(max_hamming)
        n = int(bits.size)
        if n < SYNC_BITS:
            return []
        x = bits.astype(np.float64) * 2.0 - 1.0
        npos = n - SYNC_BITS + 1
        best_h = np.full(npos, 999, dtype=np.int32)
        best_j = np.zeros(npos, dtype=np.int8)
        for j, (hb, _phase, _swap) in enumerate(self._sync_hypotheses):
            h = hb.astype(np.float64) * 2.0 - 1.0
            corr = self._fft_corr(x, h)
            ham = np.rint((SYNC_BITS - corr) * 0.5).astype(np.int32)
            better = ham < best_h
            best_h = np.where(better, ham, best_h)
            best_j = np.where(better, j, best_j)
        candidates: List[FrameCandidate] = []
        for start in np.nonzero(best_h <= limit)[0]:
            i = int(start)
            _hb, phase, swap = self._sync_hypotheses[int(best_j[i])]
            tail = bits[i + SYNC_BITS: i + SYNC_BITS + 2048]
            candidates.append(FrameCandidate(
                sync_symbol_index=i // 2,
                hamming=int(best_h[i]),
                phase=phase,
                swapped=swap,
                frame_bits=tail,
                frame_bytes=_bits_to_bytes(tail),
            ))
        candidates.sort(key=lambda c: c.hamming)
        return candidates

    def _recover_soft_symbols(self, iq: np.ndarray) -> Tuple[np.ndarray, int]:
        """前馈定时恢复：试 0..sps-1 个采样偏移，返回 (复符号, 偏移)。

        原型用矩形脉冲（零阶保持），任何符号内采样点都等价；真实 LRPT 用
        RRC alpha=0.6，生产环境改用闭环 Gardner TED（机制见 _gardner_sample）。

        偏移评分用**多探点**（流头 + 1/4 + 1/2 + 3/4 处 64-bit 同步窗）：
        长流开头可能是静默/噪声，单看流头会选错定时（2026-11-03 连帧实测）。
        短流（<4 同步窗）退回单流头探点，与旧行为一致。
        """
        best_syms = None
        best_score = 1 << 30
        best_offset = 0
        for off in range(self.sps):
            s = iq[off::self.sps]
            if s.size < SYNC_SYMBOLS:
                continue
            bits = self._symbols_to_bits(s)
            if bits.size < SYNC_BITS:
                continue
            positions = [0]
            if bits.size >= 4 * SYNC_BITS:
                for frac in (0.25, 0.5, 0.75):
                    positions.append(int((bits.size - SYNC_BITS) * frac))
            score = 0
            for p0 in positions:
                window = bits[p0:p0 + SYNC_BITS]
                score += min(
                    int(np.count_nonzero(window != hb))
                    for hb, _, _ in self._sync_hypotheses
                )
            if score < best_score:
                best_score = score
                best_syms = s
                best_offset = off
        if best_syms is None:
            return np.empty(0, dtype=np.complex128), 0
        return best_syms, best_offset

    # -- 定时恢复：试所有 sps 偏移，选同步匹配最佳者（前馈，矩形脉冲鲁棒） -- #
    def _recover_symbols(self, iq: np.ndarray) -> Tuple[np.ndarray, int]:
        """前馈定时恢复（硬判决位流包装，兼容旧接口）。"""
        syms, off = self._recover_soft_symbols(iq)
        return self._symbols_to_bits(syms), off

    # -- 主流程 --------------------------------------------------------------- #
    def demodulate_soft(self, iq: np.ndarray,
                        max_hamming: Optional[int] = None
                        ) -> Tuple[np.ndarray, List[FrameCandidate]]:
        """全流软判决解调：AGC → 粗 CFO → [RRC 匹配滤波] → 定时 → **Costas 闭环**
        → 软位流 + 帧候选。

        与 :meth:`process` 同链，但返回**软位流**（符号幅度，Viterbi/RS
        纠错边际所在），且 Costas 环在全流符号上连续跟踪——残余 CFO 不再
        让星座朝向逐帧漂移、帧网格滑动，**连续 VCDU 流（连帧）由此可解**
        （2026-11-03 连帧破层的机制修复；闭环全流只需跑一遍，不再逐锚点重扫）。

        构造参数 ``rrc_alpha`` / ``use_gardner`` 决定是否启用匹配滤波与
        Gardner 定时闭环（真录链推荐 0.6/False+前馈；合成矩形脉冲链保持
        None/False）。``max_hamming`` 覆盖同步捕获阈值（松阈值 + ASM 验证
        是连帧扫描的标准姿势，见 :meth:`_find_frames`）。

        返回:
            (soft_bits, candidates)。soft_bits 为 float64 位串（2N,），
            顺序 I,Q,I,Q…，正负即硬判决、幅度即置信度；无信号时 ([], [])。
        """
        iq = np.asarray(iq, dtype=np.complex128)
        if iq.size < self.sps * SYNC_SYMBOLS:
            return np.empty(0, dtype=np.float64), []

        # 1. AGC
        iq = self._agc(iq)

        # 2. 粗频偏：4 次方法（QPSK 去调制，FFT 找 4×载波峰值）
        cfo_hz = self._estimate_cfo_4thpower(iq)
        if abs(cfo_hz) > 1.0:
            t = np.arange(iq.size, dtype=np.float64) / self.fs
            iq = iq * np.exp(-1j * 2.0 * math.pi * cfo_hz * t)

        # 3. RRC 匹配滤波（真录脉冲成型；合成矩形脉冲链默认关闭）
        src = iq
        if self._rrc_taps is not None:
            src = np.convolve(iq, self._rrc_taps, mode="same")

        # 4. 定时恢复：Gardner 闭环（实验，前馈热启动）或 sps 偏移扫描（默认）
        if self.use_gardner:
            _syms_ff, off = self._recover_soft_symbols(src)
            syms = self._gardner_sample(src, tau0=float(off))
        else:
            syms, _offset = self._recover_soft_symbols(src)
        if syms.size < SYNC_SYMBOLS:
            return np.empty(0, dtype=np.float64), []

        # 5. Costas 符号速率载波跟踪（闭环，全流连续）
        syms = self._costas_track_symbols(syms)

        # 6. 软位流（I/Q 幅度）+ 硬判决 → 帧同步
        soft = np.empty(syms.size * 2, dtype=np.float64)
        soft[0::2] = np.real(syms)
        soft[1::2] = np.imag(syms)
        bits = (soft > 0.0).astype(np.uint8)
        return soft, self._find_frames(bits, max_hamming=max_hamming)

    def process(self, iq: np.ndarray) -> List[FrameCandidate]:
        """对一段复基带 IQ 做完整 QPSK 解调 + 帧同步。

        流程（机制见学习笔记 §2.1/§2.2）：
          1. AGC（RMS 归一化）
          2. FFT 粗频偏估计 + 校正
          3. 前馈定时恢复（多探点评分选 sps 偏移）
          4. Costas 符号速率环精跟踪（闭环，全流连续）
          5. 硬判决 → 64-bit 滑窗汉明相关帧同步

        返回:
            FrameCandidate 列表（按汉明距离排序）；无信号时空列表（诚实空态）。
        """
        _soft, cands = self.demodulate_soft(iq)
        return cands
