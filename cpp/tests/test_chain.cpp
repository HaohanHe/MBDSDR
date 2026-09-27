// End-to-end receive-chain test: synthesize a modulated carrier at the source
// rate, push it through Channelizer -> demod -> AudioResampler exactly as the
// engine does, and verify the recovered audio is the original modulation tone
// (not wideband noise). Covers NFM (narrow IF) and WFM (high IF + /5).
#include <cmath>
#include <vector>
#include <complex>
#include <algorithm>
#include <cstdio>
#include "dsp/channelizer.h"
#include "dsp/audio_resampler.h"
#include "dsp/demod.h"
#include "dsp/fft.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

// Frequency-modulated carrier at fs; modulation tone fm, peak deviation fdev.
static std::vector<std::complex<float>> fmCarrier(
        double fs, long n, double fm, double fdev) {
    std::vector<std::complex<float>> x(n);
    double phase = 0.0;
    for (long i = 0; i < n; ++i) {
        double t = i / fs;
        double inst = fdev * std::sin(2 * M_PI * fm * t); // Hz
        if (i > 0) phase += 2 * M_PI * inst / fs;
        x[i] = std::complex<float>(std::cos(phase), std::sin(phase));
    }
    return x;
}

static double dominantHz(const std::vector<float>& audio, double fs) {
    std::size_t N = 1;
    while (N < audio.size() * 2) N <<= 1;
    std::vector<std::complex<float>> X(N, {0, 0});
    // feed the steady-state (second half)
    std::size_t start = audio.size() / 2;
    std::size_t cnt = std::min(audio.size() - start, N);
    for (std::size_t i = 0; i < cnt; ++i) X[i] = {audio[start + i], 0};
    fft(X);
    std::size_t b = 0; float mag = 0;
    for (std::size_t i = 0; i < N / 2; ++i)
        if (std::norm(X[i]) > mag) { mag = std::norm(X[i]); b = i; }
    return static_cast<double>(b) / N * fs;
}

static void runCase(const char* name, double fdev, double ifTarget,
                    double chBw, bool wfm) {
    const double fs = 2.4e6, fm = 1000.0;
    const long n = 480000; // 0.2 s
    auto in = fmCarrier(fs, n, fm, fdev);

    Channelizer ch; ch.configure(fs, ifTarget, chBw, 31); ch.reset();
    const double ifRate = ch.effectiveOutputRateHz();
    std::unique_ptr<IDemod> demod;
    if (wfm) demod = std::make_unique<DemodWFM>(ifRate, chBw);
    else demod = std::make_unique<DemodNFM>(ifRate, 12500);
    AudioResampler rs; rs.configure(ifRate, 48000.0, 31);

    std::vector<float> audio;
    const long chunk = 60000;
    for (long p = 0; p < n; p += chunk) {
        std::size_t c = static_cast<std::size_t>(std::min<long>(chunk, n - p));
        std::vector<std::complex<float>> part(in.begin() + p, in.begin() + p + c);
        auto base = ch.process(part);
        auto aif = demod->process(base);
        auto a = rs.process(aif);
        audio.insert(audio.end(), a.begin(), a.end());
    }
    double got = dominantHz(audio, 48000.0);
    std::printf("%s: recovered tone %.1f Hz (want %.0f)\n", name, got, fm);
    check(std::abs(got - fm) < 60.0, "recovered modulation tone ~= 1 kHz");
}

int main() {
    runCase("NFM", 3000.0, 48000.0, 12500.0, false);
    runCase("WFM", 75000.0, 240000.0, 200000.0, true);
    if (failures == 0) std::printf("test_chain: ALL PASS\n");
    else std::printf("test_chain: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
