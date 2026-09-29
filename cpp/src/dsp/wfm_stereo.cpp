// SPDX-License-Identifier: MIT
#include "wfm_stereo.h"

#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

// ---- Streaming linear-phase windowed-sinc low-pass -------------------------
void WfmStereoDecoder::Lowpass::init(double cutoffNorm, int numTaps) {
    n = numTaps;
    taps.resize(n);
    delay.assign(n, 0.0f);
    const double M = n - 1;
    double sum = 0.0;
    for (int i = 0; i < n; ++i) {
        const double x = i - M / 2.0;
        double sinc = (x == 0.0) ? 1.0 : std::sin(2.0 * M_PI * cutoffNorm * x) / (M_PI * x);
        double hann = 0.5 * (1.0 - std::cos(2.0 * M_PI * i / M));
        taps[i] = static_cast<float>(sinc * hann);
        sum += taps[i];
    }
    // Normalize to unity in-band gain (the windowed-sinc sum is not exactly 1).
    const float inv = static_cast<float>(1.0 / sum);
    for (int i = 0; i < n; ++i) taps[i] *= inv;
}

void WfmStereoDecoder::Lowpass::reset() {
    std::fill(delay.begin(), delay.end(), 0.0f);
}

float WfmStereoDecoder::Lowpass::push(float x) {
    std::copy_backward(delay.begin(), delay.end() - 1, delay.end());
    delay[0] = x;
    float acc = 0.0f;
    for (int i = 0; i < n; ++i) acc += taps[i] * delay[i];
    return acc;
}

// ---------------------------------------------------------------------------
WfmStereoDecoder::WfmStereoDecoder(double sampleRateHz) : fs_(sampleRateHz) {
    omega0_ = static_cast<float>(2.0 * M_PI * kPilotFreqHz / fs_);

    // I/Q post-mix single-pole (narrow: rejects the 2*19k=38k product and any
    // out-of-band energy, leaving a clean pilot phasor for atan2).
    {
        const double dt = 1.0 / fs_;
        const double tau = kIqSmoothMs * 1e-3;
        iqAlpha_ = static_cast<float>(dt / (tau + dt));
    }

    // Second-order loop: standard proportional+integrator coefficients with
    // damping zeta, natural frequency wn (rad/sample).
    {
        const double wn = 2.0 * M_PI * kPllLoopBwHz / fs_;
        kp_ = static_cast<float>(2.0 * kPllDamping * wn);
        ki_ = static_cast<float>(wn * wn);
        freqClamp_ = static_cast<float>(2.0 * M_PI * kPllMaxFreqDevHz / fs_);
    }

    // Pilot envelope follower coefficients.
    {
        const double dt = 1.0 / fs_;
        attackAlpha_ = static_cast<float>(dt / (kPilotAttackMs * 1e-3 + dt));
        decayAlpha_  = static_cast<float>(dt / (kPilotDecayMs  * 1e-3 + dt));
    }

    // M-level RMS follower (one-pope on the squared signal).
    {
        const double dt = 1.0 / fs_;
        mlevelAlpha_ = static_cast<float>(dt / (kMlevelMs * 1e-3 + dt));
    }

    // 50 us de-emphasis, identical both paths.
    {
        const double dt = 1.0 / fs_;
        deAlpha_ = static_cast<float>(dt / (kDeemphasisTauSec + dt));
    }

    // Blend slew steps per sample.
    {
        const double dt = 1.0 / fs_;
        blendAttackStep_ = static_cast<float>(dt / (kBlendAttackMs * 1e-3 + dt));
        blendDecayStep_  = static_cast<float>(dt / (kBlendDecayMs  * 1e-3 + dt));
    }

    // Identical 15 kHz FIR on both M and S paths (normalized half-cutoff).
    const double cutoffNorm = kAudioCutoffHz / fs_;
    firM_.init(cutoffNorm, kAudioFirTaps);
    firS_.init(cutoffNorm, kAudioFirTaps);
}

void WfmStereoDecoder::reset() {
    phase_ = 0.f;
    iI_ = iQ_ = 0.f;
    integ_ = 0.f;
    ctrl_ = 0.f;
    pilotAmp_ = 0.f;
    mLevel_ = 0.f;
    deStateM_ = deStateS_ = 0.f;
    blend_ = 0.f;
    locked_ = false;
    quality_ = 0.f;
    firM_.reset();
    firS_.reset();
}

void WfmStereoDecoder::setEnabled(bool on) { enabled_ = on; }
void WfmStereoDecoder::setForceMono(bool on) { forceMono_ = on; }

