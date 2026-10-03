// SPDX-License-Identifier: MIT
// MBDSDR VOR (VHF Omnidirectional Range, ICAO Annex 10) audio-baseband receiver.
//
// Clean-room, hardware-independent decoder for the COMPOSITE AUDIO that comes
// out of the VOR carrier's FM demodulator. It does NOT touch the RF carrier.
//
// The audio carries three things at once:
//   * Variable 30 Hz  -- a spatial amplitude-modulation tone whose phase rotates
//                        with the aircraft's magnetic bearing FROM the station.
//   * Reference 30 Hz -- a phase-FIXED tone hidden as frequency modulation of a
//                        9960 Hz subcarrier (peak deviation +/-480 Hz, index 16).
//                        We isolate the subcarrier, FM-discriminate it, and the
//                        recovered frequency deviation IS a 30 Hz tone.
//   * 1020 Hz Morse    -- an on/off-keyed carrier spelling the station ID.
//
// The radial (magnetic bearing 0..359 deg) is the phase difference between the
// two 30 Hz components:  radial = phase(variable) - phase(reference).
//
// Measurement style (feed -> take, mirrors the other dsp decoders): audio is fed
// in blocks; once an integer-second block (60 x 30 Hz cycles) accumulates it is
// split into sub-measurements. Each sub-measurement coherently integrates (a
// Goertzel-style product with a running 30 Hz NCO) so every off-tone interferer
// averages to zero. The sub-measurement radials are combined as a circular mean;
// their scatter -- the circular mean-resultant length R in [0,1] -- IS the honest
// signal quality. When R is low (weak/absent 30 Hz, e.g. pure noise) the result
// is reported as UNLOCKED with NO fabricated bearing. No station database is
// baked in: the ID is whatever the 1020 Hz keying actually spells.
#pragma once

#include <QString>

#include <vector>
#include <string>

namespace mbdsdr {
namespace dsp {

// One decoded VOR reading. `locked` is the honest gate: when false, `radialDeg`
// is meaningless and must be ignored (we refuse to invent a bearing).
struct VorResult {
    bool   locked    = false;  // a reliable radial is available
    double radialDeg = 0.0;    // 0..359 magnetic bearing; valid only if locked
    double quality   = 0.0;    // 0..1 sub-measurement agreement (resultant R)
    QString morseId;           // station ID from 1020 Hz keying; empty if none
    double varLevel  = 0.0;    // debug: normalized variable-30 Hz coherence
    double refLevel  = 0.0;    // debug: normalized reference-30 Hz coherence
};

class VorReceiver {
public:
    // sampleRateHz: the composite audio rate. Must exceed ~22 kHz so the 9960 Hz
    // subcarrier and its +/-480 Hz deviation sit below Nyquist.
    explicit VorReceiver(double sampleRateHz = 44100.0);

    void setSampleRate(double sr);

    // Feed a block of demodulated composite audio (mono, [-1,1] float).
    void feed(const std::vector<float>& audio);

    // Take the most recent finished reading (and clear it). If no block has
    // locked yet, returns an honest unlocked/empty result.
    VorResult take();

    void reset();

private:
    // --- RBJ-cookbook biquad (direct form I), own implementation -------------
    struct Biquad {
        float b0 = 1.0f, b1 = 0.0f, b2 = 0.0f;
        float a1 = 0.0f, a2 = 0.0f;
        float x1 = 0.0f, x2 = 0.0f, y1 = 0.0f, y2 = 0.0f;
        float process(float x);
        void reset();
    };

    // One recorded on/off transition of the 1020 Hz keying, in seconds.
    struct Seg { bool on; double sec; };

    void designFilters();
    void resetAccumulators();
    void processSample(float x);
    void endSubBlock();        // fold one sub-measurement, start next
    void endBlock();           // circular-combine sub-measurements -> a reading
    static double wrapDeg(double deg);

    double fs_ = 44100.0;

    // --- 30 Hz NCO (shared reference for both channels) ---
    double d30_  = 0.0;        // radians per sample
    double th30_ = 0.0;        // running phase

    // --- 9960 Hz subcarrier down-conversion NCO ---
    double dSub_  = 0.0;
    double thSub_ = 0.0;

    // --- sub-carrier complex baseband low-pass (keeps +/-480 Hz deviation) ---
    double subLpAlpha_ = 0.0;
    double subI_ = 0.0, subQ_ = 0.0;
    double prevArg_ = 0.0;
    bool   havePrevArg_ = false;

    // --- sub-block coherent accumulators (reset each sub-measurement) ---
    double iv_ = 0.0, qv_ = 0.0;   // variable 30 Hz: I/Q products
    double ir_ = 0.0, qr_ = 0.0;   // reference 30 Hz (discriminated dev) I/Q
    long   subN_ = 0;              // samples in current sub-block

    // --- finished sub-measurements held until the whole block closes ---
    std::vector<double> subRadial_;   // e^{j radial} stored as atan2 later
    std::vector<double> subRx_;       // unit-vector real part
    std::vector<double> subIy_;       // unit-vector imag part
    std::vector<double> subVarMag_;   // normalized var coherence this sub-block
    std::vector<double> subRefMag_;   // normalized ref coherence this sub-block
    int subCount_ = 0;

    // --- 1020 Hz Morse keying chain ---
    Biquad morseBp_;             // bandpass around 1020 Hz
    double envAlpha_ = 0.0;
    double env_ = 0.0;
    double envPeak_ = 0.0;        // slow peak track for the on/off threshold
    bool   morseOn_ = false;
    long   morseRun_ = 0;         // samples in current on/off run
    std::vector<Seg> segs_;       // recorded transitions (seconds)

    // --- pending reading produced by endBlock() ---
    bool haveResult_ = false;
    VorResult pending_;

    static std::string decodeMorseSegments(const std::vector<Seg>& segs, double wpm);
};

} // namespace dsp
} // namespace mbdsdr
