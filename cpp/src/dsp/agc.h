// SPDX-License-Identifier: MIT
// Envelope-follower AGC.
#pragma once

#include <vector>

namespace mbdsdr {
namespace dsp {

class Agc {
public:
    // Default operating point (carrier-AGC off).
    static constexpr float DefaultTarget = 0.3f;
    static constexpr double DefaultAttackMs = 5.0;
    static constexpr double DefaultDecayMs  = 100.0;
    // Carrier AGC operating point.
    static constexpr float CarrierTarget = 0.5f;
    static constexpr double CarrierAttackMs = 2.0;

    explicit Agc(double blockDurMs = 20.0);
    void setTarget(float level) { target_ = level; }
    // Runtime attack/decay time-constant update (ms); re-derives per-block alphas.
    void setAttackMs(double ms);
    void setDecayMs(double ms);
    // Carrier AGC mode: raises target and speeds up attack. Off restores defaults.
    void setCarrierAgc(bool on);
    double attackMs() const { return attackTauMs_; }
    double decayMs() const { return decayTauMs_; }
    void reset();
    std::vector<float> process(const std::vector<float>& in);
    // Gain-exposing variant: identical envelope-follow math to process(), but
    // also writes the per-sample LINEAR gain actually applied (target_/env) into
    // `gain` and the leveled+clamped samples into `out`. Both are resized to
    // in.size(). The stereo engine re-uses this exact same gain on the M/S matrix
    // so mono and the L/R channels share one AGC envelope (no level mismatch).
    void processWithGain(const std::vector<float>& in,
                         std::vector<float>* out,
                         std::vector<float>* gain);
    float currentLevelDb() const;
private:
    float target_ = DefaultTarget;
    float env_ = 0.0f;
    float attackAlpha_, decayAlpha_;
    double blockDurMs_;
    double attackTauMs_ = DefaultAttackMs;
    double decayTauMs_  = DefaultDecayMs;
};

} // namespace dsp
} // namespace mbdsdr
