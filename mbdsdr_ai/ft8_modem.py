# SPDX-License-Identifier: MIT
"""FT8 8-FSK 调制器 + Costas 粗同步器（干净室自写，第①步）。

本模块依据 ``docs/learn/phase63/ft8-mechanism-study.md``（干净室机制笔记）
自述重写，只依赖 NumPy，可离线逐比特复现。WSJT-X (GPL) 源码仅作机制证据
（笔记中已记 file:line），本仓未包含其代码文本；下列物理层常量（12000 S/s、
0.16 s/符号、6.25 Hz 音距、7×7 Costas、格雷表、S7/D29/S7/D29/S7 帧布局）
均为 FT8 公开标准规定的协议事实，不构成 GPL 代码表达。

能力边界（诚实声明）：
  - 本轮只做 **物理层符号合成 + Costas 粗同步**：
      * :class:`Ft8Modulator`：连续相位 8-FSK 复基带 IQ 合成（12 kS/s），
        可注入频偏 / 时偏 / AWGN（固定种子可复现）；
      * :class:`Ft8CostasSync`：4× 时间过采样 / 2× 频率过采样的符号谱上做
        频偏×时偏二维 Costas 相关峰搜索，输出频偏、时偏、79 个符号与
        峰/次峰比 sync_quality。
  - **不做** LDPC(174,91) 编码 / 解交织 / BP 解码 / 77-bit 消息 unpack
    （留第②步）。:meth:`Ft8Modulator.encode_message` 仅留接口签名，调用即
    :class:`NotImplementedError`；本轮数据段由
    :meth:`Ft8Modulator.placeholder_data_symbols` 注入**确定性伪随机符号**
    （固定 seed，非真实编码）。
  - 无信号 / 纯噪声时返回**诚实空态**：``synced=False``、符号数组为空，
    不编造呼号 / 网格 / 同步结果。

参考（仅机制证据，未读入代码）：
  - wsjtx ``lib/ft8/ft8_params.f90``（NSPS=1920、NN=79、ND=58、NS=21）
  - wsjtx ``lib/ft8/genft8.f90``（graymap、icos7、S/D/S/D/S 布局）
  - wsjtx ``lib/ft8/sync8.f90``（nssy=4、nfos=2、三块 Costas 相关）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

__all__ = [
    "FS_HZ",
    "NSPS",
    "SYMBOL_SEC",
    "TONE_SPACING_HZ",
    "N_TONES",
    "N_SYMBOLS",
    "N_DATA_SYMBOLS",
    "GRAY_MAP",
    "COSTAS_SEQ",
    "FRAME_LAYOUT",
    "WINDOW_SEC",
    "Ft8Modulator",
    "Ft8CostasSync",
    "SyncResult",
    "gray_to_tone",
    "tone_to_gray",
    "iq_to_interleaved",
]

# --------------------------------------------------------------------------- #
# 协议常量（FT8 公开标准事实；证据见机制笔记 §7）
# --------------------------------------------------------------------------- #
FS_HZ: int = 12_000            # 采样率 S/s（ft8_params.f90:8）
NSPS: int = 1_920               # 样本/符号（ft8_params.f90:6）
SYMBOL_SEC: float = NSPS / FS_HZ          # = 0.16 s/符号
TONE_SPACING_HZ: float = 6.25  # 音距（ft8_downsample.f90:31）
N_TONES: int = 8               # 8-FSK
N_SYMBOLS: int = 79            # 一帧总符号（ft8_params.f90: NN=79）
N_DATA_SYMBOLS: int = 58       # 数据符号（ND=58 = 29+29）
N_SYNC_SYMBOLS: int = 21       # 同步符号（NS=21 = 7×3）
WINDOW_SEC: float = 15.0        # 接收搜索窗（T/R 槽）

#: 3-bit 码字 -> tone 索引的格雷映射（genft8.f90:15）。相邻码字对应相邻音，
#: 频偏小错只翻 1 bit（留给第②步 LDPC 纠正）。
GRAY_MAP: Tuple[int, ...] = (0, 1, 3, 2, 5, 6, 4, 7)

#: 7×7 Costas 阵列（genft8.f90:14）。7 个音在 0..6 内无重复，自相关峰锐利。
COSTAS_SEQ: Tuple[int, ...] = (3, 1, 4, 0, 6, 5, 2)

#: 帧布局符号起止（genft8.f90:32-35）：S7 D29 S7 D29 S7。
FRAME_LAYOUT: Tuple[Tuple[str, int, int], ...] = (
    ("S", 0, 7),     # Costas 块 1：符号 0..6
    ("D", 7, 36),    # 数据段 1：符号 7..35（29 符号）
    ("S", 36, 43),   # Costas 块 2：符号 36..42
    ("D", 43, 72),   # 数据段 2：符号 43..71（29 符号）
    ("S", 72, 79),   # Costas 块 3：符号 72..78
)


def gray_to_tone(word: int) -> int:
    """3-bit 码字 (0..7) -> tone 索引（格雷映射 GRAY_MAP）。"""
    if not 0 <= int(word) < 8:
        raise ValueError("FT8 3-bit 码字须在 0..7")
    return GRAY_MAP[int(word)]


def tone_to_gray(tone: int) -> int:
    """tone 索引 (0..7) -> 3-bit 码字（格雷映射逆映射）。"""
    if not 0 <= int(tone) < 8:
        raise ValueError("FT8 tone 索引须在 0..7")
    return GRAY_MAP.index(int(tone))


def iq_to_interleaved(iq: np.ndarray) -> np.ndarray:
    """复基带 IQ -> interleaved float32 ``[I0,Q0,I1,Q1,...]``（SigMF/C++ 消费）。

    FT8 是 8-FSK（非 PPM）；这里给出的是**时域波形样本**的交错序列化，
    便于后续喂 C++ 侧或 SigMF 录制，与调制方式无关。
    """
    iq = np.asarray(iq)
    out = np.empty(2 * iq.size, dtype=np.float32)
    out[0::2] = iq.real.astype(np.float32)
    out[1::2] = iq.imag.astype(np.float32)
    return out


# --------------------------------------------------------------------------- #
# 调制器
# --------------------------------------------------------------------------- #
class Ft8Modulator:
    """确定性 FT8 8-FSK 复基带调制器（12 kS/s，float32 复数）。

    参数:
        fs:            采样率（默认 12000 S/s，FT8 公开标准）。
        nsps:          样本/符号（默认 1920 = 0.16 s）。
        tone_spacing:  音距 Hz（默认 6.25）。
        n_tones:       音数（默认 8）。

    确定性：所有内部 RNG 用 ``np.random.default_rng(seed)`` 显式播种；
    同一输入 + 同一 seed 逐样本可复现。
    """

    def __init__(self, fs: int = FS_HZ, nsps: int = NSPS,
                 tone_spacing: float = TONE_SPACING_HZ,
                 n_tones: int = N_TONES) -> None:
        self.fs = int(fs)
        self.nsps = int(nsps)
        self.tone_spacing = float(tone_spacing)
        self.n_tones = int(n_tones)
        self.gray_map = np.array(GRAY_MAP, dtype=np.int8)
        self.costas = np.array(COSTAS_SEQ, dtype=np.int8)
        if self.n_tones != 8:
            raise ValueError("第①步仅实现 8-FSK（n_tones=8）")

    # -- 真实编码（第②步，委托 ft8_codec） ----------------------------------- #
    def encode_message(self, message_text: Optional[str] = None, *,
                       from_call: str = "K1ABC", to_call: str = "K2DEF",
                       grid4: str = "EM12", report: bool = False) -> np.ndarray:
        """把标准 FT8 消息编码成 58 个数据 tone 符号（int8，0..7）。

        流程：pack77(from,to,grid) -> 77 bit -> CRC14 -> 91 bit ->
        LDPC(174,91) -> 174 bit -> 每 3 bit 经格雷表选 tone（58 个）。
        实现见 :mod:`mbdsdr_ai.ft8_codec`（干净室 MIT，自写）。

        参数:
            message_text: 预留（文本解析留后续）；当前用显式 from/to/grid。
            from_call:    占位呼号（不预置真实呼号，默认 K1ABC/K2DEF）。
            to_call:      占位呼号。
            grid4:        4 字符 Maidenhead 网格（默认 EM12 占位）。
            report:       是否 R+ 报告格式。
        """
        from . import ft8_codec as _c
        msg77 = _c.pack77(from_call, to_call, grid4, report=report)
        bits91 = msg77 + _c.crc14_bits(msg77)
        codec = _c.Ft8Codec()
        cw = codec.encode(bits91)                 # 174 bit
        # 每 3 bit -> 格雷 tone 索引
        tones = np.empty(N_DATA_SYMBOLS, dtype=np.int8)
        for s in range(N_DATA_SYMBOLS):
            bits3 = (int(cw[s * 3]) << 2) | (int(cw[s * 3 + 1]) << 1) \
                | int(cw[s * 3 + 2])
            tones[s] = self.gray_map[bits3]
        return tones

    # -- 确定性占位数据段 --------------------------------------------------- #
    def placeholder_data_symbols(self, seed: int = 20261010,
                                  n: int = N_DATA_SYMBOLS) -> np.ndarray:
        """生成**确定性伪随机**数据 tone 符号（0..7），固定 seed 可复现。

        这不是真实 LDPC 编码输出，只是第①步让帧结构闭合的占位；
        文档与实验均明确标注 data-origin: synthetic。
        """
        rng = np.random.default_rng(int(seed))
        return rng.integers(0, self.n_tones, size=int(n)).astype(np.int8)

    # -- 帧组装 ------------------------------------------------------------- #
    def build_frame_symbols(self, data_symbols: Optional[np.ndarray] = None
                            ) -> np.ndarray:
        """按 S7 D29 S7 D29 S7 组装 79 个 tone 符号（int8，0..7）。

        参数:
            data_symbols: 长度 58 的数据 tone 符号；None 时用
                          :meth:`placeholder_data_symbols` 确定性生成。
        """
        if data_symbols is None:
            data_symbols = self.placeholder_data_symbols()
        data_symbols = np.asarray(data_symbols, dtype=np.int8).reshape(-1)
        if data_symbols.size != N_DATA_SYMBOLS:
            raise ValueError(
                f"数据段须为 {N_DATA_SYMBOLS} 个符号（得到 {data_symbols.size}）")
        if np.any((data_symbols < 0) | (data_symbols >= self.n_tones)):
            raise ValueError("数据 tone 符号须在 0..7")
        itone = np.empty(N_SYMBOLS, dtype=np.int8)
        # 三段 Costas（同一 icos7）+ 两段各 29 符号数据
        itone[0:7] = self.costas
        itone[7:36] = data_symbols[0:29]
        itone[36:43] = self.costas
        itone[43:72] = data_symbols[29:58]
        itone[72:79] = self.costas
        return itone

    # -- 波形合成 ----------------------------------------------------------- #
    def modulate(self, itone: Optional[np.ndarray] = None, *,
                 freq_offset_hz: float = 0.0,
                 time_offset_samples: int = 0,
                 awgn_snr_db: Optional[float] = None,
                 noise_seed: int = 20261010) -> np.ndarray:
        """合成连续相位 8-FSK 复基带 IQ（complex64，12 kS/s）。

        参数:
            itone:               79 个 tone 符号；None 时用 build_frame_symbols()。
            freq_offset_hz:      注入频偏（Hz，相对基带中心；测试范围 ±100）。
            time_offset_samples: 注入时偏（样本数；在帧前补这么多零）。
            awgn_snr_db:         AWGN 信噪比（dB）；None 不加噪。
            noise_seed:          AWGN RNG 种子（固定可复现）。

        返回:
            complex64 numpy 数组。若 time_offset_samples>0，数组前部为补零。
        """
        if itone is None:
            itone = self.build_frame_symbols()
        itone = np.asarray(itone, dtype=np.int8).reshape(-1)
        if itone.size != N_SYMBOLS:
            raise ValueError(f"帧须为 {N_SYMBOLS} 个符号（得到 {itone.size}）")

        # 每个符号的瞬时频率（Hz）：8 音居中，tone t -> (t-3.5)*spacing
        tone_hz = (itone.astype(np.float64) - (self.n_tones - 1) / 2.0) \
            * self.tone_spacing
        freq_per_sample = np.repeat(tone_hz, self.nsps)
        n = freq_per_sample.size
        t = np.arange(n, dtype=np.float64) / self.fs
        # 连续相位：cumsum 不重置符号间相位（避免硬切换宽带泄漏）
        phase = 2.0 * math.pi * np.cumsum(freq_per_sample) / self.fs
        iq = np.exp(1j * phase).astype(np.complex128)

        # 频偏注入（复数混频）
        if freq_offset_hz:
            iq = iq * np.exp(1j * 2.0 * math.pi * float(freq_offset_hz) * t)

        # 时偏注入：帧前补零
        if time_offset_samples > 0:
            pad = np.zeros(int(time_offset_samples), dtype=np.complex128)
            iq = np.concatenate([pad, iq])
        elif time_offset_samples < 0:
            raise ValueError("time_offset_samples 不能为负（时偏只向前补零）")

        # AWGN：SNR = 信号功率 / 复噪声功率。单位幅度 FSK 信号功率 = 1。
        if awgn_snr_db is not None:
            rng = np.random.default_rng(int(noise_seed))
            sig_power = float(np.mean(np.abs(iq[time_offset_samples:]) ** 2)) \
                if time_offset_samples else float(np.mean(np.abs(iq) ** 2))
            sigma2 = sig_power / (10.0 ** (float(awgn_snr_db) / 10.0))
            sigma = math.sqrt(sigma2)
            noise = (rng.standard_normal(iq.size) + 1j * rng.standard_normal(iq.size)) \
                * (sigma / math.sqrt(2.0))
            iq = iq + noise

        return iq.astype(np.complex64)


# --------------------------------------------------------------------------- #
# Costas 粗同步器
# --------------------------------------------------------------------------- #
@dataclass
class SyncResult:
    """Costas 粗同步输出（诚实空态：synced=False 时 symbols 为空数组）。"""
    synced: bool = False
    freq_offset_hz: float = 0.0
    time_offset_samples: int = 0
    symbols: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int8))
    sync_quality: float = 0.0       # 相关峰 / 次峰比
    n_time_bins: int = 0             # 搜索窗内时间 bin 数（诊断用）


class Ft8CostasSync:
    """FT8 Costas 粗同步器：符号谱上做频偏×时偏二维相关峰搜索。

    机制（自述，证据见机制笔记 §2/§5）：
      - 时间过采样 4×：每 NSPS/4=480 样本一个谱窗；
      - 频率过采样 2×：DFT 长度 N=fs/(tone/2)=3840，bin 间距 3.125 Hz；
      - 三个 S7 块（符号偏移 0/36/72）在 (off_bin, j) 处对齐 21 个 Costas
        音位，求和得相关平面；峰位 = 信号频偏与时偏。

    参数:
        fs:                    采样率。
        nsps:                  样本/符号。
        tone_spacing:          音距 Hz。
        time_oversample:      时间过采样倍（默认 4）。
        freq_oversample:      频率过采样倍（默认 2）。
        freq_search_hz:        频偏搜索范围 (lo, hi)，默认 ±100 Hz。
        sync_quality_threshold: 峰/次峰比门限；低于则 synced=False（诚实空态）。
    """

    def __init__(self, fs: int = FS_HZ, nsps: int = NSPS,
                 tone_spacing: float = TONE_SPACING_HZ,
                 time_oversample: int = 4, freq_oversample: int = 2,
                 freq_search_hz: Tuple[float, float] = (-100.0, 100.0),
                 sync_quality_threshold: float = 1.5) -> None:
        self.fs = int(fs)
        self.nsps = int(nsps)
        self.tone_spacing = float(tone_spacing)
        self.ts = int(time_oversample)
        self.fs_os = int(freq_oversample)
        # 频率 bin 间距 = 音距 / 频率过采样 = 3.125 Hz
        self.df = self.tone_spacing / self.fs_os
        # DFT 长度：fs/df = 12000/3.125 = 3840
        self.nfft = int(round(self.fs / self.df))
        # 时间步：nsps/时间过采样 = 480 样本
        self.step = self.nsps // self.ts
        lo = int(math.floor(freq_search_hz[0] / self.df))
        hi = int(math.ceil(freq_search_hz[1] / self.df))
        self.off_bins = np.arange(lo, hi + 1, dtype=np.int64)
        self.quality_threshold = float(sync_quality_threshold)
        # Costas 抽头：(块内四分之一符号偏移, tone)。三块起始四分之一符号偏移
        # = 0 / 36*4=144 / 72*4=288（sync8.f90:62-74）。
        self.taps = []
        for block_sym_off in (0, 36, 72):
            for k, tone in enumerate(COSTAS_SEQ):
                self.taps.append((block_sym_off * self.ts + k * self.ts, int(tone)))
        # 帧在四分之一符号轴上的总长 = 79*4 = 316；最后抽头时间索引 = 288+6*4=312
        self.frame_qs = N_SYMBOLS * self.ts

    # -- 符号谱 ------------------------------------------------------------- #
    def _symbol_spectrum(self, iq: np.ndarray) -> Tuple[np.ndarray, int]:
        """构造幅度符号谱 |S[bin, time_bin]|，返回 (谱矩阵, n_time_bins)。"""
        iq = np.asarray(iq)
        n = iq.size
        if n < self.nfft:
            return np.empty((0, self.nfft)), 0
        n_win = (n - self.nfft) // self.step + 1
        if n_win <= 0:
            return np.empty((0, self.nfft)), 0
        # 滑窗矩阵 + 一次矩阵 DFT
        starts = np.arange(n_win) * self.step
        shape = (n_win, self.nfft)
        strides = (iq.strides[0] * self.step, iq.strides[0])
        windows = np.lib.stride_tricks.as_strided(
            iq, shape=shape, strides=strides, writeable=False)
        spec = np.abs(np.fft.fftshift(np.fft.fft(windows), axes=1))
        return spec, n_win

    # -- 主流程 ------------------------------------------------------------- #
    def process(self, iq: np.ndarray) -> SyncResult:
        """对一段 12 kS/s 复基带做 Costas 粗同步。"""
        iq = np.asarray(iq)
        spec, n_win = self._symbol_spectrum(iq)
        if n_win == 0:
            return SyncResult(synced=False, n_time_bins=0)

        # 时偏搜索范围：j + 最后抽头(312) <= n_win-1  ⇒  j <= n_win-313
        j_max = n_win - 1 - (self.frame_qs - self.ts)  # = n_win-313
        if j_max < 0:
            return SyncResult(synced=False, n_time_bins=n_win)
        j_range = np.arange(0, j_max + 1, dtype=np.int64)

        # 相关平面 corr[off_bin_idx, j_idx]
        corr = np.zeros((self.off_bins.size, j_range.size), dtype=np.float64)
        for ib, ob in enumerate(self.off_bins):
            s = np.zeros(j_range.size, dtype=np.float64)
            for tqs, tone in self.taps:
                time_idx = j_range + tqs
                freq_idx = self.nfft // 2 + int(ob) \
                    + int(round((tone - (N_TONES - 1) / 2.0) * self.fs_os))
                s += spec[time_idx, freq_idx]
            corr[ib, :] = s

        # 峰搜索
        peak_pos = int(np.argmax(corr))
        ib_peak, j_peak = np.unravel_index(peak_pos, corr.shape)
        p_peak = float(corr[ib_peak, j_peak])

        # 次峰：挖掉峰邻域（±2 bin 频偏 / ±8 时间步 ≈ ±2 符号）后取最大
        masked = corr.copy()
        i0, i1 = max(0, ib_peak - 2), min(corr.shape[0], ib_peak + 3)
        j0, j1 = max(0, j_peak - 8), min(corr.shape[1], j_peak + 9)
        masked[i0:i1, j0:j1] = -1.0
        p_2nd = float(masked.max())

        quality = p_peak / p_2nd if p_2nd > 1e-12 else float("inf")
        if not math.isfinite(quality) or quality < self.quality_threshold:
            # 诚实空态：不编造符号
            return SyncResult(synced=False, sync_quality=quality,
                              n_time_bins=n_win)

        off_peak = int(self.off_bins[ib_peak])
        j_approx = int(j_range[j_peak])
        freq_offset_hz = off_peak * self.df

        # -- 精同步：粗频偏混掉后，用逐符号匹配滤波找精确符号边界 ----------- #
        iq = np.asarray(iq)
        n = iq.size
        t = np.arange(n, dtype=np.float64) / self.fs
        iq_m = (iq.astype(np.complex128)
                * np.exp(-2.0 * math.pi * 1j * freq_offset_hz * t))

        # 8 音 × 1920 样本的匹配滤波本地振荡（相对于信号中心）
        osc = np.empty((N_TONES, self.nsps), dtype=np.complex128)
        nvec = np.arange(self.nsps, dtype=np.float64)
        for tone in range(N_TONES):
            f = (tone - (N_TONES - 1) / 2.0) * self.tone_spacing
            osc[tone] = np.exp(2.0 * math.pi * 1j * f * nvec / self.fs)

        def _block_energy(start: int) -> np.ndarray:
            blk = iq_m[start:start + self.nsps]
            if blk.size < self.nsps:
                return np.zeros(N_TONES)
            return np.abs(blk @ osc.conj().T)

        # Costas 位置（符号索引, tone）：三块 icos7
        costas_pos = []
        for block_sym_off in (0, 36, 72):
            for k, tone in enumerate(COSTAS_SEQ):
                costas_pos.append((block_sym_off + k, int(tone)))

        # 在粗时偏 ±2 符号内搜精确边界 b（48 样本步）
        center = j_approx * self.step
        lo_b = max(0, center - 2 * self.nsps)
        hi_b = min(n - self.nsps, center + 2 * self.nsps)
        best_b, best_E = center, -1.0
        for b in range(lo_b, hi_b + 1, self.nsps // 40):
            E = 0.0
            for sym_idx, tone in costas_pos:
                e = _block_energy(b + sym_idx * self.nsps)
                if e.size:
                    E += e[tone]
            if E > best_E:
                best_E, best_b = E, b
        time_offset_samples = int(best_b)

        # 精边界处提取 79 个符号（8 音匹配能量 argmax）
        symbols = np.empty(N_SYMBOLS, dtype=np.int8)
        for k in range(N_SYMBOLS):
            e = _block_energy(time_offset_samples + k * self.nsps)
            symbols[k] = int(np.argmax(e))

        return SyncResult(
            synced=True,
            freq_offset_hz=freq_offset_hz,
            time_offset_samples=time_offset_samples,
            symbols=symbols,
            sync_quality=quality,
            n_time_bins=n_win,
        )
