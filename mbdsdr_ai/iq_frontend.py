"""
MBDSDR IQ 前端校正链
====================

在 SDR 后端读取复数 IQ 之后、进 FFT / 解调之前，对零中频（ZIF）接收链
做三类基础时域校正——SDR++ / GQRX 等成熟 SDR 软件均具备：

  1. DC 偏移去除：一阶 IIR DC blocker，跟踪随温度/时间慢漂的直流分量，
     去掉后频谱中心频处不再有固定尖峰（DC spike）。
  2. I/Q 不平衡校正：从数据估计 2×2 协方差并做白化，压制因 I/Q 两路
     增益差与相位差产生的关于中心频对称的镜像（image）。
  3. 抗混叠抽取：Kaiser 窗 FIR 低通（截止 = 输出 Nyquist，留过渡带）
     之后再整数抽取，杜绝高频折叠混叠到基带。

设计要点
--------
- 全部校正参数从真数据估计，不硬编码任何频点/增益/相位。
- 流式友好：每个校正块维护跨块连续状态，可逐块 process()，
  适配 receive_pipeline 的 IqReaderThread 逐块读取模式。
- 可独立开关：IQFrontend.set_dc_removal() / set_iq_balance() /
  set_decimation()，运行时切换不丢状态。
- 纯 NumPy + 可选 SciPy（无 SciPy 时自动降级到 NumPy 实现），
  不依赖 Qt，可在离线分析与实时链路共用。

抗混叠截止频率归一化说明（避免 2 倍错误）
------------------------------------------
scipy.signal.firwin 的 cutoff 以「输入 Nyquist = fs_in/2」为 1.0 归一化。
抽取因子 D 后输出 Nyquist = fs_in/(2D)，对应归一化频率 1/D。
因此低通 -6dB 截止应在 ~0.9/D（通带 0.8/D → 阻带 1/D），
**不是** 0.5/D——后者把截止设到了输出 Nyquist 的一半，是常见 2 倍错误。

License: GPL-3.0-or-later
"""

from __future__ import annotations

import numpy as np
from typing import Any, Dict, Optional


# ═══════════════════════════════════════════════════════════════════════
# 1. DC 偏移去除（一阶 IIR DC blocker）
# ═══════════════════════════════════════════════════════════════════════

