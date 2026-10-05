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

    // Block-level lookahead anti-clip (clean-room; the *idea* of previewing the
    // block peak before scaling, written independently). Before touching the
    // envelope, scan this block ONCE for its loudest sample -- O(n), deliberately
    // not a per-sample nested inner scan. A burst that arrives while the
    // envelope is still settling from a quiet block would otherwise be amplified
    // by a too-high gain on its first samples and slam into OutputCeiling.
    // Knowing the block peak up front lets us pre-empt exactly that.
    float blockPeak = 0.0f;
    double sumAbs = 0.0;   // mean magnitude, for reset fast-capture seeding
    for (const float v : in) {
        const float m = std::abs(v);
        if (m > blockPeak) blockPeak = m;
        sumAbs += m;
    }

    // First block after reset (env_ still exactly 0): seed the envelope from
    // the block's mean magnitude instead of letting the first sample derive a
    // tentative gain from a near-zero envelope. Clean-room; the *idea* of
    // setting gain in one shot from the first block's average magnitude
    // (gr-analog agc3 sweeps the first samples to compute gain directly),
    // written independently. Matters for a QUIET block: the tentative first
    // gain would otherwise ride the maxGain ceiling, and because
    // blockPeak*ceiling < OutputCeiling the block-peak lookahead would never
    // pull it down -- overshooting the first samples. Seeding env_ to the mean
    // puts the first sample already at its settled gain. A loud block still
    // trips the lookahead below exactly as before.
    if (env_ == 0.0f && !in.empty()) {
        env_ = static_cast<float>(sumAbs / static_cast<double>(in.size()));
    }

    for (std::size_t i = 0; i < in.size(); ++i) {
        const float mag = std::abs(in[i]);
        const float a = (mag > env_) ? attackAlpha_ : decayAlpha_;
        env_ += a * (mag - env_);
        // gain = target/env, but CEIL it: an empty channel's near-zero noise
        // envelope must not be amplified up to target volume (the sandpaper
        // hiss). A real voice raises env_ well above the floor, so its gain
        // (~target/env, a few x) never reaches the ceiling.
        float gRaw = target_ / std::max(env_, 1e-4f);
        float g = std::min(gRaw, maxGain_);

        // Lookahead: would the gain derived so far push the block's known peak
        // past the output ceiling? If so the envelope is lagging a burst. Jump
        // the envelope up to the block peak NOW (attack ahead of the one-pole)
        // and recompute gain; release is effectively backed off for the rest of
        // the block because we re-pin whenever the gain would creep up again.
        // The loudest sample then lands at ~target_ <= OutputCeiling and never
        // touches the clamp. Steady state needs no help: blockPeak*g ~= target_.
        if (blockPeak > 0.0f && blockPeak * g > OutputCeiling) {
            env_ = std::max(env_, blockPeak);
            gRaw = target_ / std::max(env_, 1e-4f);
            g = std::min(gRaw, maxGain_);
        }

        (*gain)[i] = g;
        (*out)[i] = std::clamp(in[i] * g, -OutputCeiling, OutputCeiling);
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
