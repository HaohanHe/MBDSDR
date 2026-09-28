// SPDX-License-Identifier: MIT
#include "squelch.h"
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

float rmsDbfs(const std::vector<float>& x) {
    if (x.empty()) return -150.0f;
    double sum = 0;
    for (float v : x) sum += v*v;
    return static_cast<float>(10.0 * std::log10(sum / x.size() + 1e-12));
}

Squelch::Squelch(double blockDurMs) : blockMs_(blockDurMs) {
    attackAlpha_ = static_cast<float>(1.0 - std::exp(-blockMs_ / attackTauMs_));
    decayAlpha_   = static_cast<float>(1.0 - std::exp(-blockMs_ / decayTauMs_));
}

void Squelch::reset() {
    open_ = false; smoothDb_ = -150.0f; hangLeftMs_ = 0;
}

std::vector<float> Squelch::apply(const std::vector<float>& audio, float rmsDb) {
    // Off / AlwaysOpen: pass audio through ungated.
    if (mode_ != Mode::Gate) {
        open_ = true;
        return audio;
    }
    // Gate mode: smooth RMS, fast attack, slow decay
    const float a = (rmsDb > smoothDb_) ? attackAlpha_ : decayAlpha_;
    smoothDb_ += a * (rmsDb - smoothDb_);

    if (smoothDb_ >= thresholdDb_) {
        open_ = true;
        hangLeftMs_ = hangMs_;
    } else if (open_) {
        hangLeftMs_ -= blockMs_;
        if (hangLeftMs_ <= 0) open_ = false;
    }

    if (!open_) {
        std::vector<float> silent(audio.size(), 0.0f);
        return silent;
    }
    return audio;
}

} // namespace dsp
} // namespace mbdsdr
