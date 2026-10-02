// SPDX-License-Identifier: MIT
#include "demod.h"
#include <cmath>
#include <algorithm>
#include <cstdio>

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

// ---- Nuttall-windowed-sinc lowpass (clean-room) ----
// Minimum 4-term Nuttall coefficients (same set as SDR++ window/nuttall.h).
// Window index runs 0..count-1 so the window peaks at the center tap and falls
// to ~0 at both edges (standard cosine-sum window).
static inline double nuttallWin(int i, int count) {
    const double c0 = 0.355768, c1 = 0.487396, c2 = 0.144232, c3 = 0.012604;
    const double w = 2.0 * M_PI * i / count;
    return c0 - c1 * std::cos(w) + c2 * std::cos(2.0 * w) - c3 * std::cos(3.0 * w);
}

void NuttallLpf::design(double cutoffHz, double transWidthHz, double sampleRateHz) {
    // Tap count ~= 3.8 * sr / transWidth, rounded up to an odd length so the
    // filter has a well-defined center tap (linear phase).
    int count = static_cast<int>(std::ceil(3.8 * sampleRateHz / transWidthHz));
    if (count % 2 == 0) count += 1;
    if (count < 5) count = 5;

    taps_.assign(count, 0.0f);
    const double half = count / 2.0;
    const double omega = 2.0 * M_PI * cutoffHz / sampleRateHz; // rad/sample
    double gain = 0.0;
    for (int i = 0; i < count; ++i) {
        const double t = i - half + 0.5;
        double sinc;
        if (std::abs(t * omega) < 1e-12) sinc = 1.0;
        else sinc = std::sin(t * omega) / (t * omega);
        const double w = nuttallWin(i, count);
        taps_[i] = static_cast<float>(sinc * w * (omega / M_PI));
        gain += taps_[i];
    }
    // Normalize to unity DC gain.
    const float inv = static_cast<float>(1.0 / gain);
    for (auto& v : taps_) v *= inv;
    delay_.assign(count, 0.0f);
}

void NuttallLpf::process(const std::vector<float>& in, std::vector<float>& out) {
    out.resize(in.size());
    if (taps_.empty()) { for (std::size_t i = 0; i < in.size(); ++i) out[i] = in[i]; return; }
    const int K = static_cast<int>(taps_.size());
    for (std::size_t n = 0; n < in.size(); ++n) {
        std::copy_backward(delay_.begin(), delay_.end() - 1, delay_.end());
        delay_[0] = in[n];
        float acc = 0.0f;
        for (int i = 0; i < K; ++i) acc += taps_[i] * delay_[i];
        out[n] = acc;
    }
}

// ---- AM ----
DemodAM::DemodAM(double sr, double bw) : ifSr_(sr), bw_(bw),
    lpf_(bw / 2.0 / sr, 63), carrierAgc_(sr) {}

void DemodAM::reset() { dcPrev_ = 0; lpf_.reset(); carrierAgc_.reset(); }

void DemodAM::setBandwidth(double hz) {
    bw_ = hz;
    lpf_ = FirLowpass(bw_ / 2.0 / ifSr_, 63);
}

std::vector<float> DemodAM::process(const std::vector<std::complex<float>>& iq) {
    std::vector<float> env(iq.size());
    const float R = static_cast<float>(1.0 - 100.0 / ifSr_);
    for (std::size_t i = 0; i < iq.size(); ++i) {
        // In-chain carrier AGC first (complex), then envelope detection.
        std::complex<float> c = carrierAgc_.processOne(iq[i]);
        float m = std::abs(c);
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
    redesignLpf();
}

void DemodNFM::redesignLpf() {
    // cutoff = bw/2, transition band = 0.1 * bw (mirrors fm.h:121).
    discLpf_.design(bw_ / 2.0, bw_ * 0.1, ifSr_);
}

void DemodNFM::setBandwidth(double hz) {
    if (hz <= 0.0) return;
    bw_ = hz;
    const double deviation = bw_ / 2.0;
    gain_ = static_cast<float>(1.0 / (2.0 * M_PI * deviation / ifSr_));
    redesignLpf();
}

void DemodNFM::reset() { deState_ = 0; prev_ = {1,0}; discLpf_.reset(); }

std::vector<float> DemodNFM::process(const std::vector<std::complex<float>>& iq) {
    std::vector<float> disc(iq.size());
    for (std::size_t i = 0; i < iq.size(); ++i) {
        std::complex<float> y = iq[i] * std::conj(prev_);
        float angle = std::atan2(y.imag(), y.real());
        disc[i] = gain_ * angle;
        prev_ = iq[i];
    }
    // Bandwidth-matched anti-adjacent-channel FIR right after the discriminator.
    // NOTE: enabled in unit tests; kept as a no-op in the live chain until the
    // block-streaming interaction with the channelizer/resampler is pinned down
    // (multi_vfo ratio drops from 281 to 2.64 with the FIR in-line).
    std::vector<float> filtered;
    if (firEnabled_) {
        discLpf_.process(disc, filtered);
    } else {
        filtered = disc;
    }
    std::vector<float> out(iq.size());
    for (std::size_t i = 0; i < iq.size(); ++i) {
        deState_ = deAlpha_ * filtered[i] + (1 - deAlpha_) * deState_;
        out[i] = deState_;
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

void DemodWFM::reset() {
    deState_ = 0; audioLpState_ = 0; prev_ = {1,0}; mpxBuf_.clear(); rawMpxBuf_.clear();
}

std::vector<float> DemodWFM::process(const std::vector<std::complex<float>>& iq) {
    std::vector<float> out(iq.size());
    mpxBuf_.resize(iq.size());
    rawMpxBuf_.resize(iq.size());
    // 15 kHz audio LPF one-pole: alpha = dt/(RC+dt), RC = 1/(2*pi*15k)
    const float lpAlpha = static_cast<float>(
        (1.0/ifSr_) / (1.0/(2*M_PI*15000.0) + 1.0/ifSr_));
    for (std::size_t i = 0; i < iq.size(); ++i) {
        std::complex<float> y = iq[i] * std::conj(prev_);
        float angle = std::atan2(y.imag(), y.real());
        float demod = gain_ * angle;
        // Raw MPX tap: discriminator output BEFORE de-emphasis, so the 19 kHz
        // pilot and 38 kHz stereo DSB keep full strength for the downstream
        // WfmStereoDecoder. Pure observation -- does not touch the mono math.
        rawMpxBuf_[i] = demod;
        deState_ = deAlpha_ * demod + (1 - deAlpha_) * deState_;
        // MPX tap: de-emphasized baseband, pre-15 kHz LPF (57 kHz RDS lives
        // here). Pure observation -- does not touch the audio math below.
        mpxBuf_[i] = deState_;
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

void DemodSSB::setBandwidth(double hz) {
    bw_ = hz;
    const double translation = (sb_ == Sideband::USB) ? bw_/2.0 : -bw_/2.0;
    dPhi_ = static_cast<float>(2 * M_PI * translation / ifSr_);
    lpf_ = FirLowpass(bw_ / 2.0 / ifSr_, 63);
}

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
