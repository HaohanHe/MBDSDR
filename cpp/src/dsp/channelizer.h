// SPDX-License-Identifier: GPL-3.0-or-later
// Channelizer: the missing stage between the wideband source and the narrowband
// demodulator. Frequency-translates the selected VFO to baseband, applies a
// channel low-pass, and integer-decimates down to the IF rate. Streaming; NCO
// phase and filter history are preserved across blocks.
#pragma once

#include <complex>
#include <vector>
#include <memory>
#include <cstddef>

namespace mbdsdr {
namespace dsp {

// Continuous-phase complex oscillator.
class Nco {
public:
    // shiftHz / sampleRateHz; negative shifts down in frequency.
    void configure(double shiftHz, double sampleRateHz);
    void reset();
    std::complex<float> next();   // e^{-j phi} (down-mix), advances phase
private:
    float phase_ = 0.0f;
    float dphi_  = 0.0f;
};

// Decimating FIR channel filter. Only one convolution sum is evaluated per
// OUTPUT sample (the dropped input samples are never computed), which matches
// polyphase-decimator cost. Taps are a windowed-sinc low-pass designed at the
// INPUT rate; total taps = tapsPerBranch * decimation.
class Channelizer {
public:
    Channelizer() = default;

    // inRate: source sample rate; outRate: desired IF rate; channelBwHz: the
    // receiver bandwidth (the passband is min(bw/2, outRate/2 * 0.85)).
    void configure(double inRateHz, double outRateHz, double channelBwHz,
                   int tapsPerBranch = 31);
    void setVfoOffsetHz(double offsetHz); // vfo - source center
    void reset();

    double inputRateHz() const { return inSr_; }
    double effectiveOutputRateHz() const; // inSr / decimation_
    int decimation() const { return decimation_; }

    // Returns baseband IQ at effectiveOutputRateHz (variable length per call).
    std::vector<std::complex<float>> process(const std::vector<std::complex<float>>& in);

private:
    void designTaps(double cutoffNorm);

    double inSr_ = 0.0;
    double outSr_ = 0.0;
    double bw_ = 0.0;
    int decimation_ = 1;
    int tapsPerBranch_ = 31;
    Nco nco_;
    std::vector<float> taps_;                 // length T
    std::vector<std::complex<float>> tail_;   // T-1 previous (mixed) samples
    long nextBase_ = 0;                       // next output base, relative to block 0
    bool primed_ = false;
};

} // namespace dsp
} // namespace mbdsdr
