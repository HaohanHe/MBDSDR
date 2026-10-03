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

void Agc::processWithGain(const std::vector<float>& in,
                          std::vector<float>* out,
                          std::vector<float>* gain) {
    out->resize(in.size());
    gain->resize(in.size());
    for (std::size_t i = 0; i < in.size(); ++i) {
        const float mag = std::abs(in[i]);
        const float a = (mag > env_) ? attackAlpha_ : decayAlpha_;
        env_ += a * (mag - env_);
        // gain = target/env, but CEIL it: an empty channel's near-zero noise
        // envelope must not be amplified up to target volume (the sandpaper
        // hiss). A real voice raises env_ well above the floor, so its gain
        // (~target/env, a few x) never reaches the ceiling.
        const float gRaw = target_ / std::max(env_, 1e-4f);
        const float g = std::min(gRaw, maxGain_);
        (*gain)[i] = g;
        (*out)[i] = std::clamp(in[i] * g, -1.0f, 1.0f);
    }
}

std::vector<float> Agc::process(const std::vector<float>& in) {
    std::vector<float> out, gain;
    processWithGain(in, &out, &gain);
    return out;
}

float Agc::currentLevelDb() const {
    if (env_ < 1e-6f) return -120.0f;
    return static_cast<float>(20.0 * std::log10(env_));
}

// ---- ComplexCarrierAgc (clean-room carrier AGC) ---------------------------
ComplexCarrierAgc::ComplexCarrierAgc(double sr, float setPoint,
                                     double attackMs, double decayMs,
                                     float maxGain)
    : setPoint_(setPoint), maxGain_(maxGain) {
    const double dt = 1.0 / sr;
    attackAlpha_ = static_cast<float>(1.0 - std::exp(-dt / (attackMs * 1e-3)));
    decayAlpha_  = static_cast<float>(1.0 - std::exp(-dt / (decayMs  * 1e-3)));
}

void ComplexCarrierAgc::reset() { env_ = 0.0f; gain_ = 1.0f; }

std::complex<float> ComplexCarrierAgc::processOne(std::complex<float> x) {
    if (!enabled_) { gain_ = 1.0f; return x; }
    const float amp = std::abs(x);
    if (amp > env_) env_ += attackAlpha_ * (amp - env_);
    else            env_ += decayAlpha_  * (amp - env_);
    gain_ = std::min(setPoint_ / std::max(env_, 1e-6f), maxGain_);
    return x * gain_;
}

std::vector<std::complex<float>>
ComplexCarrierAgc::process(const std::vector<std::complex<float>>& in) {
    std::vector<std::complex<float>> out(in.size());
    for (std::size_t i = 0; i < in.size(); ++i) out[i] = processOne(in[i]);
    return out;
}

} // namespace dsp
} // namespace mbdsdr
