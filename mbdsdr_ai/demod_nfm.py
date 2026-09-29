# SPDX-License-Identifier: MIT
"""
Narrow-band FM: phase-difference discriminator + RC de-emphasis (stateful NumPy).

Quadrature discriminator::

    out[n] = gain * unwrap( angle(z[n]) - angle(z[n-1]) )

which is equivalent to ``gain * angle(z[n] * conj(z[n-1]))``.  The frequency
deviation is ``bandwidth/2`` and the discriminator gain is
``1/(2*pi*deviation/if_sr)``.

One-pole RC de-emphasis::

    dt = 1/if_sr;  alpha = dt/(tau+dt)
    out[n] = alpha*in[n] + (1-alpha)*out[n-1]

with tau = 50 us (EU/CN) / 75 us (US) / 22 us.  No explicit limiter is needed:
the preceding AGC flattens the amplitude, and the phase-difference discriminator
is insensitive to amplitude.
"""
from __future__ import annotations
import numpy as np


class DeemphasisIIR:
    """First-order RC de-emphasis low-pass."""

    def __init__(self, tau: float, samplerate: float):
        self.tau = float(tau)
        self.sr = float(samplerate)
        self._last = 0.0
        self._update_alpha()

    def _update_alpha(self) -> None:
        dt = 1.0 / self.sr
        self.alpha = dt / (self.tau + dt)

    def set_tau(self, tau: float) -> None:
        self.tau = float(tau)
        self._update_alpha()

    def reset(self) -> None:
        self._last = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.tau <= 0 or len(x) == 0:
            return np.asarray(x, dtype=np.float64)
        x = np.asarray(x, dtype=np.float64)
        out = np.empty_like(x)
        prev = self._last
        a = self.alpha
        for i in range(len(x)):
            prev = a * x[i] + (1.0 - a) * prev
            out[i] = prev
        self._last = out[-1]
        return out


class DemodNFM:
    """Narrow-band FM discriminator.

    Parameters
    ----------
    if_sr : IF complex sample rate in Hz (default 50000).
    bandwidth : channel bandwidth in Hz (default 12500; deviation = bandwidth/2).
    deemph_tau : de-emphasis time constant in seconds (0 = no de-emphasis).
    """

    def __init__(self, if_sr: float = 50000.0, bandwidth: float = 12500.0,
                 deemph_tau: float = 0.0):
        self.if_sr = float(if_sr)
        self.bandwidth = float(bandwidth)
        self.deviation = self.bandwidth / 2.0
        self._gain = 1.0 / (2.0 * np.pi * self.deviation / self.if_sr)
        self._phase = 0.0
        self.deemph = DeemphasisIIR(deemph_tau, self.if_sr)

    def reset(self) -> None:
        self._phase = 0.0
        self.deemph.reset()

    def process(self, iq: np.ndarray) -> np.ndarray:
        """Complex IF -> real audio at the IF sample rate."""
        z = np.asarray(iq, dtype=np.complex128)
        n = len(z)
        if n == 0:
            return np.zeros(0, dtype=np.float32)

        # Phase-difference discriminator.
        phase = np.angle(z)
        dphase = np.empty(n)
        dphase[0] = phase[0] - self._phase
        dphase[1:] = phase[1:] - phase[:-1]
        # Wrap to [-pi, pi].
        dphase = (dphase + np.pi) % (2.0 * np.pi) - np.pi
        self._phase = phase[-1] if n else self._phase
        audio = dphase * self._gain

        # De-emphasis (passthrough when tau = 0).
        audio = self.deemph.process(audio)
        return audio.astype(np.float32)
