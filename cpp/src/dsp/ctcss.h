// SPDX-License-Identifier: MIT
//
// CTCSS (Continuous Tone-Coded Squelch System) sub-audible tone detector.
//
// Mechanism (clean-room, derived from the standard Goertzel bin-energy idea):
// the NFM audio stream (real-valued, 48 kHz) is fed through a streaming
// Goertzel resonator tuned to the configured sub-audible tone f0 (67.0 ..
// 254.1 Hz). Every N = round(sampleRate / passband) samples we compare the
// squared magnitude of the f0 bin against the block's total signal energy:
// a real in-band tone concentrates a large share of energy into that single
// narrow bin, while speech / broadband noise spreads energy over the whole
// band and leaves the bin share near the noise-floor expectation. Consecutive
// above-threshold cycles latch the tone present; a few below-threshold cycles
// then release it (hangover), so brief noise dips do not chatter the gate.
//
// This file implements the DSP only. It knows nothing about squelch gating,
// channels or the UI -- the host (SpectrumEngine) feeds it the selected VFO's
// demodulated mono audio and reads tonePresent() back.
//
// NOTE: CDCSS/DCS (digital coded squelch, 134.4 bps 3-of-8 code) is NOT
// implemented here; this round lands the analog CTCSS tone path only.
#pragma once

#include <cmath>

namespace mbdsdr {
namespace dsp {

class CtcssToneDetector {
public:
    CtcssToneDetector() = default;

    // (Re)configure the target tone. toneHz is clamped to the legal CTCSS
    // domain (tokens::kCtcssToneHzMin .. kCtcssToneHzMax) by the caller; the
    // detector ignores out-of-domain values and keeps its previous tuning.
    // Reconfiguring resets the streaming state (a new bin coefficient).
    void configure(double sampleRateHz, double toneHz);

    // Drop all streaming / debounce state. tonePresent() becomes false.
    void reset();

    // Enable / disable detection. While disabled, process() keeps feeding
    // nothing (state stays cleared) and tonePresent() is always false -- an
    // honest "no squelch code requested" read-back, never a fabricated tone.
    void setEnabled(bool on) { enabled_ = on; if (!on) reset(); }
    bool enabled() const { return enabled_; }

    // Feed one block of demodulated mono audio. Returns the detection state
    // AFTER this block (same value tonePresent() returns). When disabled the
    // call is a no-op and returns false.
    bool process(const float* audio, int n);

    // True only when the debounce latch currently holds a tone. Honest: no
    // signal / disabled / warm-up all read false.
    bool tonePresent() const { return enabled_ && present_; }

    // Read-back of the active tuning (Hz) and the Goertzel bin width (Hz),
    // for status display / tests.
    double toneHz() const { return toneHz_; }
    double binBandwidthHz() const;

private:
    // Finish one N-sample measurement cycle and update the debounce latch.
    void measureCycle();

    bool   enabled_  = false;
    bool   present_  = false;
    double sampleRateHz_ = 0.0;
    double toneHz_  = 0.0;

    // Goertzel streaming recurrence state: s[n] = x[n] + coeff*s[n-1] - s[n-2].
    double coeff_ = 0.0;   // 2*cos(2*pi*k/N)
    double s1_ = 0.0, s2_ = 0.0;
    long   count_ = 0;     // samples accumulated in the current cycle
    int    N_ = 0;         // cycle length (samples) = round(sr / passband)

    // Total squared energy of the current cycle (denominator of the ratio).
    double energy_ = 0.0;

    // Debounce latch counters (measurement cycles).
    int hits_  = 0;
    int miss_  = 0;
};

} // namespace dsp
} // namespace mbdsdr
