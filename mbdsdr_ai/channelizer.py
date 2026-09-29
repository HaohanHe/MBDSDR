# SPDX-License-Identifier: MIT
"""
channelizer.py — Xlating FIR 数字下变频信道化（纯 numpy，状态化流式）。

依据公开数字下变频（DDC）DSP 方法独立实现（SDR++ 仅作技术参考，本仓未包含其源代码）：
  * NCO 相位累加复指数混频
  * 混频 → 重采样抽取 → FIR 低通
  * windowed-sinc + Nuttall 窗低通抽头设计

管线（NCO → 多级整数抽取 → 有理重采样 → 信道 LPF）：
    1) NCO 混频：  y[n] = x[n] * e^{j φ[n]},  φ[n] = φ[n-1] + Δφ
                    Δφ = 2π·(-offset)/in_sr
    2) 多级整数抽取：每级 D≤8，用 DecimatingFIR 抗混叠，把 in_sr 降到 mid_sr
    3) 有理重采样：mid_sr → out_sr（scipy.signal.resample_poly）
    4) FIR 低通：  截止 = BW/2，抽头数有上限（max ~70ms 群延迟），
                    保证单块 50ms 输入即可得到非零输出。

修复历史
────────
旧版把信道 LPF 直接放在 out_sr 上，过渡带 = BW/2*0.1。对 CW（BW=200Hz,
out_sr=3kHz）来说 trans=10Hz → n_taps≈1201 → 群延迟≈200ms。单块 50ms 输入
在 resample 后只有 ~150 个样点，远小于滤波器长度，输出仍在零状态上电斜坡，
rms≈3e-6，等于"无声"。

修复策略（两级措施）：
  (a) 先做多级整数抽取（每级 D≤8），把 256kHz 粗抽到 ~32kHz 再有理重采样，
      降低 resample_poly 的抽取比（256→32）与计算量；
  (b) 信道 LPF 的抽头数封顶在 ~0.07·out_sr（群延迟 ≤ 35ms），必要时放宽
      过渡带。对宽带模式（AM/NFM/WFM）自然抽头数本来就小，封顶不生效；
      对窄带模式（CW/SSB）封顶生效，把 1201 抽头降到 ~200，单块即可出声。
"""
from __future__ import annotations
import numpy as np
from scipy.signal import resample_poly
from math import gcd

from .decimating_fir import DecimatingFIR


__all__ = ["XlatingFIR", "lowpass_taps"]


def _nuttall(M: int) -> np.ndarray:
    """Nuttall 窗 —— 对应 core/src/dsp/window/nuttall.h。"""
    n = np.arange(M)
    a0, a1, a2, a3 = 0.355768, 0.487396, 0.144232, 0.012604
    return (a0
            - a1 * np.cos(2.0 * np.pi * n / (M - 1))
            + a2 * np.cos(4.0 * np.pi * n / (M - 1))
            - a3 * np.cos(6.0 * np.pi * n / (M - 1)))


def lowpass_taps(cutoff: float, trans_width: float, sample_rate: float,
                 odd_taps: bool = False) -> np.ndarray:
    """窗函数 sinc 低通抽头 —— 对应 core/src/dsp/taps/low_pass.h:7-11。

    保持与旧版 channelizer.py 完全一致的抽头数公式（4·fs/trans），
    避免群延迟变化破坏已有测试。
    """
    n_taps = int(np.ceil(4.0 * sample_rate / trans_width)) | 1
    if odd_taps and not (n_taps % 2):
        n_taps += 1
    n_taps = max(n_taps, 11)
    n = np.arange(n_taps) - (n_taps - 1) / 2.0
    h = 2.0 * cutoff / sample_rate * np.sinc(2.0 * cutoff / sample_rate * n)
    h *= _nuttall(n_taps)
    h /= h.sum()
    return h.astype(np.float64)


