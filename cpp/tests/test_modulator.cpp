// SPDX-License-Identifier: GPL-3.0-or-later
// Modulator -> Demodulator loopback. No hardware: synthesized audio/IQ, this is
// a NOT-HARDWARE DSP self-check. Each analog mode must reconstruct the tone.
#include "tx/modulator.h"
#include "dsp/demod.h"

#include <cmath>
#include <cstdio>
#include <vector>

using mbdsdr::tx::IModulator;
using mbdsdr::tx::ModulatorAM;
using mbdsdr::tx::ModulatorFM;
using mbdsdr::tx::ModulatorSSB;
using mbdsdr::tx::ModulatorCW;
namespace dsp = mbdsdr::dsp;

static int g_failures = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { std::printf("FAIL: %s\n", msg); ++g_failures; } \
} while (0)

static std::vector<float> makeTone(double sr, double f, double amp, int n) {
    std::vector<float> x(n);
    for (int i = 0; i < n; ++i)
        x[i] = static_cast<float>(amp * std::sin(2.0 * M_PI * f * i / sr));
    return x;
}

// Goertzel power at target frequency over [begin,end).
static double goertzel(const std::vector<float>& x, int begin, int end,
                       double target, double sr) {
    const double w = 2.0 * M_PI * target / sr;
    const double c = 2.0 * std::cos(w);
    double s1 = 0, s2 = 0;
    for (int i = begin; i < end; ++i) {
        const double s = x[i] + c * s1 - s2;
        s2 = s1; s1 = s;
    }
    return s1 * s1 + s2 * s2 - c * s1 * s2;
}

static double rms(const std::vector<float>& x, int begin, int end) {
    double a = 0;
    for (int i = begin; i < end; ++i) a += double(x[i]) * x[i];
    return std::sqrt(a / (end - begin));
}

// Returns true if the recovered signal is dominated by the 1 kHz tone.
static bool toneRecovered(const std::vector<float>& y, int skip, double sr) {
    const int end = static_cast<int>(y.size());
    const double p1k = goertzel(y, skip, end, 1000.0, sr);
    const double p2k = goertzel(y, skip, end, 2000.0, sr);
    const double r = rms(y, skip, end);
    // 1k power >> 2k power, and there is real signal present.
    return p1k > 25.0 * (p2k + 1e-12) && r > 0.01;
}

int main() {
    // --- AM ---
    {
        const double sr = 48000, bw = 10000;
        auto audio = makeTone(sr, 1000, 0.5, 48000);
        ModulatorAM mod(sr, bw);
        dsp::DemodAM demod(sr, bw);
        auto iq = mod.process(audio);
        auto y = demod.process(iq);
        CHECK(toneRecovered(y, 6000, sr), "AM loopback 1kHz");
    }
    // --- NFM ---
    {
        const double sr = 48000, bw = 12500;
        auto audio = makeTone(sr, 1000, 0.5, 48000);
        ModulatorFM mod(sr, bw, /*wide=*/false);
        dsp::DemodNFM demod(sr, bw);
        auto y = demod.process(mod.process(audio));
        CHECK(toneRecovered(y, 4000, sr), "NFM loopback 1kHz");
    }
    // --- WFM ---
    {
        const double sr = 250000, bw = 150000;
        auto audio = makeTone(sr, 1000, 0.5, 250000);
        ModulatorFM mod(sr, bw, /*wide=*/true);
        dsp::DemodWFM demod(sr, bw);
        auto y = demod.process(mod.process(audio));
        CHECK(toneRecovered(y, 20000, sr), "WFM loopback 1kHz");
    }
    // --- SSB USB ---
    {
        const double sr = 48000, bw = 2800;
        auto audio = makeTone(sr, 1000, 0.5, 48000);
        ModulatorSSB mod(ModulatorSSB::Sideband::USB, sr, bw);
        dsp::DemodSSB demod(dsp::DemodSSB::Sideband::USB, sr, bw);
        auto y = demod.process(mod.process(audio));
        CHECK(toneRecovered(y, 4000, sr), "SSB USB loopback 1kHz");
    }
    // --- SSB LSB ---
    {
        const double sr = 48000, bw = 2800;
        auto audio = makeTone(sr, 1000, 0.5, 48000);
        ModulatorSSB mod(ModulatorSSB::Sideband::LSB, sr, bw);
        dsp::DemodSSB demod(dsp::DemodSSB::Sideband::LSB, sr, bw);
        auto y = demod.process(mod.process(audio));
        CHECK(toneRecovered(y, 4000, sr), "SSB LSB loopback 1kHz");
    }
    // --- CW: carrier present on mark, absent on space ---
    {
        const double sr = 48000, off = 600;
        std::vector<float> key(24000, 0.0f);
        std::fill(key.begin(), key.begin() + 12000, 1.0f);
        ModulatorCW mod(sr, off);
        auto iq = mod.process(key);
        double markMag = 0, spaceMag = 0;
        for (int i = 2000; i < 12000; ++i) markMag += std::abs(iq[i]);
        for (int i = 14000; i < 24000; ++i) spaceMag += std::abs(iq[i]);
        markMag /= 10000; spaceMag /= 10000;
        CHECK(markMag > 0.9, "CW mark carrier on");
        CHECK(spaceMag < 0.05, "CW space carrier off");
    }

    if (g_failures == 0) std::printf("modulator loopback: all checks passed\n");
    return g_failures ? 1 : 0;
}
