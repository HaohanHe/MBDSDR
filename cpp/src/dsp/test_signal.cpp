// SPDX-License-Identifier: MIT
#include "test_signal.h"

#include <cmath>
#include <cstdlib>
#include <random>

namespace mbdsdr {
namespace dsp {

// Build a repeating frame of differentially encoded BPSK/QPSK symbols.
// *** SYNTHETIC TEST DATA -- NOT HARDWARE ***
void TestSignalSource::rebuildDigitalSymbols() {
    const DigMode dm = (modulation_ == "qpsk") ? DigMode::QPSK : DigMode::BPSK;
    // A fixed pseudo-random bit pattern (deterministic across runs).
    std::vector<int> bits;
    std::uint32_t s = 0x12345678u;
    const int nBits = (dm == DigMode::QPSK) ? 200 : 100;
    for (int i = 0; i < nBits; ++i) {
        s = s * 1664525u + 1013904223u;
        bits.push_back((s >> 16) & 1);
    }
    digSymbols_ = DigitalDemod::diffEncode(bits, dm);
    digSymIdx_ = 0;
    digFrac_ = 0.0;
}

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
    } else if (modulation_ == "bpsk" || modulation_ == "qpsk") {
        // *** OFFLINE SYNTHETIC DIGITAL SIGNAL -- NOT HARDWARE ***
        // Rectangular differentially-encoded BPSK/QPSK symbols modulated onto a
        // small (+3 kHz) IF offset so the source DC blocker does not remove the
        // carrier at baseband center. The downstream Costas loop pulls the
        // residual offset in. sps = fs_/symbolRate; channelizer + DigitalDemod.
        if (digSymbols_.empty()) rebuildDigitalSymbols();
        const double sps = fs_ / digSymbolRate_;
        const float amp = 0.4f * gainLin;
        const double digCarrierHz = 200.0;   // avoid DC notch, within Costas pull-in
        const double dph = 2.0 * M_PI * digCarrierHz / fs_;
        static double digPh_ = 0.0;
        for (std::size_t i = 0; i < n; ++i) {
            std::complex<float> osc(std::cos(digPh_), std::sin(digPh_));
            out[i] = digSymbols_[digSymIdx_] * amp * osc;
            digPh_ += dph;
            digFrac_ += 1.0;
            if (digFrac_ >= sps) {
                digFrac_ -= sps;
                digSymIdx_ = (digSymIdx_ + 1) % digSymbols_.size();
            }
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
