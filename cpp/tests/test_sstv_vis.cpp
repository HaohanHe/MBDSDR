// SPDX-License-Identifier: MIT
// SSTV pure-function tests: freq->pixel map, VIS code decode, zero-crossing
// instantaneous frequency on synthetic sine waves (deterministic, no audio IO).
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>
#include "dsp/sstv_vis.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

static double medianNonDefault(const std::vector<float>& v) {
    std::vector<float> xs;
    for (float f : v) if (std::abs(f - 1500.0f) > 1.0f) xs.push_back(f);
    if (xs.empty()) return 1500.0;
    std::sort(xs.begin(), xs.end());
    return xs[xs.size() / 2];
}

int main() {
    // freq -> pixel mapping.
    check(sstvFreqToPixel(1500.0f) == 0, "1500 Hz = black");
    check(sstvFreqToPixel(2300.0f) == 255, "2300 Hz = white");
    check(sstvFreqToPixel(1900.0f) == 128, "1900 Hz mid grey");
    check(sstvFreqToPixel(1000.0f) == 0, "below band clamped black");
    check(sstvFreqToPixel(3000.0f) == 255, "above band clamped white");

    // VIS decode: Robot36 VIS = 0x08 (bit3 set, LSB first).
    // mark=1100 -> 1, space=1300 -> 0. ones=1 -> parity bit must be mark.
    float bits[7] = {1300, 1300, 1300, 1100, 1300, 1300, 1300};
    SstvVisResult r = sstvDecodeVisBits(bits, 1100.0f);
    check(r.allInBand && r.code == 8 && r.parityOk, "Robot36 VIS=8 even parity");

    // Wrong parity tone -> parity fails, code still readable.
    SstvVisResult r2 = sstvDecodeVisBits(bits, 1300.0f);
    check(r2.code == 8 && !r2.parityOk, "parity mismatch detected");

    // Out-of-band bit -> code rejected.
    bits[2] = 2000.0f;
    SstvVisResult r3 = sstvDecodeVisBits(bits, 1100.0f);
    check(r3.code == -1 && !r3.allInBand, "oob bit rejected");

    // Zero-crossing estimator on synthetic sine waves at 48 kHz.
    const double sr = 48000.0;
    for (double f : {1200.0, 1900.0}) {
        std::vector<float> s(4800);
        for (int i = 0; i < 4800; ++i)
            s[i] = static_cast<float>(std::sin(2.0 * M_PI * f * i / sr));
        std::vector<float> out;
        sstvInstFreqZeroCrossing(s.data(), s.size(), sr, out);
        const double med = medianNonDefault(out);
        std::printf("sine %.0f Hz -> median inst freq %.1f Hz\n", f, med);
        check(std::abs(med - f) < 150.0, "zero-crossing tracks synthetic tone");
    }

    // Constant signal (no zero crossings) -> neutral 1500 fill.
    std::vector<float> dc(4800, 0.5f);
    std::vector<float> out;
    sstvInstFreqZeroCrossing(dc.data(), dc.size(), sr, out);
    bool neutral = true;
    for (float v : out) if (std::abs(v - 1500.0f) > 1.0f) neutral = false;
    check(neutral, "DC input -> neutral 1500 Hz fill");

    std::printf("sstv_vis: %s\n", failures ? "FAILURES" : "all green");
    return failures ? 1 : 0;
}
