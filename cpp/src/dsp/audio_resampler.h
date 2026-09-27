// SPDX-License-Identifier: GPL-3.0-or-later
// Rational audio resampler to a fixed output rate: integer polyphase-style
// decimation (windowed-sinc anti-alias) followed by a streaming linear
// fractional resampler that lands exactly on the requested rate. The fractional
// stage only corrects small ratios (near 1 for narrowband, 1..2 for the WFM
// post-decimation stage), where linear interpolation is inaudible for voice.
#pragma once

#include <vector>
#include <cstddef>

namespace mbdsdr {
namespace dsp {

class AudioResampler {
public:
    AudioResampler() = default;
    void configure(double inRateHz, double outRateHz, int tapsPerBranch = 31);
    void reset();
    std::vector<float> process(const std::vector<float>& in);

private:
    double inSr_ = 0.0, outSr_ = 0.0;
    int M_ = 1;                 // integer decimation
    std::vector<float> taps_;   // integer-stage filter
    std::vector<float> tail_;   // integer-stage history
    long nextBase_ = 0;

    // fractional stage state
    double step_ = 1.0;         // stage-A samples per output sample
    double fracPos_ = 0.0;      // continuous read position
    float carry_ = 0.0f;        // last stage-A sample carried across blocks
    bool hasCarry_ = false;

    void design(double cutoffNorm, int T);
    std::vector<float> decimate(const std::vector<float>& in);
    std::vector<float> fractional(const std::vector<float>& in);
};

} // namespace dsp
} // namespace mbdsdr
