// SPDX-License-Identifier: MIT
//
// test_anr.cpp — bare-g++ unit test for AudioNoiseReduction (ANR).
//
// NOTE / 数据声明:
// ---------------------------------------------------------------------------
// All signal data below is SYNTHETIC IN SOFTWARE (deterministic pseudo-random
// Gaussian noise + mathematical sine waves generated in this file).
// NOT HARDWARE / 非硬件合成: no SDR hardware, no RF capture, no real audio
// device, no wav file, no location, no station data is used anywhere in this
// test. Every energy / attenuation number printed below is computed from the
// actual float vectors produced by anr.cpp — never an estimate.
//
// Build & run (no cmake):
//   g++ -std=c++17 -I cpp/src cpp/tests/test_anr.cpp cpp/src/dsp/anr.cpp
//       -o cpp/scratch/test_anr && cpp/scratch/test_anr

#include "dsp/anr.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <random>
#include <string>

using mbdsdr::dsp::AudioNoiseReduction;

namespace {

int g_failures = 0;
int g_checks   = 0;

void check(bool cond, const std::string& msg) {
    ++g_checks;
    if (cond) {
        std::printf("  [PASS] %s\n", msg.c_str());
    } else {
        ++g_failures;
        std::printf("  [FAIL] %s\n", msg.c_str());
    }
}

// Deterministic Gaussian noise generator (Box-Muller).
struct GaussianNoise {
    std::mt19937 rng;
    std::normal_distribution<float> nd;
    explicit GaussianNoise(uint32_t seed, float sigma)
        : rng(seed), nd(0.0f, sigma) {}
    std::vector<float> block(int n) {
        std::vector<float> v(n);
        for (auto& x : v) x = nd(rng);
        return v;
    }
};

std::vector<float> sineBlock(int n, float fs, float f0, float amp, float phase = 0.0f) {
    std::vector<float> v(n);
    for (int i = 0; i < n; ++i) {
        v[i] = amp * std::sin(2.0f * (float)M_PI * f0 * i / fs + phase);
    }
    return v;
}

double energy(const std::vector<float>& v) {
    double e = 0.0;
    for (float x : v) e += (double)x * x;
    return e;
}

double db(double ratio) {
    return 10.0 * std::log10(ratio);
}

// Magnitude of the DFT bin at f0 (coherent average over the block).
double toneBinMag(const std::vector<float>& v, float fs, float f0) {
    double re = 0.0, im = 0.0;
    const double w = 2.0 * M_PI * f0 / fs;
    for (size_t i = 0; i < v.size(); ++i) {
        const double a = w * i;
        re += v[i] * std::cos(a);
        im -= v[i] * std::sin(a);
    }
    return std::sqrt(re * re + im * im) / v.size();
}

// Band energy: sum of squared DFT magnitudes on a coarse grid inside [fLo,fHi].
double bandEnergy(const std::vector<float>& v, float fs, float fLo, float fHi) {
    double e = 0.0;
    for (float f = fLo; f <= fHi; f += 50.0f) {
        double m = toneBinMag(v, fs, f);
        e += m * m;
    }
    return e;
}

constexpr float FS = 48000.0f;

} // namespace

