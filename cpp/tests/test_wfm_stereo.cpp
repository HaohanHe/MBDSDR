// SPDX-License-Identifier: MIT
// Offline WFM stereo decoder test. No UI, no hardware:
//
//   SYNTHETIC -- NOT HARDWARE. The composite FM baseband MPX is built by hand
//   here (mono sum M, 19 kHz pilot, 38 kHz DSB difference S), frequency-modulated
//   onto complex IF IQ at 240 kHz, then run through the REAL DemodWFM quadrature
//   discriminator (using its new pre-de-emphasis rawMpxOut() tap) and the REAL
//   WfmStereoDecoder. Three acceptance scenarios from the integration contract:
//
//     1. L=1 kHz, R=3 kHz, clean pilot  -> strong stereo, channel separation >20 dB
//     2. pilot removed                   -> blend eases to 0, Ld == Rd (mono)
//     3. weak / marginal pilot           -> quality below LO, blend falls back
//
// Tonal energy is measured with a coherent DFT (single-bin correlation) over an
// integer number of cycles -- no fft.cpp dependency.
#include <QtTest/QtTest>

#include "dsp/demod.h"
#include "dsp/wfm_stereo.h"

#include <cmath>
#include <complex>
#include <vector>
#include <random>

using namespace mbdsdr::dsp;

// Coherent single-bin DFT magnitude (proportional to tone amplitude; for an
// integer number of cycles of A*cos(.) it returns A/2).
static double dftMag(const std::vector<float>& x, int start, int n, double f, double fs) {
    double re = 0.0, im = 0.0;
    for (int i = 0; i < n; ++i) {
        const double ph = 2.0 * M_PI * f * (start + i) / fs;
        re += x[start + i] * std::cos(ph);
        im += x[start + i] * std::sin(ph);
    }
    return std::sqrt(re * re + im * im) / n;
}

// Build composite MPX -> FM-modulated complex IF IQ.
//   L(t)=al*cos(2pi*1k*t), R(t)=ar*cos(2pi*3k*t), pilot amp = ap.
// noiseStd>0 adds white Gaussian noise to the MPX; atten scales the whole MPX.
static std::vector<std::complex<float>> buildIq(double fs, double seconds,
                                                double al, double ar, double ap,
                                                double noiseStd, double atten,
                                                std::mt19937& rng) {
    const int n = (int)(fs * seconds);
    std::normal_distribution<float> noise(0.0f, (float)noiseStd);
    std::vector<std::complex<float>> iq(n);
    double phase = 0.0;
    for (int i = 0; i < n; ++i) {
        const double t = i / fs;
        const double L = al * std::cos(2.0 * M_PI * 1000.0 * t);
        const double R = ar * std::cos(2.0 * M_PI * 3000.0 * t);
        const double M = 0.5 * (L + R);
        const double S = 0.5 * (L - R);
        double mpx = M + ap * std::cos(2.0 * M_PI * 19000.0 * t)
                    + S * std::cos(2.0 * M_PI * 38000.0 * t);
        mpx = atten * mpx + noise(rng);
        phase += 2.0 * M_PI * 75000.0 * mpx / fs;
        iq[i] = {(float)std::cos(phase), (float)std::sin(phase)};
    }
    return iq;
}

struct StereoRun {
    std::vector<float> mono, side;
    float blend = 0, pilotAmp = 0, quality = 0;
    bool locked = false;
};

static StereoRun runChain(const std::vector<std::complex<float>>& iq, double fs) {
    DemodWFM demod(fs, 200000.0);
    WfmStereoDecoder dec(fs);
    const int block = 24000;
    StereoRun out;
    for (int p = 0; p < (int)iq.size(); p += block) {
        const int c = std::min(block, (int)iq.size() - p);
        std::vector<std::complex<float>> chunk(iq.begin() + p, iq.begin() + p + c);
        demod.process(chunk);
        dec.feed(demod.rawMpxOut());
        out.mono.insert(out.mono.end(), dec.monoOut().begin(), dec.monoOut().end());
        out.side.insert(out.side.end(), dec.sideOut().begin(), dec.sideOut().end());
    }
    out.blend = dec.blend();
    out.pilotAmp = dec.pilotAmplitude();
    out.quality = dec.pilotQuality();
    out.locked = dec.locked();
    return out;
}

class TestWfmStereo : public QObject {
    Q_OBJECT
private slots:
    void stereoSeparation();
    void noPilotFallsBackToMono();
    void weakSignalBlendFallback();
    void mediumPilotProportionalBlend();
};

