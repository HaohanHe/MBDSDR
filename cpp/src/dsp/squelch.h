// SPDX-License-Identifier: MIT
// RMS-based squelch with attack/decay smoothing and hangover.
#pragma once

#include <vector>

namespace mbdsdr {
namespace dsp {

float rmsDbfs(const std::vector<float>& x);

class Squelch {
public:
    enum class Mode {
        Off,         // squelch disabled: pass audio through, mark open
        AlwaysOpen,  // explicitly open: pass audio through, no gating
        Gate         // RMS gate: mute when smoothed RMS is below threshold
    };

    explicit Squelch(double blockDurMs = 20.0);
    void setThresholdDb(float db) { thresholdDb_ = db; }
    // Legacy compatibility wrapper: true -> Gate, false -> Off.
    void setEnabled(bool e) { setMode(e ? Mode::Gate : Mode::Off); }
    void setMode(Mode m) { mode_ = m; }
    Mode mode() const { return mode_; }
    bool open() const { return open_; }
    void reset();
    /// Gate the audio block; returns gated copy (silent when closed).
    std::vector<float> apply(const std::vector<float>& audio, float rmsDb);
    /// Update the smoothing / hangover state and return whether the gate is
    /// open for THIS block, WITHOUT muting the audio. Lets the engine feed the
    /// real (un-gated) audio to AGC / the gated recorder while muting only the
    /// speaker path. apply() is implemented on top of decide().
    bool decide(const std::vector<float>& audio, float rmsDb);
private:
    Mode mode_ = Mode::Off;
    bool open_ = false;
    float thresholdDb_ = -50.0f;
    float smoothDb_ = -150.0f;
    double attackTauMs_ = 5.0;
    double decayTauMs_   = 50.0;
    double hangMs_       = 200.0;
    double blockMs_      = 20.0;
    double hangLeftMs_   = 0.0;
    float attackAlpha_, decayAlpha_;
};

} // namespace dsp
} // namespace mbdsdr
