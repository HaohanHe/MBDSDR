// SPDX-License-Identifier: MIT
//
// End-to-end tests for the streaming NOAA APT DSP decoder.
//
// NOT HARDWARE: all inputs are synthetic. We synthesize a known grayscale test
// pattern, pack it into an APT waveform (sync word + space + video A/B +
// telemetry wedge, AM-modulated onto 2400 Hz), feed it through AptDecoder in
// streaming chunks, and assert that the recovered image matches the original.
//
// Clean-room encoder/decoder; no GPL code copied.
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <random>
#include <string>
#include <vector>

#include <QImage>

#include "dsp/apt_decoder.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static int checks = 0;
static void check(bool c, const char* m, const char* detail = "") {
    ++checks;
    if (!c) { ++failures; std::printf("FAIL: %s %s\n", m, detail); }
    else    { std::printf("  ok:  %s %s\n", m, detail); }
}

// ---------------------------------------------------------------------------
// Test-pattern definition (channel A left, channel B right; each 909 wide).
// ---------------------------------------------------------------------------
static const int kRows = 30;   // APT rows to synthesize (15 s at 2 rows/s)

static float patA(int r, int c) {
    float v = 0.25f + 0.5f * (float)r / (kRows - 1);   // vertical gradient
    if (r >= 8 && r <= 20 && c >= 300 && c <= 520) v = 0.95f;  // white square
    if (std::abs(c - r * 22) < 6) v = 0.05f;                   // dark diagonal
    return v;
}
static float patB(int r, int c) {
    float v = 0.25f + 0.5f * (float)c / 908.0f;        // horizontal gradient
    if (r >= 10 && r <= 16 && c >= 100 && c <= 800) v = 0.90f; // white band
    if (std::abs(c - 454) < 8) v = 0.05f;                      // dark stripe
    return v;
}

// 38-sample bipolar sync guard at 1 sample/pixel (identical to the decoder).
static std::vector<int> syncTemplate1() {
    std::vector<int> s(38, -1);
    for (int c = 0; c < 7; ++c) {
        s[2 + c * 4 + 2] = +1;
        s[2 + c * 4 + 3] = +1;
    }
    return s;
}

// ---------------------------------------------------------------------------
// Synthesize APT baseband audio from the test pattern.
//
// withSync=false produces a flat video tone (no 7-pulse word) for the
// "no sync -> no lock" negative test. noiseStddev adds Gaussian noise.
// ---------------------------------------------------------------------------
static std::vector<float> generateAptAudio(double fs, bool withSync,
                                          float noiseStddev, unsigned seed) {
    const auto sT = syncTemplate1();
    // Build the 1-sample/pixel video envelope for every row.
    std::vector<float> video((size_t)kRows * APT_PX_PER_ROW, 0.0f);
    for (int r = 0; r < kRows; ++r) {
        float* row = &video[(size_t)r * APT_PX_PER_ROW];
        // Channel A: sync frame (high at +1 template positions, else low).
        for (int p = 0; p < 38; ++p)
            row[p] = withSync ? (sT[p] == +1 ? 1.0f : 0.0f) : 0.5f;
        row[38] = 0.0f;                                   // last sync pixel
        for (int p = 39; p < APT_VIDEO_A_OFFSET; ++p) row[p] = 0.0f;  // space
        for (int p = 0; p < APT_PX_VIDEO; ++p)
            row[APT_VIDEO_A_OFFSET + p] = patA(r, p);
        for (int p = 0; p < APT_PX_TELEMETRY; ++p)        // 16-step wedge
            row[APT_VIDEO_A_OFFSET + APT_PX_VIDEO + p] = (float)p / (APT_PX_TELEMETRY - 1);
        // Channel B: sync word INVERTED (anti-correlates, no false lock).
        for (int p = 0; p < 38; ++p)
            row[APT_PX_PER_CHANNEL + p] =
                withSync ? (sT[p] == +1 ? 0.0f : 1.0f) : 0.5f;
        row[APT_PX_PER_CHANNEL + 38] = 0.0f;
        for (int p = APT_PX_PER_CHANNEL + 39; p < APT_VIDEO_B_OFFSET; ++p) row[p] = 0.0f;
        for (int p = 0; p < APT_PX_VIDEO; ++p)
            row[APT_VIDEO_B_OFFSET + p] = patB(r, p);
        for (int p = 0; p < APT_PX_TELEMETRY; ++p)
            row[APT_VIDEO_B_OFFSET + APT_PX_VIDEO + p] = (float)p / (APT_PX_TELEMETRY - 1);
    }

    // Upsample to audio rate (nearest pixel) and AM-modulate onto 2400 Hz.
    const double spp = fs / APT_PIXEL_RATE_HZ;         // samples per pixel
    const int total = (int)(kRows * APT_PX_PER_ROW * spp);
    std::vector<float> audio(total);
    for (int i = 0; i < total; ++i) {
        const double gpix = i / spp;
        int rr = (int)(gpix / APT_PX_PER_ROW);
        int pp = (int)(gpix - rr * APT_PX_PER_ROW);
        if (rr >= kRows) rr = kRows - 1;
        const float env = video[(size_t)rr * APT_PX_PER_ROW + pp];
        const double t = i / fs;
        audio[i] = (float)((0.2 + 0.8 * env) * std::cos(2.0 * M_PI * 2400.0 * t));
    }

    if (noiseStddev > 0.0f) {
        std::mt19937 rng(seed);
        std::normal_distribution<float> gauss(0.0f, noiseStddev);
        for (float& a : audio) a += gauss(rng);
    }
    return audio;
}

