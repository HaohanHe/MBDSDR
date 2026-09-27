// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <vector>
#include <deque>
#include <string>
#include <unordered_map>

namespace mbdsdr {
namespace dsp {

class CWDecoder {
public:
    CWDecoder();

    void setSampleRate(double sr);
    void feed(const std::vector<float>& audio);
    QString takeText();
    double wpm() const;
    void reset();

private:
    double sr_ = 48000.0;
    std::string decodedText_;
    std::string currentSymbol_;   // dots/dashes of current char
    std::vector<float> envelopeBuf_;
    double threshold_ = 0.0;
    bool on_ = false;
    long long onCount_ = 0;
    long long offCount_ = 0;
    double unitMs_ = -1.0;
    std::vector<double> markDurations_; // collected mark durations in samples

    static const std::unordered_map<std::string, QChar> morseTable_;

    void processSegments();
    void flushChar();
};

} // namespace dsp
} // namespace mbdsdr
