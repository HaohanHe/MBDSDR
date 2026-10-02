// SPDX-License-Identifier: MIT
#include "channelizer.h"

#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

// ---- NCO ----
void Nco::configure(double shiftHz, double sampleRateHz) {
    dphi_ = static_cast<float>(2.0 * M_PI * shiftHz / sampleRateHz);
}
void Nco::reset() { phase_ = 0.0f; }
std::complex<float> Nco::next() {
    std::complex<float> v(std::cos(phase_), -std::sin(phase_)); // e^{-j phase}
    phase_ += dphi_;
    if (phase_ > 2 * M_PI) phase_ -= 2 * M_PI;
    if (phase_ < -2 * M_PI) phase_ += 2 * M_PI;
    return v;
}

// ---- Channelizer ----
void Channelizer::designTaps(double cutoffNorm) {
    int T = tapsPerBranch_ * decimation_;
    if (T % 2 == 0) ++T;                 // keep an odd, symmetric kernel
    taps_.resize(static_cast<std::size_t>(T));
    const double M = T - 1;
    const double mid = M / 2.0;
    double sum = 0.0;
    for (int i = 0; i < T; ++i) {
        const double x = i - mid;
        const double u = 2.0 * cutoffNorm * x;
        // normalized sinc: sin(pi u)/(pi u), =1 at u=0
        const double s = (u == 0.0) ? 1.0 : std::sin(M_PI * u) / (M_PI * u);
        const double hann = 0.5 * (1.0 - std::cos(2.0 * M_PI * i / M));
        const double h = 2.0 * cutoffNorm * s * hann;
        taps_[i] = static_cast<float>(h);
        sum += h;
    }
    for (float& h : taps_) h = static_cast<float>(h / sum); // unity DC gain
}

void Channelizer::configure(double inRateHz, double outRateHz, double channelBwHz,
                            int tapsPerBranch, double transitionRatio) {
    inSr_ = inRateHz;
    outSr_ = outRateHz;
    bw_ = channelBwHz;
    tapsPerBranch_ = tapsPerBranch;
    transitionRatio_ = transitionRatio;
    skipFilter_ = false;

    // Decide whether in/out is an exact integer (fast path) or a fractional
    // ratio that needs exact rational resampling. The old round(in/out) pick
    // a single integer that did NOT land on outRate for fractional ratios
    // (e.g. 2.048MHz/48kHz -> 43 -> 47.62kHz, drifting pitch / RDS subcarrier).
    const double ratio = inRateHz / outRateHz;
    const long k = std::lround(ratio);
    const bool integerExact =
        (k >= 1) && (std::abs(inRateHz - outRateHz * static_cast<double>(k)) <=
                    1e-6 * inRateHz);

    if (integerExact) {
        mode_ = ChannelizerMode::Integer;
        decimation_ = static_cast<int>(k);
        const double effOut = inSr_ / decimation_;
        // Pure filter decision: cutoff = bw/2 clamped so bw/2 + transition stays
        // under Nyquist (replaces the old magic 0.85 back-off).
        spec_ = planChannelFilter(effOut, bw_, transitionRatio_);
        // RxVFO short-circuit: no rate change AND the channel covers the whole
        // output band -> the low-pass is identity. Skip the FIR (and its history
        // bookkeeping) entirely; only the NCO tuning runs.
        skipFilter_ = (decimation_ == 1) && (!spec_.filterNeeded);
        if (skipFilter_) {
            taps_.clear();
        } else {
            designTaps(spec_.designCutoffHz / inSr_);
        }
        resampler_.configure(inSr_ / decimation_, outSr_); // -> Passthrough
    } else {
        // Largest power-of-two pre-decimation that stays >= outRate. This is
        // the integer DDC stage; the residual fraction (intermediate -> out)
        // is handled exactly by the rational polyphase resampler.
        long floorRatio = static_cast<long>(std::floor(ratio));
        int predecPow = 0;
        while ((1L << (predecPow + 1)) <= floorRatio && predecPow < 20) ++predecPow;
        const int predecRatio = 1 << predecPow;

        mode_ = ChannelizerMode::Rational;
        decimation_ = predecRatio;
        // The decimating FIR is the anti-alias stage here; it always runs. The
        // Nyquist reference matches the historical outSr/2 guard (old 0.9).
        spec_ = planChannelFilter(outSr_, bw_, transitionRatio_);
        skipFilter_ = false;
        designTaps(spec_.designCutoffHz / inSr_);

        const double intermediate = inSr_ / predecRatio;
        resampler_.configure(intermediate, outSr_, 32);
    }
    reset();
}

void Channelizer::setVfoOffsetHz(double offsetHz) {
    nco_.configure(offsetHz, inSr_);
}

void Channelizer::reset() {
    nco_.reset();
    nco_.configure(0.0, inSr_);
    const std::size_t T = taps_.size();
    tail_.assign(T >= 1 ? T - 1 : 0, std::complex<float>(0.0f, 0.0f));
    nextBase_ = 0;
    primed_ = true;
    resampler_.reset();
}

double Channelizer::effectiveOutputRateHz() const {
    if (mode_ == ChannelizerMode::Rational) return outSr_; // exact target
    return decimation_ > 0 ? inSr_ / decimation_ : inSr_;
}

std::vector<std::complex<float>> Channelizer::process(
        const std::vector<std::complex<float>>& in) {
    const std::size_t L = in.size();
    std::vector<std::complex<float>> out;
    if (!primed_ || L == 0) return out;

    // Whole-band passthrough fast path: only the NCO down-mix runs; no channel
    // LPF and no rate change (the resampler is Passthrough by construction).
    // This is the RxVFO `if (!filterNeeded) return resamp.process(...)` short-
    // circuit applied to the only case where MBDSDR's fused decimating FIR would
    // otherwise be a pure overhead.
    if (skipFilter_) {
        out.resize(L);
        for (std::size_t i = 0; i < L; ++i) out[i] = in[i] * nco_.next();
        return out;
    }

    const std::size_t T = taps_.size();
    if (T == 0) return out;

    // 1) Down-mix so the VFO offset lands at 0 Hz (continuous phase).
    std::vector<std::complex<float>> mixed(L);
    for (std::size_t i = 0; i < L; ++i) mixed[i] = in[i] * nco_.next();

    // 2) Splice with the retained filter history.
    const std::size_t hist = T - 1;
    std::vector<std::complex<float>> combined(hist + L);
    std::copy(tail_.begin(), tail_.end(), combined.begin());
    std::copy(mixed.begin(), mixed.end(), combined.begin() + hist);

    // 3) One convolution per output sample; output bases advance by decimation.
    for (long idx = nextBase_ + static_cast<long>(hist);
         idx <= static_cast<long>(combined.size()) - 1;
         nextBase_ += decimation_, idx = nextBase_ + static_cast<long>(hist)) {
        std::complex<float> acc(0.0f, 0.0f);
        for (std::size_t k = 0; k < T; ++k)
            acc += taps_[k] * combined[static_cast<std::size_t>(idx) - k];
        out.push_back(acc);
    }

    // 4) Retain the last T-1 (mixed) samples; rebase next output to new block.
    std::copy(combined.end() - static_cast<long>(hist), combined.end(),
              tail_.begin());
    nextBase_ -= static_cast<long>(L);

    // 5) In rational mode the decimating FIR has only reached the intermediate
    //    (power-of-two) rate; finish the residual fraction exactly on outRate.
    if (mode_ == ChannelizerMode::Rational) return resampler_.process(out);
    return out;
}

} // namespace dsp
} // namespace mbdsdr