// Pearson correlation of two equal-length samples (affine-invariant).
static double pearson(const std::vector<float>& x, const std::vector<float>& y) {
    const std::size_t n = x.size();
    if (n < 2) return 0.0;
    double mx = 0, my = 0;
    for (std::size_t i = 0; i < n; ++i) { mx += x[i]; my += y[i]; }
    mx /= n; my /= n;
    double sxx = 0, syy = 0, sxy = 0;
    for (std::size_t i = 0; i < n; ++i) {
        double dx = x[i] - mx, dy = y[i] - my;
        sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
    }
    if (sxx <= 0 || syy <= 0) return 0.0;
    return sxy / std::sqrt(sxx * syy);
}

// Feed audio in streaming chunks; return the decoder.
static AptDecoder runDecode(const std::vector<float>& audio, double fs,
                            int chunk = 8192) {
    AptDecoder dec(fs);
    for (std::size_t off = 0; off < audio.size(); off += chunk) {
        std::vector<float> block(audio.begin() + off,
                                 audio.begin() + std::min(off + (std::size_t)chunk, audio.size()));
        dec.feed(block);
    }
    return dec;
}

// Best Pearson between decoded A/B regions and the original pattern, allowing
// a small row offset (lock may start mid-row).
static void bestImageCorr(const QImage& img, double& corrA, double& corrB) {
    corrA = corrB = -2.0;
    for (int d = -3; d <= 3; ++d) {
        std::vector<float> xa, ya, xb, yb;
        for (int i = 0; i < img.height(); ++i) {
            int pr = i + d;
            if (pr < 0 || pr >= kRows) continue;
            const uchar* line = img.constScanLine(i);
            for (int c = 0; c < APT_PX_VIDEO; ++c) {
                xa.push_back(line[c]);
                ya.push_back(patA(pr, c));
                xb.push_back(line[APT_PX_VIDEO + c]);
                yb.push_back(patB(pr, c));
            }
        }
        corrA = std::max(corrA, pearson(xa, ya));
        corrB = std::max(corrB, pearson(xb, yb));
    }
}

