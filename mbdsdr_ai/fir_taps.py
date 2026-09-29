# SPDX-License-Identifier: MIT
"""
Window-method FIR tap design.

Generates real-valued prototype FIR coefficients (low-pass, high-pass,
band-pass, and root-raised-cosine pulse-shaping) with the standard
windowed-sinc method.  The implementation depends only on NumPy.

Design conventions:
  * The number of taps is estimated from the transition width; an odd tap count
    selects a Type-I linear-phase filter.
  * Windows are centred on zero (argument ``n = i - N/2``).
  * The windowed sinc is scaled so the DC (pass-band) gain is unity.
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "estimate_tap_count",
    "nuttall",
    "hann",
    "hamming",
    "windowed_sinc",
    "lowpass_taps",
    "highpass_taps",
    "bandpass_taps",
    "root_raised_cosine_taps",
]


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def _cosine_window(n: np.ndarray, N: float, coefs) -> np.ndarray:
    """Generalised cosine window: alternating-sum cosine series."""
    win = np.zeros_like(n, dtype=np.float64)
    sign = 1.0
    for i, c in enumerate(coefs):
        win += sign * c * np.cos(i * 2.0 * np.pi * n / N)
        sign = -sign
    return win


def nuttall(n: np.ndarray, N: float) -> np.ndarray:
    """Third-order Nuttall window (very low side lobes, ~ -93 dB)."""
    return _cosine_window(n, N, (0.355768, 0.487396, 0.144232, 0.012604))


def hann(n: np.ndarray, N: float) -> np.ndarray:
    """Hann window: 0.5 - 0.5*cos(2*pi*n/N)."""
    return _cosine_window(n, N, (0.5, 0.5))


def hamming(n: np.ndarray, N: float) -> np.ndarray:
    """Hamming window: 0.54 - 0.46*cos(2*pi*n/N)."""
    return _cosine_window(n, N, (0.54, 0.46))


# ---------------------------------------------------------------------------
# Tap-count estimate and windowed sinc
# ---------------------------------------------------------------------------
def estimate_tap_count(trans_width_hz: float, sample_rate_hz: float) -> int:
    """Estimate the FIR tap count needed for a given transition width.

    The rule ``3.8 * sample_rate / transition_width`` is the well-known
    empirical estimate for a Nuttall-windowed design (~93 dB stop band).
    """
    if trans_width_hz <= 0:
        raise ValueError(f"trans_width must be > 0, got {trans_width_hz}")
    return int(round(3.8 * sample_rate_hz / trans_width_hz))


def windowed_sinc(count: int, cutoff_hz: float, sample_rate_hz: float,
                  window=nuttall) -> np.ndarray:
    """Windowed-sinc real low-pass prototype FIR.

        omega = 2*pi*cutoff/sr
        taps[i] = sinc(t[i]*omega/pi) * window(t[i]-half, count) * (omega/pi)
    """
    count = int(count)
    if count < 1:
        raise ValueError("count must be >= 1")
    half = count / 2.0
    omega = 2.0 * np.pi * cutoff_hz / sample_rate_hz
    corr = omega / np.pi

    i = np.arange(count, dtype=np.float64)
    t = i - half + 0.5
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.sinc(t * omega / np.pi)
    w = window(t - half, count)
    taps = s * w * corr
    return taps.astype(np.float64)


def lowpass_taps(cutoff_hz: float, trans_width_hz: float, sample_rate_hz: float,
                 odd: bool = False, window=nuttall) -> np.ndarray:
    """Low-pass FIR taps.

    Args:
        cutoff_hz: -6 dB cutoff (Hz), must be < sample_rate/2.
        trans_width_hz: transition-band width (Hz); sets the tap count.
        sample_rate_hz: sample rate (Hz).
        odd: force an odd tap count (Type-I linear phase).
    """
    count = estimate_tap_count(trans_width_hz, sample_rate_hz)
    if odd and count % 2 == 0:
        count += 1
    if count < 3:
        count = 3 if not odd or 3 % 2 else 3
    return windowed_sinc(count, cutoff_hz, sample_rate_hz, window)


def highpass_taps(cutoff_hz: float, trans_width_hz: float, sample_rate_hz: float,
                  window=nuttall) -> np.ndarray:
    """High-pass FIR = low-pass prototype modulated to +/- Nyquist.

    High-pass filters must use an odd (Type-I) tap count so the DC value is
    exactly zero.
    """
    count = estimate_tap_count(trans_width_hz, sample_rate_hz)
    if count % 2 == 0:
        count += 1
    lp = windowed_sinc(count, cutoff_hz, sample_rate_hz, window)
    n = np.arange(count)
    return (lp * ((-1.0) ** n)).astype(np.float64)


def bandpass_taps(low_cut_hz: float, high_cut_hz: float, trans_width_hz: float,
                  sample_rate_hz: float, window=nuttall) -> np.ndarray:
    """Band-pass FIR = difference of two low-pass prototypes."""
    count = estimate_tap_count(trans_width_hz, sample_rate_hz)
    if count % 2 == 0:
        count += 1
    lp_hi = windowed_sinc(count, high_cut_hz, sample_rate_hz, window)
    lp_lo = windowed_sinc(count, low_cut_hz, sample_rate_hz, window)
    return (lp_hi - lp_lo).astype(np.float64)


# ---------------------------------------------------------------------------
# Root Raised Cosine (RRC) pulse shaping
# ---------------------------------------------------------------------------
def root_raised_cosine_taps(count: int, beta: float,
                            symbol_rate_hz: float, sample_rate_hz: float) -> np.ndarray:
    """Root-raised-cosine pulse-shaping FIR.

    ``Ts = sample_rate / symbol_rate`` samples per symbol; the standard closed
    form is used, with the analytic limit at t=0 and at t=+/-Ts/(4*beta).
    """
    if not 0.0 < beta <= 1.0:
        raise ValueError(f"beta must be in (0,1], got {beta}")
    count = int(count)
    Ts = sample_rate_hz / symbol_rate_hz
    half = count / 2.0
    limit = Ts / (4.0 * beta)

    i = np.arange(count, dtype=np.float64)
    t = i - half + 0.5
    taps = np.zeros(count, dtype=np.float64)

    for k in range(count):
        tk = t[k]
        if tk == 0.0:
            taps[k] = (1.0 + beta * (4.0 / np.pi - 1.0)) / Ts
        elif abs(tk - limit) < 1e-12 or abs(tk + limit) < 1e-12:
            taps[k] = (
                ((1.0 + 2.0 / np.pi) * np.sin(np.pi / (4.0 * beta))
                 + (1.0 - 2.0 / np.pi) * np.cos(np.pi / (4.0 * beta)))
                * beta / (Ts * np.sqrt(2.0))
            )
        else:
            x = 4.0 * beta * tk / Ts
            taps[k] = (
                (np.sin((1.0 - beta) * np.pi * tk / Ts)
                 + np.cos((1.0 + beta) * np.pi * tk / Ts) * x)
                / ((1.0 - x * x) * np.pi * tk / Ts)
            ) / Ts
    return taps.astype(np.float64)
