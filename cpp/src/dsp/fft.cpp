// SPDX-License-Identifier: MIT
#include "fft.h"

#include <cmath>
#include <stdexcept>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

bool isPowerOfTwo(std::size_t n) {
    return n != 0 && (n & (n - 1)) == 0;
}

namespace {

void bitReversePermute(std::vector<std::complex<float>>& a) {
    const std::size_t n = a.size();
    std::size_t j = 0;
    for (std::size_t i = 1; i < n; ++i) {
        std::size_t bit = n >> 1;
        while (j & bit) {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if (i < j) std::swap(a[i], a[j]);
    }
}

} // namespace

void fft(std::vector<std::complex<float>>& data) {
    const std::size_t n = data.size();
    if (n <= 1) return;
    if (!isPowerOfTwo(n))
        throw std::invalid_argument("fft: size must be a power of two");

    bitReversePermute(data);

    for (std::size_t len = 2; len <= n; len <<= 1) {
        const double angle = -2.0 * M_PI / static_cast<double>(len);
        std::complex<float> wlen(static_cast<float>(std::cos(angle)),
                                 static_cast<float>(std::sin(angle)));
        for (std::size_t i = 0; i < n; i += len) {
            std::complex<float> w(1.0f, 0.0f);
            for (std::size_t k = 0; k < len / 2; ++k) {
                std::complex<float> u = data[i + k];
                std::complex<float> v = data[i + k + len / 2] * w;
                data[i + k]         = u + v;
                data[i + k + len / 2] = u - v;
                w *= wlen;
            }
        }
    }
}

void ifft(std::vector<std::complex<float>>& data) {
    const std::size_t n = data.size();
    if (n <= 1) return;
    if (!isPowerOfTwo(n))
        throw std::invalid_argument("ifft: size must be a power of two");

    // conjugate, FFT, conjugate, divide by n
    for (auto& c : data) c = std::conj(c);
    fft(data);
    const float inv = 1.0f / static_cast<float>(n);
    for (auto& c : data) c = std::conj(c) * inv;
}

} // namespace dsp
} // namespace mbdsdr
