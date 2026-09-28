// SPDX-License-Identifier: MIT
//
// Real digital demodulator for BPSK / QPSK.
//
// PURE C++17 STANDARD LIBRARY -- no Qt, no external DSP dependencies, so this
// TU can be compiled standalone with a bare `g++ -std=c++17` for unit tests.
//
// Input : channelized complex baseband (already centered to IF passband),
//         std::complex<float> stream at cfg.sampleRateHz (typical 48 kHz).
//         Each symbol occupies >= 4 samples (SPS >= 4 required).
//
// Chain (per input sample):
//   1. AGC / amplitude normalization   -> unit average power
//   2. Carrier NCO rotation          -> driven by a decision-directed Costas loop
//   3. Fractional interpolator        -> linear, between adjacent samples
//   4. On each symbol strobe:
//        a. hard decision to nearest ideal constellation point
//        b. Costas loop update (symbol rate)
//        c. Mueller-Muller timing error -> adjusts symbol-clock estimate
//        d. differential decode (resolves BPSK 180 / QPSK 90 ambiguity)
//
// Phase-ambiguity handling
// ------------------------
// A Costas / decision-directed PLL always locks to *some* rotated copy of the
// constellation: BPSK has a 180 degree ambiguity, QPSK a 90 degree ambiguity.
// We resolve it with DIFFERENTIAL coding on the transmit side and DIFFERENTIAL
// decoding here: bits are encoded as *phase transitions* between consecutive
// symbols, so a global N-fold rotation cancels out in the relative measurement.
//
//   diffEncode() / diffDecode() below are the canonical pair -- the test (and
//   any future transmitter) MUST use them so conventions match exactly.
//
//   - takeDecisions()     : hard decisions at ideal points (clean scatter)
//   - takeRecovered()     : post-Costas samples *before* hard decision -- this
//                           is what the constellation panel plots, and EVM is
//                           measured on it (decisions are ON ideal points, so
//                           EVM on decisions would be trivially zero).
//   - takeDecodedBits()   : differentially recovered payload bits (0/1).
#pragma once

#include <complex>
#include <vector>
#include <cstddef>

namespace mbdsdr {
namespace dsp {

enum class DigMode { BPSK, QPSK };

struct DigitalDemodConfig {
    double sampleRateHz = 48000.0;   // channelized IF / baseband rate
    double symbolRateBd = 2400.0;    // baud
    DigMode mode        = DigMode::BPSK;

    // Loop natural bandwidths, normalized in cycles-per-symbol.
    // Small -> stable but slow to pull in; large -> fast but noisier.
    float carrierBw = 0.030f;   // Costas loop (cycles/symbol)
    // Mueller-Muller phase-nudge gain. The fractional interpolator already
    // resamples to the correct strobe instant at the nominal SPS (provided by
    // the channelizer), which is the primary timing recovery. The MM TED below
    // is implemented for free-running clocks; it is gated to 0 by default
    // because on rectangular pulses the unfiltered TED has a small bias that
    // walks the strobe off the eye center. Set >0 for slave-clock applications.
    float timingBw  = 0.0f;
    float agcTau    = 0.05f;    // AGC smoothing factor (0..1)
};

struct DigitalLockStatus {
    bool  carrierLocked    = false;
    bool  symbolLocked     = false;
    float evmPercent       = 0.0f;   // RMS EVM vs ideal constellation, %
    long  symbolsProcessed = 0;
    float freqOffsetHz     = 0.0f;   // estimated residual carrier offset
};

class DigitalDemod {
public:
    explicit DigitalDemod(const DigitalDemodConfig& cfg);

    void reset();

    // Feed a block of channelized complex baseband samples. Appends internal
    // queues; outputs are pulled by the take*() methods below.
    void process(const std::vector<std::complex<float>>& in);

    // --- Output pull methods (clear internal queues on return) ---
    std::vector<std::complex<float>> takeDecisions();
    std::vector<std::complex<float>> takeRecovered();
    std::vector<int>                 takeDecodedBits();

    const DigitalLockStatus& status() const { return st_; }

    // Unit-energy ideal constellation points for a mode.
    static std::vector<std::complex<float>> idealPoints(DigMode m);

    // --- Canonical differential codec (shared by TX and RX) ---
    // Encode flat bit vector (0/1): BPSK consumes 1 bit/symbol, QPSK 2 bits.
    // Output unit-modulus symbols.
    static std::vector<std::complex<float>> diffEncode(const std::vector<int>& bits,
                                                       DigMode m);
    // Decode a decision stream back to flat bits. The very first symbol is used
    // as the phase reference and yields NO bits (output is one symbol shorter
    // for BPSK, one symbol shorter * 2 bits for QPSK).
    static std::vector<int> diffDecode(const std::vector<std::complex<float>>& decisions,
                                      DigMode m);

private:
    void acceptSymbol(std::complex<float> y);
    std::complex<float> decide(std::complex<float> y) const;
    static float        angleNorm(float rad);

    DigitalDemodConfig cfg_;
    float spsNom_     = 1.0f;   // expected samples / symbol
    float spsEst_     = 1.0f;   // timing-loop estimate
    float tau_        = 0.0f;   // fractional timing phase, [0,1)

    // AGC
    float agcGain_ = 1.0f;

    // Carrier NCO / Costas loop (state in RAD/SYMBOL, applied per-sample
    // divided by spsEst_ so the loop bandwidth stays correct regardless of SPS).
    float ncoPhase_ = 0.0f;       // running rotation phase (rad)
    float carFreq_  = 0.0f;      // loop-controlled offset (rad/symbol)
    float kpCar_    = 0.02f;     // proportional gain (per symbol)
    float kiCar_    = 0.0002f;    // integrator gain (per symbol)

    // Sample history for linear interpolation
    std::complex<float> xPrev_{0.f, 0.f};
    std::complex<float> xCur_{0.f, 0.f};

    // Gardner timing recovery: midpoint sample between two strobe instants.
    std::complex<float> yMid_{0.f, 0.f};
    bool                haveMid_ = false;

    // Decision memory (MM TED + differential decode)
    std::complex<float> yPrev_{0.f, 0.f};
    std::complex<float> aPrev_{1.f, 0.f};
    bool                havePrev_ = false;
    float               timingErrOut_ = 0.0f;  // set by acceptSymbol, applied in process()

    // Output queues
    std::vector<std::complex<float>> decisions_;
    std::vector<std::complex<float>> recovered_;
    std::vector<int>                 bits_;

    // Lock / EVM tracking
    DigitalLockStatus st_{};
    float evmAcc_   = 0.0f;
    long  evmN_     = 0;
    float carSmE_   = 1.0f;   // smoothed |carrier error|
    float timSmE_   = 1.0f;    // smoothed |timing error|
    int   carGood_  = 0;
    int   timGood_  = 0;
};

} // namespace dsp
} // namespace mbdsdr
