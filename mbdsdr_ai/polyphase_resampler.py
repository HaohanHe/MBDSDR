"""
MBDSDR AI 内核 - 多相有理重采样器（Polyphase Rational Resampler）
=================================================================

逐行对照 SDR++：

  - multirate/rational_resampler.h:120-165  reconfigure()
      · :136-138  gcd 化简：interp = OutSR/gcd, decim = IntSR/gcd
      · :154      tapSamplerate = intSR * interp
      · :155      tapBandwidth  = min(inSR, outSR)/2
      · :156      tapTransWidth = tapBandwidth * 0.1
      · :158      rtaps = lowPass(tapBandwidth, tapTransWidth, tapSamplerate)
      · :159      rtaps *= interp（补偿插值的零值插补增益）
  - multirate/polyphase_bank.h:15-48       buildPolyphaseBank
      · :23       tapsPerPhase = ceil(N / M)
      · :32       phases[(M-1)-(i%M)][i//M] = rtaps[i]
  - multirate/polyphase_resampler.h:69-99  process()
      · :78/:81   out = dot(buffer[offset], phases[phase], tapsPerPhase)
      · :85       phase += decim
      · :88       offset += phase // interp
      · :91       phase %= interp

为什么必须多相（而不是 scipy.signal.resample 的 FFT 重采样）：
  - 多相结构把原型低通分解成 interp 个子滤波器，每输出点只做 tapsPerPhase
    次乘加（不是 interp*taps），且天然带抗混叠：原型低通截止 = min(in,out)/2，
    上镜频与下镜像都被原型 FIR 滤掉。
  - 直接 FFT 重采样（无抗混叠）会让 > min(in,out)/2 的分量折叠，这是本任务
    红线要杜绝的假"重采样"。

流式：跨块保留历史 (tapsPerPhase-1) 个样本与 (phase, offset)，逐块连续。

License: GPL-3.0-or-later
"""

from __future__ import annotations

from math import gcd
import numpy as np

__all__ = ["PolyphaseResampler"]


def build_polyphase_bank(proto_taps: np.ndarray, num_phases: int):
    """把原型低通抽头分解成 num_phases 个多相子滤波器。

    对照 polyphase_bank.h:15-48。返回 (phases, taps_per_phase)，
    phases[p] 是第 p 相的实数抽头数组（长度 taps_per_phase，末尾补零）。
    """
    proto_taps = np.asarray(proto_taps, dtype=np.float64)
    M = int(num_phases)
    N = proto_taps.size
    taps_per_phase = (N + M - 1) // M
    phases = np.zeros((M, taps_per_phase), dtype=np.float64)
    # polyphase_bank.h:32: phases[(M-1)-(i%M)][i//M] = taps[i]
    for i in range(N):
        phase = (M - 1) - (i % M)
        row = i // M
        phases[phase, row] = proto_taps[i]
    return phases, taps_per_phase


class PolyphaseResampler:
    """有理采样率变换：in_sr -> out_sr（先抗混叠 FIR，再插值/抽取）。

    对照 rational_resampler.h:27-43 init + :120-165 reconfigure。

    Parameters:
        in_sr_hz: 输入采样率。
        out_sr_hz: 目标采样率。
    """

    def __init__(self, in_sr_hz: float, out_sr_hz: float) -> None:
        self._in_sr = float(in_sr_hz)
        self._out_sr = float(out_sr_hz)
        self._rebuild()

    def _rebuild(self) -> None:
        from .fir_taps import lowpass_taps
        in_sr = self._in_sr
        out_sr = self._out_sr

        # rational_resampler.h:134-138 gcd 化简
        int_sr = int(round(in_sr))
        out_int = int(round(out_sr))
        g = gcd(int_sr, out_int)
        self._interp = out_int // g
        self._decim = int_sr // g

        # rational_resampler.h:154-159 原型低通
        tap_sr = in_sr * self._interp
        tap_bw = min(in_sr, out_sr) / 2.0          # :155
        trans = tap_bw * 0.1                        # :156
        proto = lowpass_taps(tap_bw, trans, tap_sr, odd=False)
        proto = proto * self._interp               # :159 补偿插值增益

        self._phases, self._tpp = build_polyphase_bank(proto, self._interp)

        # 流式状态：历史缓冲 (tpp-1) 样本 + (phase, offset)
        self._history = np.zeros(self._tpp - 1, dtype=np.float64)
        self._phase = 0
        self._offset = 0

    @property
    def ratio(self) -> float:
        return self._out_sr / self._in_sr

    @property
    def interp_decim(self):
        return self._interp, self._decim

    def reset(self) -> None:
        """清空历史与相位（换源/换频时调用）。"""
        self._history = np.zeros_like(self._history)
        self._phase = 0
        self._offset = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段样本，返回重采样后样本。

        对照 polyphase_resampler.h:69-99。
        """
        x = np.asarray(x)
        if x.size == 0:
            return x
        if self._interp == 1 and self._decim == 1:
            return x

        complex_in = np.iscomplexobj(x)
        if complex_in:
            full = np.concatenate((self._history + 0j, x))
        else:
            full = np.concatenate((self._history, x.astype(np.float64)))

        count = x.shape[0]
        M = self._interp
        D = self._decim
        tpp = self._tpp

        out_list = []
        off = self._offset
        ph = self._phase
        while off < count:
            taps = self._phases[ph]
            win = full[off:off + tpp]
            if complex_in:
                y = np.dot(win.real, taps) + 1j * np.dot(win.imag, taps)
            else:
                y = np.dot(win, taps)
            out_list.append(y)
            ph += D                 # :85
            off += ph // M          # :88
            ph = ph % M             # :91
        self._offset = off - count  # :93
        self._phase = ph

        # 保存历史（polyphase_resampler.h:96 memmove）
        self._history = full[count:count + tpp - 1].copy() if tpp > 1 \
            else np.array([], dtype=np.float64)

        if not out_list:
            return np.empty(0, dtype=x.dtype)
        out = np.asarray(out_list)
        return out.astype(x.dtype)
