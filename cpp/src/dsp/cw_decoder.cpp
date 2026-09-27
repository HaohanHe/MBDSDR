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
    envelopeBuf_.clear();
    threshold_ = 0.0;
    on_ = false;
    onCount_ = 0;
    offCount_ = 0;
    unitMs_ = -1.0;
    markDurations_.clear();
}

void CWDecoder::feed(const std::vector<float>& audio) {
    // Compute envelope: |x|, then simple moving average
    for (float x : audio) {
        float env = std::abs(x);
        envelopeBuf_.push_back(env);
        if (envelopeBuf_.size() > 48) envelopeBuf_.erase(envelopeBuf_.begin());

        float smoothed = std::accumulate(envelopeBuf_.begin(), envelopeBuf_.end(), 0.0f)
                        / static_cast<float>(envelopeBuf_.size());

        // Adaptive threshold: track min/max slowly
        if (threshold_ <= 0.0) threshold_ = smoothed * 1.5f;
        threshold_ = 0.995f * threshold_ + 0.005f * smoothed;
        float thresh = threshold_ * 1.3f;

        bool isOn = smoothed > thresh;

        if (isOn == on_) {
            if (on_) onCount_++;
            else offCount_++;
        } else {
            // State transition
            if (on_) {
                // Was on, now off: record mark duration
                markDurations_.push_back(static_cast<double>(onCount_));
                if (markDurations_.size() > 50) markDurations_.erase(markDurations_.begin());
            } else {
                // Was off, now on: process gap
                if (unitMs_ > 0) {
                    double unitSamples = unitMs_ * sr_ / 1000.0;
                    double gapMs = offCount_ * 1000.0 / sr_;
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
    QString s = QString::fromStdString(decodedText_);
    decodedText_.clear();
    return s;
}

double CWDecoder::wpm() const {
    if (unitMs_ <= 0) return 0.0;
    // PARIS word = 50 units, WPM = 1200 / unit_ms
    return 1200.0 / unitMs_;
}

} // namespace dsp
} // namespace mbdsdr
