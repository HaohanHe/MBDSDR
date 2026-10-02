// SPDX-License-Identifier: MIT
// Channelizer: the missing stage between the wideband source and the narrowband
// demodulator. Frequency-translates the selected VFO to baseband, applies a
// channel low-pass, and integer-decimates down to the IF rate. Streaming; NCO
// phase and filter history are preserved across blocks.
#pragma once

#include <complex>
#include <vector>
#include <memory>
#include <cstddef>
#include "rational_resampler.h"

namespace mbdsdr {
namespace dsp {

// Continuous-phase complex oscillator.
class Nco {
public:
    // shiftHz / sampleRateHz; negative shifts down in frequency.
    void configure(double shiftHz, double sampleRateHz);
    void reset();
    std::complex<float> next();   // e^{-j phi} (down-mix), advances phase
    // Expose accumulated phase (radians) and per-step delta for continuity tests.
    double phase() const { return static_cast<double>(phase_); }
    double deltaPhase() const { return static_cast<double>(dphi_); }
private:
    float phase_ = 0.0f;
    float dphi_  = 0.0f;
};

// ---- Pure channel-filter decision (clean-room RxVFO mechanism) -------------
// Mirrors SDR++ RxVFO (rx_vfo.h, GPLv3, mechanism only):
//   - filterNeeded = (bandwidth != outSamplerate) -> whole-band passthrough
//   - generateTaps: lowPass(bandwidth/2, (bandwidth/2)*0.1, outSamplerate),
//     i.e. the transition width is an explicit fraction of the passband half-
//     width (0.1 upstream), not a magic Nyquist back-off.
// This struct is a PURE decision: no state, no allocation, no DSP. It turns the
// old hardcoded cutoffs (0.85 / 0.9 of Nyquist) into an explicit, testable
// value, and decides when the channel low-pass can be skipped entirely.
struct ChannelFilterSpec {
    bool   filterNeeded = true;    // false => channel covers the whole output band
    double passbandEdgeHz = 0.0;   // bandwidth / 2
    double transitionWidthHz = 0.0; // transitionRatio * passbandEdge
    double designCutoffHz = 0.0;   // passband edge, clamped so it + transition
                                   // stays below Nyquist (the real design cutoff)
};

// outRateHz: the rate the channel filter lives at (post decimation).
// bandwidthHz: requested channel bandwidth.
// transitionRatio: transition width / (bandwidth/2); 0.1 == upstream default.
// tol: relative tolerance for the bw==outRate short-circuit test.
inline ChannelFilterSpec planChannelFilter(double outRateHz, double bandwidthHz,
                                           double transitionRatio = 0.1,
                                           double tol = 1e-6) {
    ChannelFilterSpec s;
    const double nyquist = outRateHz / 2.0;
    s.passbandEdgeHz = (bandwidthHz > 0.0) ? bandwidthHz / 2.0 : 0.0;
    s.transitionWidthHz = transitionRatio * s.passbandEdgeHz;
    // Upstream short-circuit: bandwidth == outSamplerate -> low-pass is identity.
    s.filterNeeded = bandwidthHz < outRateHz * (1.0 - tol);
    double cutoff = s.passbandEdgeHz;
    // Reserve the transition band below Nyquist (replaces magic 0.85 / 0.9).
    const double maxEdge = nyquist - s.transitionWidthHz;
    if (cutoff > maxEdge) cutoff = maxEdge;
    if (cutoff < 0.0) cutoff = 0.0;
    s.designCutoffHz = cutoff;
    return s;
}

// Operating mode of the rate converter.
//  - Integer:  in/out is an exact integer K -> single-stage integer decimation
//    (the historical path; preserves output rate and behaviour).
//  - Rational: in/out is fractional -> power-of-two pre-decimation by the
//    decimating FIR followed by a GCD-reduced polyphase rational resampler, so
//    the output lands EXACTLY on outRate (no pitch / sub-carrier drift).
enum class ChannelizerMode { Integer, Rational };

// Decimating FIR channel filter + exact rational rate conversion. Frequency-
// translates the selected VFO to baseband, applies a channel low-pass, then
// converts to the IF rate exactly. Streaming; NCO phase, FIR history and the
// rational commutator are preserved across blocks.
class Channelizer {
public:
    Channelizer() = default;

    // inRate: source sample rate; outRate: desired IF rate; channelBwHz: the
    // receiver bandwidth. transitionRatio: transition width / (bw/2); 0.1 is the
    // upstream RxVFO default. The design cutoff is derived as bw/2 clamped so
    // bw/2 + transition stays under Nyquist (no more magic 0.85 / 0.9).
    void configure(double inRateHz, double outRateHz, double channelBwHz,
                   int tapsPerBranch = 31, double transitionRatio = 0.1);
    void setVfoOffsetHz(double offsetHz); // vfo - source center
    void reset();

    double inputRateHz() const { return inSr_; }
    double effectiveOutputRateHz() const; // always == outRateHz after configure
    int decimation() const { return decimation_; }
    ChannelizerMode mode() const { return mode_; }
    const RationalResampler& resampler() const { return resampler_; }
    // True when the channel low-pass was skipped (whole-band passthrough:
    // no rate change AND bandwidth >= output rate, a la RxVFO filterNeeded).
    bool filterSkipped() const { return skipFilter_; }
    // The pure filter decision computed at configure (for tests / introspection).
    const ChannelFilterSpec& filterSpec() const { return spec_; }
    double transitionRatio() const { return transitionRatio_; }

    // Returns baseband IQ at effectiveOutputRateHz (variable length per call).
    std::vector<std::complex<float>> process(const std::vector<std::complex<float>>& in);

private:
    void designTaps(double cutoffNorm);

    double inSr_ = 0.0;
    double outSr_ = 0.0;
    double bw_ = 0.0;
    int decimation_ = 1;
    int tapsPerBranch_ = 31;
    ChannelizerMode mode_ = ChannelizerMode::Integer;
    Nco nco_;
    RationalResampler resampler_;              // residual rational stage
    ChannelFilterSpec spec_;                   // pure filter decision (last configure)
    double transitionRatio_ = 0.1;             // transition width / (bw/2)
    bool skipFilter_ = false;                  // whole-band passthrough fast path
    std::vector<float> taps_;                 // length T (empty when skipFilter_)
    std::vector<std::complex<float>> tail_;   // T-1 previous (mixed) samples
    long nextBase_ = 0;                       // next output base, relative to block 0
    bool primed_ = false;
};

} // namespace dsp
} // namespace mbdsdr