void TestWfmStereo::stereoSeparation() {
    // SYNTHETIC -- NOT HARDWARE.
    const double fs = 240000.0;
    std::mt19937 rng(12345);
    auto iq = buildIq(fs, 1.0, 0.30, 0.30, 0.15, 0.0, 1.0, rng);
    auto r = runChain(iq, fs);

    // Discard PLL/envelope warm-up, measure over 100 ms of integer cycles.
    const int start = (int)(0.55 * fs);
    const int win   = 24000; // 0.1 s = 100 cycles of 1 kHz
    std::vector<float> Ld(win), Rd(win);
    for (int i = 0; i < win; ++i) {
        const int k = start + i;
        Ld[i] = r.mono[k] + r.blend * r.side[k];
        Rd[i] = r.mono[k] - r.blend * r.side[k];
    }
    double L1 = dftMag(Ld, 0, win, 1000.0, fs);
    double L3 = dftMag(Ld, 0, win, 3000.0, fs);
    double R1 = dftMag(Rd, 0, win, 1000.0, fs);
    double R3 = dftMag(Rd, 0, win, 3000.0, fs);

    double sepL = 20.0 * std::log10(L1 / L3);  // 1 kHz rejection out of L
    double sepR = 20.0 * std::log10(R3 / R1);  // 3 kHz rejection out of R
    double separation = std::min(sepL, sepR);

    qInfo("scenario1: pilotAmp=%.4f quality=%.3f locked=%d blend=%.3f",
          r.pilotAmp, r.quality, (int)r.locked, r.blend);
    qInfo("scenario1: L1=%.4f L3=%.5f R1=%.5f R3=%.4f", L1, L3, R1, R3);
    qInfo("scenario1: sepL=%.2f dB sepR=%.2f dB -> separation=%.2f dB",
          sepL, sepR, separation);

    QVERIFY2(r.locked, "PLL locked on clean 19 kHz pilot");
    QVERIFY2(r.blend > 0.9, "blend driven to full stereo on clean pilot");
    QVERIFY2(separation > 20.0, "channel separation > 20 dB");
}

void TestWfmStereo::noPilotFallsBackToMono() {
    // SYNTHETIC -- NOT HARDWARE. Same tones but NO 19 kHz pilot.
    const double fs = 240000.0;
    std::mt19937 rng(12345);
    auto iq = buildIq(fs, 1.0, 0.30, 0.30, 0.0 /*no pilot*/, 0.0, 1.0, rng);
    auto r = runChain(iq, fs);

    const int start = (int)(0.6 * fs);
    const int win   = 24000;
    double diff = 0.0, level = 0.0;
    for (int i = 0; i < win; ++i) {
        const int k = start + i;
        float ld = r.mono[k] + r.blend * r.side[k];
        float rd = r.mono[k] - r.blend * r.side[k];
        diff += double(ld - rd) * (ld - rd);
        level += double(ld) * ld;
    }
    double crosstalkDb = 10.0 * std::log10(diff / level); // 0 dB == full side
    qInfo("scenario2: pilotAmp=%.4f quality=%.3f locked=%d blend=%.3f L/R_diff=%.2f dB",
          r.pilotAmp, r.quality, (int)r.locked, r.blend, crosstalkDb);

    QVERIFY2(!r.locked, "no pilot -> not locked");
    QVERIFY2(r.blend < 0.05, "blend eased back to mono without pilot");
    QVERIFY2(crosstalkDb < -30.0, "Ld ~= Rd (mono) when blend ~ 0");
}

void TestWfmStereo::weakSignalBlendFallback() {
    // SYNTHETIC -- NOT HARDWARE. Weak/marginal stereo: the pilot subcarrier is
    // present but very small relative to the audio (a station fading in stereo),
    // so pilot quality lands below LO and the blend eases back to mono.
    const double fs = 240000.0;
    std::mt19937 rng(999);
    auto iq = buildIq(fs, 1.2, 0.30, 0.30, /*ap=*/0.02, 0.0, 1.0, rng);
    auto r = runChain(iq, fs);
    qInfo("scenario3: pilotAmp=%.4f quality=%.3f locked=%d blend=%.3f",
          r.pilotAmp, r.quality, (int)r.locked, r.blend);

    QVERIFY2(r.quality < WfmStereoDecoder::kPilotQualityLo,
             "weak pilot: pilot quality below LO");
    QVERIFY2(r.blend < 0.2, "weak pilot: blend smoothly fell back toward mono");
}

void TestWfmStereo::mediumPilotProportionalBlend() {
    // SYNTHETIC -- NOT HARDWARE. A mid-strength pilot: quality lands between
    // LO and HI, so the loop locks but the blend should settle at a value
    // proportional to the quality (not snapped to 0 or 1).
    const double fs = 240000.0;
    std::mt19937 rng(555);
    auto iq = buildIq(fs, 1.5, 0.30, 0.30, /*ap=*/0.045, 0.0, 1.0, rng);
    auto r = runChain(iq, fs);
    qInfo("scenario4: pilotAmp=%.4f quality=%.3f locked=%d blend=%.3f",
          r.pilotAmp, r.quality, (int)r.locked, r.blend);

    QVERIFY2(r.locked, "medium pilot: locked once quality crosses LO");
    QVERIFY2(r.quality > WfmStereoDecoder::kPilotQualityLo &&
             r.quality < WfmStereoDecoder::kPilotQualityHi,
             "medium pilot: quality lands in the LO..HI transition band");
    QVERIFY2(r.blend > 0.05 && r.blend < 0.95,
             "medium pilot: blend rests at an intermediate (proportional) value");
    // Monotonic consistency: settled blend should track the quality ramp.
    double expected = (r.quality - WfmStereoDecoder::kPilotQualityLo) /
                      (WfmStereoDecoder::kPilotQualityHi - WfmStereoDecoder::kPilotQualityLo);
    QVERIFY2(std::fabs(r.blend - expected) < 0.15,
             "medium pilot: blend proportional to quality (within slew tolerance)");
}

QTEST_MAIN(TestWfmStereo)
#include "test_wfm_stereo.moc"
