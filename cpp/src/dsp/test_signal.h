// SPDX-License-Identifier: MIT
// OFFLINE test IQ signal generator.
//
// *** TEST DATA -- NOT HARDWARE ***
// This class generates synthetic complex IQ samples for development and
// visual verification of the spectrum path. It is NOT connected to any
// SDR hardware. All UI surfaces MUST label data as "TEST SIGNAL".
#pragma once

#include <complex>
#include <vector>
#include <cstddef>
#include <cstdint>

namespace mbdsdr {
namespace dsp {

class TestSignalGenerator {
public:
    /// @param sampleRate_hz  IQ sample rate (Hz), default 2.4 MHz.
    /// @param centerFreq_hz  RF center frequency (Hz), default 98.5 MHz.
    /// Two tones at baseband offsets +/-200 kHz and +/-500 kHz, plus
    /// additive Gaussian white noise, are synthesized.
    TestSignalGenerator(double sampleRate_hz = 2.4e6,
                        double centerFreq_hz = 98.5e6);

    /// Fill `out` with the next `n` samples (continuing from internal phase).
    void next(std::vector<std::complex<float>>& out, std::size_t n);

    double sampleRate() const { return fs_; }
    double centerFreq() const { return f0_; }

    /// Convenience: baseband tone offsets (Hz) this generator emits.
    static std::vector<double> toneOffsetsHz() { return {-200e3, -500e3, 200e3, 500e3}; }

private:
    double fs_;
    double f0_;
    std::uint64_t counter_ = 0;

    // Gaussian noise via Box-Muller (cached second value)
    bool   haveSpare_ = false;
    float  spare_ = 0.0f;
    float  nextGaussian();
};

} // namespace dsp
} // namespace mbdsdr
