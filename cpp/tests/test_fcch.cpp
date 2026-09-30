// FCCH detector offline (NOT HARDWARE / 非硬件) synthetic tests.
//
// These feed fabricated complex IQ (a known sine tone + noise) to the pure-logic
// GSM FCCH detector and assert it recovers the tone and the clock-ppm offset.
// No RTL-SDR / no GSM base station is touched; on real hardware without a GSM
// signal the detector honestly returns detected=false.

#include <QtTest/QtTest>

#include <complex>
#include <cmath>
#include <vector>

#include "dsp/fcch_detector.h"

using namespace mbdsdr::dsp;

class TestFcch : public QObject {
    Q_OBJECT

private slots:
    void constantTone_recoversPpm();
    void noiseOnly_notDetected();
    void shortBuffer_notDetected();
};

// Build N samples of a complex tone at `toneHz` under Gaussian-ish noise
// (deterministic pseudo-noise so the test is reproducible).
static std::vector<std::complex<float>> makeTone(double sr, double toneHz,
                                                 double noiseAmp, std::size_t N) {
    std::vector<std::complex<float>> x(N);
    unsigned seed = 0x12345678u;
    for (std::size_t n = 0; n < N; ++n) {
        const double ph = 2.0 * M_PI * toneHz * static_cast<double>(n) / sr;
        // deterministic pseudo-noise in [-noiseAmp, noiseAmp]
        seed = seed * 1664525u + 1013904223u;
        const double nr = (static_cast<double>(seed >> 8) / 16777216.0 - 0.5) * noiseAmp;
        x[n] = std::complex<float>(static_cast<float>(std::cos(ph) + nr),
                                   static_cast<float>(std::sin(ph) + nr));
    }
    return x;
}

void TestFcch::constantTone_recoversPpm() {
    const double sr = 270833.0;
    const double center = 935.0e6;  // an arbitrary ARFCN downlink carrier
    const double truePpm = 10.0;   // LO biased +10 ppm
    // A clock error of d ppm shifts the baseband FCCH tone by ~center*d (not by
    // the tone itself): tone = fcchTone + center*d/1e6.
    const double tone = kFcchToneHz + center * truePpm / 1.0e6;

    auto x = makeTone(sr, tone, /*noiseAmp=*/0.05, 4096);
    FcchResult r = detectFcch(x, sr, center);

    QVERIFY2(r.detected, "single FCCH-strength tone must be detected");
    QVERIFY2(std::fabs(r.measuredToneHz - tone) < 800.0,
             "measured tone within ~one FFT bin of injected tone");
    QVERIFY2(std::fabs(r.ppm - truePpm) < 2.0,
             "estimated ppm within +-2 ppm of the injected clock offset");
}

void TestFcch::noiseOnly_notDetected() {
    const double sr = 270833.0;
    auto x = makeTone(sr, /*toneHz=*/0.0, /*noiseAmp=*/1.0, 4096);
    FcchResult r = detectFcch(x, sr, 935.0e6);
    QVERIFY2(!r.detected, "noise-only must honestly report not detected");
}

void TestFcch::shortBuffer_notDetected() {
    const double sr = 270833.0;
    auto x = makeTone(sr, kFcchToneHz, 0.05, 128);  // below the 512 floor
    FcchResult r = detectFcch(x, sr, 935.0e6);
    QVERIFY2(!r.detected, "too-short input must honestly report not detected");
}

QTEST_MAIN(TestFcch)
#include "test_fcch.moc"