void WfmStereoDecoder::feed(const std::vector<float>& rawMpx) {
    const std::size_t n = rawMpx.size();
    if (n == 0) return;
    sIn_.resize(n);
    mFir_.resize(n);
    sFir_.resize(n);
    monoOut_.resize(n);
    sideOut_.resize(n);

    for (std::size_t i = 0; i < n; ++i) {
        const float x = rawMpx[i];

        // Advance the NCO first (using the loop filter from the previous
        // sample), then derive the in-phase / quadrature LO and the regenerated
        // 38 kHz carrier from the *same* phase_ -- using them on different
        // phase samples would put a one-sample (here ~57 deg at 38 kHz) skew
        // between the pilot reference and the DSB down-conversion.
        phase_ += omega0_ + ctrl_;
        const float TWO_PI = static_cast<float>(2.0 * M_PI);
        while (phase_ >  static_cast<float>(M_PI)) phase_ -= TWO_PI;
        while (phase_ < -static_cast<float>(M_PI)) phase_ += TWO_PI;

        const float ci = std::cos(phase_);
        const float si = std::sin(phase_);
        iI_ += iqAlpha_ * (x * ci - iI_);
        iQ_ += iqAlpha_ * (x * si - iQ_);

        float err = std::atan2(iQ_, iI_);   // = NCO phase - pilot phase
        integ_ += ki_ * err;
        // Clamp the integrated frequency pull to stop wind-up when no pilot.
        if (integ_ >  freqClamp_) integ_ =  freqClamp_;
        if (integ_ < -freqClamp_) integ_ = -freqClamp_;
        // Negative feedback: if the NCO is ahead (err>0) slow it back down.
        ctrl_ = -(kp_ * err + integ_);
        if (ctrl_ >  freqClamp_) ctrl_ =  freqClamp_;
        if (ctrl_ < -freqClamp_) ctrl_ = -freqClamp_;

        // ---- Pilot envelope: 2*|I+jQ|, attack fast / decay slow ----
        const float mag = std::sqrt(iI_ * iI_ + iQ_ * iQ_);
        const float instPilot = 2.0f * mag;
        if (instPilot > pilotAmp_)
            pilotAmp_ += attackAlpha_ * (instPilot - pilotAmp_);
        else
            pilotAmp_ += decayAlpha_ * (instPilot - pilotAmp_);

        // ---- Regenerated 38 kHz subcarrier down-converts the DSB to S ----
        sIn_[i] = 2.0f * x * std::cos(2.0f * phase_);
        mFir_[i] = firM_.push(x);          // M path: raw MPX through 15k LPF
        sFir_[i] = firS_.push(sIn_[i]);    // S path: down-converted, same LPF
    }

    // ---- De-emphasis (identical one-pole) + M-level RMS follower ----
    float mLevelSmooth = mLevel_;
    for (std::size_t i = 0; i < n; ++i) {
        deStateM_ += deAlpha_ * (mFir_[i] - deStateM_);
        deStateS_ += deAlpha_ * (sFir_[i] - deStateS_);
        monoOut_[i] = deStateM_;
        sideOut_[i] = deStateS_;

        const float e = deStateM_;
        mLevelSmooth += mlevelAlpha_ * (e * e - mLevelSmooth);
    }
    mLevel_ = mLevelSmooth;
    const float mRms = std::sqrt(mLevel_ > 0.f ? mLevel_ : 0.f);

    // ---- Pilot quality: pilot amplitude normalised to the audio-band level ----
    const float denom = pilotAmp_ + mRms;
    quality_ = (denom > 1e-6f) ? pilotAmp_ / denom : 0.f;
    if (quality_ > 1.f) quality_ = 1.f;

    // ---- Lock: once quality reaches LO AND the loop is sitting on a small
    // instantaneous phase error, treat the pilot as locked. Note this is
    // decoupled from HI: HI only sets the *top* of the blend ramp, so that a
    // mid-quality pilot locks and blends proportionally instead of snapping.
    const float errNow = std::atan2(iQ_, iI_);
    locked_ = (quality_ >= static_cast<float>(kPilotQualityLo)) &&
              (std::fabs(errNow) < static_cast<float>(kLockPhaseErrRad));

    // ---- Blend target, strictly linear with pilot quality:
    //   quality <= LO  -> 0   (mono)
    //   quality >= HI  -> 1   (full stereo)
    //   LO < quality < HI -> (quality-LO)/(HI-LO)  (proportional blend)
    // Only applied when the pilot is actually locked; otherwise eased to mono.
    float target = 0.f;
    if (enabled_ && !forceMono_ && locked_) {
        const double q = quality_;
        if (q >= kPilotQualityHi)
            target = 1.f;
        else if (q <= kPilotQualityLo)
            target = 0.f;
        else
            target = static_cast<float>(
                (q - kPilotQualityLo) / (kPilotQualityHi - kPilotQualityLo));
    }

    // ---- Non-symmetric slew, per sample (the block holds n samples) ----
    for (std::size_t i = 0; i < n; ++i) {
        if (target > blend_)
            blend_ += blendAttackStep_ * (target - blend_);
        else
            blend_ += blendDecayStep_ * (target - blend_);
    }
    if (blend_ < 0.f) blend_ = 0.f;
    if (blend_ > 1.f) blend_ = 1.f;
}

} // namespace dsp
} // namespace mbdsdr