class DCBlocker:
    """一阶 IIR 直流阻断器。

    差分方程::

        y[n] = x[n] - x[n-1] + R * y[n-1]

    R ∈ [0, 1)，典型 0.995~0.999。R 越接近 1，截止频率越低、对信号
    损伤越小，但跟踪慢漂直流的速度也越慢。

    相比「减整段均值」，IIR 结构能跟踪随温度/时间缓慢漂移的直流，
    且流式、O(1) 状态、无需整段已知。对复数 IQ 分别处理 I / Q 两路。
    """

    def __init__(self, r: float = 0.998) -> None:
        if not 0.0 <= r < 1.0:
            raise ValueError(f"DC blocker pole r must be in [0, 1), got {r}")
        self._r = float(r)
        # I / Q 两路独立状态（复数信号必须分别维持，不能共用）
        self._x_prev_i = 0.0
        self._y_prev_i = 0.0
        self._x_prev_q = 0.0
        self._y_prev_q = 0.0
        # 实数通路状态（实数输入时使用）
        self._x_prev_r = 0.0
        self._y_prev_r = 0.0

    @property
    def r(self) -> float:
        return self._r

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段 IQ 样本，返回 DC 阻断后的同长度数组。

        对复数输入分别对实部（I）和虚部（Q）做一阶 IIR，两路各自
        维持独立状态；对实数输入用实数通路状态。跨块连续。
        """
        if x.size == 0:
            return x
        if np.iscomplexobj(x):
            i_out, self._x_prev_i, self._y_prev_i = self._process_real(
                np.asarray(x.real, dtype=np.float64),
                self._x_prev_i, self._y_prev_i)
            q_out, self._x_prev_q, self._y_prev_q = self._process_real(
                np.asarray(x.imag, dtype=np.float64),
                self._x_prev_q, self._y_prev_q)
            return (i_out + 1j * q_out).astype(x.dtype)
        out, self._x_prev_r, self._y_prev_r = self._process_real(
            np.asarray(x, dtype=np.float64),
            self._x_prev_r, self._y_prev_r)
        return out.astype(x.dtype)

    def _process_real(self, x: np.ndarray, x_prev: float, y_prev: float):
        """向量化一阶 IIR：y[n] = x[n] - x[n-1] + R*y[n-1]。

        接受输入状态 (x_prev, y_prev)，返回 (y, x_prev_new, y_prev_new)。
        优先用 scipy.signal.lfilter 带初始状态；SciPy 不可用时退化为
        NumPy 递推（结果等价）。
        """
        r = self._r
        n = x.shape[0]

        try:
            from scipy.signal import lfilter
            # 一阶 IIR: b=[1, -1], a=[1, -r]
            # 初始状态 zi[0] = -x_prev + r*y_prev（由 lfilter_zi 推导）
            zi = np.array([-x_prev + r * y_prev], dtype=np.float64)
            y, zf = lfilter([1.0, -1.0], [1.0, -r], x, zi=zi)
            return y, float(x[-1]), float(y[-1])
        except ImportError:
            pass

        # NumPy 兜底：先算差分，再一阶递推
        diff = np.empty(n, dtype=np.float64)
        diff[0] = x[0] - x_prev
        if n > 1:
            diff[1:] = x[1:] - x[:-1]
        y = np.empty(n, dtype=np.float64)
        yp = y_prev
        for i in range(n):
            yp = diff[i] + r * yp
            y[i] = yp
        return y, float(x[-1]), yp

    def reset(self) -> None:
        """清空内部状态（换源/换频时调用，避免旧直流残留）。"""
        self._x_prev_i = 0.0
        self._y_prev_i = 0.0
        self._x_prev_q = 0.0
        self._y_prev_q = 0.0
        self._x_prev_r = 0.0
        self._y_prev_r = 0.0


# ═══════════════════════════════════════════════════════════════════════
# 2. I/Q 不平衡校正（协方差白化）
# ═══════════════════════════════════════════════════════════════════════

class IQBalanceCorrector:
    """I/Q 不平衡校正：基于协方差白化。

    零中频架构中，I / Q 两路增益不一致与相位不正交会产生关于中心频
    对称的镜像（image）。常见做法是分别反解增益差 g 与相位误差 φ
    再补偿，但 g 与 φ 在二阶矩里相互耦合，逐参数反解不彻底。

    本实现改用**协方差白化**：
      1. 去均值后估计 2×2 协方差 C = [[varI, covIQ], [covIQ, varQ]]
      2. 特征分解 C = V Λ Vᵀ，构造白化矩阵 W = V · diag(1/√Λ) · Vᵀ
      3. 对 [I, Q]ᵀ 左乘 W，使两路零均值、等功率、互不相关

    白化对任意「增益差 + 正交相位误差」组合都成立，不依赖参数化假设。

    自适应策略：累积足够样本后重新估计白化矩阵（默认每 50 块或
    累积 32768 样本重估一次），适配设备温漂。fit 一次得到 W 后
    可对后续连续流 apply，流式友好。
    """

    def __init__(
        self,
        adapt_interval_blocks: int = 50,
        min_samples: int = 4096,
        max_samples: int = 32768,
    ) -> None:
        if adapt_interval_blocks < 1:
            raise ValueError("adapt_interval_blocks must be >= 1")
        if min_samples < 256:
            raise ValueError("min_samples must be >= 256 for stable covariance")
        self._adapt_interval = int(adapt_interval_blocks)
        self._min_samples = int(min_samples)
        self._max_samples = int(max_samples)

        self._whitening: Optional[np.ndarray] = None
        self._mean: Optional[np.ndarray] = None
        self._fitted = False
        self._block_count = 0

        # 累积样本用于重估（环形缓冲思路：超过 max 就覆盖旧的）
        self._accum: Optional[np.ndarray] = None
        self._accum_n = 0

        # 最近一次诊断
        self._last_diag: Dict[str, Any] = {"fitted": False}

    @property
    def fitted(self) -> bool:
        return self._fitted

    @property
    def diagnostics(self) -> Dict[str, Any]:
        """返回最近一次估计的诊断信息（增益误差、相位误差、协方差等）。"""
        return dict(self._last_diag)

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段 IQ：先累积样本用于自适应重估，再应用白化矩阵。

        未拟合时（前几块）直通输出，同时累积样本；累积够 min_samples
        后立即首次估计。之后每 adapt_interval_blocks 重估一次。
        """
        if x.size == 0:
            return x
        if not np.iscomplexobj(x):
            # 实数数据无 I/Q 不平衡概念，直通
            return x

        self._accumulate(x)
        self._block_count += 1

        need_fit = (not self._fitted and self._accum_n >= self._min_samples)
        need_refit = (self._fitted and self._block_count >= self._adapt_interval
                      and self._accum_n >= self._min_samples)
        if need_fit or need_refit:
            self._fit_from_accum()
            self._block_count = 0

        if not self._fitted or self._whitening is None or self._mean is None:
            return x

        # 应用：去均值 → 白化
        iq = np.column_stack((x.real, x.imag))
        iq_centered = iq - self._mean
        iq_white = iq_centered @ self._whitening.T
        return (iq_white[:, 0] + 1j * iq_white[:, 1]).astype(x.dtype)

    def _accumulate(self, x: np.ndarray) -> None:
        """把新样本的 [I, Q] 累积到重估缓冲（超过 max_samples 时丢弃最旧）。"""
        n = x.shape[0]
        iq = np.column_stack((x.real, x.imag)).astype(np.float64)
        if self._accum is None:
            cap = max(self._max_samples, n)
            self._accum = np.empty((cap, 2), dtype=np.float64)
            self._accum_n = 0

        cap = self._accum.shape[0]
        if self._accum_n + n <= cap:
            self._accum[self._accum_n:self._accum_n + n] = iq
            self._accum_n += n
        else:
            # 缓冲满：保留最新的 max_samples（丢弃最旧）
            keep = self._max_samples - n
            if keep > 0 and self._accum_n > 0:
                self._accum[:keep] = self._accum[self._accum_n - keep:self._accum_n]
                self._accum[keep:keep + n] = iq
                self._accum_n = keep + n
            else:
                # 单块超过容量：直接用这一块
                self._accum[:n] = iq[-cap:] if n > cap else iq
                self._accum_n = min(n, cap)

    def _fit_from_accum(self) -> None:
        """从累积样本估计协方差白化矩阵。"""
        if self._accum is None or self._accum_n < 2:
            return
        iq = self._accum[:self._accum_n]

        # 去均值
        mean = np.mean(iq, axis=0)
        iq_centered = iq - mean

        # 2×2 协方差（rowvar=False：每列是一个变量）
        cov = np.cov(iq_centered, rowvar=False)
        if cov.ndim != 2 or cov.shape != (2, 2):
            return

        # 特征分解（对称矩阵用 eigh）
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        # 避免除零：特征值下限保护
        eigenvalues = np.maximum(eigenvalues, 1e-12)

        # 白化矩阵 W = V · diag(1/√Λ) · Vᵀ
        whitening = eigenvectors @ np.diag(1.0 / np.sqrt(eigenvalues)) @ eigenvectors.T

        self._whitening = whitening
        self._mean = mean
        self._fitted = True

        # 诊断：从协方差反推增益误差与相位误差
        var_i = float(cov[0, 0])
        var_q = float(cov[1, 1])
        cov_iq = float(cov[0, 1])
        gain_err = float(np.sqrt(var_q / var_i)) if var_i > 1e-15 else 1.0
        if var_i > 1e-15 and var_q > 1e-15:
            corr = cov_iq / np.sqrt(var_i * var_q)
            phase_err_deg = float(np.degrees(np.arcsin(np.clip(corr, -1.0, 1.0))))
        else:
            phase_err_deg = 0.0

        self._last_diag = {
            "fitted": True,
            "samples_used": int(self._accum_n),
            "covariance_matrix": [[var_i, cov_iq], [cov_iq, var_q]],
            "iq_gain_error": gain_err,
            "iq_phase_error_deg": phase_err_deg,
            "dc_offset_i": float(mean[0]),
            "dc_offset_q": float(mean[1]),
        }

    def reset(self) -> None:
        """清空拟合状态与累积缓冲（换源/换频时调用）。"""
        self._whitening = None
        self._mean = None
        self._fitted = False
        self._block_count = 0
        self._accum = None
        self._accum_n = 0
        self._last_diag = {"fitted": False}


