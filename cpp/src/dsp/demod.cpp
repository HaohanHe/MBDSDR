// SPDX-License-Identifier: MIT
#include "demod.h"
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

// ---- FIR lowpass (Hann-windowed sinc) ----
FirLowpass::FirLowpass(double cutoffNorm, int numTaps)
    : numTaps_(numTaps) {
    taps_.resize(numTaps_);
    const double M = numTaps_ - 1;
    for (int i = 0; i < numTaps_; ++i) {
        const double x = i - M/2.0;
        double sinc = (x == 0) ? 1.0 : std::sin(2*M_PI*cutoffNorm*x) / (M_PI*x);
        double hann = 0.5 * (1.0 - std::cos(2*M_PI*i/M));
        taps_[i] = static_cast<float>(sinc * hann);
    }
    delayLine_.assign(numTaps_, 0.0f);
}

void FirLowpass::process(const std::vector<float>& in, std::vector<float>& out) {
    out.resize(in.size());
    for (std::size_t n = 0; n < in.size(); ++n) {
        // Shift delay line
        std::copy_backward(delayLine_.begin(), delayLine_.end()-1, delayLine_.end());
        delayLine_[0] = in[n];
        float acc = 0;
        for (int i = 0; i < numTaps_; ++i) acc += taps_[i] * delayLine_[i];
        out[n] = acc;
    }
}

// ---- AM ----
DemodAM::DemodAM(double sr, double bw) : ifSr_(sr), bw_(bw),
    lpf_(bw / 2.0 / sr, 63) {}

void DemodAM::reset() { dcPrev_ = 0; lpf_.reset(); }

std::vector<float> DemodAM::process(const std::vector<std::complex<float>>& iq) {
    std::vector<float> env(iq.size());
    const float R = static_cast<float>(1.0 - 100.0 / ifSr_);
    for (std::size_t i = 0; i < iq.size(); ++i) {
        float m = std::abs(iq[i]);
        // DC blocker
        float y = m - dcPrev_ + R * dcPrev_;
        dcPrev_ = y;
        env[i] = y;
    }
    std::vector<float> out;
    lpf_.process(env, out);
    return out;
}

// ---- NFM ----
DemodNFM::DemodNFM(double sr, double bw) : ifSr_(sr), bw_(bw) {
    const double deviation = bw / 2.0;
    gain_ = static_cast<float>(1.0 / (2.0 * M_PI * deviation / sr));
    const double tau = 50e-6, dt = 1.0 / sr;
    deAlpha_ = static_cast<float>(dt / (tau + dt));
}

void DemodNFM::reset() { deState_ = 0; prev_ = {1,0}; }

std::vector<float> DemodNFM::process(const std::vector<std::complex<float>>& iq) {
    std::vector<float> out(iq.size());
    for (std::size_t i = 0; i < iq.size(); ++i) {
        std::complex<float> y = iq[i] * std::conj(prev_);
        float angle = std::atan2(y.imag(), y.real());
        float demod = gain_ * angle;
        // de-emphasis
        deState_ = deAlpha_ * demod + (1 - deAlpha_) * deState_;
        out[i] = deState_;
        prev_ = iq[i];
    }
    return out;
}

// ---- WFM ----
DemodWFM::DemodWFM(double sr, double bw) : ifSr_(sr), bw_(bw) {
    const double deviation = 75000.0;
    gain_ = static_cast<float>(1.0 / (2.0 * M_PI * deviation / sr));
    const double tau = 50e-6, dt = 1.0 / sr;
    deAlpha_ = static_cast<float>(dt / (tau + dt));
}

void DemodWFM::reset() { deState_ = 0; audioLpState_ = 0; prev_ = {1,0}; }

std::vector<float> DemodWFM::process(const std::vector<std::complex<float>>& iq) {
    std::vector<float> out(iq.size());
    // 15 kHz audio LPF one-pole: alpha = dt/(RC+dt), RC = 1/(2*pi*15k)
    const float lpAlpha = static_cast<float>(
        (1.0/ifSr_) / (1.0/(2*M_PI*15000.0) + 1.0/ifSr_));
    for (std::size_t i = 0; i < iq.size(); ++i) {
        std::complex<float> y = iq[i] * std::conj(prev_);
        float angle = std::atan2(y.imag(), y.real());
        float demod = gain_ * angle;
        deState_ = deAlpha_ * demod + (1 - deAlpha_) * deState_;
        audioLpState_ = lpAlpha * deState_ + (1 - lpAlpha) * audioLpState_;
        out[i] = audioLpState_;
        prev_ = iq[i];
    }
    return out;
}

// ---- SSB ----
DemodSSB::DemodSSB(Sideband sb, double sr, double bw)
    : sb_(sb), ifSr_(sr), bw_(bw),
      lpf_(bw / 2.0 / sr, 63) {
    const double translation = (sb == Sideband::USB) ? bw/2.0 : -bw/2.0;
    dPhi_ = static_cast<float>(2 * M_PI * translation / sr);
}

QString DemodSSB::name() const {
    return (sb_ == Sideband::USB) ? QStringLiteral("USB") : QStringLiteral("LSB");
}

void DemodSSB::reset() { phase_ = 0; lpf_.reset(); }

std::vector<float> DemodSSB::process(const std::vector<std::complex<float>>& iq) {
    std::vector<float> mixed(iq.size());
    for (std::size_t i = 0; i < iq.size(); ++i) {
        std::complex<float> osc(std::cos(phase_), std::sin(phase_));
        mixed[i] = (iq[i] * osc).real();
        phase_ += dPhi_;
    }
    std::vector<float> out;
    lpf_.process(mixed, out);
    return out;
}

} // namespace dsp
} // namespace mbdsdr
