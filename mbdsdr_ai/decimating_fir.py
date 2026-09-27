"""
MBDSDR AI 内核 - 抗混叠抽取 FIR（Decimating FIR）
===================================================

逐行对照 SDR++ repos/sdrpp/core/src/dsp/filter/：

  - fir.h:62-83               标准 FIR：历史缓冲 (taps-1) 个样本 + 逐点点积
  - decimating_fir.h:45-68    抽取 FIR：在卷积输出上每隔 decimation 取一个
                              （先抗混叠低通，再抽取，正确顺序）

关键正确性点（红线）：
  1. 必须先做 FIR 低通抗混叠，再抽取。绝不能裸 x[::D]——否则 |f|>fs_out/2
     的高频会折叠到基带。
  2. 原型低通截止必须 ≤ 输出 Nyquist = fs_in/(2D)。抽头由 fir_taps.lowpass_taps
     给出（Nuttall 窗，transWidth = 0.1*cutoff，对照 rational_resampler.h:156）。
  3. 流式：跨块保留 (taps-1) 个历史样本与抽取相位 offset，逐块连续。

License: GPL-3.0-or-later
"""

from __future__ import annotations

import numpy as np

__all__ = ["DecimatingFIR", "design_decimation_taps"]


def design_decimation_taps(decimation: int, input_sr_hz: float,
                           trans_width_hz: float | None = None) -> np.ndarray:
    """为整数抽取设计抗混叠低通抽头。

    截止 = 输出 Nyquist = input_sr/(2*decimation)。
    对照 rational_resampler.h:155 tapBandwidth = min(in,out)/2；
    transWidth = tapBandwidth*0.1（rational_resampler.h:156）。
    """
    from .fir_taps import lowpass_taps
    cutoff = input_sr_hz / (2.0 * decimation)
    if trans_width_hz is None:
        trans_width_hz = cutoff * 0.1
    return lowpass_taps(cutoff, trans_width_hz, input_sr_hz, odd=True)


class DecimatingFIR:
    """带状态抗混叠抽取 FIR。

    对照 decimating_fir.h:13-16 init(in, taps, decimation) 与 :45-68 process。

    Parameters:
        taps: 实数 FIR 低通系数（由 design_decimation_taps 生成）。
        decimation: 整数抽取因子 D ≥ 1。
    """

    def __init__(self, taps: np.ndarray, decimation: int = 1) -> None:
        taps = np.asarray(taps, dtype=np.float64)
        if taps.ndim != 1 or taps.size < 1:
            raise ValueError("taps must be a 1-D FIR coefficient array")
        if decimation < 1:
            raise ValueError("decimation must be >= 1")
        self._taps = taps
        self._D = int(decimation)
        # 历史缓冲：上一块末尾 (N-1) 个样本（fir.h:25 bufStart = &buffer[N-1]）
        self._history = np.zeros(taps.size - 1, dtype=np.float64)
        # 抽取相位（decimating_fir.h:86 offset，跨块累加后对 count 取余）
        self._offset = 0

    @property
    def decimation(self) -> int:
        return self._D

    def reset(self) -> None:
        """清空历史与抽取相位（换源/换频时调用）。"""
        self._history = np.zeros_like(self._history)
        self._offset = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段样本，返回抽取后样本（长度 ≈ len(x)/D）。

        对照 decimating_fir.h:45-61：把输入接到历史后，在 offset, offset+D, ...
        处做完整 FIR 卷积点积。
        """
        x = np.asarray(x)
        if x.size == 0:
            return x
        if self._D <= 1:
            return x
        n_taps = self._taps.size

        # 拼上历史（fir.h:64 memcpy(bufStart, in, ...)）
        if np.iscomplexobj(x):
            full = np.concatenate((self._history + 0j, x))
        else:
            full = np.concatenate((self._history, x.astype(np.float64)))

        out_indices = []
        off = self._offset
        count = x.shape[0]
        while off < count:
            out_indices.append(off)
            off += self._D
        self._offset = off - count  # decimating_fir.h:62 offset -= count

        if not out_indices:
            # 本块没有可输出样本，仍要更新历史
            self._history = full[-(n_taps - 1):].copy() if n_taps > 1 else np.array([])
            return np.empty(0, dtype=x.dtype)

        idx = np.asarray(out_indices)
        # FFT 相关（与逐点 FIR 卷积数值等价，复杂度 O(N log N)）：
        # valid[p] = sum_n full[p+n]*taps[n]，长度 = count；再按抽取相位取
        # valid[idx]。省去 np.stack 逐输出切片的巨大开销。
        from scipy.signal import correlate
        valid = correlate(full, self._taps, mode="valid", method="fft")
        y = valid[idx]

        # 保存本块末尾 (n_taps-1) 个样本作下次历史（fir.h:80 memmove）
        self._history = full[count:count + n_taps - 1].copy() if n_taps > 1 \
            else np.array([], dtype=np.float64)
        return y.astype(x.dtype)
