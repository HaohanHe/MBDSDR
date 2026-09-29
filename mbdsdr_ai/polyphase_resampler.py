# SPDX-License-Identifier: MIT
"""
Polyphase rational resampler.

Converts a stream from ``in_sr`` to ``out_sr`` with an anti-alias FIR front-end.
The prototype low-pass is decomposed into a polyphase filter bank: instead of
up-sampling by the interpolation factor and convolving (which would cost
``interp * taps`` multiply-accumulates per output), each output selects one
short sub-filter.  The prototype cutoff is ``min(in, out)/2``, so both the image
bands from up-sampling and aliases from down-sampling are rejected.

Stateful: the trailing ``taps_per_phase-1`` input samples and the (phase,
offset) position are carried across calls so block-wise processing is continuous.
"""

from __future__ import annotations

from math import gcd
import numpy as np

__all__ = ["PolyphaseResampler"]


def build_polyphase_bank(proto_taps: np.ndarray, num_phases: int):
    """Split a prototype low-pass into ``num_phases`` polyphase sub-filters.

    Returns ``(phases, taps_per_phase)`` where ``phases[p]`` is the p-th
    sub-filter (length ``taps_per_phase``, zero-padded).
    """
    proto_taps = np.asarray(proto_taps, dtype=np.float64)
    M = int(num_phases)
    N = proto_taps.size
    taps_per_phase = (N + M - 1) // M
    phases = np.zeros((M, taps_per_phase), dtype=np.float64)
    for i in range(N):
        phase = (M - 1) - (i % M)
        row = i // M
        phases[phase, row] = proto_taps[i]
    return phases, taps_per_phase


class PolyphaseResampler:
    """Rational sample-rate conversion ``in_sr -> out_sr``.

    Parameters:
        in_sr_hz: input sample rate.
        out_sr_hz: desired output sample rate.
    """

    def __init__(self, in_sr_hz: float, out_sr_hz: float) -> None:
        self._in_sr = float(in_sr_hz)
        self._out_sr = float(out_sr_hz)
        self._rebuild()

    def _rebuild(self) -> None:
        from .fir_taps import lowpass_taps
        in_sr = self._in_sr
        out_sr = self._out_sr

        # Reduce the rate ratio by the GCD.
        int_sr = int(round(in_sr))
        out_int = int(round(out_sr))
        g = gcd(int_sr, out_int)
        self._interp = out_int // g
        self._decim = int_sr // g

        # Prototype low-pass: cutoff at half the lower rate, narrow transition,
        # scaled by the interpolation factor to compensate zero-stuffing.
        tap_sr = in_sr * self._interp
        tap_bw = min(in_sr, out_sr) / 2.0
        trans = tap_bw * 0.1
        proto = lowpass_taps(tap_bw, trans, tap_sr, odd=False)
        proto = proto * self._interp

        self._phases, self._tpp = build_polyphase_bank(proto, self._interp)

        # Streaming state.
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
        """Clear history and phase."""
        self._history = np.zeros_like(self._history)
        self._phase = 0
        self._offset = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        """Process a block and return the resampled samples."""
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
            ph += D
            off += ph // M
            ph = ph % M
        self._offset = off - count
        self._phase = ph

        # Carry the trailing samples as next-block history.
        self._history = full[count:count + tpp - 1].copy() if tpp > 1 \
            else np.array([], dtype=np.float64)

        if not out_list:
            return np.empty(0, dtype=x.dtype)
        out = np.asarray(out_list)
        return out.astype(x.dtype)
