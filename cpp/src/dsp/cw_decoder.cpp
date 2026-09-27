// SPDX-License-Identifier: MIT
#include "cw_decoder.h"
#include <algorithm>
#include <cmath>
#include <numeric>

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

void CWDecoder::setSampleRate(double sr) { sr_ = sr; }

void CWDecoder::reset() {
    decodedText_.clear();
    currentSymbol_.clear();
    on_ = false;
    onCount_ = 0;
    offCount_ = 0;
    unitMs_ = -1.0;
}

void CWDecoder::feed(const std::vector<float>& audio) {
    float env = 0.0f;
    for (float x : audio) {
        // Envelope follower: attack fast, release slow
        float absx = std::abs(x);
        if (absx > env) env = absx;
        else env = 0.95f * env;

        bool isOn = env > 0.15f;

        if (isOn == on_) {
            if (on_) onCount_++;
            else offCount_++;
        } else {
            if (on_) {
                // Just turned off: record mark duration
                double durMs = onCount_ * 1000.0 / sr_;
                // Default unit ~60ms (12 WPM); refine from observed marks
                if (unitMs_ <= 0) unitMs_ = 60.0;
                if (durMs > 1.7 * unitMs_) currentSymbol_.push_back('-');
                else currentSymbol_.push_back('.');
            } else {
                // Just turned on: process gap
                double gapMs = offCount_ * 1000.0 / sr_;
                if (unitMs_ > 0) {
                    if (gapMs >= 5.0 * unitMs_) {
                        flushChar();
                        decodedText_.push_back(' ');
                    } else if (gapMs >= 1.5 * unitMs_) {
                        flushChar();
                    }
                }
            }
            on_ = isOn;
            if (on_) onCount_ = 1; else offCount_ = 1;
        }
    }
}

void CWDecoder::flushChar() {
    if (currentSymbol_.empty()) return;
    auto it = morseTable_.find(currentSymbol_);
    if (it != morseTable_.end()) decodedText_.push_back(it->second.toLatin1());
    currentSymbol_.clear();
}

QString CWDecoder::takeText() {
    flushChar();
    QString s = QString::fromStdString(decodedText_);
    decodedText_.clear();
    return s;
}

double CWDecoder::wpm() const {
    if (unitMs_ <= 0) return 0.0;
    return 1200.0 / unitMs_;
}

} // namespace dsp
} // namespace mbdsdr
