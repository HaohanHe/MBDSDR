// SPDX-License-Identifier: MIT
// Window-normalization tests for dsp::PowerSpectrum (Phase36 W2, clean-room).
//
// Acceptance:
//  (a) Unit-RMS window: after rebuild, mean(w^2) == 1 for Hann/Blackman/Flattop
//      (the GNU Radio normalize mechanism, implemented independently).
//  (b) Level stability: the SAME full-scale real sine, run through each of the
//      three windows, must peak within <1 dB of one another (and near 0 dBFS).
//      Before the fix the on-screen level drifted ~7 dB when the user switched
//      windows (Hann -6, Blackman -7.5, Flattop -13.3 dB).
//
// Run via `ctest -R power_spectrum_window` or ./test_power_spectrum_window.
#include <QtTest/QtTest>
#include <vector>
#include <complex>
#include <cmath>
#include <algorithm>

#include "dsp/power_spectrum.h"

using namespace mbdsdr::dsp;

class TestPowerSpectrumWindow : public QObject {
    Q_OBJECT
private slots:
    void unitRmsNormalized();
    void sinePeakLevelStableAcrossWindows();
    void symmetricEndpoints();
};

static constexpr std::size_t kN = 2048;   // power-of-two FFT length

// A full-scale real cosine at a bin-aligned offset (complex IQ, imag == 0).
static std::vector<std::complex<float>> fullScaleTone(std::size_t n, std::size_t k0) {
    const double pi = 3.14159265358979323846;
    std::vector<std::complex<float>> x(n);
    for (std::size_t m = 0; m < n; ++m)
        x[m] = std::complex<float>(static_cast<float>(std::cos(2.0 * pi * k0 * m / n)), 0.0f);
    return x;
}

void TestPowerSpectrumWindow::unitRmsNormalized() {
    const auto tone = fullScaleTone(kN, kN / 8);
    for (PowerSpectrum::Window w : {PowerSpectrum::Hann, PowerSpectrum::Blackman,
                                    PowerSpectrum::Flattop}) {
        PowerSpectrum ps;
        ps.setWindow(w);
        std::vector<float> out;
        ps.process(tone, out);   // forces rebuildWindow(kN)
        const auto& taps = ps.windowTaps();
        QCOMPARE(taps.size(), kN);
        double sumSq = 0.0;
        for (float v : taps) sumSq += static_cast<double>(v) * v;
        const double meanSq = sumSq / static_cast<double>(kN);
        QVERIFY2(std::abs(meanSq - 1.0) < 1e-3,
                 "RMS-normalized window must have mean(w^2) == 1");
    }
}

void TestPowerSpectrumWindow::sinePeakLevelStableAcrossWindows() {
    const auto tone = fullScaleTone(kN, kN / 8);
    float peakDb[3];
    int idx = 0;
    for (PowerSpectrum::Window w : {PowerSpectrum::Hann, PowerSpectrum::Blackman,
                                    PowerSpectrum::Flattop}) {
        PowerSpectrum ps;
        ps.setWindow(w);
        std::vector<float> out;
        ps.process(tone, out);
        float peak = out[0];
        for (float v : out) peak = std::max(peak, v);
        peakDb[idx++] = peak;
        // Each window should now read near 0 dBFS (the rectangular calibration).
        QVERIFY2(peak > -2.0f && peak < 1.0f,
                 "full-scale tone must peak near 0 dBFS after normalization");
    }
    const float spread = *std::max_element(peakDb, peakDb + 3) -
                         *std::min_element(peakDb, peakDb + 3);
    QVERIFY2(spread < 1.0f,
             "the same sine must peak within 1 dB across Hann/Blackman/Flattop; "
             "window switching must not drift the on-screen level");
}

void TestPowerSpectrumWindow::symmetricEndpoints() {
    const auto tone = fullScaleTone(kN, kN / 8);
    for (PowerSpectrum::Window w : {PowerSpectrum::Hann, PowerSpectrum::Blackman,
                                    PowerSpectrum::Flattop}) {
        PowerSpectrum ps;
        ps.setWindow(w);
        std::vector<float> out;
        ps.process(tone, out);
        const auto& taps = ps.windowTaps();
        QVERIFY2(std::abs(taps.front() - taps.back()) < 1e-4f,
                 "symmetric windows must have equal first and last taps");
    }
}

QTEST_MAIN(TestPowerSpectrumWindow)
#include "test_power_spectrum_window.moc"
