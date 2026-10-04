// SPDX-License-Identifier: MIT
// Peak-hold (maxHold) per-frame decay test for ui::SpectrumDisplay.
//
// Acceptance (Phase35 W2):
//  hold[i] = max(frame[i], hold[i] - kMaxHoldDecayDb) each frame.
//  (a) A fresh peak seeds the hold at its dB.
//  (b) Once the peak disappears, the held bin eases DOWN by exactly the named
//      kMaxHoldDecayDb every frame (not frozen forever).
//  (c) A new rise refreshes the bin straight back up.
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

class TestSpectrumMaxHold : public QObject {
    Q_OBJECT
private slots:
    void holdSeedsOnPeak();
    void holdDecaysEachFrame();
    void freshRiseRefreshesHold();
};

static constexpr int kBins = 64;
static constexpr int  kPeakBin = 10;
static constexpr float kPeakDb = -40.0f;
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

void TestSpectrumMaxHold::holdSeedsOnPeak() {
    ui::SpectrumDisplay w;
    w.resize(800, 500);
    w.setMaxHoldEnabled(true);
    w.setSpectrum(makeFrame(kPeakDb));

    const auto& hold = w.maxHoldEnvelopeForTest();
    QCOMPARE(hold.size(), std::vector<float>::size_type(kBins));
    QVERIFY2(std::abs(hold[kPeakBin] - kPeakDb) < 1e-3f,
             "first frame must seed the held bin at the fresh peak");
}

void TestSpectrumMaxHold::holdDecaysEachFrame() {
    ui::SpectrumDisplay w;
    w.resize(800, 500);
    w.setMaxHoldEnabled(true);
    w.setSpectrum(makeFrame(kPeakDb));

    // Peak gone: every subsequent frame holds the bin at peak - n*decay.
    const float gone = kFloorDb;
    for (int frame = 1; frame <= 3; ++frame) {
        w.setSpectrum(makeFrame(gone));
        const auto& hold = w.maxHoldEnvelopeForTest();
        const float expected = kPeakDb - frame * tokens::kMaxHoldDecayDb;
        QVERIFY2(std::abs(hold[kPeakBin] - expected) < 1e-2f,
                 "held peak must ease down by exactly kMaxHoldDecayDb per frame");
    }
}

void TestSpectrumMaxHold::freshRiseRefreshesHold() {
    ui::SpectrumDisplay w;
    w.resize(800, 500);
    w.setMaxHoldEnabled(true);
    w.setSpectrum(makeFrame(kPeakDb));          // seed at -40
    w.setSpectrum(makeFrame(kFloorDb));         // -41.5
    w.setSpectrum(makeFrame(kFloorDb));         // -43.0

    // A new, higher rise snaps the bin back up immediately (no partial decay).
    const float higher = -30.0f;
    w.setSpectrum(makeFrame(higher));
    const auto& hold = w.maxHoldEnvelopeForTest();
    QVERIFY2(std::abs(hold[kPeakBin] - higher) < 1e-2f,
             "a fresh rise must refresh the held bin straight to the new peak");
}

QTEST_MAIN(TestSpectrumMaxHold)
#include "test_spectrum_maxhold.moc"
