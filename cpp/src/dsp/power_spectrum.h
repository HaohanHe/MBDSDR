// SPDX-License-Identifier: MIT
// Power spectrum (dBFS) via selectable window + FFT + fftshift, with
// optional frame averaging.
#pragma once

#include <complex>
#include <vector>
#include <cstddef>

namespace mbdsdr {
namespace dsp {

/// Compute a one-sided (fft-shifted) power spectrum in dBFS (Hann window,
/// no averaging). Kept for backwards compat / unit tests.
void powerSpectrumDbfs(const std::vector<std::complex<float>>& input,
                       std::vector<float>& output);
std::vector<float> makeHannWindow(std::size_t n);

// Stateful power spectrum: selectable window + frame averaging.
class PowerSpectrum {
public:
    enum Window { Hann, Flattop, Blackman };
    enum Average { Off, Slow, Fast };   // Slow ~16-frame, Fast ~4-frame average

    void setWindow(Window w);
    void setAverage(Average a);
    Window window() const { return win_; }
    Average average() const { return avg_; }
    // input: complex IQ (power-of-two length); output: dBFS, fft-shifted.
    void process(const std::vector<std::complex<float>>& input,
                 std::vector<float>& output);

private:
    void rebuildWindow(std::size_t n);
    Window win_ = Hann;
    Average avg_ = Off;
    std::vector<float> window_;
    std::size_t windowLen_ = 0;
    std::vector<std::vector<float>> ring_;   // ring of linear power frames
    int ringIdx_ = 0;
};

} // namespace dsp
} // namespace mbdsdr
