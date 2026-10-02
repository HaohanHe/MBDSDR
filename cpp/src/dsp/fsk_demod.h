// SPDX-License-Identifier: MIT
//
// Real 2-FSK / GFSK receiver (clean-room, mechanism learned from SDR++
// gfsk.h:31-34,131-135 and clock_recovery/mm.h:121-149 -- GPLv3, mechanism
// only; code below re-derived).
//
// Chain:
//   complex baseband IQ
//     -> quadrature discriminator (conj-multiply + atan2, normalized by deviation)
//     -> Nuttall-windowed-sinc baseband lowpass (on the PAM after discrimination)
//     -> Mueller-Muller symbol timing recovery (real-signal TED:
//          error = sign(prevSym)*curSym - prevSym*sign(curSym))
//     -> sign hard decision -> bits 0/1
//
// The RRC matched filter in SDR++ lives AFTER the discriminator because FSK
// becomes PAM once frequency is measured; here the Nuttall LPF plays that
// baseband-shaping role. Pure C++17, deterministic, no hardware.
#pragma once

#include <complex>
#include <vector>

#include "demod.h"   // NuttallLpf

namespace mbdsdr {
namespace dsp {

struct FskDemodConfig {
    double sampleRateHz = 48000.0;
    double symbolRateBd = 1200.0;
    double deviationHz  = 600.0;   // +/- peak frequency deviation from center
    double basebandTrans = 0.5;   // LPF transition width, in units of symRate
    float omegaGain = 0.001f;     // MM frequency-loop (integrator) gain
    float muGain    = 0.01f;       // MM phase-loop (proportional) gain
};

class FskDemod {
public:
    explicit FskDemod(const FskDemodConfig& cfg = FskDemodConfig{});

    void reset();
    // Feed a block of channelized complex baseband samples.
    void process(const std::vector<std::complex<float>>& iq);
    // Drain recovered soft decisions converted to bits (0/1). Clears queue.
    std::vector<int> takeBits();

    long symbolsProcessed() const { return nSym_; }
    const FskDemodConfig& config() const { return cfg_; }

private:
    FskDemodConfig cfg_;
    double sps_;
    float discGain_;

    NuttallLpf bbLpf_;

    // Discriminator state
    std::complex<float> prev_{1.0f, 0.0f};

    // MM timing-recovery state
    float prevSample_ = 0.0f;   // last filtered baseband sample
    float nAcc_      = 0.0f;   // fractional sample accumulator
    float omega_     = 0.0f;   // current samples-per-symbol estimate
    float lastSym_   = 0.0f;
    bool  haveSym_   = false;

    long nSym_ = 0;
    std::vector<int> bits_;
};

} // namespace dsp
} // namespace mbdsdr
