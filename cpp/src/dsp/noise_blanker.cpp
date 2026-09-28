// SPDX-License-Identifier: MIT
#include "noise_blanker.h"
#include <cmath>
#include <algorithm>
#include <vector>

namespace mbdsdr {
namespace dsp {

void NoiseBlanker::process(std::vector<std::complex<float>>& iq) {
    if (!enabled_ || iq.empty()) return;
    const std::size_t n = iq.size();
    // Windowed statistic on the instantaneous |IQ| magnitude.
    const int W = winSamples_;       // sliding window (samples)
    const double kSigma = 3.5;     // threshold = mean + 3.5*std
    std::vector<float> mag(n);
    for (std::size_t i = 0; i < n; ++i) mag[i] = std::abs(iq[i]);

    for (std::size_t i = 0; i < n; ++i) {
        const std::size_t lo = (i >= W) ? i - W : 0;
        const std::size_t hi = std::min(n, i + W + 1);
        double sum = 0.0, sumSq = 0.0;
        int cnt = 0;
        for (std::size_t j = lo; j < hi; ++j) {
            sum += mag[j]; sumSq += mag[j] * mag[j]; ++cnt;
        }
        const double mean = sum / cnt;
        const double var = sumSq / cnt - mean * mean;
        const double sigma = std::sqrt(std::max(0.0, var));
        if (mag[i] > mean + kSigma * sigma) {
            // Replace with the local median magnitude, keep phase.
            std::vector<float> win(mag.begin() + lo, mag.begin() + hi);
            std::nth_element(win.begin(), win.begin() + win.size() / 2, win.end());
            const float med = win[win.size() / 2];
            const float ph = std::atan2(iq[i].imag(), iq[i].real());
            iq[i] = std::complex<float>(med * std::cos(ph), med * std::sin(ph));
        }
    }
}

} // namespace dsp
} // namespace mbdsdr
