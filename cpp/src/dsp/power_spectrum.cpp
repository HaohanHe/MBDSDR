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

// === PowerSpectrum class (window + average) ===
namespace mbdsdr {
namespace dsp {

void PowerSpectrum::setWindow(Window w) {
    win_ = w;
    windowLen_ = 0;   // force rebuild on next process
}

void PowerSpectrum::setAverage(Average a) {
    avg_ = a;
    ring_.clear();
    ringIdx_ = 0;
}

void PowerSpectrum::rebuildWindow(std::size_t n) {
    window_.assign(n, 1.0f);
    if (n == 0) return;
    if (n == 1) { window_[0] = 1.0f; return; }
    const float Nm1 = static_cast<float>(n - 1);
    for (std::size_t i = 0; i < n; ++i) {
        const float x = static_cast<float>(i) / Nm1;
        float v = 1.0f;
        if (win_ == Hann) {
            v = 0.5f * (1.0f - std::cos(2.0f * static_cast<float>(M_PI) * x));
        } else if (win_ == Flattop) {
            // Sidelobe-flat window.
            const float a0 = 0.21557895f, a1 = 0.41663158f, a2 = 0.277263158f,
                        a3 = 0.083578947f, a4 = 0.006947368f;
            v = a0 - a1 * std::cos(2.0f * static_cast<float>(M_PI) * x)
                    + a2 * std::cos(4.0f * static_cast<float>(M_PI) * x)
                    - a3 * std::cos(6.0f * static_cast<float>(M_PI) * x)
                    + a4 * std::cos(8.0f * static_cast<float>(M_PI) * x);
        } else { // Blackman
            v = 0.42f + 0.5f * std::cos(2.0f * static_cast<float>(M_PI) * (x - 0.5f))
                       + 0.08f * std::cos(4.0f * static_cast<float>(M_PI) * (x - 0.5f));
        }
        window_[i] = v;
    }
    windowLen_ = n;
}

void PowerSpectrum::process(const std::vector<std::complex<float>>& input,
                            std::vector<float>& output) {
    const std::size_t n = input.size();
    if (n == 0) { output.clear(); return; }
    if (!isPowerOfTwo(n))
        throw std::invalid_argument("PowerSpectrum::process: size must be power of two");
    if (windowLen_ != n) rebuildWindow(n);

    std::vector<std::complex<float>> buf(input.begin(), input.end());
    for (std::size_t i = 0; i < n; ++i) buf[i] *= window_[i];
    fft(buf);
    const std::size_t half = n / 2;
    std::rotate(buf.begin(), buf.begin() + half, buf.end());

    // Linear power per bin.
    std::vector<float> lin(n);
    for (std::size_t i = 0; i < n; ++i)
        lin[i] = std::norm(buf[i]);

    // Optional moving-average ring.
    if (avg_ != Off) {
        const int depth = (avg_ == Slow) ? 16 : 4;
        if (static_cast<int>(ring_.size()) != depth) {
            ring_.assign(depth, std::vector<float>(n, 0.0f));
            ringIdx_ = 0;
        }
        ring_[ringIdx_] = lin;
        ringIdx_ = (ringIdx_ + 1) % depth;
        std::fill(lin.begin(), lin.end(), 0.0f);
        for (const auto& frame : ring_)
            for (std::size_t i = 0; i < n; ++i) lin[i] += frame[i];
        const float inv = 1.0f / depth;
        for (std::size_t i = 0; i < n; ++i) lin[i] *= inv;
    }

    if (output.size() != n) output.resize(n);
    const float scale = 4.0f / static_cast<float>(n * n);  // linear-power normalize
    const float epsilon = 1e-10f;
    for (std::size_t i = 0; i < n; ++i)
        output[i] = 10.0f * std::log10(lin[i] * scale + epsilon);
}

} // namespace dsp
} // namespace mbdsdr
