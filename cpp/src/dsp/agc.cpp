// SPDX-License-Identifier: MIT
#include "agc.h"
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

Agc::Agc(double blockDurMs) : blockDurMs_(blockDurMs) {
    attackAlpha_ = static_cast<float>(1.0 - std::exp(-blockDurMs_ / attackTauMs_));
    decayAlpha_   = static_cast<float>(1.0 - std::exp(-blockDurMs_ / decayTauMs_));
}

void Agc::setAttackMs(double ms) {
    if (ms <= 0) return;
    attackTauMs_ = ms;
    attackAlpha_ = static_cast<float>(1.0 - std::exp(-blockDurMs_ / attackTauMs_));
}

void Agc::setDecayMs(double ms) {
    if (ms <= 0) return;
    decayTauMs_ = ms;
    decayAlpha_ = static_cast<float>(1.0 - std::exp(-blockDurMs_ / decayTauMs_));
}

void Agc::setCarrierAgc(bool on) {
    if (on) {
        setTarget(CarrierTarget);
        setAttackMs(CarrierAttackMs);
    } else {
        setTarget(DefaultTarget);
        setAttackMs(DefaultAttackMs);
    }
}

void Agc::reset() { env_ = 0; }

std::vector<float> Agc::process(const std::vector<float>& in) {
    std::vector<float> out(in.size());
    for (std::size_t i = 0; i < in.size(); ++i) {
        const float mag = std::abs(in[i]);
        const float a = (mag > env_) ? attackAlpha_ : decayAlpha_;
        env_ += a * (mag - env_);
        const float gain = target_ / std::max(env_, 1e-4f);
        float v = in[i] * gain;
        out[i] = std::clamp(v, -1.0f, 1.0f);
    }
    return out;
}

float Agc::currentLevelDb() const {
    if (env_ < 1e-6f) return -120.0f;
    return static_cast<float>(20.0 * std::log10(env_));
}

} // namespace dsp
} // namespace mbdsdr
