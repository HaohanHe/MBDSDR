// SPDX-License-Identifier: MIT
//
// Audio Noise Reduction (ANR) — post-demodulation narrow-band audio noise
// suppressor for the MBDSDR desktop SDR.
//
// ============================================================================
// SCOPE / DISTINCTION FROM NoiseBlanker
// ============================================================================
// This module operates on REAL, post-demodulated AUDIO samples (mono float,
// e.g. 48 kHz from SSB/AM/FM demod) and suppresses STATIONARY background noise
// (thermal hiss, microphone noise, multi-path buzz) in the frequency domain.
//
// It is deliberately different from cpp/src/dsp/noise_blanker.{h,cpp}:
//   * NoiseBlanker works on COMPLEX IQ samples at the receiver front-end and
//     removes TRANSIENT IMPULSIVE noise (lightning, QRM pulses) by statistical
//     amplitude clipping / median replacement on a sample-by-sample basis.
//   * AudioNoiseReduction works on REAL audio samples AFTER demodulation and
//     reduces STEADY-STATE spectral noise; it never touches IQ data and never
//     replaces individual samples.
// Both can be active at the same time in the chain; they are complementary.
//
// ============================================================================
// ALGORITHM
// ============================================================================
// Weighted overlap-add STFT (Hann window, 50% overlap, radix-2 FFT internal):
//   1. Analysis: frame N samples, Hann-window, forward FFT.
//   2. Noise PSD estimate: per-band coefficient-of-variation (CV) VAD —
//      white-noise bins fluctuate wildly frame to frame; a steady tone bin
//      does not. Steady high-level bins freeze the noise estimate (slow
//      downward drift only), fluctuating bins track the floor fast.
//   3. Gain: decision-directed a-priori SNR -> Wiener gain G = xi/(xi+1),
//      bounded by a spectral floor that scales with `strength` to trade
//      attenuation against residual "musical noise". Gain is frequency-smoothed
//      across adjacent bins to further suppress musical noise.
//   4. Synthesis: zero-phase magnitude scaling, inverse FFT, Hann-window,
//      overlap-add with w^2 normalization (perfect reconstruction when G==1).
//   5. Streaming: internal input FIFO + output OLA queue handle arbitrary block
//      sizes and block boundaries; state fully persists across process() calls.
//
// Pure C++17 standard library only — no Qt, no external DSP deps.
#pragma once

#include <vector>

namespace mbdsdr {
namespace dsp {

class AudioNoiseReduction {
public:
    AudioNoiseReduction();

    /// (Re)configure the analyzer. fftSize must be a power of two; 512 is the
    /// recommended default (256-sample hop at 48 kHz -> ~5.3 ms frame).
    void configure(double sampleRateHz = 48000, int fftSize = 512);

    /// Master switch. Default: OFF (process() becomes an identity passthrough,
    /// bit-for-bit copy of the input).
    void setEnabled(bool on);
    bool enabled() const { return enabled_; }

    /// Suppression strength in [0, 1]. 0 = transparent (spectral floor 0 dB),
    /// 1 = aggressive (~ -24 dB noise floor). Default: 1.0.
    void setStrength(float s);
    float strength() const { return strength_; }

    /// Process one block of mono audio. Output length always equals input
    /// length; a fixed ~N/2-sample algorithmic latency exists (front of the
    /// very first blocks is zero-padded). All internal state persists between
    /// calls. When disabled this is an exact identity function.
    std::vector<float> process(const std::vector<float>& in);

    /// Drop all estimated state (noise floor, VAD level, OLA queues).
    void reset();

    // ---- Read-only statistics (for UI meters / tests) ----------------------
    /// Mean estimated noise power spectrum level, in dBFS (10*log10(mean Pn)).
    /// -120 dB if never run.
    float noiseFloorDb() const { return noiseFloorDb_; }
    /// Mean magnitude gain of the most recent processed frame, in dB
    /// (20*log10(mean G)). ~0 dB transparent, negative = attenuation.
    float averageGainDb() const { return avgGainDb_; }
    /// Current STFT frame size in samples.
    int fftSize() const { return n_; }
    /// Current hop size in samples (fftSize/2).
    int hopSize() const { return hop_; }

private:
    bool  enabled_   = false;
    float strength_  = 1.0f;
    double fs_       = 48000.0;
    int   n_         = 512;
    int   hop_       = 256;

    // Per-bin spectral state (size n_).
    std::vector<float> window_;     // Hann window
    std::vector<float> psSmoothed_; // smoothed power spectrum (mean)
    std::vector<float> psVar_;      // smoothed variance of the power spectrum
    std::vector<float> pn_;         // estimated noise power spectrum
    std::vector<float> xi_;         // decision-directed a-priori SNR
    std::vector<float> gPrev_;      // frequency-smoothed gain of last frame

    // Streaming state.
    std::vector<float> inQueue_;    // not-yet-framed input samples
    std::vector<float> outAcc_;     // OLA accumulator (windowed time domain)
    std::vector<float> outNorm_;    // OLA normalization accumulator (w^2)
    std::vector<float> outQueue_;   // ready-to-emit samples
    std::vector<float> scratchOut_; // reused output vector

    // Stats.
    float noiseFloorDb_ = -120.0f;
    float avgGainDb_    = 0.0f;

    void buildWindow();
    void processOneFrame();
};

} // namespace dsp
} // namespace mbdsdr
