// SPDX-License-Identifier: MIT
#include "cw_decoder.h"

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace dsp {

const std::unordered_map<std::string, QChar> CWDecoder::morseTable_ = {
    {".-", 'A'}, {"-...", 'B'}, {"-.-.", 'C'}, {"-..", 'D'}, {".", 'E'},
    {"..-.", 'F'}, {"--.", 'G'}, {"....", 'H'}, {"..", 'I'}, {".---", 'J'},
    {"-.-", 'K'}, {".-..", 'L'}, {"--", 'M'}, {"-.", 'N'}, {"---", 'O'},
    {".--.", 'P'}, {"--.-", 'Q'}, {".-.", 'R'}, {"...", 'S'}, {"-", 'T'},
    {"..-", 'U'}, {"...-", 'V'}, {".--", 'W'}, {"-..-", 'X'}, {"-.--", 'Y'},
    {"--..", 'Z'},
    {"-----", '0'}, {".----", '1'}, {"..---", '2'}, {"...--", '3'}, {"....-", '4'},
    {".....", '5'}, {"-....", '6'}, {"--...", '7'}, {"---..", '8'}, {"----.", '9'},
    {".-.-.-", '.'}, {"--..--", ','}, {"..--..", '?'}, {"-....-", '-'},
    {".----.", '\''}, {"-..-.", '/'}, {"-.--.", '('}, {"-.--.-", ')'},
    {"---...", ':'}, {"-.-.--", '!'}, {"...-..-", '@'}, {".-...", '&'},
};

CWDecoder::CWDecoder() = default;

// ---------------------------------------------------------------------------
// Biquad
// ---------------------------------------------------------------------------
float CWDecoder::Biquad::process(float x) {
    float y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
    x2 = x1; x1 = x;
    y2 = y1; y1 = y;
    return y;
}

void CWDecoder::Biquad::reset() {
    x1 = x2 = y1 = y2 = 0.0f;
}

// ---------------------------------------------------------------------------
// Filter design (Butterworth-ish, Q = 1/sqrt(2))
// ---------------------------------------------------------------------------
void CWDecoder::designFilters() {
    const double Q = 0.7071067811865476;
    auto makeLP = [&](double f0) {
        double w0 = 2.0 * M_PI * f0 / sr_;
        double c = std::cos(w0), s = std::sin(w0);
        double alpha = s / (2.0 * Q);
        double a0 = 1.0 + alpha;
        Biquad b;
        b.b0 = static_cast<float>(((1.0 - c) / 2.0) / a0);
        b.b1 = static_cast<float>(((1.0 - c)) / a0);
        b.b2 = static_cast<float>(((1.0 - c) / 2.0) / a0);
        b.a1 = static_cast<float>((-2.0 * c) / a0);
        b.a2 = static_cast<float>((1.0 - alpha) / a0);
        return b;
    };
    auto makeHP = [&](double f0) {
        double w0 = 2.0 * M_PI * f0 / sr_;
        double c = std::cos(w0), s = std::sin(w0);
        double alpha = s / (2.0 * Q);
        double a0 = 1.0 + alpha;
        Biquad b;
        b.b0 = static_cast<float>(((1.0 + c) / 2.0) / a0);
        b.b1 = static_cast<float>((-(1.0 + c)) / a0);
        b.b2 = static_cast<float>(((1.0 + c) / 2.0) / a0);
        b.a1 = static_cast<float>((-2.0 * c) / a0);
        b.a2 = static_cast<float>((1.0 - alpha) / a0);
        return b;
    };
    hp_ = makeHP(500.0);
    lp_ = makeLP(900.0);
}

void CWDecoder::setSampleRate(double sr) {
    sr_ = sr;
    designFilters();
    // Envelope one-pole time constant ~2 ms (smooths rectified carrier ripple).
    const double tau = 0.002;
    envAlpha_ = static_cast<float>(1.0 - std::exp(-1.0 / (tau * sr_)));
}

void CWDecoder::reset() {
    decodedText_.clear();
    currentSymbol_.clear();
    env_ = 0.0f;
    noiseFloor_ = 0.0f;
    threshold_ = 0.0f;
    on_ = false;
    onCount_ = 0;
    offCount_ = 0;
    unitMs_ = -1.0;
    preCount_ = 0;
    hp_.reset();
    lp_.reset();
}

// ---------------------------------------------------------------------------
// Symbol / gap bookkeeping
// ---------------------------------------------------------------------------
void CWDecoder::processMarkEnd(double durMs) {
    if (preCount_ < PRE_N) {
        preMarks_[preCount_++] = static_cast<float>(durMs);
        if (preCount_ == PRE_N) {
            // Lock unit time to the shortest observed mark (a dot).
            double mn = preMarks_[0];
            for (int i = 1; i < PRE_N; ++i)
                mn = std::min(mn, static_cast<double>(preMarks_[i]));
            unitMs_ = mn;
            // Backfill the pre-lock marks now that we can classify them.
            for (int i = 0; i < PRE_N; ++i) {
                double d = preMarks_[i];
                currentSymbol_.push_back(d < 1.8 * unitMs_ ? '.' : '-');
            }
        }
        return;
    }

    if (durMs < 1.8 * unitMs_) {
        currentSymbol_.push_back('.');
        // EMA-refine the unit estimate toward observed dot durations.
        unitMs_ = 0.75 * unitMs_ + 0.25 * durMs;
    } else {
        currentSymbol_.push_back('-');
    }
}

void CWDecoder::processGapEnd(double gapMs) {
    if (unitMs_ <= 0.0) return;  // not locked yet; keep accumulating
    if (gapMs >= 5.0 * unitMs_) {
        flushChar();
        decodedText_.push_back(' ');
    } else if (gapMs >= 2.0 * unitMs_) {
        flushChar();
    }
}

void CWDecoder::flushChar() {
    if (currentSymbol_.empty()) return;
    auto it = morseTable_.find(currentSymbol_);
    if (it != morseTable_.end())
        decodedText_.push_back(it->second.toLatin1());
    currentSymbol_.clear();
}

// ---------------------------------------------------------------------------
// Streaming feed
// ---------------------------------------------------------------------------
void CWDecoder::feed(const std::vector<float>& audio) {
    for (float x : audio) {
        // 1. Bandpass 500-900 Hz.
        float bp = lp_.process(hp_.process(x));

        // 2. Envelope: rectify + one-pole low-pass (~2 ms), which averages the
        //    800 Hz carrier ripple and smooths noise peaks.
        float a = std::fabs(bp);
        env_ += envAlpha_ * (a - env_);

        // 3. Noise floor: slow upward tracking (so a CW burst barely inflates
        //    it) and fast downward tracking (so it returns to the noise level
        //    immediately after a mark ends).
        if (env_ < noiseFloor_) noiseFloor_ += 0.02f * (env_ - noiseFloor_);
        else noiseFloor_ += 0.00002f * (env_ - noiseFloor_);

        // 4. Adaptive threshold with hysteresis.
        threshold_ = std::max(noiseFloor_ * 3.0f, 0.02f);
        float onThr = threshold_ * 1.2f;
        float offThr = threshold_ * 0.8f;

        bool isOn = on_ ? (env_ > offThr) : (env_ > onThr);

        if (isOn == on_) {
            if (on_) ++onCount_;
            else ++offCount_;
        } else {
            if (on_) {
                double durMs = onCount_ * 1000.0 / sr_;
                processMarkEnd(durMs);
            } else {
                double gapMs = offCount_ * 1000.0 / sr_;
                processGapEnd(gapMs);
            }
            on_ = isOn;
            if (on_) onCount_ = 1; else offCount_ = 1;
        }
    }
}

QString CWDecoder::takeText() {
    flushChar();
    QString s = QString::fromStdString(decodedText_);
    decodedText_.clear();
    return s;
}

double CWDecoder::wpm() const {
    if (unitMs_ <= 0.0) return 0.0;
    return 1200.0 / unitMs_;
}

} // namespace dsp
} // namespace mbdsdr
