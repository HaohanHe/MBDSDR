# SPDX-License-Identifier: MIT
"""
Anti-aliasing decimating FIR.

Decimation must be preceded by a low-pass anti-alias filter: blindly taking
every ``D``-th sample (``x[::D]``) would fold any energy above the output
Nyquist frequency ``fs_in/(2D)`` back into the base band.  This module first
filters with a prototype low-pass FIR and then keeps every ``D``-th output.

Correctness points:
  1. Low-pass filtering always precedes decimation.
  2. The prototype cutoff is at most the output Nyquist ``fs_in/(2D)``; taps are
     produced by :mod:`mbdsdr_ai.fir_taps` (window method, transition width
     about 10% of the cutoff).
  3. Stateful: the last ``taps-1`` input samples and the decimation phase are
     carried across calls so block-wise processing is continuous.
"""

from __future__ import annotations

import numpy as np

__all__ = ["DecimatingFIR", "design_decimation_taps"]


def design_decimation_taps(decimation: int, input_sr_hz: float,
                           trans_width_hz: float | None = None) -> np.ndarray:
    """Design anti-alias low-pass taps for integer decimation.

    The cutoff is the output Nyquist ``input_sr/(2*decimation)`` and the
    transition width defaults to 10% of the cutoff.
    """
    from .fir_taps import lowpass_taps
    cutoff = input_sr_hz / (2.0 * decimation)
    if trans_width_hz is None:
        trans_width_hz = cutoff * 0.1
    return lowpass_taps(cutoff, trans_width_hz, input_sr_hz, odd=True)


class DecimatingFIR:
    """Stateful anti-aliasing decimating FIR.

    Parameters:
        taps: real FIR low-pass coefficients (from :func:`design_decimation_taps`).
        decimation: integer decimation factor D >= 1.
    """

    def __init__(self, taps: np.ndarray, decimation: int = 1) -> None:
        taps = np.asarray(taps, dtype=np.float64)
        if taps.ndim != 1 or taps.size < 1:
            raise ValueError("taps must be a 1-D FIR coefficient array")
        if decimation < 1:
            raise ValueError("decimation must be >= 1")
        self._taps = taps
        self._D = int(decimation)
        # History: trailing (N-1) samples from the previous block.
        self._history = np.zeros(taps.size - 1, dtype=np.float64)
        # Decimation phase carried across blocks.
        self._offset = 0

    @property
    def decimation(self) -> int:
        return self._D

    def reset(self) -> None:
        """清空历史与抽取相位（换源/换频时调用）。"""
        self._history = np.zeros_like(self._history)
        self._offset = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        """Process a block; returns the decimated samples (length ~ len(x)/D)."""
        x = np.asarray(x)
        if x.size == 0:
            return x
        if self._D <= 1:
            return x
        n_taps = self._taps.size

        # Prepend history so the first output of this block is continuous.
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
        self._offset = off - count

        if not out_indices:
            self._history = full[-(n_taps - 1):].copy() if n_taps > 1 else np.array([])
            return np.empty(0, dtype=x.dtype)

        idx = np.asarray(out_indices)
        # Full valid correlation, then pick the decimation-phase outputs.
        from scipy.signal import correlate
        valid = correlate(full, self._taps, mode="valid", method="fft")
        y = valid[idx]

        # Retain the trailing (n_taps-1) samples as next-block history.
        self._history = full[count:count + n_taps - 1].copy() if n_taps > 1 \
            else np.array([], dtype=np.float64)
        return y.astype(x.dtype)
