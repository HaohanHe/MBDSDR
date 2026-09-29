// SPDX-License-Identifier: MIT
// WFM (FM broadcast) stereo composite-baseband decoder.
//
// Clean-room implementation following the classical FM stereo signal model:
//
//   raw MPX (post quadrature discriminator, BEFORE de-emphasis) =
//       M(t) + P*cos(2*pi*19kHz*t) + S(t)*cos(2*pi*38kHz*t)
//   with  M = (L+R)/2  (0..15 kHz)
//         S = (L-R)/2  (23..53 kHz, DSB suppressed-carrier at 38 kHz)
//
// Recovery (see cpp/scratch/wfm_stereo_contract.md):
//   * A 19 kHz NCO is driven by a second-order (proportional+integrator) PLL.
//     The raw MPX is mixed onto the NCO's cos/sin, the I/Q baseband is low
//     passed with a narrow single pole, and the phase error is atan2(Q,I).
//     The integrator drives the static quadrature lag (from the I/Q smoothing)
//     to zero -- no pilot band-pass FIR, so no pilot-path group delay.
//   * The regenerated 38 kHz subcarrier is cos(2*phi);
//         d = 2 * rawMpx * cos(2*phi)
//     folds the DSB S down to baseband (the factor of 2 restores its unity
//     gain). M and the recovered S then traverse an *identical* 15 kHz FIR
//     low-pass and an *identical* 50 us one-pole de-emphasis, so M/S and hence
//     L/R stay sample-aligned by construction.
//   * Matrix (applied by the engine/audio layer with the smoothed blend):
//         L = M + blend*S ,  R = M - blend*S
//
// Stereo presence is judged purely from the real recovered pilot: when the
// pilot is weak / absent / noisy the blend smoothly eases back to mono.
#pragma once

#include <vector>

namespace mbdsdr {
namespace dsp {

class WfmStereoDecoder {
public:
    // ---- Tunables (fixed after offline calibration against synthetic MPX) ----
    static constexpr double kPilotFreqHz        = 19000.0;  // pilot tone
    static constexpr double kSubcarrierFreqHz   = 38000.0;  // DSB carrier
    static constexpr double kAudioCutoffHz      = 15000.0;  // M/S band limit
    static constexpr double kDeemphasisTauSec   = 50e-6;    // 50 us deemph
    static constexpr int    kAudioFirTaps       = 201;      // linear-phase LPF

    // Second-order pilot PLL (normalized to sample rate at construction).
    // Loop bandwidth chosen for fast, low-jitter lock on a clean 19 kHz tone.
    static constexpr double kPllLoopBwHz        = 20.0;     // ~ natural frequency
    static constexpr double kPllDamping         = 0.707;
    static constexpr double kIqSmoothMs        = 2.0;      // I/Q post-mix LP tau
    static constexpr double kPllMaxFreqDevHz   = 1500.0;   // NCO clamp around 19k

    // Pilot envelope (fast attack, slow decay) on 2*|I+jQ|.
    static constexpr double kPilotAttackMs     = 1.0;
    static constexpr double kPilotDecayMs       = 60.0;

    // M-level envelope used to normalise pilot strength (RMS follower).
    static constexpr double kMlevelMs           = 120.0;

    // pilotQuality = pilotAmp/(pilotAmp + mLevel): 1 when pilot dominates the
    // audio band, 0 when there is no pilot. Calibrated so a clean stereo
    // broadcast lands above HI and a noise-buried signal falls below LO.
    static constexpr double kPilotQualityLo    = 0.18;
    static constexpr double kPilotQualityHi    = 0.34;
    // "locked" additionally requires the instantaneous PLL phase error to be
    // small (radians) -- guards against a drifting loop calling stereo on a
    // borderline pilot.
    static constexpr double kLockPhaseErrRad   = 0.35;     // ~20 degrees

    // Non-symmetric blend slew: stereo engages slowly (pump-up) but drops back
    // to mono faster (honest fallback), with the settle times below.
    static constexpr double kBlendAttackMs     = 40.0;
    static constexpr double kBlendDecayMs      = 12.0;

    explicit WfmStereoDecoder(double sampleRateHz);
    void reset();

    void setEnabled(bool on);            // master switch; off => blend target 0
    void setForceMono(bool on);          // on => blend forced to 0
    bool forceMono() const { return forceMono_; }

    // Feed one block of pre-de-emphasis composite MPX (discriminator output).
    void feed(const std::vector<float>& rawMpx);

    const std::vector<float>& monoOut() const { return monoOut_; } // M, aligned
    const std::vector<float>& sideOut() const { return sideOut_; } // S, aligned

    float pilotAmplitude() const { return pilotAmp_; } // smoothed 2*|I+jQ|
    float pilotQuality()   const { return quality_; }   // normalised 0..1
    bool  locked()         const { return locked_; }    // pilot lock
    float blend()          const { return blend_; }

private:
    double fs_;
    float  omega0_ = 0.f;      // NCO angular step for 19 kHz (rad/sample)

    // PLL state
    float  phase_ = 0.f;       // NCO phase (wrapped)
    float  iqAlpha_ = 0.f;     // I/Q post-mix one-pole coefficient
    float  iI_ = 0.f, iQ_ = 0.f;
    float  kp_ = 0.f, ki_ = 0.f;
    float  integ_ = 0.f;
    float  ctrl_ = 0.f;
    float  freqClamp_ = 0.f;

    // Pilot envelope
    float  pilotAmp_ = 0.f;
    float  attackAlpha_ = 0.f, decayAlpha_ = 0.f;

    // M-level RMS follower
    float  mLevel_ = 0.f;
    float  mlevelAlpha_ = 0.f;

    // De-emphasis (identical on both paths)
    float  deAlpha_ = 0.f;
    float  deStateM_ = 0.f, deStateS_ = 0.f;

    // Blend
    bool   enabled_ = true;
    bool   forceMono_ = false;
    float  blend_ = 0.f;
    float  blendAttackStep_ = 0.f, blendDecayStep_ = 0.f;
    bool   locked_ = false;
    float  quality_ = 0.f;

    // Streaming 15 kHz FIR LPF, identical taps for M and S.
    struct Lowpass {
        std::vector<float> taps, delay;
        int n = 0;
        void init(double cutoffNorm, int taps);
        void reset();
        float push(float x);
    };
    Lowpass firM_, firS_;

    std::vector<float> sIn_;        // scratch: 2*rawMpx*cos(2*phi)
    std::vector<float> mFir_, sFir_; // scratch: post-FIR, pre-deemph
    std::vector<float> monoOut_, sideOut_;
};

} // namespace dsp
} // namespace mbdsdr
