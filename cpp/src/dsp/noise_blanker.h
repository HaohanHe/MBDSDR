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
    void process(std::vector<std::complex<float>>& iq);

private:
    bool enabled_ = false;
};

} // namespace dsp
} // namespace mbdsdr
