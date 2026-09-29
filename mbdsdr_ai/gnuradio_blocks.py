# SPDX-License-Identifier: MIT
"""
Self-contained DSP primitives for the SDR receive chain.

This module implements a small set of widely-documented discrete-time signal
processing building blocks used by the downstream channel, demodulation and
clock-recovery stages.  The algorithms are standard textbook results in
digital signal processing (see e.g. Oppenheim & Schafer, *Discrete-Time Signal
Processing*, and Lyons, *Understanding Digital Signal Processing*):

  * :class:`FIRFilter`        direct-form finite-impulse-response filter
  * :class:`FFTFilter`       fast convolution via the overlap-add method
  * :class:`IIRFilter`       direct-form-II recursive filter
  * :class:`PFBArbResampler` polyphase filter-bank arbitrary-rate resampler
  * :class:`AGC2`            attack/decay automatic gain control loop
  * :class:`RationalResampler` rational (interpolate-then-decimate) resampler
  * :class:`ClockRecoveryMM`  Mueller & Müller symbol-timing recovery

All blocks keep their own streaming state (history / tail / phase accumulator)
so they can be fed sample-by-sample or in chunks, and only depend on NumPy
( SciPy is used as an optional accelerator where noted).
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
# 1. Direct-form FIR filter
# ═══════════════════════════════════════════════════════════
class FIRFilter:
    """Finite-impulse-response filter for real or complex input.

    The output of a linear-phase FIR filter is the discrete convolution of the
    input with the impulse response ``h``::

        y[n] = sum_k h[k] * x[n - k]

    which is exactly :func:`numpy.convolve` in its ``'full'`` form.  The
    coefficients are supplied by the caller; the filter performs no reversal
    of its own (the convolution definition already accounts for it).
    """

    def __init__(self, taps):
        self.taps = np.asarray(taps, dtype=np.float64)
        self.ntaps = len(self.taps)
        # Streaming history: the last ``ntaps-1`` input samples are kept so a
        # block-wise call produces a causal output aligned to the new data.
        self._hist = np.zeros(self.ntaps, dtype=np.complex128)

    def filter(self, x: np.ndarray) -> np.ndarray:
        """One-shot filtering of a whole block.

        Returns the *full* convolution, i.e. length ``len(x) + ntaps - 1``.
        """
        x = np.asarray(x)
        return np.convolve(x, self.taps)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Causal block-wise filtering, output length == input length.

        The filter state (history) is carried across calls so successive
        blocks chain together as one continuous convolution.
        """
        x = np.atleast_1d(np.asarray(x))
        n = len(x)
        padded = np.concatenate([self._hist, x])
        full = np.convolve(padded, self.taps)
        out = full[self.ntaps - 1: self.ntaps - 1 + n]
        # Retain the trailing input samples for the next call.
        self._hist = padded[-(self.ntaps - 1):] if self.ntaps > 1 else np.zeros(0)
        if np.iscomplexobj(x) or x.dtype == object:
            return out
        return out.real if np.allclose(out.imag, 0) else out

    def reset(self):
        self._hist = np.zeros(self.ntaps, dtype=np.complex128)


