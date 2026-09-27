// Channelizer correctness tests: rate-correct baseband, channel selection,
// blocker rejection, passthrough. Standalone, no external deps.
#include <cmath>
#include <vector>
#include <complex>
#include <algorithm>
#include <cstdio>
#include "dsp/channelizer.h"
#include "dsp/fft.h"

using namespace mbdsdr::dsp;

static std::vector<std::complex<float>> makeSignal(
        double fs, long n, double fDesired, double fBlocker) {
    std::vector<std::complex<float>> x(n);
    for (long i = 0; i < n; ++i) {
        float p1 = static_cast<float>(2 * M_PI * fDesired * i / fs);
        float p2 = static_cast<float>(2 * M_PI * fBlocker * i / fs);
        x[i] = std::complex<float>(std::cos(p1) + std::cos(p2),
                                   std::sin(p1) + std::sin(p2));
    }
    return x;
}

// Returns |FFT| power spectrum (one-sided folded) with frequency resolution fs/N.
static std::vector<float> powerBins(const std::vector<std::complex<float>>& x,
                                    std::size_t N, double fs, double& outFs) {
    std::vector<std::complex<float>> X(N, {0, 0});
    std::size_t copy = std::min(x.size(), N);
    std::copy_n(x.begin(), copy, X.begin());
    fft(X);
    std::vector<float> p(N / 2 + 1);
    for (std::size_t i = 0; i < p.size(); ++i) p[i] = std::norm(X[i]);
    outFs = fs;
    return p;
}

static int failures = 0;
static void check(bool cond, const char* msg) {
    if (!cond) { ++failures; std::printf("FAIL: %s\n", msg); }
}

int main() {
    const double fs = 2.4e6;
    const double ifRate = 48000.0;
    const long n = 240000; // 0.1 s

    // ---- Test 1: positive offset, blocker rejection ----
    {
        Channelizer ch;
        ch.configure(fs, ifRate, 10000.0, 31);
        check(ch.decimation() == 50, "decimation should be 50");
        check(std::abs(ch.effectiveOutputRateHz() - 48000.0) < 1.0,
              "effective IF rate 48k");

        const double fWant = 3000.0, fBlock = 60000.0;
        auto in = makeSignal(fs, n, fWant, fBlock);
        ch.setVfoOffsetHz(fWant);

        std::vector<std::complex<float>> out;
        const int blk = 2048;
        for (long pos = 0; pos < n; pos += blk) {
            std::size_t cnt = static_cast<std::size_t>(std::min<long>(blk, n - pos));
            std::vector<std::complex<float>> part(in.begin() + pos,
                                                  in.begin() + pos + cnt);
            auto o = ch.process(part);
            out.insert(out.end(), o.begin(), o.end());
        }
        const long expected = n / 50;
        check(std::llabs(static_cast<long>(out.size()) - expected) < 200,
              "output count ~= input/50");

        double ofs;
        const std::size_t N = 16384;
        auto p = powerBins(out, N, ch.effectiveOutputRateHz(), ofs);
        const double binHz = ch.effectiveOutputRateHz() / N;
        // DC (desired now at 0) power.
        float dc = p[0];
        // Peak power outside +/- 6 kHz (the blocker alias lives near 9 kHz).
        float oob = 0.0f;
        std::size_t k6 = static_cast<std::size_t>(6000.0 / binHz);
        for (std::size_t i = k6; i < p.size(); ++i) oob = std::max(oob, p[i]);
        check(dc > 100.0f * oob, "desired at DC >> blocker (>20 dB)");
    }

    // ---- Test 2: negative offset ----
    {
        Channelizer ch;
        ch.configure(fs, ifRate, 10000.0, 31);
        const double fWant = -1500.0, fBlock = 80000.0;
        auto in = makeSignal(fs, n, fWant, fBlock);
        ch.setVfoOffsetHz(fWant);
        std::vector<std::complex<float>> out;
        for (long pos = 0; pos < n; pos += 4096) {
            std::size_t cnt = static_cast<std::size_t>(std::min<long>(4096, n - pos));
            std::vector<std::complex<float>> part(in.begin() + pos,
                                                  in.begin() + pos + cnt);
            auto o = ch.process(part);
            out.insert(out.end(), o.begin(), o.end());
        }
        double ofs;
        const std::size_t N = 16384;
        auto p = powerBins(out, N, ch.effectiveOutputRateHz(), ofs);
        const double binHz = ch.effectiveOutputRateHz() / N;
        std::size_t kNear = static_cast<std::size_t>(300.0 / binHz);
        float inBand = 0.0f;
        for (std::size_t i = 0; i <= kNear; ++i) inBand = std::max(inBand, p[i]);
        float farBand = 0.0f;
        std::size_t kFar = static_cast<std::size_t>(6000.0 / binHz);
        for (std::size_t i = kFar; i < p.size(); ++i) farBand = std::max(farBand, p[i]);
        check(inBand > 100.0f * farBand, "negative-offset desired lands near DC");
    }

    // ---- Test 3: passthrough when requested rate >= input rate ----
    {
        Channelizer ch;
        ch.configure(fs, fs, 1000000.0, 31);
        check(ch.decimation() == 1, "passthrough decimation 1");
        const double fWant = 100000.0;
        auto in = makeSignal(fs, 8192, fWant, 0.0);
        ch.setVfoOffsetHz(fWant);
        auto out = ch.process(in);
        check(out.size() == in.size(), "passthrough keeps length");
        double ofs;
        auto p = powerBins(out, 16384, ch.effectiveOutputRateHz(), ofs);
        const double binHz = ch.effectiveOutputRateHz() / 16384;
        std::size_t k0 = static_cast<std::size_t>(500.0 / binHz);
        float nearDc = 0.0f;
        for (std::size_t i = 0; i <= k0; ++i) nearDc = std::max(nearDc, p[i]);
        float rest = 0.0f;
        for (std::size_t i = k0 + 1; i < p.size(); ++i) rest = std::max(rest, p[i]);
        check(nearDc > 50.0f * rest, "passthrough tuned tone at DC");
    }

    if (failures == 0) std::printf("test_channelizer: ALL PASS\n");
    else std::printf("test_channelizer: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