int main() {
    std::printf("== AudioNoiseReduction (ANR) bare-g++ self test ==\n");
    std::printf("   (synthetic software signals only; NOT HARDWARE / 非硬件合成)\n\n");

    // ------------------------------------------------------------------
    std::printf("[1] Pure Gaussian noise: strong suppression (>=6 dB)\n");
    {
        AudioNoiseReduction anr;
        anr.configure(FS, 512);
        anr.setEnabled(true);
        anr.setStrength(1.0f);

        GaussianNoise noise(12345, 0.2f); // sigma=0.2 -> power 0.04
        // 1 s warmup so the noise-floor VAD settles (output discarded).
        anr.process(noise.block(FS));
        // Measured 1 s block.
        auto in  = noise.block(FS);
        auto out = anr.process(in);
        check(out.size() == in.size(), "output length == input length");

        double eIn  = energy(in);
        double eOut = energy(out);
        double att  = db(eOut / eIn); // negative = attenuation
        std::printf("       input energy=%.3f  output energy=%.3f  attenuation=%.2f dB\n",
                    eIn, eOut, att);
        std::printf("       (ANR stats: noiseFloor=%.1f dBFS, avgGain=%.2f dB)\n",
                    anr.noiseFloorDb(), anr.averageGainDb());
        check(att <= -6.0, "noise block attenuated by >= 6 dB");
    }

    // ------------------------------------------------------------------
    std::printf("[2] Zero input -> zero output (no musical noise in silence)\n");
    {
        AudioNoiseReduction anr;
        anr.setEnabled(true);
        anr.setStrength(1.0f);
        std::vector<float> zeros(FS / 2, 0.0f); // 0.5 s of digital silence
        auto out = anr.process(zeros);
        double maxAbs = 0.0;
        for (float x : out) maxAbs = std::max(maxAbs, (double)std::fabs(x));
        std::printf("       max |output| over 0.5 s silence = %.2e\n", maxAbs);
        check(maxAbs < 1e-6, "silence produces no musical-noise artifacts");
    }

    // ------------------------------------------------------------------
    std::printf("[3] Pure sine tone: not distorted (loss < 3 dB)\n");
    {
        AudioNoiseReduction anr;
        anr.setEnabled(true);
        anr.setStrength(1.0f);
        auto warm = sineBlock(FS, FS, 1000.0f, 0.2f); // 1 s warmup
        anr.process(warm);
        auto in  = sineBlock(FS, FS, 1000.0f, 0.2f);
        auto out = anr.process(in);
        double mIn  = toneBinMag(in, FS, 1000.0f);
        double mOut = toneBinMag(out, FS, 1000.0f);
        double loss = db(mOut / mIn);
        std::printf("       tone-bin mag in=%.4f out=%.4f  loss=%.2f dB\n",
                    mIn, mOut, loss);
        check(loss > -3.0, "pure tone survives within 3 dB");
    }

    // ------------------------------------------------------------------
    std::printf("[4] Tone + noise: tone preserved, off-tone band suppressed\n");
    {
        AudioNoiseReduction anr;
        anr.setEnabled(true);
        anr.setStrength(1.0f);

        GaussianNoise noise(999, 0.2f);
        // 0.8 s noise-only warmup to establish the noise floor, then 1 s of
        // tone+noise warmup so the decision-directed SNR converges.
        anr.process(noise.block(FS * 0.8));
        auto warmN = noise.block(FS);
        auto warmT = sineBlock(FS, FS, 1000.0f, 0.15f);
        for (size_t i = 0; i < warmN.size(); ++i) warmN[i] += warmT[i];
        anr.process(warmN);

        auto n = noise.block(FS);
        auto t = sineBlock(FS, FS, 1000.0f, 0.15f);
        std::vector<float> in(FS);
        for (int i = 0; i < FS; ++i) in[i] = n[i] + t[i];
        auto out = anr.process(in);

        double mIn  = toneBinMag(in, FS, 1000.0f);
        double mOut = toneBinMag(out, FS, 1000.0f);
        double toneLoss = db(mOut / mIn);
        std::printf("       tone-bin loss=%.2f dB\n", toneLoss);
        check(toneLoss > -3.0, "tone energy kept within 3 dB while ANR runs");

        // Off-tone band: 3500-4000 Hz, far from the 1 kHz tone.
        double bIn  = bandEnergy(in, FS, 3500.0f, 4000.0f);
        double bOut = bandEnergy(out, FS, 3500.0f, 4000.0f);
        double bandAtt = db(bOut / bIn);
        std::printf("       off-tone band(3.5-4.0kHz) attenuation=%.2f dB\n", bandAtt);
        check(bandAtt <= -6.0, "off-tone noise band suppressed by >= 6 dB");
    }

    // ------------------------------------------------------------------
    std::printf("[5] Disabled -> exact identity passthrough\n");
    {
        AudioNoiseReduction anr; // default: disabled
        GaussianNoise noise(42, 0.1f);
        auto in  = noise.block(2048);
        auto out = anr.process(in);
        bool identical = (out.size() == in.size());
        for (size_t i = 0; identical && i < in.size(); ++i)
            identical = (in[i] == out[i]);
        check(identical, "disabled: output is bit-exact copy of input");
    }

    // ------------------------------------------------------------------
    std::printf("[6] Strength monotonicity: more strength -> more attenuation\n");
    {
        double att[3] = {0.0, 0.0, 0.0};
        const float strengths[3] = {0.3f, 0.6f, 1.0f};
        for (int s = 0; s < 3; ++s) {
            AudioNoiseReduction anr;
            anr.setEnabled(true);
            anr.setStrength(strengths[s]);
            GaussianNoise noise(777, 0.2f);
            anr.process(noise.block(FS));        // warmup
            auto in  = noise.block(FS);
            auto out = anr.process(in);
            att[s] = db(energy(out) / energy(in));
            std::printf("       strength=%.1f -> attenuation=%.2f dB\n",
                        strengths[s], att[s]);
        }
        check(att[0] < 0.0 && att[0] > att[1] && att[1] > att[2],
              "attenuation strictly increases with strength");
    }

    // ------------------------------------------------------------------
    std::printf("\n== %d checks, %d failures ==\n", g_checks, g_failures);
    if (g_failures == 0) {
        std::printf("ALL GREEN\n");
        return 0;
    }
    return 1;
}
