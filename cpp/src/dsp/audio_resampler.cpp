// SPDX-License-Identifier: MIT
#include "audio_resampler.h"

#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

void AudioResampler::design(double cutoffNorm, int T) {
    taps_.resize(static_cast<std::size_t>(T));
    const double M = T - 1;
    const double mid = M / 2.0;
    double sum = 0.0;
    for (int i = 0; i < T; ++i) {
        const double x = i - mid;
        const double u = 2.0 * cutoffNorm * x;
        const double s = (u == 0.0) ? 1.0 : std::sin(M_PI * u) / (M_PI * u);
        const double hann = 0.5 * (1.0 - std::cos(2.0 * M_PI * i / M));
        const double h = 2.0 * cutoffNorm * s * hann;
        taps_[i] = static_cast<float>(h);
        sum += h;
    }
    for (float& h : taps_) h = static_cast<float>(h / sum);
}

void AudioResampler::configure(double inRateHz, double outRateHz,
                               int tapsPerBranch) {
    inSr_ = inRateHz;
    outSr_ = outRateHz;
    M_ = std::max(1, static_cast<int>(std::floor(inRateHz / outRateHz)));
    const double stageARate = inSr_ / M_;
    if (M_ > 1) {
        int T = tapsPerBranch * M_;
        if (T % 2 == 0) ++T;
        const double cutoff = std::min(outSr_ / 2.0 * 0.9,
                                       stageARate / 2.0 * 0.9);
        design(cutoff / inSr_, T);
    } else {
        taps_.clear();
    }
    step_ = stageARate / outSr_;
    reset();
}

void AudioResampler::reset() {
    if (!taps_.empty()) {
        tail_.assign(taps_.size() - 1, 0.0f);
        nextBase_ = 0;
    } else {
        tail_.clear();
    }
    fracPos_ = 0.0;
    carry_ = 0.0f;
    hasCarry_ = false;
}

std::vector<float> AudioResampler::decimate(const std::vector<float>& in) {
    if (M_ == 1 || taps_.empty()) return in;
    const std::size_t L = in.size();
    const std::size_t T = taps_.size();
    const std::size_t hist = T - 1;

    std::vector<float> combined(hist + L);
    std::copy(tail_.begin(), tail_.end(), combined.begin());
    std::copy(in.begin(), in.end(), combined.begin() + hist);

    std::vector<float> out;
    for (long idx = nextBase_ + static_cast<long>(hist);
         idx <= static_cast<long>(combined.size()) - 1;
         nextBase_ += M_, idx = nextBase_ + static_cast<long>(hist)) {
        float acc = 0.0f;
        for (std::size_t k = 0; k < T; ++k)
            acc += taps_[k] * combined[static_cast<std::size_t>(idx) - k];
        out.push_back(acc);
    }
    std::copy(combined.end() - static_cast<long>(hist), combined.end(),
              tail_.begin());
    nextBase_ -= static_cast<long>(L);
    return out;
}

std::vector<float> AudioResampler::fractional(const std::vector<float>& in) {
    std::vector<float> list;
    list.reserve(in.size() + 1);
    if (hasCarry_) list.push_back(carry_);
    list.insert(list.end(), in.begin(), in.end());

    std::vector<float> out;
    double pos = fracPos_;
    while (true) {
        long i = static_cast<long>(std::floor(pos));
        if (i + 1 > static_cast<long>(list.size()) - 1) break;
        double f = pos - i;
        out.push_back(static_cast<float>((1.0 - f) * list[i]
                                        + f * list[i + 1]));
        pos += step_;
    }
    const long lastIdx = static_cast<long>(list.size()) - 1;
    fracPos_ = pos - lastIdx;
    if (lastIdx >= 0) carry_ = list[lastIdx];
    hasCarry_ = true;
    return out;
}

std::vector<float> AudioResampler::process(const std::vector<float>& in) {
    if (in.empty()) return {};
    return fractional(decimate(in));
}

} // namespace dsp
} // namespace mbdsdr
