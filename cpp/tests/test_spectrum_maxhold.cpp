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
// Phase63 adds the user-selectable decay TIER (慢/中/快, view/maxHoldDecay):
//  (d) setMaxHoldDecayDb() switches the per-frame step to the chosen tier, so the
//      held peak falls faster on 快 (3.0) and lingers longer on 慢 (0.5).
//  (e) A persisted tier is honoured by the canvas ctor on the very first frame.
//  (f) A persisted value outside the named tiers (or non-numeric) honestly falls
//      back to the default 1.5 dB/frame.
//
// Offscreen: drives setSpectrum() and reads the test-seam envelope; no paint.
// Persistence tests write then REMOVE the real QSettings key so they leave no
// residue in the user's MBDSDR config.
#include <QtTest/QtTest>
#include <QApplication>
#include <QSettings>
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
    void decayTierChangesFalloff();
    void persistedTierHonouredOnCtor();
    void illegalPersistedValueFallsBack();
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

// Phase63 (d): the chosen 慢/中/快 tier changes the per-frame fall-off step.
void TestSpectrumMaxHold::decayTierChangesFalloff() {
    ui::SpectrumDisplay w;
    w.resize(800, 500);
    w.setMaxHoldEnabled(true);

    // 快 tier: held peak should ease down by exactly 3.0 dB/frame.
    w.setMaxHoldDecayDb(tokens::kMaxHoldDecayFast);
    QVERIFY2(std::abs(w.maxHoldDecayDbForTest() - tokens::kMaxHoldDecayFast) < 1e-6f,
             "setter must record the 快 tier");
    w.setSpectrum(makeFrame(kPeakDb));
    for (int frame = 1; frame <= 3; ++frame) {
        w.setSpectrum(makeFrame(kFloorDb));
        const auto& hold = w.maxHoldEnvelopeForTest();
        const float expected = kPeakDb - frame * tokens::kMaxHoldDecayFast;
        QVERIFY2(std::abs(hold[kPeakBin] - expected) < 1e-2f,
                 "held peak must fall by 3.0 dB/frame on the 快 tier");
    }

    // 慢 tier: re-arm a fresh peak, now it eases down by only 0.5 dB/frame -- a
    // burst lingers visibly longer than on 快.
    w.setMaxHoldDecayDb(tokens::kMaxHoldDecaySlow);
    QVERIFY2(std::abs(w.maxHoldDecayDbForTest() - tokens::kMaxHoldDecaySlow) < 1e-6f,
             "setter must record the 慢 tier");
    w.setSpectrum(makeFrame(kPeakDb));
    for (int frame = 1; frame <= 3; ++frame) {
        w.setSpectrum(makeFrame(kFloorDb));
        const auto& hold = w.maxHoldEnvelopeForTest();
        const float expected = kPeakDb - frame * tokens::kMaxHoldDecaySlow;
        QVERIFY2(std::abs(hold[kPeakBin] - expected) < 1e-2f,
                 "held peak must fall by 0.5 dB/frame on the 慢 tier");
    }
}

// Phase63 (e): a persisted tier is honoured by the canvas ctor on the first
// frame (the widget combo is just the live reflection). Writes then removes the
// real key so the user's config is left untouched.
void TestSpectrumMaxHold::persistedTierHonouredOnCtor() {
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(tokens::kSettingsKeyMaxHoldDecay, tokens::kMaxHoldDecaySlow);
    }
    {
        ui::SpectrumDisplay w;
        w.resize(800, 500);
        QVERIFY2(std::abs(w.maxHoldDecayDbForTest() - tokens::kMaxHoldDecaySlow) < 1e-6f,
                 "a persisted 慢 tier must be read by the ctor");
    }
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(tokens::kSettingsKeyMaxHoldDecay, tokens::kMaxHoldDecayFast);
    }
    {
        ui::SpectrumDisplay w;
        w.resize(800, 500);
        QVERIFY2(std::abs(w.maxHoldDecayDbForTest() - tokens::kMaxHoldDecayFast) < 1e-6f,
                 "a persisted 快 tier must be read by the ctor");
    }
    QSettings("MBDSDR", "MBDSDR").remove(tokens::kSettingsKeyMaxHoldDecay);
}

// Phase63 (f): a persisted value outside the named tiers (hand-edited config), or
// a non-numeric value, honestly falls back to the default 1.5 dB/frame rather
// than being silently clamped into an arbitrary continuous band.
void TestSpectrumMaxHold::illegalPersistedValueFallsBack() {
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(tokens::kSettingsKeyMaxHoldDecay, 99.0);   // not a named tier
    }
    {
        ui::SpectrumDisplay w;
        w.resize(800, 500);
        QVERIFY2(std::abs(w.maxHoldDecayDbForTest() - tokens::kMaxHoldDecayDefault) < 1e-6f,
                 "an out-of-set persisted decay must fall back to the default tier");
    }
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(tokens::kSettingsKeyMaxHoldDecay, "not-a-number");
    }
    {
        ui::SpectrumDisplay w;
        w.resize(800, 500);
        QVERIFY2(std::abs(w.maxHoldDecayDbForTest() - tokens::kMaxHoldDecayDefault) < 1e-6f,
                 "a non-numeric persisted decay must fall back to the default tier");
    }
    QSettings("MBDSDR", "MBDSDR").remove(tokens::kSettingsKeyMaxHoldDecay);
}

QTEST_MAIN(TestSpectrumMaxHold)
#include "test_spectrum_maxhold.moc"
