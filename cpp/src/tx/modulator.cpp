// SPDX-License-Identifier: MIT
#include "modulator.h"

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace tx {

// ---- Hilbert 90-degree FIR (type III, Hann windowed) ----
HilbertFir::HilbertFir(int numTaps)
    : numTaps_(numTaps), center_((numTaps - 1) / 2) {
    taps_.assign(numTaps_, 0.0f);
    line_.assign(numTaps_, 0.0f);
    const double M = numTaps_ - 1;
    for (int i = 0; i < numTaps_; ++i) {
        const int k = i - center_;
        double h = 0.0;
        if (k != 0 && (k & 1)) h = 2.0 / (M_PI * k);  // odd offsets only
        const double hann = 0.5 - 0.5 * std::cos(2 * M_PI * i / M);
        taps_[i] = static_cast<float>(h * hann);
    }
}

float HilbertFir::pushQ(float x) {
    std::copy_backward(line_.begin(), line_.end() - 1, line_.end());
    line_[0] = x;
    float acc = 0.0f;
    for (int i = 0; i < numTaps_; ++i) acc += taps_[i] * line_[i];
    return acc;
}

float HilbertFir::pushI(float x) {
    // Direct path delayed by the FIR group delay so I and Q stay aligned.
    std::copy_backward(line_.begin(), line_.begin() + center_,
                       line_.begin() + center_ + 1);
    line_[0] = x;
    return line_[center_];
}

void HilbertFir::reset() {
    std::fill(line_.begin(), line_.end(), 0.0f);
}

// ---- AM ----
ModulatorAM::ModulatorAM(double /*sampleRate*/, double /*bandwidth*/)
    : depth_(0.9f) {}

std::vector<std::complex<float>> ModulatorAM::process(
        const std::vector<float>& audio) {
    std::vector<std::complex<float>> iq(audio.size());
    for (std::size_t i = 0; i < audio.size(); ++i) {
        const float carrier = 1.0f + depth_ * audio[i];
        iq[i] = std::complex<float>(carrier, 0.0f);
    }
    return iq;
}

// ---- FM (NFM / WFM) ----
ModulatorFM::ModulatorFM(double sampleRate, double bandwidth, bool wide) {
    const double deviation = wide ? 75000.0 : bandwidth / 2.0;
    dphiPerUnit_ = static_cast<float>(2.0 * M_PI * deviation / sampleRate);
    const double tau = 50e-6, dt = 1.0 / sampleRate;
    preAlpha_ = static_cast<float>(dt / (tau + dt));
    reset();
}

void ModulatorFM::reset() {
    phase_ = 0.0f;
    prePrev_ = 0.0f;
}

std::vector<std::complex<float>> ModulatorFM::process(
        const std::vector<float>& audio) {
    std::vector<std::complex<float>> iq(audio.size());
    const float a = preAlpha_;
    for (std::size_t i = 0; i < audio.size(); ++i) {
        // Pre-emphasis = exact inverse of the demod's de-emphasis one-pole:
        // de-emph: y = a*x + (1-a)*y_prev  =>  x = (y - (1-a)*y_prev)/a.
        const float x = (audio[i] - (1.0f - a) * prePrev_) / a;
        prePrev_ = audio[i];
        phase_ += dphiPerUnit_ * x;
        // Wrap phase to avoid loss of float precision on long transmissions.
        phase_ = std::atan2(std::sin(phase_), std::cos(phase_));
        iq[i] = std::complex<float>(std::cos(phase_), std::sin(phase_));
    }
    return iq;
}

// ---- SSB (Hilbert phasing method) ----
ModulatorSSB::ModulatorSSB(Sideband sb, double sampleRate, double bandwidth)
    : sb_(sb), hilbert_(63) {
    // Shift sign is the opposite of the demod's translation sign.
    const double sign = (sb == Sideband::USB) ? -1.0 : +1.0;
    dphi_ = static_cast<float>(sign * 2.0 * M_PI * (bandwidth / 2.0) / sampleRate);
    reset();
}

void ModulatorSSB::reset() {
    phase_ = 0.0f;
    hilbert_.reset();
}

std::vector<std::complex<float>> ModulatorSSB::process(
        const std::vector<float>& audio) {
    std::vector<std::complex<float>> iq(audio.size());
    for (std::size_t i = 0; i < audio.size(); ++i) {
        const float direct = hilbert_.pushI(audio[i]);
        const float quadr = hilbert_.pushQ(audio[i]);
        std::complex<float> analytic(direct, (sb_ == Sideband::USB) ? quadr : -quadr);
        const std::complex<float> osc(std::cos(phase_), std::sin(phase_));
        iq[i] = analytic * osc;
        phase_ += dphi_;
    }
    return iq;
}

// ---- CW ----
ModulatorCW::ModulatorCW(double sampleRate, double toneOffsetHz) {
    dphi_ = static_cast<float>(2.0 * M_PI * toneOffsetHz / sampleRate);
    reset();
}

void ModulatorCW::reset() {
    phase_ = 0.0f;
}

std::vector<std::complex<float>> ModulatorCW::process(
        const std::vector<float>& key) {
    std::vector<std::complex<float>> iq(key.size());
    for (std::size_t i = 0; i < key.size(); ++i) {
        const float env = key[i] > 0.5f ? 1.0f : 0.0f;
        iq[i] = env * std::complex<float>(std::cos(phase_), std::sin(phase_));
        phase_ += dphi_;
    }
    return iq;
}

} // namespace tx
} // namespace mbdsdr
