// SPDX-License-Identifier: MIT
// Minimum-hold (minHold) per-frame envelope test for ui::SpectrumDisplay.
//
// Acceptance (symmetric to max-hold, clean-room SDR++ "min hold"):
//  hold[i] = min(frame[i], hold[i]) every frame -- NO decay.
//  (a) The first frame seeds each bin at its fresh value.
//  (b) A QUIETER frame (lower dBFS) pulls the held bin straight down.
//  (c) Once a quieter value is seen, a later louder frame HOLDS the minimum
//      (the envelope never rises and never fades) until re-armed.
//
// Offscreen: drives setSpectrum() and reads the test-seam envelope; no paint.
#include <QtTest/QtTest>
#include <QApplication>
#include <vector>
#include <cmath>

#include "ui/spectrum_display.h"
#include "core/spectrum_frame.h"
#include "core/tokens.h"

using namespace mbdsdr;

class TestSpectrumMinHold : public QObject {
    Q_OBJECT
private slots:
    void seedsOnFirstFrame();
    void pullsDownOnQuieterFrame();
    void holdsMinimumWithoutRiseOrDecay();
};

static constexpr int kBins = 64;
static constexpr int  kPeakBin = 10;
static constexpr float kFloorDb = -80.0f;

static SpectrumFrame makeFrame(float peakDb) {
    SpectrumFrame fr;
    fr.sampleRateHz = 2.4e6;
    fr.centerFreqHz = 98.5e6;
    fr.fftSize = kBins;
    fr.dbfs.assign(kBins, kFloorDb);
    fr.dbfs[kPeakBin] = peakDb;
    fr.sourceName = "test";
    fr.isTestSignal = true;
    return fr;
}

void TestSpectrumMinHold::seedsOnFirstFrame() {
    ui::SpectrumDisplay w;
    w.resize(800, 500);
    w.setMinHoldEnabled(true);
    w.setSpectrum(makeFrame(-40.0f));

    const auto& hold = w.minHoldEnvelopeForTest();
    QCOMPARE(hold.size(), std::vector<float>::size_type(kBins));
    // First frame seeds each bin at its fresh dBFS (the running minimum so far).
    QVERIFY2(std::abs(hold[kPeakBin] - (-40.0f)) < 1e-3f,
             "first frame must seed the held bin at the fresh value");
    QVERIFY2(std::abs(hold[0] - kFloorDb) < 1e-3f,
             "a quiet bin must seed at the floor on the first frame");
}

void TestSpectrumMinHold::pullsDownOnQuieterFrame() {
    ui::SpectrumDisplay w;
    w.resize(800, 500);
    w.setMinHoldEnabled(true);
    w.setSpectrum(makeFrame(-40.0f));     // held at -40

    // A quieter frame pulls the running minimum straight down.
    w.setSpectrum(makeFrame(-60.0f));
    const auto& hold = w.minHoldEnvelopeForTest();
    QVERIFY2(std::abs(hold[kPeakBin] - (-60.0f)) < 1e-2f,
             "a quieter frame must pull the held bin down to the new minimum");
}

void TestSpectrumMinHold::holdsMinimumWithoutRiseOrDecay() {
    ui::SpectrumDisplay w;
    w.resize(800, 500);
    w.setMinHoldEnabled(true);
    w.setSpectrum(makeFrame(-40.0f));     // held at -40
    w.setSpectrum(makeFrame(-60.0f));     // held at -60 (deeper)

    // The carrier returns to its old level: the envelope must HOLD the minimum
    // (-60), never rise back up and never decay (no per-frame fall-off).
    w.setSpectrum(makeFrame(-40.0f));
    w.setSpectrum(makeFrame(-40.0f));
    const auto& hold = w.minHoldEnvelopeForTest();
    QVERIFY2(std::abs(hold[kPeakBin] - (-60.0f)) < 1e-2f,
             "min-hold must hold the running minimum: no rise, no decay");
}

QTEST_MAIN(TestSpectrumMinHold)
#include "test_spectrum_minhold.moc"
