// SPDX-License-Identifier: GPL-3.0-or-later
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
                            int tapsPerBranch) {
    inSr_ = inRateHz;
    outSr_ = outRateHz;
    bw_ = channelBwHz;
    tapsPerBranch_ = tapsPerBranch;
    decimation_ = std::max(1, static_cast<int>(std::round(inRateHz / outRateHz)));
    const double effOut = inSr_ / decimation_;
    const double cutoff = std::min(bw_ / 2.0, effOut / 2.0 * 0.85);
    designTaps(cutoff / inSr_);
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
}

double Channelizer::effectiveOutputRateHz() const {
    return decimation_ > 0 ? inSr_ / decimation_ : inSr_;
}

std::vector<std::complex<float>> Channelizer::process(
        const std::vector<std::complex<float>>& in) {
    const std::size_t L = in.size();
    const std::size_t T = taps_.size();
    std::vector<std::complex<float>> out;
    if (!primed_ || T == 0 || L == 0) return out;

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
    return out;
}

} // namespace dsp
} // namespace mbdsdr