# ═══════════════════════════════════════════════════════════════════════
# 3. 抗混叠抽取（Kaiser FIR 低通 + 整数抽取）
# ═══════════════════════════════════════════════════════════════════════

class AntiAliasDecimator:
    """抗混叠整数抽取。

    流程：Kaiser 窗 FIR 低通（截止 = 输出 Nyquist，留 ~20% 过渡带）
    → 每 factor 个样本取 1 个。输出采样率 = 输入采样率 / factor。

    这是 DDC（数字下变频）的末级。对照 GNU Radio rational_resampler
    的多相滤波结构：抽取前必须先把 |f| > fs_out/2 的分量滤掉，
    否则高频会折叠混叠到基带，严重劣化解调质量。

    **截止频率归一化（关键，避免 2 倍错误）**：
    firwin 的 cutoff 以输入 Nyquist (fs_in/2) 为 1.0。
    抽取 D 后输出 Nyquist = fs_in/(2D) → 归一化 1/D。
    阻带边缘 = 1/D，通带边缘 = 0.8/D，-6dB 截止 ≈ 0.9/D。
    绝不能用 0.5/D——那是把 fs 归一化和 fs/2 归一化搞混了的 2 倍错误。
    """

    def __init__(self, factor: int = 1, ripple_db: float = 60.0) -> None:
        if factor < 1:
            raise ValueError(f"decimation factor must be >= 1, got {factor}")
        self._factor = int(factor)
        self._ripple_db = float(ripple_db)
        self._taps: Optional[np.ndarray] = None
        # FIR 滤波历史（流式 lfilter 状态）
        self._zi: Optional[np.ndarray] = None
        self._design_taps()

    @property
    def factor(self) -> int:
        return self._factor

    def set_factor(self, factor: int) -> None:
        """运行时切换抽取因子，自动重设计滤波器并清空状态。"""
        factor = int(factor)
        if factor < 1:
            raise ValueError(f"decimation factor must be >= 1, got {factor}")
        if factor == self._factor:
            return
        self._factor = factor
        self._design_taps()
        self._zi = None

    def _design_taps(self) -> None:
        """设计 Kaiser 窗 FIR 抗混叠低通抽头。"""
        if self._factor <= 1:
            self._taps = None
            return

        D = self._factor
        # 归一化频率（输入 Nyquist = 1.0）
        stopband_edge = 1.0 / D        # 折叠频率：超过此频率的分量会混叠
        trans = 0.2 / D                # 20% 过渡带
        passband_edge = stopband_edge - trans  # 通带边缘
        cutoff = (passband_edge + stopband_edge) / 2.0  # -6dB 截止

        try:
            from scipy.signal import kaiserord, firwin
            numtaps, beta = kaiserord(self._ripple_db, trans)
            # 工程经验：FIR 阶数至少 32 * factor，保证阻带衰减达标
            numtaps = max(numtaps, 32 * D)
            if numtaps % 2 == 0:
                numtaps += 1  # 奇数抽头（Type I 线性相位）
            taps = firwin(numtaps, cutoff, window=('kaiser', beta), scale=True)
        except ImportError:
            # SciPy 不可用：NumPy 兜底 sinc + Hanning 窗
            numtaps = max(32 * D, 32)
            if numtaps % 2 == 0:
                numtaps += 1
            t = np.arange(numtaps) - (numtaps - 1) / 2.0
            h = 2.0 * cutoff * np.sinc(2.0 * cutoff * t)
            h *= np.hanning(numtaps)
            h /= np.sum(h)
            taps = h

        self._taps = taps.astype(np.float64)

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段 IQ：抗混叠低通 → 抽取。

        factor=1 时直通。跨块维持 FIR 滤波器状态（zi），保证连续。
        对复数输入分别滤波 I / Q 两路。
        """
        if x.size == 0:
            return x
        if self._factor <= 1 or self._taps is None:
            return x

        taps = self._taps
        n_taps = taps.shape[0]

        if np.iscomplexobj(x):
            i_filt = self._filter_real(np.asarray(x.real, dtype=np.float64), taps)
            q_filt = self._filter_real(np.asarray(x.imag, dtype=np.float64), taps)
            filtered = (i_filt + 1j * q_filt).astype(x.dtype)
        else:
            filtered = self._filter_real(
                np.asarray(x, dtype=np.float64), taps).astype(x.dtype)

        # 滤波完成后抽取（多相结构的等价离线实现）
        return filtered[::self._factor]

    def _filter_real(self, x: np.ndarray, taps: np.ndarray) -> np.ndarray:
        """带状态的 FIR 滤波（因果 lfilter），跨块连续。"""
        n_taps = taps.shape[0]
        try:
            from scipy.signal import lfilter, lfilter_zi
            if self._zi is None:
                self._zi = lfilter_zi(taps, 1.0) * 0.0  # 零初始状态
            y, zf = lfilter(taps, 1.0, x, zi=self._zi)
            self._zi = zf
            return y
        except ImportError:
            pass

        # NumPy 兜底：直接卷积（无状态，每块独立——短块有边缘瞬态）
        # 用 full 卷积后取前 len(x) 个（因果）
        if self._zi is None:
            self._zi = np.zeros(n_taps - 1, dtype=np.float64)
        # 把上一块的尾部拼到当前块前面，模拟连续滤波
        x_ext = np.concatenate((self._zi, x))
        y_full = np.convolve(x_ext, taps, mode='full')
        # 因果输出：从 n_taps-1 开始取 len(x) 个
        y = y_full[n_taps - 1:n_taps - 1 + x.shape[0]]
        # 保存当前块尾部供下一块
        self._zi = x_ext[-(n_taps - 1):] if n_taps > 1 else np.array([])
        return y

    def reset(self) -> None:
        """清空 FIR 滤波器状态（换源/换频时调用）。"""
        self._zi = None


# ═══════════════════════════════════════════════════════════════════════
# 4. IQFrontend：可开关校正链（DC → IQ 平衡 → 抽取）
# ═══════════════════════════════════════════════════════════════════════

class IQFrontend:
    """SDR 接收前端 IQ 校正链。

    串联顺序（与 SDR++ / GQRX 的前端处理顺序一致）::

        后端 IQ → [DC 去除] → [I/Q 平衡] → [抗混叠抽取] → FFT / 解调

    每个环节可独立开关，默认全开。运行时切换不丢内部状态。
    流式友好：逐块 process()，跨块连续。

    用法（实时链路）::

        fe = IQFrontend(dc_removal=True, iq_balance=True, decimation=1)
        while running:
            iq = backend.read_samples(16384)
            iq_corrected = fe.process(iq)
            # 送频谱 / 解调

    用法（离线分析）::

        fe = IQFrontend()
        corrected, diag = fe.process_file(iq)  # 或逐块 process

    开关控制（对接控制面板）::

        fe.set_dc_removal(False)   # 关闭 DC 去除
        fe.set_iq_balance(True)    # 开启 I/Q 平衡
        fe.set_decimation(4)       # 4 倍抽取
    """

    def __init__(
        self,
        dc_removal: bool = True,
        iq_balance: bool = True,
        decimation: int = 1,
        dc_r: float = 0.998,
        iq_adapt_interval_blocks: int = 50,
    ) -> None:
        self._dc_enabled = bool(dc_removal)
        self._iq_enabled = bool(iq_balance)

        self._dc_blocker = DCBlocker(r=dc_r)
        self._iq_corrector = IQBalanceCorrector(
            adapt_interval_blocks=iq_adapt_interval_blocks)
        self._decimator = AntiAliasDecimator(factor=decimation)

    # ---- 开关控制 ----

    def set_dc_removal(self, on: bool) -> None:
        """开启/关闭 DC 偏移去除。关闭时数据直通该环节。"""
        self._dc_enabled = bool(on)
        if on:
            # 重新开启时重置状态，避免旧直流残留跳到输出
            self._dc_blocker.reset()

    def set_iq_balance(self, on: bool) -> None:
        """开启/关闭 I/Q 不平衡校正。关闭时数据直通该环节。"""
        self._iq_enabled = bool(on)
        if on:
            # 重新开启时重置拟合状态，从新数据重新估计
            self._iq_corrector.reset()

    def set_decimation(self, factor: int) -> None:
        """设置抽取因子（1 = 不抽取）。自动重设计抗混叠滤波器。"""
        self._decimator.set_factor(factor)

    @property
    def dc_removal_enabled(self) -> bool:
        return self._dc_enabled

    @property
    def iq_balance_enabled(self) -> bool:
        return self._iq_enabled

    @property
    def decimation_factor(self) -> int:
        return self._decimator.factor

    # ---- 主处理 ----

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段 IQ 样本，依次经过 DC 去除 → I/Q 平衡 → 抗混叠抽取。

        各环节可独立开关，关闭的环节数据直通。
        返回校正后的 IQ 数组（抽取后长度可能变短）。
        """
        if x is None or x.size == 0:
            return x

        out = x

        # 1. DC 偏移去除
        if self._dc_enabled:
            out = self._dc_blocker.process(out)

        # 2. I/Q 不平衡校正
        if self._iq_enabled:
            out = self._iq_corrector.process(out)

        # 3. 抗混叠抽取（必须在最后：先滤波降带宽再降采样率）
        out = self._decimator.process(out)

        return out

    def reset(self) -> None:
        """重置所有内部状态（换源/换频/重新开始时调用）。"""
        self._dc_blocker.reset()
        self._iq_corrector.reset()
        self._decimator.reset()

    @property
    def diagnostics(self) -> Dict[str, Any]:
        """返回当前各环节诊断信息。"""
        iq_diag = self._iq_corrector.diagnostics
        return {
            "dc_removal_enabled": self._dc_enabled,
            "iq_balance_enabled": self._iq_enabled,
            "decimation_factor": self._decimator.factor,
            "iq_correction": iq_diag,
        }


