// SPDX-License-Identifier: MIT
// Envelope-follower AGC.
#pragma once

#include <complex>
#include <vector>

namespace mbdsdr {
namespace dsp {

class Agc {
public:
    // Default operating point (carrier-AGC off).
    static constexpr float DefaultTarget = 0.3f;
    static constexpr double DefaultAttackMs = 5.0;
    static constexpr double DefaultDecayMs  = 100.0;
    // Gain ceiling (linear). Without it, an empty channel's tiny noise-floor
    // envelope drives gain = target/env up to ~1e3, blasting the quiet
    // background to listening volume -- the classic "sandpaper hiss on an empty
    // channel". Real receivers cap the boost: a well-received voice already sits
    // near target (gain ~1..3x) and never touches the ceiling, while a quiet
    // noise floor is left quiet instead of being amplified. Named value lives in
    // core/tokens.h (kAgcMaxGainLin); this constant is the fallback default.
    static constexpr float DefaultMaxGain = 12.0f;
    // Carrier AGC operating point.
    static constexpr float CarrierTarget = 0.5f;
    static constexpr double CarrierAttackMs = 2.0;

    explicit Agc(double blockDurMs = 20.0);
    void setTarget(float level) { target_ = level; }
    // Cap the linear gain applied to the audio. <= 0 means "no ceiling"
    // (legacy behaviour); a finite value keeps an idle noise floor quiet.
    void setMaxGain(float g) { maxGain_ = (g > 0.0f) ? g : 1e9f; }
    float maxGain() const { return maxGain_; }
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
    float maxGain_ = DefaultMaxGain;
    float attackAlpha_, decayAlpha_;
    double blockDurMs_;
    double attackTauMs_ = DefaultAttackMs;
    double decayTauMs_  = DefaultDecayMs;
};

// ---- Complex carrier AGC (clean-room, mirrors SDR++ am.h:34-35,103-106) ----
// Runs on the COMPLEX IQ stream BEFORE envelope detection. Tracks the input
// modulus |x| with independent attack/decay one-poles and scales x so the
// average envelope settles to setPoint. Pure sample-by-sample state machine ->
// fully deterministic / unit-testable. Off by default; enable inside DemodAM.
class ComplexCarrierAgc {
public:
    static constexpr float DefaultSetPoint = 1.0f;
    static constexpr double DefaultAttackMs = 2.0;
    static constexpr double DefaultDecayMs  = 100.0;
    static constexpr float DefaultMaxGain   = 10000.0f;

    explicit ComplexCarrierAgc(double sampleRateHz = 48000.0,
                               float setPoint = DefaultSetPoint,
                               double attackMs = DefaultAttackMs,
                               double decayMs = DefaultDecayMs,
                               float maxGain = DefaultMaxGain);
    void reset();
    void setEnabled(bool on) { enabled_ = on; }
    bool enabled() const { return enabled_; }
    std::complex<float> processOne(std::complex<float> x);
    std::vector<std::complex<float>> process(const std::vector<std::complex<float>>& in);
    float currentEnvelope() const { return env_; }
    float currentGain() const { return gain_; }
private:
    float setPoint_, attackAlpha_, decayAlpha_, maxGain_;
    float env_ = 0.0f, gain_ = 1.0f;
    bool  enabled_ = true;
};

} // namespace dsp
} // namespace mbdsdr
