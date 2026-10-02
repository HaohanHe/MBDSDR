// Rational channelizer resampler tests: prove 2.048MHz -> 48kHz lands EXACTLY
// on 48kHz (no 47.62kHz pitch / RDS sub-carrier drift), that the integer fast
// path is unchanged, and that output is deterministic. Standalone, no Qt GUI.
#include <cmath>
#include <vector>
#include <complex>
#include <algorithm>
#include <cstdio>
#include "dsp/channelizer.h"
#include "dsp/rational_resampler.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool cond, const char* msg) {
    if (!cond) { ++failures; std::printf("FAIL: %s\n", msg); }
}

// Complex analytic tone at +f Hz relative to clock fs.
static std::vector<std::complex<float>> tone(double fs, long n, double f) {
    std::vector<std::complex<float>> x(n);
    for (long i = 0; i < n; ++i) {
        const double a = 2.0 * M_PI * f * i / fs;
        x[i] = std::complex<float>(static_cast<float>(std::cos(a)),
                                   static_cast<float>(std::sin(a)));
    }
    return x;
}

// Estimate tone frequency from phase increments (skip warm-up samples).
static double estimateFreq(const std::vector<std::complex<float>>& x,
                           double fs, std::size_t skip) {
    double ang = 0.0; long n = 0;
    for (std::size_t i = skip + 1; i < x.size(); ++i) {
        std::complex<double> d(x[i] * std::conj(x[i - 1]));
        ang += std::arg(d);
        ++n;
    }
    if (n == 0) return 0.0;
    return (ang / n) * fs / (2.0 * M_PI);
}

// Run a channelizer over a long block, streaming in chunks.
static std::vector<std::complex<float>> runStream(Channelizer& ch,
        const std::vector<std::complex<float>>& in, long blk = 8192) {
    std::vector<std::complex<float>> out;
    for (long pos = 0; pos < static_cast<long>(in.size()); pos += blk) {
        long cnt = std::min<long>(blk, static_cast<long>(in.size()) - pos);
        std::vector<std::complex<float>> part(in.begin() + pos,
                                             in.begin() + pos + cnt);
        auto o = ch.process(part);
        out.insert(out.end(), o.begin(), o.end());
    }
    return out;
}

int main() {
    const double fs = 2048000.0;   // 2.048 MHz
    const double f48 = 48000.0;

    // ---- 1) GCD decomposition for the 2.048M -> 48k chain ----
    {
        Channelizer ch;
        ch.configure(fs, f48, 20000.0, 31);
        check(ch.mode() == ChannelizerMode::Rational, "2.048M->48k is Rational mode");
        check(ch.decimation() == 32, "power-of-two predecimation = 32");
        check(std::abs(ch.effectiveOutputRateHz() - 48000.0) < 1.0,
              "effective output rate == 48k exactly");
        check(ch.resampler().mode() == RationalResampler::Mode::Rational,
              "residual stage is Rational");
        check(ch.resampler().interp() == 3 && ch.resampler().decim() == 4,
              "residual GCD reduces to interp=3 / decim=4");
    }

    // ---- 2) Integer fast path unchanged (2.048M -> 64k is exact 32:1) ----
    {
        Channelizer ch;
        ch.configure(fs, 64000.0, 20000.0, 31);
        check(ch.mode() == ChannelizerMode::Integer, "2.048M->64k is Integer mode");
        check(ch.decimation() == 32, "integer decimation = 32");
        check(std::abs(ch.effectiveOutputRateHz() - 64000.0) < 1.0,
              "integer output rate == 64k");
        check(ch.resampler().mode() == RationalResampler::Mode::Passthrough,
              "integer path resampler passthrough");
    }

    // ---- 3) Exact output sample count: 1 s @ 2.048M -> ~48000, NOT 47619 ----
    {
        Channelizer ch;
        ch.configure(fs, f48, 20000.0, 31);
        auto in = tone(fs, 2048000, 1000.0); // 1 second
        ch.setVfoOffsetHz(0.0);
        auto out = runStream(ch, in);
        // Old buggy path: round(2048000/48000)=43 -> 2048000/43 = 47619.
        // New rational path: ~48000. Reject anything at the old rate.
        check(out.size() > 47800 && out.size() < 48300,
              "output sample count ~48000 (not 47619)");
        std::printf("   [info] 2.048M->48k output samples over 1 s: %zu\n",
                    out.size());
    }

    // ---- 4) Pitch correctness: 1 kHz tone must stay 1 kHz (no drift) ----
    {
        Channelizer ch;
        ch.configure(fs, f48, 20000.0, 31);
        auto in = tone(fs, 2048000, 1000.0);
        ch.setVfoOffsetHz(0.0);
        auto out = runStream(ch, in);
        const double fEst = estimateFreq(out, ch.effectiveOutputRateHz(), 400);
        std::printf("   [info] estimated tone after 2.048M->48k: %.2f Hz\n", fEst);
        check(std::fabs(fEst - 1000.0) < 10.0,
              "1 kHz tone demodulates to ~1 kHz (pitch not drifted)");
    }

    // ---- 5) Integer path pitch still correct (no regression) ----
    {
        Channelizer ch;
        ch.configure(fs, 64000.0, 20000.0, 31);
        auto in = tone(fs, 2048000, 1000.0);
        ch.setVfoOffsetHz(0.0);
        auto out = runStream(ch, in);
        const double fEst = estimateFreq(out, ch.effectiveOutputRateHz(), 400);
        std::printf("   [info] estimated tone after 2.048M->64k: %.2f Hz\n", fEst);
        check(std::fabs(fEst - 1000.0) < 10.0,
              "integer path 1 kHz tone stays 1 kHz");
    }

    // ---- 6) RationalResampler direct: ratio, pitch, determinism ----
    {
        RationalResampler r;
        r.configure(64000.0, 48000.0, 32);
        check(r.interp() == 3 && r.decim() == 4, "direct resampler L=3 M=4");
        auto in = tone(64000.0, 64000, 1000.0);
        auto out = r.process(in);
        const double fEst = estimateFreq(out, 48000.0, 200);
        std::printf("   [info] direct 64k->48k tone: %.2f Hz, samples %zu\n",
                    fEst, out.size());
        check(std::fabs(fEst - 1000.0) < 10.0, "direct resampler pitch ~1 kHz");

        // Determinism: reconfigure + reprocess must reproduce identical output.
        RationalResampler r2;
        r2.configure(64000.0, 48000.0, 32);
        auto out2 = r2.process(tone(64000.0, 64000, 1000.0));
        bool same = out.size() == out2.size();
        if (same)
            for (std::size_t i = 0; i < out.size(); ++i)
                if (std::abs(out[i] - out2[i]) > 1e-6f) { same = false; break; }
        check(same, "resampler output is deterministic across runs");
    }

    if (failures == 0) std::printf("test_rational_resampler: ALL PASS\n");
    else std::printf("test_rational_resampler: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
