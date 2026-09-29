// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <vector>
#include <string>
#include <unordered_map>

namespace mbdsdr {
namespace dsp {

// Morse (CW) decoder for the receive audio path.
//
// Pipeline per sample:
//   1. 4th-order bandpass (Butterworth HP ~500 Hz + LP ~900 Hz) to reject
//      out-of-band noise.
//   2. Envelope follower (fast attack, ~1.5 ms release).
//   3. Adaptive threshold from a two-time-constant noise-floor tracker
//      (fast downward, slow upward), with hysteresis.
//   4. Online unit-time estimate: the shortest observed mark is a dot
//      (1 unit); subsequent dot-like marks EMA-refine it. Dash ~3 units.
//   5. Gap-based segmentation: ~2 units = char boundary, ~5 units = word.
//
// Public API is kept source-compatible with the Phase-5 version used by
// spectrum_engine / main_window.
class CWDecoder {
public:
    CWDecoder();

    void setSampleRate(double sr);
    void feed(const std::vector<float>& audio);
    QString takeText();
    double wpm() const;
    void reset();

private:
    // RBJ-cookbook biquad (direct form I).
    struct Biquad {
        float b0 = 1.0f, b1 = 0.0f, b2 = 0.0f;
        float a1 = 0.0f, a2 = 0.0f;
        float x1 = 0.0f, x2 = 0.0f, y1 = 0.0f, y2 = 0.0f;
        float process(float x);
        void reset();
    };

    void designFilters();
    void processMarkEnd(double durMs);
    void processGapEnd(double gapMs);
    void flushChar();

    double sr_ = 48000.0;
    Biquad hp_;   // high-pass ~500 Hz
    Biquad lp_;   // low-pass ~900 Hz

    float envAlpha_ = 0.01f;   // envelope one-pole coefficient (per sample)

    float env_ = 0.0f;          // smoothed envelope
    float noiseFloor_ = 0.0f;   // tracked noise level
    float threshold_ = 0.0f;

    bool on_ = false;
    long long onCount_ = 0;
    long long offCount_ = 0;

    double unitMs_ = -1.0;      // estimated 1 unit in ms; <0 until locked
    std::string decodedText_;
    std::string currentSymbol_;

    // Pre-lock: collect the first few mark durations; the shortest is a dot.
    static constexpr int PRE_N = 3;
    float preMarks_[PRE_N] = {};
    int preCount_ = 0;

    static const std::unordered_map<std::string, QChar> morseTable_;
};

} // namespace dsp
} // namespace mbdsdr
