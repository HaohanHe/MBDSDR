// SPDX-License-Identifier: MIT
// Power spectrum (dBFS) via Hann window + FFT + fftshift.
#pragma once

#include <complex>
#include <vector>
#include <cstddef>

namespace mbdsdr {
namespace dsp {

/// Compute a one-sided (fft-shifted) power spectrum in dBFS.
/// input: complex IQ samples, length must be a power of two.
/// output: vector<float> of same length, ordered from -fs/2 .. +fs/2.
void powerSpectrumDbfs(const std::vector<std::complex<float>>& input,
                       std::vector<float>& output);

/// Pre-compute a Hann window of length n (called internally, exposed for reuse).
std::vector<float> makeHannWindow(std::size_t n);

} // namespace dsp
} // namespace mbdsdr
