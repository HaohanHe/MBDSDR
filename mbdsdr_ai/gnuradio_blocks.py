"""
MBDSDR AI 内核 — GNU Radio 真实 DSP 块移植
=============================================

本模块逐行对照 GNU Radio (repos/gnuradio) 的 C++ 内核实现移植，
每个类的关键常量/公式都标注来源 ``gr-xxx/lib/xxx.cc:行号``。

覆盖块：
  - FIRFilter          gr-filter/lib/fir_filter.cc / fir_filter_with_buffer.cc
  - FFTFilter          gr-filter/lib/fft_filter.cc        (overlap-add 快速卷积)
  - IIRFilter          gr-filter/lib/iir_filter.cc
  - PFBArbResampler    gr-filter/lib/pfb_arb_resampler.cc (多相任意重采样)
  - AGC2               gr-analog/include/.../agc2.h       (attack/release)
  - RationalResampler  gr-filter/lib/rational_resampler_impl.cc (插值/抽取)
  - ClockRecoveryMM    gr-digital/lib/clock_recovery_mm_ff_impl.cc (Mueller-Mueller)

设计原则：
  - 与 GNU Radio 数值等价（FIR 输出 == numpy.convolve(x, taps)）。
  - 流式状态保留（buffer/tail/history），可逐块 process。
  - 仅依赖 numpy（scipy 可选加速）。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "FIRFilter",
    "FFTFilter",
    "IIRFilter",
    "PFBArbResampler",
    "AGC2",
    "RationalResampler",
    "ClockRecoveryMM",
]


# ═══════════════════════════════════════════════════════════
# 1. FIR 滤波器
#    来源: gr-filter/lib/fir_filter.cc
#          gr-filter/lib/fir_filter_with_buffer.cc
# ═══════════════════════════════════════════════════════════
class FIRFilter:
    """直接型 FIR 滤波器（实数/复数输入，实数抽头）。

    GNU Radio 内核 ``kernel::fir_filter``：
      - set_taps() 内部把用户抽头**反转**
        (fir_filter.cc:34  ``std::reverse(d_taps.begin(), d_taps.end());``)
      - filter() 对输入滑动窗与“反转抽头”做点积
        (fir_filter.cc:91-114，volk_*_dot_prod)
      - 数学上等价于线性卷积 y[n] = sum_k taps[k] * x[n-k]，
        即 numpy.convolve(x, taps, mode='full')。
    """

    def __init__(self, taps):
        # 来源: fir_filter.cc:32  d_ntaps = taps.size()
        self.taps = np.asarray(taps, dtype=np.float64)
        self.ntaps = len(self.taps)
        # 流式环形历史（对应 fir_filter_with_buffer.cc:48 的双份缓冲）
        self._hist = np.zeros(self.ntaps, dtype=np.complex128)

    # ── 批处理（与 np.convolve 完全一致）──────────────────
    def filter(self, x: np.ndarray) -> np.ndarray:
        """整段滤波，输出长度 = len(x)+ntaps-1（full 卷积）。"""
        x = np.asarray(x)
        return np.convolve(x, self.taps)

    # ── 流式单样本/逐块（带历史）─────────────────────────
    def process(self, x: np.ndarray) -> np.ndarray:
        """逐块流式滤波，输出与输入等长（因果，历史状态保留）。

        对应 fir_filter_with_buffer.cc:69-84 的环形缓冲写法：
        新样本写入环形缓冲，再与反转抽头做点积。
        """
        x = np.atleast_1d(np.asarray(x))
        out = np.empty(len(x), dtype=np.complex128)
        # 把历史拼到前面，做因果卷积后截掉历史段
        full = np.convolve(np.concatenate([self._hist, x]), self.taps)
        # full 长度 = (hist+ntaps-1)+(x+ntaps-1)... 取与 x 对齐的因果输出
        n = len(x)
        out = full[self.ntaps - 1: self.ntaps - 1 + n]
        # 更新历史 = 最后 ntaps-1 个输入样本
        combined = np.concatenate([self._hist, x])
        self._hist = combined[-(self.ntaps - 1):] if self.ntaps > 1 else np.zeros(0)
        if np.iscomplexobj(x) or x.dtype == object:
            return out
        return out.real if np.allclose(out.imag, 0) else out

    def reset(self):
        self._hist = np.zeros(self.ntaps, dtype=np.complex128)


# ═══════════════════════════════════════════════════════════
# 2. FFT 快速卷积滤波器（overlap-add）
#    来源: gr-filter/lib/fft_filter.cc
# ═══════════════════════════════════════════════════════════
class FFTFilter:
    """FFT 快速卷积滤波（overlap-add），长抽头时远快于直接 FIR。

    严格对照 fft_filter.cc：
      - fftsize = 2 * 2^ceil(log2(ntaps))
        (fft_filter.cc:76 / :207 / :338
         ``d_fftsize = (int)(2*pow(2.0, ceil(log(double(ntaps))/log(2.0))));``)
      - nsamples = fftsize - ntaps + 1
        (fft_filter.cc:77 / :208)
      - tailsize = ntaps - 1
        (fft_filter.h:72  ``int tailsize() const { return d_ntaps - 1; }``)
      - 抽头先乘 scale=1/fftsize 再做正向 FFT（吸收归一化）
        (fft_filter.cc:52 / :183 / :314  ``float scale = 1.0/d_fftsize;``)
      - 每帧：nsamples 新样本 + 末尾补零 → FFT → 频域乘 → 逆 FFT →
        前 tailsize 加上一帧 tail → 输出前 nsamples → 保存末 tailsize 为新 tail
        (fft_filter.cc:115-149)
    """

    def __init__(self, taps):
        self.taps = np.asarray(taps)
        self.ntaps = len(self.taps)
        if self.ntaps < 1:
            raise ValueError("FFTFilter: need >=1 tap")
        # fft_filter.cc:76  fftsize = 2 * 2^ceil(log2(ntaps))
        self.fftsize = int(2 * 2 ** np.ceil(np.log2(self.ntaps)))
        # fft_filter.cc:77  nsamples = fftsize - ntaps + 1
        self.nsamples = self.fftsize - self.ntaps + 1
        # fft_filter.h:72  tailsize = ntaps - 1
        self.tailsize = self.ntaps - 1
        # 频域抽头：抽头 * (1/fftsize) 后做 FFT（fft_filter.cc:56-66）
        scale = 1.0 / self.fftsize
        self._H = np.fft.fft(self.taps * scale, self.fftsize)
        # fft_filter.cc:45-47  d_tail 清零
        self._tail = np.zeros(self.tailsize, dtype=np.complex128) if self.tailsize else np.zeros(0)

    def filter(self, x: np.ndarray) -> np.ndarray:
        """整段重叠相加滤波，输出 == FIRFilter.filter(x)（数值误差量级 1e-12）。"""
        x = np.asarray(x)
        complex_mode = np.iscomplexobj(x) or np.iscomplexobj(self.taps)
        dt = np.complex128
        n0 = len(x)
        out_parts = []
        tail = np.zeros(self.tailsize, dtype=dt) if self.tailsize else np.zeros(0, dtype=dt)
        ns = self.nsamples
        # 末尾补零到 nsamples 整数倍，保证最后一块也是满块（标准 overlap-add）
        xpad = np.zeros(int(np.ceil(n0 / ns)) * ns, dtype=dt)
        xpad[:n0] = x
        for i in range(0, len(xpad), ns):
            frame = np.zeros(self.fftsize, dtype=dt)
            chunk = xpad[i:i + ns]
            frame[:ns] = chunk
            # fft_filter.cc:122-129  FFT → 频域乘 → 逆 FFT
            # numpy ifft 自带 1/N 归一化，FFTW_BACKWARD 不归一化，故 *fftsize 还原
            yb = np.fft.ifft(np.fft.fft(frame) * self._H) * self.fftsize
            # fft_filter.cc:132-133  前 tailsize 叠加 carry tail
            yb[:self.tailsize] += tail
            out_parts.append(yb[:ns].copy())
            # fft_filter.cc:144-148  保存末 tailsize 为新 tail
            if self.tailsize:
                tail = yb[ns:ns + self.tailsize].copy()
        # 整段一次性滤波时，末尾 carry tail 即线性卷积的最后 tailsize 个样本
        out_parts.append(tail.copy())
        y = np.concatenate(out_parts)
        # 裁剪到完整线性卷积长度 n0+ntaps-1
        y = y[:n0 + self.ntaps - 1]
        return y if complex_mode else y.real


# ═══════════════════════════════════════════════════════════
# 3. IIR 滤波器（前馈/反馈系数）
#    来源: gr-filter/lib/iir_filter.cc
# ═══════════════════════════════════════════════════════════
class IIRFilter:
    """直接 II 型 IIR：y[n] = b0 x[n] + sum_{i>=1} b[i] x[n-i]
                             + sum_{i>=1} a[i] y[n-i]。

    严格对照 iir_filter.cc:32-42：
        acc = d_fftaps[0] * input;
        for i=1..n-1: acc += d_fftaps[i] * prev_input[latest_n+i];
        for i=1..m-1: acc += d_fbtaps[i] * prev_output[latest_m+i];
    注意：反馈抽头 fbtaps[0] 不参与（隐含 a[0]=1），且反馈项是**加号**累加，
    即用户传入的 fbtaps[i] 对应 -(scipy a[i])。
    """

    def __init__(self, fftaps, fbtaps):
        self.fftaps = np.asarray(fftaps, dtype=np.float64)
        self.fbtaps = np.asarray(fbtaps, dtype=np.float64)
        self.n = len(self.fftaps)
        self.m = len(self.fbtaps)
        # iir_filter.cc:39-42  prev_input/prev_output 双份环形缓冲
        self._xhist = np.zeros(self.n, dtype=np.complex128)
        self._yhist = np.zeros(self.m, dtype=np.complex128)

    def process(self, x: np.ndarray) -> np.ndarray:
        """逐样本递推（与 iir_filter.cc 的 sample 循环一致）。"""
        x = np.atleast_1d(np.asarray(x))
        out = np.empty(len(x), dtype=np.complex128)
        xh = self._xhist.copy()
        yh = self._yhist.copy()
        ln = self.n - 1  # latest_n：最新样本在历史中的位置（写前）
        lm = self.m - 1
        for t, xt in enumerate(x):
            # iir_filter.cc:32-36
            acc = self.fftaps[0] * complex(xt)
            for i in range(1, self.n):
                acc += self.fftaps[i] * xh[(ln + i) % self.n]
            for i in range(1, self.m):
                acc += self.fbtaps[i] * yh[(lm + i) % self.m]
            # iir_filter.cc:39-42  双份写入避免回绕
            yh[lm] = acc
            xh[ln] = complex(xt)
            ln = (ln - 1) % self.n
            lm = (lm - 1) % self.m
            out[t] = acc
        self._xhist = xh
        self._yhist = yh
        return out

    def reset(self):
        self._xhist = np.zeros(self.n, dtype=np.complex128)
        self._yhist = np.zeros(self.m, dtype=np.complex128)


# ═══════════════════════════════════════════════════════════
# 4. 多相任意重采样器 PFB arbitrary resampler
#    来源: gr-filter/lib/pfb_arb_resampler.cc
# ═══════════════════════════════════════════════════════════
class PFBArbResampler:
    """多相滤波器组任意重采样（rate = f_out / f_in）。

    对照 pfb_arb_resampler.cc：
      - int_rate = filter_size（分支数/内插率）        (:41)
      - set_rate: dec_rate = floor(int_rate/rate);      (:150-151)
                  flt_rate = int_rate/rate - dec_rate
      - 多相分解：branch[i][j] = tmp[i + j*int_rate]    (:100)
        taps_per_filter = ceil(ntaps/int_rate)          (:83)
      - 微分抽头：dtaps[i]=taps[i+1]-taps[i], 末位 0     (:112-123)
        （导数滤波器 [-1, +1]）
      - 输出循环 (:189-206)：
          o0 = fir[j](&input[i_in]);  o1 = difffir[j](&input[i_in])
          out = o0 + o1 * d_acc
          d_acc += flt_rate;  j += dec_rate + floor(d_acc);  d_acc %= 1
    """

    def __init__(self, rate: float, taps, filter_size: int = 32):
        # pfb_arb_resampler.cc:30  d_acc = 0
        self.acc = 0.0
        # :41  d_int_rate = filter_size
        self.int_rate = int(filter_size)
        self.taps = np.asarray(taps, dtype=np.float64)
        # :148-152  set_rate
        self.rate = float(rate)
        self.dec_rate = int(np.floor(self.int_rate / self.rate))
        self.flt_rate = self.int_rate / self.rate - self.dec_rate
        # :44  d_last_filter = (taps.size()/2) % filter_size
        self.last_filter = (len(self.taps) // 2) % self.int_rate
        self._build_polyphase()
        # 输入历史：分支 FIR 需要向前看 L 个样本（kernel::fir_filter 无状态前向点积）
        self._hist = np.zeros(self.taps_per_filter, dtype=np.complex128)

    def _build_polyphase(self):
        # :83  taps_per_filter = ceil(ntaps/int_rate)
        self.taps_per_filter = int(np.ceil(len(self.taps) / self.int_rate))
        # :90-94  补零到 int_rate*taps_per_filter
        tmp = np.concatenate([self.taps,
                              np.zeros(self.int_rate * self.taps_per_filter - len(self.taps))])
        # :96-101  ourtaps[i][j] = tmp[i + j*int_rate]
        self.branches = np.zeros((self.int_rate, self.taps_per_filter))
        for i in range(self.int_rate):
            for j in range(self.taps_per_filter):
                self.branches[i, j] = tmp[i + j * self.int_rate]
        # :108-124  微分抽头 [-1,+1]
        d = np.diff(self.taps)
        d = np.concatenate([d, [0.0]])
        dtmp = np.concatenate([d, np.zeros(self.int_rate * self.taps_per_filter - len(d))])
        self.dbranches = np.zeros((self.int_rate, self.taps_per_filter))
        for i in range(self.int_rate):
            for j in range(self.taps_per_filter):
                self.dbranches[i, j] = dtmp[i + j * self.int_rate]

    def _fir_dot(self, branch: np.ndarray, win: np.ndarray) -> complex:
        # kernel::fir_filter: set_taps 反转抽头后与前向输入窗点积
        # fir_filter.cc:34 反转 → dot(win, branch[::-1])
        return complex(np.dot(branch[::-1], win))

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.complex128)
        # 拼接历史 + 新样本，分支滤波从“当前输入指针”前向取窗
        buf = np.concatenate([self._hist, x])
        out = []
        j = self.last_filter
        acc = self.acc
        L = self.taps_per_filter
        i_in = 0
        n_total = len(x)  # 本块新样本数
        while i_in < n_total:
            while j < self.int_rate:
                # win 相对 buf 起点：历史占 L，新样本从 index L 开始
                base = L + i_in
                win = buf[base:base + L]
                if len(win) < L:
                    break
                o0 = self._fir_dot(self.branches[j], win)
                o1 = self._fir_dot(self.dbranches[j], win)
                # :196  out = o0 + o1*acc （线性插值）
                out.append(o0 + o1 * acc)
                # :200-202
                acc += self.flt_rate
                j += self.dec_rate + int(np.floor(acc))
                acc = acc - np.floor(acc)
            if len(win) < L:
                break
            consumed = j // self.int_rate  # :204
            i_in += consumed
            j = j % self.int_rate          # :205
        # 保存跨块状态
        self.last_filter = j
        self.acc = acc
        # 历史 = 本块最后 L-1 个样本（供下块前向窗）
        keep = max(0, L - 1)
        self._hist = x[-keep:] if keep else np.zeros(0, dtype=np.complex128)
        return np.asarray(out)


# ═══════════════════════════════════════════════════════════
# 5. AGC2（attack / decay 自动增益）
#    来源: gr-analog/include/gnuradio/analog/agc2.h:64-85
# ═══════════════════════════════════════════════════════════
class AGC2:
    """复数 AGC2：输出幅度收敛到 reference。

    逐样本算法（agc2.h:64-85，kernel::agc2_cc::scale）：
        output = input * gain
        tmp    = |output| - reference
        rate   = decay_rate;  if tmp > gain: rate = attack_rate
        gain  -= tmp * rate
        if gain < 0: gain = 10e-5           (agc2.h:78-79)
        if max_gain>0 and gain>max_gain: gain = max_gain

    默认参数（agc2.h:41-45）：
        attack_rate=1e-1, decay_rate=1e-2, reference=1.0, gain=1.0, max_gain=0(无限)
    """

    def __init__(self, attack_rate=1e-1, decay_rate=1e-2, reference=1.0,
                 gain=1.0, max_gain=0.0):
        # agc2.h:46-50 构造初始化
        self.attack_rate = float(attack_rate)
        self.decay_rate = float(decay_rate)
        self.reference = float(reference)
        self.gain = float(gain)
        self.max_gain = float(max_gain)

    def scale(self, inp: complex) -> complex:
        # agc2.h:66
        out = inp * self.gain
        # agc2.h:68-69  tmp = |out| - reference
        tmp = -self.reference + np.sqrt(out.real ** 2 + out.imag ** 2)
        # agc2.h:70-73  触发 attack
        rate = self.decay_rate
        if tmp > self.gain:
            rate = self.attack_rate
        # agc2.h:74
        self.gain -= tmp * rate
        # agc2.h:78-79  gain 下限
        if self.gain < 0.0:
            self.gain = 10e-5
        # agc2.h:81-83  gain 上限
        if self.max_gain > 0.0 and self.gain > self.max_gain:
            self.gain = self.max_gain
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        # agc2.h:87-91  scaleN：逐样本 scale
        x = np.asarray(x, dtype=np.complex128)
        out = np.empty(len(x), dtype=np.complex128)
        for i, v in enumerate(x):
            out[i] = self.scale(complex(v))
        return out

    def reset(self, gain=1.0):
        self.gain = float(gain)


# ═══════════════════════════════════════════════════════════
# 6. 有理重采样器 Rational Resampler（插值/抽取）
#    来源: gr-filter/lib/rational_resampler_impl.cc
# ═══════════════════════════════════════════════════════════
class RationalResampler:
    """有理倍率重采样：f_out/f_in = interpolation/decimation。

    对照 rational_resampler_impl.cc：
      - 自动设计抽头时（:43-74 design_resampler_filter）：
          Kaiser 窗，beta=7.0 (:55)，fractional_bw 默认 0.4 (:124/:142)
          gain = interpolation (:68)
      - 多相分解 (:197-201)：xtaps[i%nfilters][i/nfilters] = taps[i]
      - general_work (:248-256)：
          out = firs[ctr].filter(in); ctr += decimation;
          while ctr>=interpolation: ctr-=interpolation; in++
    """

    def __init__(self, interpolation: int, decimation: int, taps=None,
                 fractional_bw: float = 0.4):
        if interpolation < 1 or decimation < 1:
            raise ValueError("interpolation/decimation must be >= 1")
        self.interp = int(interpolation)
        self.decim = int(decimation)
        g = np.gcd(self.interp, self.decim)
        # :147-148  无用户抽头时按 GCD 约简
        self._auto_taps = taps is None
        if taps is None:
            i = self.interp // g
            d = self.decim // g
            taps = self._design_taps(i, d, fractional_bw)
        self.taps = np.asarray(taps, dtype=np.float64)
        self._ctr = 0

    @staticmethod
    def _design_taps(interp: int, decim: int, fractional_bw: float):
        # rational_resampler_impl.cc:55-73  design_resampler_filter
        try:
            from scipy.signal import kaiserord, firwin
        except Exception:
            # 退化：线性插值抽头
            return np.ones(interp * 8) / interp
        beta = 7.0                       # :55
        halfband = 0.5
        rate = interp / decim
        if rate >= 1.0:
            trans_width = halfband - fractional_bw       # :61
            mid = halfband - trans_width / 2.0           # :62
        else:
            trans_width = rate * (halfband - fractional_bw)  # :64
            mid = rate * halfband - trans_width / 2.0         # :65
        # :68  gain=interp, Fs=interp
        numtaps = max(33, int(4.0 / max(trans_width, 1e-3) * interp) | 1)
        taps = firwin(numtaps, mid, width=trans_width,
                      window=('kaiser', beta)) * interp   # :68 gain=interp
        return taps

    def process(self, x: np.ndarray) -> np.ndarray:
        """整段有理重采样（多相 polyphase 结构，与 scipy.resample_poly 等价）。"""
        try:
            from scipy.signal import resample_poly
        except Exception:
            # 无 scipy：上采样=插零后卷积，下采样=抽取
            up = np.zeros(len(x) * self.interp, dtype=np.asarray(x).dtype)
            up[::self.interp] = x
            y = np.convolve(up, self.taps)
            return y[::self.decim]
        # rational_resampler 的等效频域/多相实现
        return resample_poly(np.asarray(x), self.interp, self.decim,
                             window=self.taps)


# ═══════════════════════════════════════════════════════════
# 7. Mueller-Mueller 位同步 Clock Recovery
#    来源: gr-digital/lib/clock_recovery_mm_ff_impl.cc
#    切片: gr-digital/lib/binary_slicer_fb_impl.cc
# ═══════════════════════════════════════════════════════════
def _slice(v: float) -> float:
    """二进制切片：volk_32f_binary_slicer_8i → v>=0 ? +1 : -1。
    来源: binary_slicer_fb_impl.cc work()。"""
    return 1.0 if v >= 0.0 else -1.0


class ClockRecoveryMM:
    """Mueller-Mueller 时钟恢复（实数眼图采样）。

    严格对照 clock_recovery_mm_ff_impl.cc:82-94 general_work：
        out  = interp(&in[ii], mu)
        mm   = slice(last)*out - slice(out)*last        (:85)
        last = out
        omega += gain_omega * mm                        (:88)
        omega = omega_mid + clip(omega-omega_mid, omega_lim)  (:89)
        mu   += omega + gain_mu * mm                    (:90)
        ii   += floor(mu);  mu -= floor(mu)             (:92-93)

    说明：GNU Radio 用 mmse_fir_interpolator（NTAPS=8,NSTEPS=32，
    gr-filter/lib/interpolator_taps.h）做分数延迟插值；此处用线性插值近似，
    环路更新方程与增益标定完全一致。
    """

    def __init__(self, omega: float, gain_omega: float, mu: float,
                 gain_mu: float, omega_relative_limit: float = 0.005):
        # clock_recovery_mm_ff_impl.cc:35-39
        if omega < 1:
            raise ValueError("clock rate omega must be >= 1")
        self.omega = float(omega)
        self.omega_mid = float(omega)             # :65
        self.omega_lim = self.omega_mid * omega_relative_limit  # :66
        self.mu = float(mu)
        self.gain_omega = float(gain_omega)
        self.gain_mu = float(gain_mu)
        self.last_sample = 0.0                    # :39

    @staticmethod
    def _interp(x: np.ndarray, mu: float) -> float:
        """分数延迟线性插值（GR 为 8-tap MMSE，此处线性近似）。"""
        i0 = int(np.floor(mu))
        frac = mu - i0
        if i0 + 1 < len(x):
            return (1 - frac) * x[i0] + frac * x[i0 + 1]
        return x[min(i0, len(x) - 1)]

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        out = []
        ii = 0
        # :79  ni = len - ntaps（这里线性插值只需 1 个前瞻）
        ni = len(x) - 2
        while ii < ni:
            o = self._interp(x[ii:], self.mu)
            # :85  Mueller-Muller 误差检测器
            mm = _slice(self.last_sample) * o - _slice(o) * self.last_sample
            self.last_sample = o
            # :88-89  环路滤波（omega 支路）+ 限幅
            self.omega += self.gain_omega * mm
            self.omega = self.omega_mid + np.clip(self.omega - self.omega_mid,
                                                  -self.omega_lim, self.omega_lim)
            # :90  二阶 mu 更新
            self.mu += self.omega + self.gain_mu * mm
            # :92-93  步进
            step = int(np.floor(self.mu))
            ii += step
            self.mu -= step
            out.append(o)
        return np.asarray(out)

    def reset(self):
        self.omega = self.omega_mid
        self.mu = 0.5
        self.last_sample = 0.0
