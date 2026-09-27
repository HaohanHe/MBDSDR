// SPDX-License-Identifier: MIT
// Envelope-follower AGC.
#pragma once

#include <vector>

namespace mbdsdr {
namespace dsp {

class Agc {
public:
    explicit Agc(double blockDurMs = 20.0);
    void setTarget(float level) { target_ = level; }
    void reset();
    std::vector<float> process(const std::vector<float>& in);
    float currentLevelDb() const;
private:
    float target_ = 0.3f;
    float env_ = 0.0f;
    float attackAlpha_, decayAlpha_;
    double attackTauMs_ = 5.0;
    double decayTauMs_  = 100.0;
};

} // namespace dsp
} // namespace mbdsdr
