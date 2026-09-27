// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <vector>

namespace mbdsdr {

struct SpectrumFrame {
    std::vector<float> dbfs;
    double centerFreqHz = 0.0;
    double sampleRateHz  = 0.0;
    int    fftSize       = 0;
    bool   isTestSignal  = true;
    QString sourceName;       // e.g. "RTL-SDR" or "Test Signal"
};

} // namespace mbdsdr
