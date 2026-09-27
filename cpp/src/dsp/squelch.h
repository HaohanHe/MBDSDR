// SPDX-License-Identifier: MIT
// RMS-based squelch with attack/decay smoothing and hangover.
#pragma once

#include <vector>

namespace mbdsdr {
namespace dsp {

float rmsDbfs(const std::vector<float>& x);

class Squelch {
public:
    explicit Squelch(double blockDurMs = 20.0);
    void setThresholdDb(float db) { thresholdDb_ = db; }
    void setEnabled(bool e) { enabled_ = e; }
    bool open() const { return open_; }
    void reset();
    /// Gate the audio block; returns gated copy (silent when closed).
    std::vector<float> apply(const std::vector<float>& audio, float rmsDb);
private:
    bool enabled_ = false;
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