# ═══════════════════════════════════════════════════════════
# 2. Fast convolution via overlap-add
# ═══════════════════════════════════════════════════════════
class FFTFilter:
    """Long-tap FIR evaluated by the overlap-add FFT method.

    For a filter with ``N`` taps the direct convolution costs O(n*N); splitting
    the input into short blocks and convolving each block by means of a fast
    Fourier transform reduces the cost to O(n log N).  Each block produces an
    output longer than the block by ``N-1`` samples; the excess tails from
    adjacent blocks overlap and are added back together.

    The FFT size is the smallest power of two that is at least twice the number
    of taps, so that the circular convolution inside one frame does not wrap
    into the next frame's data.
    """

    def __init__(self, taps):
        self.taps = np.asarray(taps)
        self.ntaps = len(self.taps)
        if self.ntaps < 1:
            raise ValueError("FFTFilter: need at least one tap")
        # FFT frame length: smallest power of two >= 2*ntaps.
        self.fftsize = int(2 * 2 ** np.ceil(np.log2(self.ntaps)))
        # New input samples consumed per frame; the rest of the frame is the
        # room needed for the convolution tail.
        self.nsamples = self.fftsize - self.ntaps + 1
        self.tailsize = self.ntaps - 1
        # Pre-transform the impulse response.  The 1/fftsize factor folds in the
        # forward-transform scaling so the per-frame round-trip is exact.
        scale = 1.0 / self.fftsize
        self._H = np.fft.fft(self.taps * scale, self.fftsize)
        self._tail = (np.zeros(self.tailsize, dtype=np.complex128)
                      if self.tailsize else np.zeros(0))

    def filter(self, x: np.ndarray) -> np.ndarray:
        """Filter a whole block; result equals :meth:`FIRFilter.filter`."""
        x = np.asarray(x)
        complex_mode = np.iscomplexobj(x) or np.iscomplexobj(self.taps)
        dt = np.complex128
        n0 = len(x)
        out_parts = []
        tail = np.zeros(self.tailsize, dtype=dt) if self.tailsize else np.zeros(0, dtype=dt)
        ns = self.nsamples
        # Pad the input to an integer number of frames so the last frame is full.
        xpad = np.zeros(int(np.ceil(n0 / ns)) * ns, dtype=dt)
        xpad[:n0] = x
        for i in range(0, len(xpad), ns):
            frame = np.zeros(self.fftsize, dtype=dt)
            frame[:ns] = xpad[i:i + ns]
            # Forward FFT -> multiply in frequency -> inverse FFT.
            yb = np.fft.ifft(np.fft.fft(frame) * self._H) * self.fftsize
            # Overlap the carried-in tail with the start of this frame.
            yb[:self.tailsize] += tail
            out_parts.append(yb[:ns].copy())
            if self.tailsize:
                tail = yb[ns:ns + self.tailsize].copy()
        # The carried-out tail is the final stretch of the full convolution.
        out_parts.append(tail.copy())
        y = np.concatenate(out_parts)
        y = y[:n0 + self.ntaps - 1]
        return y if complex_mode else y.real