int main() {
    std::printf("== NOAA APT decoder end-to-end tests (synthetic, NOT HARDWARE) ==\n");
    const double fs = 48000.0;

    // --- 1. clean signal: lock + image correlation ----------------------- //
    std::printf("\n[1] clean signal end-to-end\n");
    {
        auto audio = generateAptAudio(fs, true, 0.0f, 42);
        AptDecoder dec = runDecode(audio, fs);
        check(dec.isLocked(), "clean: isLocked() true");
        check(dec.rowCount() >= 20, "clean: assembled >=20 rows",
              (std::string("rows=") + std::to_string(dec.rowCount())).c_str());
        double ca, cb;
        bestImageCorr(dec.image(), ca, cb);
        char d[160];
        std::snprintf(d, sizeof d, "syncCorr=%.3f imgA=%.3f imgB=%.3f",
                       dec.lastSyncCorrelation(), ca, cb);
        check(ca > 0.95 && cb > 0.95, "clean: image Pearson > 0.95", d);
        std::printf("       (clean) %s\n", d);
        check(dec.telemetryWedgeA().size() == APT_PX_TELEMETRY,
              "clean: telemetry wedge extracted (45 px)");
    }

    // --- 2. sync lock period accuracy ----------------------------------- //
    std::printf("\n[2] row-start period = 2080 px (fs/2 s)\n");
    {
        // Locked rows must be ~0.5 s apart. rowCount gives the throughput;
        // sanity: at 48kHz, 30 rows span 15 s = 720000 samples.
        auto audio = generateAptAudio(fs, true, 0.0f, 7);
        AptDecoder dec = runDecode(audio, fs);
        double expectRows = audio.size() / (fs * 0.5);
        char d[128];
        std::snprintf(d, sizeof d, "rows=%d expected~%.1f", dec.rowCount(), expectRows);
        check(std::abs(dec.rowCount() - expectRows) <= 2.0,
              "row rate ~2 rows/s (1-2 row tolerance)", d);
    }

    // --- 3. noise robustness -------------------------------------------- //
    std::printf("\n[3] noisy signal (SNR ~ 3 dB)\n");
    {
        // Signal rms ~0.4; noise std 0.22 -> SNR ~ 5 dB.
        auto audio = generateAptAudio(fs, true, 0.22f, 123);
        AptDecoder dec = runDecode(audio, fs);
        check(dec.isLocked(), "noisy: still locked");
        double ca, cb;
        bestImageCorr(dec.image(), ca, cb);
        char d[160];
        std::snprintf(d, sizeof d, "syncCorr=%.3f imgA=%.3f imgB=%.3f",
                      dec.lastSyncCorrelation(), ca, cb);
        check(ca > 0.8 && cb > 0.8, "noisy: image Pearson > 0.8", d);
        std::printf("       (noisy) %s\n", d);
    }

    // --- 4. loss and re-acquisition ------------------------------------- //
    std::printf("\n[4] lock -> garbage -> relock\n");
    {
        AptDecoder dec(fs);
        auto clean = generateAptAudio(fs, true, 0.0f, 99);
        auto flat  = generateAptAudio(fs, false, 0.0f, 100);  // no sync word
        dec.feed(clean);
        bool locked1 = dec.isLocked();
        int rowsAfterClean = dec.rowCount();
        // Feed several seconds of flat (sync-less) video -> should unlock.
        dec.feed(flat);
        // Feed clean again -> should re-lock.
        dec.feed(clean);
        char d[160];
        std::snprintf(d, sizeof d, "locked1=%d rows=%d nowLocked=%d",
                      locked1, dec.rowCount(), dec.isLocked());
        check(locked1, "relock: locked on first clean", d);
        check(dec.isLocked(), "relock: re-locked after clean again", d);
        check(dec.rowCount() > rowsAfterClean, "relock: image grew after re-lock", d);
    }

    // --- 5. deliberately no sync -> never locks, no fake image ---------- //
    std::printf("\n[5] no sync word -> no lock, no fake image\n");
    {
        auto flat = generateAptAudio(fs, false, 0.0f, 5);
        AptDecoder dec = runDecode(flat, fs);
        check(!dec.isLocked(), "no-sync: isLocked() stays false");
        check(dec.rowCount() == 0, "no-sync: zero rows (no fabricated image)");
    }

    std::printf("\n== checks: %d, failures: %d ==\n", checks, failures);
    return failures ? 1 : 0;
}