class XlatingFIR:
    """Xlating FIR 信道化（NCO → 多级抽取 → 有理重采样 → 信道 LPF）。

    对应 core/src/dsp/channel/RxVFO。接口与旧版完全兼容：
      * __init__(in_sr, out_sr, bandwidth, offset=0)
      * process(iq) -> complex128 ndarray
      * reset()

    内部为窄带模式增加了多级整数抽取与 LPF 抽头数封顶，但 process/reset
    签名、返回类型、状态语义（跨块连续）都不变。
    """

    #: 信道 LPF 群延迟上限（秒）。单块 50ms 输入在此群延迟下应能覆盖
    #: 滤波器窗函数左半支，保证首块 rms 不为零。
    _MAX_GROUP_DELAY_S = 0.035

    def __init__(self, in_sr: float, out_sr: float, bandwidth: float,
                 offset: float = 0.0):
        self.in_sr = float(in_sr)
        self.out_sr = float(out_sr)
        self.bandwidth = float(bandwidth)
        self.offset = float(offset)

        # NCO 相位增量（frequency_xlator.h:17）
        self._dphi = -2.0 * np.pi * self.offset / self.in_sr
        self._phase = 0.0

        cutoff = self.bandwidth / 2.0

        # ── 先判断是否需要多级抽取 ─────────────────────────────────────
        # 自然抽头数（trans = cutoff*0.1）若已经在 max_taps 以内，说明
        # 滤波器足够短、单块即可出声，不需要多级抽取——保持旧版行为，
        # 避免额外群延迟破坏已有测试。只有窄带模式（CW/SSB）才触发多级。
        natural_trans = cutoff * 0.1
        natural_taps = int(np.ceil(4.0 * self.out_sr / natural_trans)) | 1
        max_taps = int(np.ceil(2.0 * self._MAX_GROUP_DELAY_S * self.out_sr)) | 1
        max_taps = max(max_taps, 31)
        need_multistage = natural_taps > max_taps

        # ── 多级整数抽取：in_sr → mid_sr ──────────────────────────────
        # 每级 D ≤ 8，抗混叠 LPF 截止 = next_sr/2。
        self._decim_stages: list[DecimatingFIR] = []
        cur_sr = self.in_sr
        if need_multistage:
            while cur_sr / self.out_sr > 8.0:
                # 目标下一级速率：至少 8×out_sr，最好是 cur_sr/8
                target_next = max(self.out_sr * 8.0, cur_sr / 8.0)
                D = int(round(cur_sr / target_next))
                D = max(1, min(D, 8))
                if D <= 1:
                    break
                next_sr = cur_sr / D
                # 抗混叠低通：截止 = next_sr/2；过渡带宽一些以减少抽头数。
                aa_cutoff = next_sr / 2.0
                aa_trans = max(aa_cutoff * 0.3, cutoff * 1.5)
                try:
                    taps = lowpass_taps(aa_cutoff, aa_trans, cur_sr)
                except Exception:
                    break
                self._decim_stages.append(DecimatingFIR(taps, D))
                cur_sr = next_sr
        self._mid_sr = cur_sr

        # ── 有理重采样：mid_sr → out_sr ──────────────────────────────
        g = gcd(int(round(self.out_sr)), int(round(self._mid_sr)))
        self._up = int(round(self.out_sr)) // g
        self._down = int(round(self._mid_sr)) // g

        # ── 信道 LPF（在 out_sr 上设计，抽头数封顶）──────────────────
        if need_multistage:
            trans = 4.0 * self.out_sr / max_taps
        else:
            trans = natural_trans
        self._lpf = lowpass_taps(cutoff, trans, self.out_sr)

        # FIR 卷积状态（跨块连续）
        self._z = np.zeros(len(self._lpf) - 1, dtype=np.complex128)

    # ------------------------------------------------------------------
    def reset(self) -> None:
        self._phase = 0.0
        for stage in self._decim_stages:
            stage.reset()
        self._z = np.zeros(len(self._lpf) - 1, dtype=np.complex128)

    # ------------------------------------------------------------------
    def process(self, iq: np.ndarray) -> np.ndarray:
        """处理一段复 IQ，返回信道化后的复基带（out_sr 采样率）。"""
        x = np.asarray(iq, dtype=np.complex128)
        n = len(x)
        if n == 0:
            return np.zeros(0, dtype=np.complex128)

        # 1) NCO 混频（frequency_xlator.h:43-48 相位累加旋转器）
        phases = self._phase + self._dphi * np.arange(n)
        mixed = x * np.exp(1j * phases)
        self._phase = (self._phase + self._dphi * n) % (2.0 * np.pi)

        # 2) 多级整数抽取（状态跨块连续）
        y = mixed
        for stage in self._decim_stages:
            y = stage.process(y)
            if y.size == 0:
                return np.zeros(0, dtype=np.complex128)

        # 3) 有理重采样/抽取（rx_vfo.h:94 resamp.process）
        if self._up == 1 and self._down == 1:
            dec = y
        else:
            dec = resample_poly(y, self._up, self._down)

        # 4) 信道 LPF（rx_vfo.h:97 filter.process），状态跨块连续。
        m = len(self._lpf)
        if m > 1:
            ext = np.concatenate((self._z, dec))
            conv = np.convolve(ext, self._lpf, mode="full")
            out = conv[m - 1: m - 1 + len(dec)]
            self._z = ext[-(m - 1):].copy()
        else:
            out = dec

        return out.astype(np.complex128)
