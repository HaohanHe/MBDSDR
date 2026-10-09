// SPDX-License-Identifier: MIT
//
// CDCSS / DCS (Digital Coded Squelch System) decoder.
//
// Mechanism (clean-room, derived from the public algebraic description of the
// Golay(23,12) code and the standard DCS frame format -- no GPL code copied):
//
//   * The NFM post-ANR mono audio (real-valued, 48 kHz) carries a 134.4 baud
//     Manchester-coded subaudible data stream.  Each over-the-air word is a
//     23-bit Golay(23,12) codeword representing a 12-bit octal DCS address
//     (e.g. 023, 025, ... 754 -- the public DCS code table).
//
//   * Front end: coherent I demodulation at a fixed subaudible carrier f_c
//     (this round fixes f_c in the DSP layer; carrier recovery / PLL is left
//     for engine integration).  The recovered I envelope is strobed once per
//     Manchester half-bit, paired into bits, and shifted into a 23-bit word.
//
//   * Each completed 23-bit word runs through a Golay(23,12) syndrome decoder
//     that corrects up to 2 bit errors (the (23,12,7) code has minimum distance
//     7; we table weight-0..2 error patterns this round).  The recovered
//     12-bit address is compared against the configured DCS code.
//
//   * Debounce: N consecutive matching words latch codePresent(); a few
//     non-matching words release it (mirror of the CTCSS DetectHits/Misses
//     honest de-chatter).  Constants live in this translation unit -- tokens.h
//     is owned by a parallel session and must not be touched.
//
// This file implements the DSP only.  It knows nothing about squelch gating,
// the three-channel control plane or the UI -- the host feeds it the selected
// VFO's demodulated mono audio and reads codePresent() back.
#pragma once

#include <cmath>
#include <cstdint>

namespace mbdsdr {
namespace dsp {

class CdcssDecoder {
public:
    CdcssDecoder() = default;

    // (Re)configure the decoder.  sampleRateHz is the post-ANR mono audio
    // rate (48 kHz).  code12 is the desired DCS address as a 12-bit integer
    // (octal 023 == 19 .. octal 754 == 492).  Out-of-table values are
    // rejected honestly: the detector stays disabled for that code and keeps
    // the previous tuning.  Reconfiguring resets streaming state.
    void configure(double sampleRateHz, int code12);

    // Drop all streaming / debounce state.  codePresent() becomes false.
    void reset();

    // Enable / disable detection.  While disabled, process() feeds nothing and
    // codePresent() is always false -- an honest "no DCS code requested"
    // read-back, never a fabricated code.
    void setEnabled(bool on) { enabled_ = on; if (!on) reset(); }
    bool enabled() const { return enabled_; }

    // Feed one block of demodulated mono audio.  Returns codePresent() after
    // this block.  When disabled the call is a no-op.
    bool process(const float* audio, int n);

    // True only when the debounce latch currently holds the configured code.
    // Honest: no signal / disabled / warm-up all read false.
    bool codePresent() const { return enabled_ && present_; }

    // The last successfully decoded 12-bit address, or -1 if none yet.  This
    // is the raw decoded address (before the match against configuredCode_),
    // so the host can display what is actually on the air.
    int lastCode() const { return lastCode_; }

    // Read-backs for status display / tests.
    double sampleRateHz() const { return sampleRateHz_; }
    int configuredCode() const { return configuredCode_; }

private:
    // Close out one Manchester half-bit: sign the averaged I envelope and push
    // it through the Manchester pairer -> 23-bit word assembler.
    void pushHalfBit(bool high);

    // One 23-bit word has been assembled: Golay-decode, match, update latch.
    void onWordComplete(uint32_t w23);

    bool   enabled_ = false;
    bool   present_ = false;
    double sampleRateHz_ = 0.0;
    int    configuredCode_ = -1;
    int    lastCode_ = -1;

    // Coherent I demodulation state.
    double phase_  = 0.0;   // local-oscillator phase (radians)
    double dPhase_ = 0.0;   // phase step per sample
    double iAcc_   = 0.0;   // accumulated I over the current half-bit
    int    iN_     = 0;     // samples accumulated in the current half-bit

    // Half-bit strobe clock (fractional, to absorb the non-integer 48k/134.4).
    double halfBitPeriod_ = 0.0;   // samples per Manchester half-bit
    double countdown_     = 0.0;   // samples until next half-bit strobe

    // Manchester pairing + 23-bit word assembler.
    bool   prevHalf_  = false;     // previous half-bit level
    bool   havePrev_  = false;
    uint32_t word_    = 0;         // 23-bit shift register
    int    bitsInWord_ = 0;

    // Debounce latch counters (whole words).
    int hits_ = 0;
    int miss_ = 0;
};

} // namespace dsp
} // namespace mbdsdr
