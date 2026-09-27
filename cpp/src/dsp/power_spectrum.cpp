// SPDX-License-Identifier: MIT
#include "power_spectrum.h"
#include "fft.h"

#include <cmath>
#include <algorithm>
#include <stdexcept>

namespace mbdsdr {
namespace dsp {

std::vector<float> makeHannWindow(std::size_t n) {
    std::vector<float> w(n);
    if (n == 0) return w;
    if (n == 1) { w[0] = 1.0f; return w; }
    for (std::size_t i = 0; i < n; ++i) {
        w[i] = 0.5f * (1.0f - std::cos(2.0f * static_cast<float>(M_PI) *
                                       static_cast<float>(i) /
                                       static_cast<float>(n - 1)));
    }
    return w;
}

void powerSpectrumDbfs(const std::vector<std::complex<float>>& input,
                       std::vector<float>& output) {
    const std::size_t n = input.size();
    if (n == 0) { output.clear(); return; }
    if (!isPowerOfTwo(n))
        throw std::invalid_argument("powerSpectrumDbfs: input size must be power of two");

    // Apply Hann window
    static thread_local std::vector<float> window;
    static thread_local std::size_t windowN = 0;
    if (windowN != n) {
        window = makeHannWindow(n);
        windowN = n;
    }

    std::vector<std::complex<float>> buf(n);
    for (std::size_t i = 0; i < n; ++i) {
        buf[i] = input[i] * window[i];
    }

    fft(buf);

    // fftshift: swap halves
    const std::size_t half = n / 2;
    std::rotate(buf.begin(), buf.begin() + half, buf.end());

    // Magnitude -> dBFS. Full-scale sine wave amplitude = 1.0 -> |FFT peak| ~ n/2.
    // We normalize by n/2 so that a full-scale tone hits ~0 dBFS.
    if (output.size() != n) output.resize(n);
    const float scale = 2.0f / static_cast<float>(n);
    const float epsilon = 1e-10f;
    for (std::size_t i = 0; i < n; ++i) {
        float mag = std::abs(buf[i]) * scale;
        output[i] = 20.0f * std::log10(mag + epsilon);
    }
}

} // namespace dsp
} // namespace mbdsdr
