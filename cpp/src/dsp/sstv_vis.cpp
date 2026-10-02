// SPDX-License-Identifier: MIT
#include "sstv_vis.h"

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace dsp {

int sstvFreqToPixel(float freqHz) {
    float v = (freqHz - 1500.0f) / 800.0f * 255.0f;
    if (v < 0.0f) v = 0.0f;
    if (v > 255.0f) v = 255.0f;
    return static_cast<int>(v + 0.5f);
}

SstvVisResult sstvDecodeVisBits(const float dataBitFreqs[7], float parityBitFreq) {
    SstvVisResult r;
    int code = 0;
    int ones = 0;
    bool allIn = true;
    for (int i = 0; i < 7; ++i) {
        const float f = dataBitFreqs[i];
        bool mark = (f > 1050.0f && f < 1150.0f);
        bool space = (f > 1250.0f && f < 1350.0f);
        if (!mark && !space) { allIn = false; continue; }
        if (mark) { code |= (1 << i); ++ones; }
    }
    const float pf = parityBitFreq;
    const bool pMark = (pf > 1050.0f && pf < 1150.0f);
    const bool pSpace = (pf > 1250.0f && pf < 1350.0f);
    if (!pMark && !pSpace) allIn = false;
    const int pBit = pMark ? 1 : 0;
    r.allInBand = allIn;
    r.code = allIn ? code : -1;
    r.parityOk = (((ones + pBit) % 2) == 0);
    return r;
}

void sstvInstFreqZeroCrossing(const float* samples, std::size_t n,
                              double sampleRateHz, std::vector<float>& out) {
    out.assign(n, 1500.0f);
    if (!samples || n < 4) return;

    // Remove DC so a DC offset cannot fake a sign.
    double mean = 0.0;
    for (std::size_t i = 0; i < n; ++i) mean += samples[i];
    mean /= static_cast<double>(n);

    // Collect interpolated zero-crossing positions.
    std::vector<double> zc;
    zc.reserve(n / 8 + 4);
    for (std::size_t i = 0; i + 1 < n; ++i) {
        const float y0 = samples[i] - static_cast<float>(mean);
        const float y1 = samples[i + 1] - static_cast<float>(mean);
        if ((y0 >= 0.0f && y1 < 0.0f) || (y0 < 0.0f && y1 >= 0.0f)) {
            const float denom = y1 - y0;
            const double frac = (denom == 0.0f) ? 0.0 :
                                static_cast<double>(-y0 / denom);
            zc.push_back(static_cast<double>(i) + frac);
        }
    }
    if (zc.size() < 2) return;

    // Frequency between two consecutive crossings spans half a period.
    for (std::size_t k = 0; k + 1 < zc.size(); ++k) {
        const double gap = zc[k + 1] - zc[k];
        if (gap <= 0.0) continue;
        const double freq = sampleRateHz / (2.0 * gap);
        const std::size_t a = static_cast<std::size_t>(zc[k]);
        const std::size_t b = static_cast<std::size_t>(zc[k + 1]);
        if (a >= n) continue;
        for (std::size_t s = a; s < b && s < n; ++s) out[s] = static_cast<float>(freq);
    }
}

} // namespace dsp
} // namespace mbdsdr
