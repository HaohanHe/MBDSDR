// SPDX-License-Identifier: MIT
#include "test_signal.h"

#include <cmath>
#include <cstdlib>

namespace mbdsdr {
namespace dsp {

TestSignalSource::TestSignalSource(double sampleRateHz, double centerFreqHz)
    : fs_(sampleRateHz), f0_(centerFreqHz) {}

bool TestSignalSource::start() { return true; }  // always available
void TestSignalSource::stop()  {}

void TestSignalSource::setCenterFreq(double freqHz) { f0_ = freqHz; }
void TestSignalSource::setSampleRate(double rateHz) { fs_ = rateHz; }
void TestSignalSource::setGain(double gainDb) { gainDb_ = gainDb; }

float TestSignalSource::nextGaussian() {
    if (haveSpare_) { haveSpare_ = false; return spare_; }
    float u = 0, v = 0, s = 0;
    do {
        u = 2.0f * (static_cast<float>(rand()) / RAND_MAX) - 1.0f;
        v = 2.0f * (static_cast<float>(rand()) / RAND_MAX) - 1.0f;
        s = u*u + v*v;
    } while (s >= 1.0f || s == 0.0f);
    const float mag = std::sqrt(-2.0f * std::log(s) / s);
    spare_ = v * mag;
    haveSpare_ = true;
    return u * mag;
}

std::size_t TestSignalSource::readIQ(std::vector<std::complex<float>>& out) {
    const std::size_t n = out.size();
    if (n == 0) return 0;

    // Gain scales tone amplitudes (dB -> linear, 0 dB = nominal)
    const float gainLin = std::pow(10.0f, static_cast<float>(gainDb_) / 20.0f);
    const float amp1 = 0.35f * gainLin;  // +/-200 kHz
    const float amp2 = 0.20f * gainLin;  // +/-500 kHz
    const float noiseStd = 0.05f * gainLin;

    const double twoPiFs = 2.0 * M_PI / fs_;
    const double off1 = 200e3;
    const double off2 = 500e3;

    for (std::size_t i = 0; i < n; ++i) {
        const double t = static_cast<double>(counter_++);
        float re = amp1 * std::cos(twoPiFs * off1 * t)
                 + amp1 * std::cos(-twoPiFs * off1 * t)
                 + amp2 * std::cos(twoPiFs * off2 * t)
                 + amp2 * std::cos(-twoPiFs * off2 * t);
        float im = amp1 * std::sin(twoPiFs * off1 * t)
                 + amp1 * std::sin(-twoPiFs * off1 * t)
                 + amp2 * std::sin(twoPiFs * off2 * t)
                 + amp2 * std::sin(-twoPiFs * off2 * t);
        re += noiseStd * nextGaussian();
        im += noiseStd * nextGaussian();
        out[i] = std::complex<float>(re, im);
    }
    return n;
}

} // namespace dsp
} // namespace mbdsdr
