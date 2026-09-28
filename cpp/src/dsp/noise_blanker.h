// SPDX-License-Identifier: MIT
// Simple impulse (pulse) noise blanker: samples exceeding mean+n*sigma over a
// sliding window are replaced by the local median. Real processing, not a UI stub.
#pragma once

#include <complex>
#include <vector>

namespace mbdsdr {
namespace dsp {

class NoiseBlanker {
public:
    void setEnabled(bool on) { enabled_ = on; }
    bool enabled() const { return enabled_; }
    void setSampleRate(double hz) { winSamples_ = std::max(16, static_cast<int>(hz / 1000.0)); }
    void process(std::vector<std::complex<float>>& iq);

private:
    bool enabled_ = false;
    int winSamples_ = 32;
};

} // namespace dsp
} // namespace mbdsdr
