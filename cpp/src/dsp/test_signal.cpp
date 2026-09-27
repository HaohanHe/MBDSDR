// SPDX-License-Identifier: MIT
#include "test_signal.h"

#include <cmath>
#include <cstdlib>

namespace mbdsdr {
namespace dsp {

TestSignalSource::TestSignalSource(double sampleRateHz, double centerFreqHz)
    : fs_(sampleRateHz), f0_(centerFreqHz) {}

bool TestSignalSource::start() { return true; }
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

    const float gainLin = std::pow(10.0f, static_cast<float>(gainDb_) / 20.0f);
    const double twoPiFs = 2.0 * M_PI / fs_;

    if (modulation_ == "am") {
        // Carrier at +10 kHz offset, AM-modulated by 1 kHz sine, m=0.5
        const double fc = 10e3, faudio = 1e3, m = 0.5;
        for (std::size_t i = 0; i < n; ++i) {
            const double t = static_cast<double>(counter_++);
            float env = static_cast<float>(1.0 + m * std::cos(2*M_PI*faudio*t/fs_));
            float c = static_cast<float>(std::cos(twoPiFs*fc*t));
            float s = static_cast<float>(std::sin(twoPiFs*fc*t));
            out[i] = std::complex<float>(env*c, env*s) * gainLin * 0.5f;
        }
    } else if (modulation_ == "fm") {
        // Carrier at center, FM-modulated by 1 kHz, deviation 3 kHz
        const double faudio = 1e3, fdev = 3e3;
        double phase = 0;
        for (std::size_t i = 0; i < n; ++i) {
            const double t = static_cast<double>(counter_++);
            phase += 2*M_PI*fdev/fs_ * std::sin(2*M_PI*faudio*t/fs_);
            out[i] = std::complex<float>(static_cast<float>(std::cos(phase)),
                                         static_cast<float>(std::sin(phase))) * gainLin * 0.5f;
        }
    } else {
        // "tone": dual tones + noise (default for spectrum display)
        const float amp1 = 0.35f * gainLin;
        const float amp2 = 0.20f * gainLin;
        const float noiseStd = 0.05f * gainLin;
        const double off1 = 200e3, off2 = 500e3;
        for (std::size_t i = 0; i < n; ++i) {
            const double t = static_cast<double>(counter_++);
            float re = amp1*std::cos(twoPiFs*off1*t) + amp1*std::cos(-twoPiFs*off1*t)
                     + amp2*std::cos(twoPiFs*off2*t) + amp2*std::cos(-twoPiFs*off2*t);
            float im = amp1*std::sin(twoPiFs*off1*t) + amp1*std::sin(-twoPiFs*off1*t)
                     + amp2*std::sin(twoPiFs*off2*t) + amp2*std::sin(-twoPiFs*off2*t);
            re += noiseStd * nextGaussian();
            im += noiseStd * nextGaussian();
            out[i] = std::complex<float>(re, im);
        }
    }
    return n;
}

} // namespace dsp
} // namespace mbdsdr
