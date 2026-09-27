// SPDX-License-Identifier: MIT
// Unified spectrum frame: the single data structure the engine produces
// and the UI consumes. No intermediate model layer.
#pragma once

#include <vector>

namespace mbdsdr {

struct SpectrumFrame {
    std::vector<float> dbfs;       // fftSize bins, already fft-shifted, dBFS
    double centerFreqHz = 0.0;     // RF center frequency (Hz)
    double sampleRateHz = 0.0;     // IQ sample rate (Hz)
    int    fftSize      = 0;       // FFT bin count
    bool   isTestSignal = true;    // ALWAYS true in Phase 1 -- NOT hardware
};

} // namespace mbdsdr
