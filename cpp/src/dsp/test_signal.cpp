// SPDX-License-Identifier: MIT
#include "test_signal.h"

#include <cmath>
#include <cstdlib>

namespace mbdsdr {
namespace dsp {

TestSignalGenerator::TestSignalGenerator(double sampleRate_hz,
                                         double centerFreq_hz)
    : fs_(sampleRate_hz), f0_(centerFreq_hz) {}

float TestSignalGenerator::nextGaussian() {
    if (haveSpare_) {
        haveSpare_ = false;
        return spare_;
    }
    float u = 0.0f, v = 0.0f, s = 0.0f;
    // reject zeros to avoid log(0)
    do {
        u = 2.0f * (static_cast<float>(rand()) / RAND_MAX) - 1.0f;
        v = 2.0f * (static_cast<float>(rand()) / RAND_MAX) - 1.0f;
        s = u * u + v * v;
    } while (s >= 1.0f || s == 0.0f);
    const float mag = std::sqrt(-2.0f * std::log(s) / s);
    spare_ = v * mag;
    haveSpare_ = true;
    return u * mag;
}

void TestSignalGenerator::next(std::vector<std::complex<float>>& out,
                               std::size_t n) {
    out.resize(n);
    // Tone amplitudes (relative, not calibrated to hardware)
    constexpr float amp1 = 0.35f;  // +/-200 kHz
    constexpr float amp2 = 0.20f;  // +/-500 kHz
    constexpr float noiseStd = 0.05f;

    const double twoPiFs = 2.0 * M_PI / fs_;
    const double off1 = 200e3;
    const double off2 = 500e3;

    for (std::size_t i = 0; i < n; ++i) {
        const double t = static_cast<double>(counter_++);
        // Four tones: +200k, -200k, +500k, -500k
        float re = amp1 * std::cos(twoPiFs * off1 * t)
                 + amp1 * std::cos(-twoPiFs * off1 * t)
                 + amp2 * std::cos(twoPiFs * off2 * t)
                 + amp2 * std::cos(-twoPiFs * off2 * t);
        float im = amp1 * std::sin(twoPiFs * off1 * t)
                 + amp1 * std::sin(-twoPiFs * off1 * t)
                 + amp2 * std::sin(twoPiFs * off2 * t)
                 + amp2 * std::sin(-twoPiFs * off2 * t);
        // Add Gaussian noise (I and Q independent)
        re += noiseStd * nextGaussian();
        im += noiseStd * nextGaussian();
        out[i] = std::complex<float>(re, im);
    }
}

} // namespace dsp
} // namespace mbdsdr
