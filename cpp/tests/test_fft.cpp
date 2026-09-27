// SPDX-License-Identifier: MIT
// FFT unit tests. Run via `ctest` or directly `./test_fft`.
#include <QtTest/QtTest>
#include <complex>
#include <vector>
#include <cmath>
#include <random>

#include "dsp/fft.h"

using namespace mbdsdr::dsp;

class TestFft : public QObject {
    Q_OBJECT
private slots:
    void sineTonePeak();
    void deltaFunction();
    void dcSignal();
    void ifftRoundtrip();
};

// Test 1: pure complex tone at bin k=10, N=1024. After FFT, peak should be
// at bin 10 with magnitude ~N (complex tone, amplitude 1 -> |FFT| = N at that bin).
void TestFft::sineTonePeak() {
    const int N = 1024;
    const int k = 10;
    std::vector<std::complex<float>> a(N);
    for (int i = 0; i < N; ++i) {
        a[i] = std::exp(std::complex<float>(0, 2.0f * (float)M_PI * k * i / N));
    }
    fft(a);

    // Find peak
    int peakBin = 0;
    float peakMag = 0;
    for (int i = 0; i < N; ++i) {
        float m = std::abs(a[i]);
        if (m > peakMag) { peakMag = m; peakBin = i; }
    }
    QCOMPARE(peakBin, k);
    // Complex tone amplitude 1.0 -> |FFT peak| = N (no window)
    QVERIFY2(std::abs(peakMag - N) / N < 0.01f,
             qPrintable(QString("peak mag %1 expected %2").arg(peakMag).arg(N)));
    // All other bins < 1% of peak (pure tone has zero leakage in exact-bin case)
    for (int i = 0; i < N; ++i) {
        if (i == k) continue;
        QVERIFY2(std::abs(a[i]) < peakMag * 0.01f,
                 qPrintable("sidelobe above 1% of peak"));
    }
}

// Test 2: delta function [1,0,...] -> FFT should be all 1+0j
void TestFft::deltaFunction() {
    const int N = 1024;
    std::vector<std::complex<float>> a(N, 0.0f);
    a[0] = std::complex<float>(1.0f, 0.0f);
    fft(a);
    for (int i = 0; i < N; ++i) {
        QVERIFY2(std::abs(a[i] - std::complex<float>(1,0)) < 1e-4f,
                 qPrintable(QString("delta bin %1 = %2").arg(i).arg(std::abs(a[i]))));
    }
}

// Test 3: DC signal (all ones) -> bin 0 = N, others ~0
void TestFft::dcSignal() {
    const int N = 1024;
    std::vector<std::complex<float>> a(N, std::complex<float>(1.0f, 0.0f));
    fft(a);
    QVERIFY2(std::abs(std::abs(a[0]) - N) < 1e-3f,
             qPrintable(QString("DC bin0 mag %1 expected %2").arg(std::abs(a[0])).arg(N)));
    for (int i = 1; i < N; ++i) {
        QVERIFY2(std::abs(a[i]) < 1e-3f,
                 qPrintable(QString("DC bin %1 not zero: %2").arg(i).arg(std::abs(a[i]))));
    }
}

// Test 4: FFT -> IFFT roundtrip
void TestFft::ifftRoundtrip() {
    const int N = 1024;
    std::mt19937 rng(42);
    std::normal_distribution<float> dist(0.0f, 1.0f);
    std::vector<std::complex<float>> orig(N);
    for (int i = 0; i < N; ++i) orig[i] = {dist(rng), dist(rng)};

    std::vector<std::complex<float>> a = orig;
    fft(a);
    ifft(a);

    float maxErr = 0;
    for (int i = 0; i < N; ++i) {
        float e = std::abs(a[i] - orig[i]);
        if (e > maxErr) maxErr = e;
    }
    QVERIFY2(maxErr < 1e-3f,
             qPrintable(QString("IFFT roundtrip max err %1").arg(maxErr)));
}

QTEST_MAIN(TestFft)
#include "test_fft.moc"
