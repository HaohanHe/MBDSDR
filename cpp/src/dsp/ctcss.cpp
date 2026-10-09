// SPDX-License-Identifier: MIT
#include "dsp/ctcss.h"

#include "core/tokens.h"

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace dsp {

void CtcssToneDetector::configure(double sampleRateHz, double toneHz) {
    // Out-of-domain tuning is rejected honestly: keep the previous bin rather
    // than silently tuning somewhere meaningless. The engine clamps before
    // calling, but a direct unit test of an illegal value must also no-op.
    if (sampleRateHz <= 0.0) return;
    if (toneHz < tokens::kCtcssToneHzMin || toneHz > tokens::kCtcssToneHzMax) {
        return;
    }

    sampleRateHz_ = sampleRateHz;
    toneHz_ = toneHz;

    // Cycle length: one Goertzel measurement covers ~kCtcssGoertzelBandwidthHz
    // of bin width (fres = sr / N). N=9600 @48 kHz -> 5.0 Hz bin, 200 ms/cycle.
    N_ = static_cast<int>(std::round(sampleRateHz / tokens::kCtcssGoertzelBandwidthHz));
    N_ = std::max(8, N_);

    // Bin index for f0 (exact alignment: k = N*f0/sr). Guard into [1, N/2].
    double kd = N_ * toneHz / sampleRateHz;
    long k = std::max(1L, std::min(static_cast<long>(N_ / 2), std::lround(kd)));

    coeff_ = 2.0 * std::cos(2.0 * M_PI * static_cast<double>(k) / static_cast<double>(N_));
    reset();
}

void CtcssToneDetector::reset() {
    s1_ = 0.0;
    s2_ = 0.0;
    count_ = 0;
    energy_ = 0.0;
    hits_ = 0;
    miss_ = 0;
    present_ = false;
}

double CtcssToneDetector::binBandwidthHz() const {
    if (N_ <= 0 || sampleRateHz_ <= 0.0) return 0.0;
    return sampleRateHz_ / static_cast<double>(N_);
}

bool CtcssToneDetector::process(const float* audio, int n) {
    if (!enabled_ || N_ <= 0 || audio == nullptr || n <= 0) return tonePresent();

    for (int i = 0; i < n; ++i) {
        const double x = static_cast<double>(audio[i]);
        // Goertzel recurrence: s[n] = x[n] + coeff*s[n-1] - s[n-2].
        const double s = x + coeff_ * s1_ - s2_;
        s2_ = s1_;
        s1_ = s;
        energy_ += x * x;

        if (++count_ >= N_) {
            measureCycle();
        }
    }
    return tonePresent();
}

void CtcssToneDetector::measureCycle() {
    // |X[k]|^2 = s1^2 + s2^2 - coeff*s1*s2  (Goertzel bin magnitude squared).
    const double power = s1_ * s1_ + s2_ * s2_ - coeff_ * s1_ * s2_;
    const double ratio = (energy_ > 0.0) ? (power / energy_) : 0.0;

    // Ratio scale sanity (N = 9600 @48k):
    //   pure aligned tone : power/E ~= N/2 ~= 4800 (sinc^2 rolloff >= 0.4 at
    //                                   worst half-bin offset -> ~1900)
    //   broadband noise    : power/E ~= O(1), exponentially distributed
    // The threshold sits decades above the noise expectation and decades
    // below a real tone, so neither speech energy (which only grows the
    // denominator) nor quiet-band noise can flip the latch.
    const bool hit = ratio > tokens::kCtcssMinBinEnergyRatio;

    if (hit) {
        miss_ = 0;
        if (++hits_ >= tokens::kCtcssDetectHits) present_ = true;
    } else {
        hits_ = 0;
        if (++miss_ >= tokens::kCtcssDetectMisses) present_ = false;
    }

    // Start the next measurement window. The Goertzel poles lie on the unit
    // circle, so the recurrence has INFINITE memory: unless s1/s2 are dropped
    // at the boundary, the previous window's bin output keeps ringing forever
    // and a tone that has already left still looks present. Each N-window is
    // an independent block DFT, so restart the recurrence from zero.
    count_ = 0;
    energy_ = 0.0;
    s1_ = 0.0;
    s2_ = 0.0;
}

} // namespace dsp
} // namespace mbdsdr