# ═══════════════════════════════════════════════════════════
# 3. Direct-form-II IIR filter
# ═══════════════════════════════════════════════════════════
class IIRFilter:
    """Recursive filter with separate feed-forward and feedback coefficients.

    The difference equation is

    ``y[n] = b0*x[n] + sum_{i>=1} b[i]*x[n-i] + sum_{i>=1} c[i]*y[n-i]``

    where ``b`` are the feed-forward taps and ``c`` the *feedback* taps entered
    with a plus sign (so ``c[i] == -a[i]`` when expressed in the standard
    ``(b, a)`` polynomial form).  ``c[0]`` is not supplied; it is implicitly 1.
    """

    def __init__(self, fftaps, fbtaps):
        self.fftaps = np.asarray(fftaps, dtype=np.float64)
        self.fbtaps = np.asarray(fbtaps, dtype=np.float64)
        self.n = len(self.fftaps)
        self.m = len(self.fbtaps)
        self._xhist = np.zeros(self.n, dtype=np.complex128)
        self._yhist = np.zeros(self.m, dtype=np.complex128)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Process a block sample-by-sample, carrying filter state onward."""
        x = np.atleast_1d(np.asarray(x))
        out = np.empty(len(x), dtype=np.complex128)
        xh = self._xhist.copy()
        yh = self._yhist.copy()
        ln = self.n - 1
        lm = self.m - 1
        for t, xt in enumerate(x):
            acc = self.fftaps[0] * complex(xt)
            for i in range(1, self.n):
                acc += self.fftaps[i] * xh[(ln + i) % self.n]
            for i in range(1, self.m):
                acc += self.fbtaps[i] * yh[(lm + i) % self.m]
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
# 4. Polyphase filter-bank arbitrary-rate resampler
# ═══════════════════════════════════════════════════════════
class PFBArbResampler:
    """Arbitrary-rate resampler built from a polyphase filter bank.

    Given a prototype low-pass impulse response, the filter is decomposed into
    ``P = filter_size`` polyphase (sub)filters, one for each phase of the
    upsample grid.  An output sample selects the subfilter that best matches
    the current fractional phase and, between two adjacent phases, blends them
    by linear interpolation.  A phase accumulator steps through the bank at a
    rate of ``P / rate`` subfilters per input sample, realising an output rate
    of ``rate = f_out / f_in``.

    A companion set of *differential* taps (the first difference of the
    prototype) supplies the slope used in the linear phase interpolation.
    """

    def __init__(self, rate: float, taps, filter_size: int = 32):
        self.acc = 0.0
        self.int_rate = int(filter_size)
        self.taps = np.asarray(taps, dtype=np.float64)
        self.rate = float(rate)
        # Integer and fractional subfilter advance per input sample.
        self.dec_rate = int(np.floor(self.int_rate / self.rate))
        self.flt_rate = self.int_rate / self.rate - self.dec_rate
        # Subfilter on which the very first output tap sits.
        self.last_filter = (len(self.taps) // 2) % self.int_rate
        self._build_polyphase()
        self._hist = np.zeros(self.taps_per_filter, dtype=np.complex128)

    def _build_polyphase(self):
        # Number of taps per subfilter, then split the (zero-padded) prototype
        # into the bank: branch[i][j] = taps[i + j*P].
        self.taps_per_filter = int(np.ceil(len(self.taps) / self.int_rate))
        padded = np.concatenate([
            self.taps,
            np.zeros(self.int_rate * self.taps_per_filter - len(self.taps)),
        ])
        self.branches = np.zeros((self.int_rate, self.taps_per_filter))
        for i in range(self.int_rate):
            for j in range(self.taps_per_filter):
                self.branches[i, j] = padded[i + j * self.int_rate]
        # Differential taps for the fractional-phase interpolation.
        d = np.diff(self.taps)
        d = np.concatenate([d, [0.0]])
        dpad = np.concatenate([d, np.zeros(self.int_rate * self.taps_per_filter - len(d))])
        self.dbranches = np.zeros((self.int_rate, self.taps_per_filter))
        for i in range(self.int_rate):
            for j in range(self.taps_per_filter):
                self.dbranches[i, j] = dpad[i + j * self.int_rate]

    @staticmethod
    def _fir_dot(branch: np.ndarray, win: np.ndarray) -> complex:
        # Direct dot product of a subfilter with a forward input window.
        return complex(np.dot(branch[::-1], win))

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.complex128)
        buf = np.concatenate([self._hist, x])
        out = []
        j = self.last_filter
        acc = self.acc
        L = self.taps_per_filter
        i_in = 0
        n_total = len(x)
        while i_in < n_total:
            while j < self.int_rate:
                base = L + i_in
                win = buf[base:base + L]
                if len(win) < L:
                    break
                o0 = self._fir_dot(self.branches[j], win)
                o1 = self._fir_dot(self.dbranches[j], win)
                out.append(o0 + o1 * acc)
                acc += self.flt_rate
                j += self.dec_rate + int(np.floor(acc))
                acc -= np.floor(acc)
            if len(win) < L:
                break
            consumed = j // self.int_rate
            i_in += consumed
            j = j % self.int_rate
        self.last_filter = j
        self.acc = acc
        keep = max(0, L - 1)
        self._hist = x[-keep:] if keep else np.zeros(0, dtype=np.complex128)
        return np.asarray(out)


# ═══════════════════════════════════════════════════════════
# 5. Attack/decay automatic gain control (AGC)
# ═══════════════════════════════════════════════════════════
class AGC2:
    """Automatic gain control that drives the output magnitude to a set point.

    Per sample, the current gain multiplies the input; the magnitude of the
    scaled output is compared with the reference level.  When the output has
    overshot the reference the loop reacts quickly (``attack_rate``), otherwise
    it drifts slowly (``decay_rate``).  The gain is clamped to a small positive
    floor and optionally to a ceiling.
    """

    def __init__(self, attack_rate=1e-1, decay_rate=1e-2, reference=1.0,
                 gain=1.0, max_gain=0.0):
        self.attack_rate = float(attack_rate)
        self.decay_rate = float(decay_rate)
        self.reference = float(reference)
        self.gain = float(gain)
        self.max_gain = float(max_gain)

    def scale(self, inp: complex) -> complex:
        out = inp * self.gain
        err = np.sqrt(out.real ** 2 + out.imag ** 2) - self.reference
        rate = self.attack_rate if err > self.gain else self.decay_rate
        self.gain -= err * rate
        if self.gain < 0.0:
            self.gain = 10e-5
        if self.max_gain > 0.0 and self.gain > self.max_gain:
            self.gain = self.max_gain
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.complex128)
        out = np.empty(len(x), dtype=np.complex128)
        for i, v in enumerate(x):
            out[i] = self.scale(complex(v))
        return out

    def reset(self, gain=1.0):
        self.gain = float(gain)


# ═══════════════════════════════════════════════════════════
# 6. Rational (interpolate / decimate) resampler
# ═══════════════════════════════════════════════════════════
class RationalResampler:
    """Resample by the rational factor ``interpolation / decimation``.

    The standard multirate construction first upsamples by ``interpolation``
    (inserting zeros), applies an anti-imaging/anti-alias low-pass, and then
    downsamples by ``decimation``.  When no prototype taps are supplied a
    suitable Kaiser-windowed FIR is designed automatically.  On top of SciPy
    this delegates to the optimized polyphase :func:`scipy.signal.resample_poly`.
    """

    def __init__(self, interpolation: int, decimation: int, taps=None,
                 fractional_bw: float = 0.4):
        if interpolation < 1 or decimation < 1:
            raise ValueError("interpolation/decimation must be >= 1")
        self.interp = int(interpolation)
        self.decim = int(decimation)
        g = np.gcd(self.interp, self.decim)
        self._auto_taps = taps is None
        if taps is None:
            i = self.interp // g
            d = self.decim // g
            taps = self._design_taps(i, d, fractional_bw)
        self.taps = np.asarray(taps, dtype=np.float64)
        self._ctr = 0

    @staticmethod
    def _design_taps(interp: int, decim: int, fractional_bw: float):
        # Kaiser-windowed low-pass; transition band placed below the (up-sampled)
        # Nyquist frequency, with pass-band gain equal to the interpolation factor.
        try:
            from scipy.signal import kaiserord, firwin
        except Exception:
            return np.ones(interp * 8) / interp
        beta = 7.0
        halfband = 0.5
        rate = interp / decim
        if rate >= 1.0:
            trans_width = halfband - fractional_bw
            mid = halfband - trans_width / 2.0
        else:
            trans_width = rate * (halfband - fractional_bw)
            mid = rate * halfband - trans_width / 2.0
        numtaps = max(33, int(4.0 / max(trans_width, 1e-3) * interp) | 1)
        return firwin(numtaps, mid, width=trans_width,
                      window=('kaiser', beta)) * interp

    def process(self, x: np.ndarray) -> np.ndarray:
        """Whole-block rational resampling (polyphase, equivalent to resample_poly)."""
        try:
            from scipy.signal import resample_poly
        except Exception:
            up = np.zeros(len(x) * self.interp, dtype=np.asarray(x).dtype)
            up[::self.interp] = x
            y = np.convolve(up, self.taps)
            return y[::self.decim]
        return resample_poly(np.asarray(x), self.interp, self.decim,
                             window=self.taps)


# ═══════════════════════════════════════════════════════════
# 7. Mueller & Müller symbol-timing recovery
# ═══════════════════════════════════════════════════════════
def _slice(v: float) -> float:
    """Binary decision: +1 for non-negative samples, -1 otherwise."""
    return 1.0 if v >= 0.0 else -1.0


class ClockRecoveryMM:
    """Mueller & Müller symbol-timing recovery for a real eye pattern.

    The loop interpolates the input at a fractional point, estimates a timing
    error from the product of the current and previous sliced samples, and uses
    that error both to correct the instantaneous sampling phase (``mu``) and to
    track the average samples-per-symbol (``omega``).  The interpolator here
    is linear; the loop-filter gains and the error detector follow the classic
    Mueller-Müller derivation.
    """

    def __init__(self, omega: float, gain_omega: float, mu: float,
                 gain_mu: float, omega_relative_limit: float = 0.005):
        if omega < 1:
            raise ValueError("clock rate omega must be >= 1")
        self.omega = float(omega)
        self.omega_mid = float(omega)
        self.omega_lim = self.omega_mid * omega_relative_limit
        self.mu = float(mu)
        self.gain_omega = float(gain_omega)
        self.gain_mu = float(gain_mu)
        self.last_sample = 0.0

    @staticmethod
    def _interp(x: np.ndarray, mu: float) -> float:
        """Linear fractional-delay interpolation."""
        i0 = int(np.floor(mu))
        frac = mu - i0
        if i0 + 1 < len(x):
            return (1 - frac) * x[i0] + frac * x[i0 + 1]
        return x[min(i0, len(x) - 1)]

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        out = []
        ii = 0
        ni = len(x) - 2
        while ii < ni:
            o = self._interp(x[ii:], self.mu)
            # Mueller-Müller timing error detector.
            mm = _slice(self.last_sample) * o - _slice(o) * self.last_sample
            self.last_sample = o
            # Average-rate correction, clamped around the nominal rate.
            self.omega += self.gain_omega * mm
            self.omega = self.omega_mid + np.clip(self.omega - self.omega_mid,
                                                  -self.omega_lim, self.omega_lim)
            # Instantaneous phase update and sample advance.
            self.mu += self.omega + self.gain_mu * mm
            step = int(np.floor(self.mu))
            ii += step
            self.mu -= step
            out.append(o)
        return np.asarray(out)

    def reset(self):
        self.omega = self.omega_mid
        self.mu = 0.5
        self.last_sample = 0.0
