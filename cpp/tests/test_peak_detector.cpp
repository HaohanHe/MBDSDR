// SPDX-License-Identifier: MIT
// Peak detector unit tests. Run via `ctest` or directly `./test_peak_detector`.
#include <QtTest/QtTest>
#include <vector>
#include <cmath>

#include "dsp/peak_detector.h"

using namespace mbdsdr::dsp;

class TestPeakDetector : public QObject {
    Q_OBJECT
private slots:
    void detectsTwoRealPeaks();
    void filtersBelowAbsoluteFloor();
};

// Build an fft-shifted dbfs frame: a flat -140 dBFS noise floor with two
// shaped gaussian-ish peaks on bins 256 and 768, plus a small bump at bin 128.
static std::vector<float> makeFrame(int N) {
    std::vector<float> db(N, -140.0f);
    auto bump = [&](int center, float peakDb) {
        db[center] = peakDb;
        db[center - 1] = peakDb - 4.0f;
        db[center + 1] = peakDb - 4.0f;
        db[center - 2] = peakDb - 10.0f;
        db[center + 2] = peakDb - 10.0f;
    };
    bump(256, -10.0f);   // strong carrier
    bump(768, -30.0f);   // weaker carrier
    bump(128, -110.0f);  // bump that passes the RELATIVE gate but not the ABS floor
    return db;
}

void TestPeakDetector::detectsTwoRealPeaks() {
    const int N = 1024;
    const double fs = 2.4e6;
    const double f0 = 98.5e6;
    std::vector<float> db = makeFrame(N);

    QList<PeakInfo> peaks = detectPeaks(db, fs, f0, /*thresholdDb=*/15.0,
                                        /*absFloorDbfs=*/-100.0);

    // Exactly the two real carriers survive; the -110 bump is filtered.
    QCOMPARE(peaks.size(), 2);

    // Loudest first: peak A (-10) then peak B (-30).
    QVERIFY(peaks[0].dbfs > peaks[1].dbfs);
    QVERIFY(std::abs(peaks[0].dbfs - (-10.0f)) < 1.0f);
    QVERIFY(std::abs(peaks[1].dbfs - (-30.0f)) < 1.0f);

    // Frequencies land on the expected bins (within ~1.5 bins).
    const double binHz = fs / (N - 1);
    const double fLowEdge = f0 - fs / 2.0;
    const double expectedA = fLowEdge + 256 * binHz;
    const double expectedB = fLowEdge + 768 * binHz;
    const double err = 1.5 * binHz;
    // peaks[0] = A (loudest), peaks[1] = B.
    QVERIFY(std::abs(peaks[0].freqHz - expectedA) < err);
    QVERIFY(std::abs(peaks[1].freqHz - expectedB) < err);

    // -3 dB bandwidth is positive and physically sane (a few bins wide).
    QVERIFY(peaks[0].bandwidthHz > 0.0);
    QVERIFY(peaks[0].bandwidthHz < fs);
}

void TestPeakDetector::filtersBelowAbsoluteFloor() {
    const int N = 1024;
    const double fs = 2.4e6;
    const double f0 = 98.5e6;
    std::vector<float> db = makeFrame(N);

    // With NO absolute floor (very low floor), the -110 bump at bin 128 now
    // survives the relative gate and must be reported.
    QList<PeakInfo> loose = detectPeaks(db, fs, f0, 15.0, /*absFloor=*/-200.0);
    QCOMPARE(loose.size(), 3);

    // With the production floor (-100 dBFS), that bump is suppressed.
    QList<PeakInfo> tight = detectPeaks(db, fs, f0, 15.0, /*absFloor=*/-100.0);
    QCOMPARE(tight.size(), 2);
}

QTEST_MAIN(TestPeakDetector)
#include "test_peak_detector.moc"
