// SPDX-License-Identifier: MIT
// First-order exponential DC blocker for complex IQ -- the SDR++ dc_blocker.h
// algorithm (out[i] = in[i] - offset; offset += out[i] * rate). Tracks and
// subtracts the receiver's DC offset (the RTL-SDR zero-IF LO-leakage spike at
// the tuned center frequency) without touching anything off-center. A pure
// software correction: the raw IQ tap / recorder still see the unmodified
// samples when the block is bypassed.
#pragma once

#include <complex>
#include <vector>

namespace mbdsdr {
namespace dsp {

class DcBlocker {
public:
    void setRate(float r) { rate_ = r; }
    float rate() const { return rate_; }
    void reset() { offset_ = {0.0f, 0.0f}; }

    void process(std::vector<std::complex<float>>& io) {
        for (auto& c : io) {
            c -= offset_;
            offset_ += c * rate_;
        }
    }

private:
    std::complex<float> offset_{0.0f, 0.0f};
    float rate_ = 0.001f;
};

} // namespace dsp
} // namespace mbdsdr