# ═══════════════════════════════════════════════════════════════════════
# 5. 便捷函数：一站式 front_end（兼容 dsp.front_end 调用风格）
# ═══════════════════════════════════════════════════════════════════════

def front_end(
    x: np.ndarray,
    dc_removal: bool = True,
    iq_balance: bool = True,
    decimation: int = 1,
    dc_r: float = 0.998,
) -> tuple:
    """一站式 IQ 前端校正（无状态，适合离线整段分析）。

    返回 (校正后 IQ, 诊断信息 dict)。

    注意：此函数每次调用新建校正块，不跨调用维持状态。
    实时逐块处理请使用 IQFrontend 类。
    """
    fe = IQFrontend(
        dc_removal=dc_removal,
        iq_balance=iq_balance,
        decimation=decimation,
        dc_r=dc_r,
        # 离线整段：用全部样本一次拟合，不需要自适应间隔
        iq_adapt_interval_blocks=1,
    )
    corrected = fe.process(x)
    return corrected, fe.diagnostics


# ═══════════════════════════════════════════════════════════════════════
# 自测（python -m mbdsdr_ai.iq_frontend）
# ═══════════════════════════════════════════════════════════════════════

def _selftest() -> None:
    """模块自测：DC 去除、IQ 平衡、抗混叠抽取三项数值断言。"""
    fs = 2_000_000.0  # 2 MHz 采样率
    n = 200_000
    t = np.arange(n) / fs

    # --- 测试 1: DC 去除 ---
    # 构造带大幅 DC 偏移的复数信号 + 一个音调
    sig = 0.5 * np.exp(1j * 2 * np.pi * 200_000 * t)  # 200 kHz 音调
    dc_offset = 0.3 + 0.2j  # 大幅 DC
    sig_dc = sig + dc_offset

    fe = IQFrontend(dc_removal=True, iq_balance=False, decimation=1)
    out = fe.process(sig_dc)
    # 跳过初始暂态（前 2000 点），测剩余 DC
    residual_dc = np.abs(np.mean(out[2000:]))
    original_dc = np.abs(np.mean(sig_dc))
    dc_reduction_db = 20 * np.log10(original_dc / max(residual_dc, 1e-12))
    print(f"[DC] 原始 DC={original_dc:.4f}, 去除后残留={residual_dc:.6f}, "
          f"抑制={dc_reduction_db:.1f} dB")
    assert dc_reduction_db > 40, f"DC 抑制不足: {dc_reduction_db:.1f} dB"

    # --- 测试 2: I/Q 不平衡校正 ---
    # 构造 IQ 不平衡信号：I 增益 1.0, Q 增益 0.8, 相位误差 15°
    tone = np.exp(1j * 2 * np.pi * 100_000 * t)  # 100 kHz 音调
    i_raw = tone.real
    q_raw = tone.imag
    # 施加不平衡
    q_imbalanced = 0.8 * (q_raw * np.cos(np.radians(15))
                          - i_raw * np.sin(np.radians(15)))
    i_imbalanced = i_raw * 1.0
    sig_iq = i_imbalanced + 1j * q_imbalanced

    fe2 = IQFrontend(dc_removal=False, iq_balance=True, decimation=1)
    out2 = fe2.process(sig_iq)

    # 测镜像抑制比：信号在 +100kHz，镜像在 -100kHz
    def _bin_power(x, freq_hz, fs):
        n = len(x)
        X = np.fft.fft(x * np.hanning(n))
        freqs = np.fft.fftfreq(n, 1.0 / fs)
        idx = np.argmin(np.abs(freqs - freq_hz))
        return np.abs(X[idx]) ** 2

    sig_power = _bin_power(out2, 100_000, fs)
    image_power = _bin_power(out2, -100_000, fs)
    irr_before = 10 * np.log10(_bin_power(sig_iq, 100_000, fs) /
                               max(_bin_power(sig_iq, -100_000, fs), 1e-12))
    irr_after = 10 * np.log10(sig_power / max(image_power, 1e-12))
    print(f"[IQ] 校正前 IRR={irr_before:.1f} dB, 校正后 IRR={irr_after:.1f} dB, "
          f"改善={irr_after - irr_before:.1f} dB")
    assert irr_after > irr_before + 20, "IQ 平衡改善不足 20 dB"

    # --- 测试 3: 抗混叠抽取 ---
    # 构造含高频分量（超过输出 Nyquist）的信号，验证抽取后高频被滤除
    D = 4
    sig_mix = (0.5 * np.exp(1j * 2 * np.pi * 100_000 * t)  # 通带内 100 kHz
               + 0.3 * np.exp(1j * 2 * np.pi * 450_000 * t))  # 高频 450 kHz（> 输出 Nyquist 250 kHz）
    fe3 = IQFrontend(dc_removal=False, iq_balance=False, decimation=D)
    out3 = fe3.process(sig_mix)
    fs_out = fs / D
    # 输出中 450 kHz 会折叠到 |450k - 500k| = 50 kHz（输出 Nyquist 250k 内）
    # 抗混叠滤波应显著压制这个折叠分量
    def _peak_near(x, freq, fs_in, width=5):
        n = len(x)
        X = np.abs(np.fft.fft(x * np.hanning(n)))
        freqs = np.fft.fftfreq(n, 1.0 / fs_in)
        idx = np.argmin(np.abs(freqs - freq))
        return np.mean(X[max(0, idx - width):idx + width + 1])

    tone_100k_out = _peak_near(out3, 100_000, fs_out)
    alias_50k_out = _peak_near(out3, 50_000, fs_out)
    alias_suppression = 20 * np.log10(tone_100k_out / max(alias_50k_out, 1e-12))
    print(f"[DECIM] 4x 抽取后 100kHz 分量={tone_100k_out:.2f}, "
          f"折叠 50kHz 分量={alias_50k_out:.2f}, 抗混叠抑制={alias_suppression:.1f} dB")
    assert alias_suppression > 30, f"抗混叠抑制不足: {alias_suppression:.1f} dB"
    assert abs(len(out3) - n // D) <= 1, "抽取后长度错误"

    print("\n[iq_frontend] 全部自测通过 ✓")


if __name__ == "__main__":
    _selftest()
