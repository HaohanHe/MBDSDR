// SPDX-License-Identifier: MIT
// In-place radix-2 Cooley-Tukey complex FFT, no external dependencies.
#pragma once

#include <complex>
#include <vector>
#include <cstddef>

namespace mbdsdr {
namespace dsp {

/// In-place complex FFT. n must be a power of two.
/// data is overwritten with the transform.
void fft(std::vector<std::complex<float>>& data);

/// In-place inverse complex FFT. n must be a power of two.
void ifft(std::vector<std::complex<float>>& data);

/// Return true if n is a power of two.
bool isPowerOfTwo(std::size_t n);

} // namespace dsp
} // namespace mbdsdr
