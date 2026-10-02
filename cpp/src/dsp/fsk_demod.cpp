// SPDX-License-Identifier: MIT
#include "fsk_demod.h"

#include <cmath>

namespace mbdsdr {
namespace dsp {

FskDemod::FskDemod(const FskDemodConfig& cfg) : cfg_(cfg) {
    sps_ = cfg_.sampleRateHz / cfg_.symbolRateBd;
    discGain_ = static_cast<float>(
        cfg_.sampleRateHz / (2.0 * M_PI * cfg_.deviationHz));
    // Baseband shaping LPF on the discriminator output: cutoff ~ symbol rate,
    // transition band parameterized (default 0.5 * symRate).
    const double cutoff = cfg_.symbolRateBd;
    const double trans  = cfg_.basebandTrans * cfg_.symbolRateBd;
    bbLpf_.design(cutoff, trans, cfg_.sampleRateHz);
    reset();
}

void FskDemod::reset() {
    prev_ = {1.0f, 0.0f};
    prevSample_ = 0.0f;
    nAcc_ = static_cast<float>(sps_ * 0.5);   // first strobe near symbol center
    omega_ = static_cast<float>(sps_);
    lastSym_ = 0.0f;
    haveSym_ = false;
    nSym_ = 0;
    bits_.clear();
    bbLpf_.reset();
}

void FskDemod::process(const std::vector<std::complex<float>>& iq) {
    if (iq.empty()) return;

    // 1) Quadrature discriminator -> normalized PAM (+/-1 for +/- deviation).
    std::vector<float> disc(iq.size());
    for (std::size_t i = 0; i < iq.size(); ++i) {
        std::complex<float> y = iq[i] * std::conj(prev_);
        disc[i] = discGain_ * std::atan2(y.imag(), y.real());
        prev_ = iq[i];
    }

    // 2) Baseband matched/lowpass filter.
    std::vector<float> baseband;
    bbLpf_.process(disc, baseband);

    // 3) Mueller-Muller strobe + sign decision.
    for (float cur : baseband) {
        nAcc_ += 1.0f;
        while (nAcc_ >= omega_) {
            float overshoot = nAcc_ - omega_;      // [0, ~1)
            float frac = 1.0f - overshoot;         // position prev->cur
            if (frac < 0.0f) frac = 0.0f;
            if (frac > 1.0f) frac = 1.0f;
            const float sym = prevSample_ * (1.0f - frac) + cur * frac;

            if (haveSym_) {
                // Real-signal MM TED: error = sign(prev)*cur - prev*sign(cur).
                const float sp = (lastSym_ > 0.0f) ? 1.0f : -1.0f;
                const float sc = (sym    > 0.0f) ? 1.0f : -1.0f;
                float err = sp * sym - lastSym_ * sc;
                if (err > 1.0f) err = 1.0f;
                if (err < -1.0f) err = -1.0f;
                omega_ += cfg_.omegaGain * err;
                nAcc_  += cfg_.muGain * err;
            }

            bits_.push_back(sym >= 0.0f ? 1 : 0);
            lastSym_ = sym;
            haveSym_ = true;
            ++nSym_;
            nAcc_ -= omega_;
        }
        prevSample_ = cur;
    }
}

std::vector<int> FskDemod::takeBits() {
    std::vector<int> out;
    out.swap(bits_);
    return out;
}

} // namespace dsp
} // namespace mbdsdr
