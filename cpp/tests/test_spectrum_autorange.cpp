// SPDX-License-Identifier: MIT
// Offscreen tests for the two spectrum-interaction hardening items:
//
//  Task 1 -- dB axis auto-range:
//    * driven purely by the real sliding peak of frame.dbfs (no synthetic peak);
//    * a sustained strong-but-not-full-scale signal pulls the ceiling DOWN so the
//      trace stops wasting the upper plot area, then eases back when it recedes;
//    * the on-screen ceiling glides by a bounded step per frame (no flicker);
//    * the manual toggle FREEZES the current on-screen range (not jump back to
//      the old spinbox values) -- "关自动 = 定格当前量程".
//
//  Task 2 -- drag-tune / waterfall contract (the "trace and waterfall disagree"
//  red line):
//    (a) dragging a VFO body onto a known peak retunes it to that peak's
//        frequency (xForFreq <-> freqForX round-trip);
//    (b) the waterfall crop walks the SAME visible window as the trace (its
//        left source column maps to visLoHz within one bin);
//    (c) during a smooth pan drag the waterfall start frequency moves
//        monotonically, never jumping more than one bin between steps.
#include <QtTest/QtTest>
#include <QApplication>
#include <QSettings>
#include <cmath>

#include "ui/spectrum_display.h"
#include "core/spectrum_frame.h"
#include "core/tokens.h"

using namespace mbdsdr;

class TestSpectrumAutoRange : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void cleanupTestCase();
    void defaultCeilingStaysAtFullScale();
    void strongSignalPullsCeilingDownSmoothly();
    void ceilingRecoversWhenSignalFalls();
    void manualToggleFreezesCurrentRange();
    void freezeThenStrongFrameDoesNotMoveCeiling();
    void freezeThenWeakFrameDoesNotMoveCeiling();
    void freezeHonestEmptyState();
    void vfoDragLandsOnPeakFrequency();
    void waterfallCropSharesTraceWindow();
    void panDragWaterfallStartsSmoothly();

private:
    struct SavedKey { QString key; bool had = false; QVariant val; };
    QList<SavedKey> saved_;
};

static constexpr double kFs = 2.4e6;
static constexpr double kF0 = 98.5e6;
static constexpr int    kBins = 512;

static SpectrumFrame makeFrame(int bins, int peakBin, float peakDb, float floorDb) {
    SpectrumFrame fr;
    fr.sampleRateHz = kFs;
    fr.centerFreqHz = kF0;
    fr.fftSize = bins;
    fr.dbfs.assign(bins, floorDb);
    if (peakBin >= 0 && peakBin < bins) fr.dbfs[peakBin] = peakDb;
    fr.sourceName = "test";
    fr.isTestSignal = true;
    return fr;
}

// True frequency of a bin centre.
static double binCenterFreq(int bin, int bins) {
    return (kF0 - kFs / 2.0) + (bin + 0.5) * (kFs / bins);
}

void TestSpectrumAutoRange::initTestCase() {
    const QStringList keys = {
        tokens::kSettingsKeySpecFraction,
        tokens::kSettingsKeyScrollSpeed,
        tokens::kSettingsKeyPalette,
        QStringLiteral("rx/peakThresholdDb"),
    };
    QSettings s("MBDSDR", "MBDSDR");
    saved_.clear();
    for (const QString& k : keys) {
        SavedKey sk; sk.key = k; sk.had = s.contains(k);
        if (sk.had) sk.val = s.value(k);
        s.remove(k);
        saved_.append(sk);
    }
    s.sync();
}

void TestSpectrumAutoRange::cleanupTestCase() {
    QSettings s("MBDSDR", "MBDSDR");
    for (const SavedKey& sk : saved_) {
        if (sk.had) s.setValue(sk.key, sk.val);
        else        s.remove(sk.key);
    }
    s.sync();
}

// Full-scale (0 dBFS) carriers must NOT move the ceiling: there is no headroom
// above full scale, so the scale stays at the default 0 dBFS top.
void TestSpectrumAutoRange::defaultCeilingStaysAtFullScale() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    QVERIFY(w.autoRangeOn());
    for (int i = 0; i < 30; ++i)
        w.setSpectrum(makeFrame(kBins, 256, 0.0f, -100.0f));
    QCOMPARE(w.currentDbCeil(), 0.0f);
    QCOMPARE(w.currentDbFloor(), -100.0f);
}

// A sustained -30 dBFS peak (on a -100..0 scale that sits mid-plot) must pull
// the ceiling down toward a coarse step so the trace expands into the upper
// plot, and every per-frame move must be a small bounded glide (no jump).
void TestSpectrumAutoRange::strongSignalPullsCeilingDownSmoothly() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();

    float prev = w.currentDbCeil();
    float maxStep = 0.0f;
    for (int i = 0; i < 40; ++i) {
        w.setSpectrum(makeFrame(kBins, 200, -30.0f, -100.0f));
        const float now = w.currentDbCeil();
        maxStep = std::max(maxStep, std::fabs(now - prev));
        prev = now;
    }
    // The ceiling eased off the 0 dBFS default toward a lower sensitivity step.
    QVERIFY2(w.currentDbCeil() < -5.0f,
             "sustained strong signal must pull the ceiling down from 0 dBFS");
    // It should settle on a coarse 10 dB step (anti-clip keeps it >= peak+4).
    QVERIFY2(w.currentDbCeil() >= -40.0f, "ceiling must not go below the spinbox floor");
    // Smoothness: no single frame may jump more than the easing cap (+slack).
    QVERIFY2(maxStep <= 1.5f + 0.01f,
             "ceiling must glide <=1.5 dB/frame, never jump");
    // The peak must not be clipped: ceiling stays above the real peak.
    QVERIFY2(w.currentDbCeil() > -30.0f, "ceiling must stay above the real peak (no clip)");
}

// Once the signal returns to full scale the ceiling must ease back up to 0.
void TestSpectrumAutoRange::ceilingRecoversWhenSignalFalls() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();

    for (int i = 0; i < 40; ++i)
        w.setSpectrum(makeFrame(kBins, 200, -30.0f, -100.0f));
    QVERIFY(w.currentDbCeil() < -5.0f);

    for (int i = 0; i < 60; ++i)
        w.setSpectrum(makeFrame(kBins, 200, 0.0f, -100.0f));
    QCOMPARE(w.currentDbCeil(), 0.0f);
}

// Turning auto-range off must FREEZE the current on-screen range (not jump
// back to the old manual spinbox values). The eased ceiling becomes the new
// locked ceiling; the floor stays pinned to its current value.
void TestSpectrumAutoRange::manualToggleFreezesCurrentRange() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setDbRange(-100.0f, 0.0f);
    for (int i = 0; i < 40; ++i)
        w.setSpectrum(makeFrame(kBins, 200, -30.0f, -100.0f));
    QVERIFY(w.currentDbCeil() < -5.0f);   // auto pulled ceiling down from 0
    const float frozenCeil  = w.currentDbCeil();
    const float frozenFloor = w.currentDbFloor();

    w.setAutoRangeOn(false);
    QVERIFY2(!w.autoRangeOn(), "toggle must report off");
    // The current range is FROZEN -- no jump back to the old manual 0.0f.
    QCOMPARE(w.currentDbCeil(), frozenCeil);
    QCOMPARE(w.currentDbFloor(), frozenFloor);
}

// After freezing the range (auto off) at a low ceiling, a much stronger signal
// must NOT move the ceiling up -- the range is locked.
void TestSpectrumAutoRange::freezeThenStrongFrameDoesNotMoveCeiling() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    // Start with a weak signal -> auto keeps ceiling settled low.
    for (int i = 0; i < 40; ++i)
        w.setSpectrum(makeFrame(kBins, 200, -60.0f, -100.0f));
    const float frozenCeil = w.currentDbCeil();
    QVERIFY2(frozenCeil < -10.0f,
             qPrintable(QString("weak signal must keep ceiling low, got %1 dB")
                        .arg(frozenCeil)));

    w.setAutoRangeOn(false);
    // Now push much stronger frames -> ceiling must NOT move up.
    for (int i = 0; i < 30; ++i)
        w.setSpectrum(makeFrame(kBins, 200, -10.0f, -100.0f));
    QCOMPARE(w.currentDbCeil(), frozenCeil);
    QCOMPARE(w.currentDbFloor(), -100.0f);
}

// After freezing at a high ceiling, weaker frames must NOT pull it down.
void TestSpectrumAutoRange::freezeThenWeakFrameDoesNotMoveCeiling() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    // Start with a strong full-scale signal -> auto raises ceiling to 0.
    for (int i = 0; i < 40; ++i)
        w.setSpectrum(makeFrame(kBins, 200, -5.0f, -100.0f));
    QVERIFY2(w.currentDbCeil() > -15.0f,
             qPrintable(QString("strong signal must raise ceiling, got %1 dB")
                        .arg(w.currentDbCeil())));
    const float frozenCeil = w.currentDbCeil();

    w.setAutoRangeOn(false);
    // Now push weak frames -> ceiling must NOT move down.
    for (int i = 0; i < 30; ++i)
        w.setSpectrum(makeFrame(kBins, 200, -70.0f, -100.0f));
    QCOMPARE(w.currentDbCeil(), frozenCeil);
}

// Honest empty state: no frame pushed, turn off auto -> stays at defaults,
// then pushing frames keeps the default range locked (no invented scaling).
void TestSpectrumAutoRange::freezeHonestEmptyState() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    // No frames pushed yet. Current range = defaults (0 dB ceil, -100 dB floor).
    const float defaultCeil  = w.currentDbCeil();
    const float defaultFloor = w.currentDbFloor();
    w.setAutoRangeOn(false);
    QCOMPARE(w.currentDbCeil(), defaultCeil);    // no-op freeze at defaults
    QCOMPARE(w.currentDbFloor(), defaultFloor);

    // Now push real frames -> range stays at defaults (locked, no auto scaling).
    for (int i = 0; i < 30; ++i)
        w.setSpectrum(makeFrame(kBins, 200, -20.0f, -100.0f));
    QCOMPARE(w.currentDbCeil(), defaultCeil);
    QCOMPARE(w.currentDbFloor(), defaultFloor);
}

// (a) Drag the VFO body onto a known peak bin; the emitted tuning frequency
//     must round-trip to that peak's frequency (within one bin).
void TestSpectrumAutoRange::vfoDragLandsOnPeakFrequency() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();

    const int peakBin = 128;
    w.setSpectrum(makeFrame(kBins, peakBin, -40.0f, -100.0f));
    const double peakF = binCenterFreq(peakBin, kBins);

    // One wide NFM VFO so the centre press lands firmly in the body (not on a
    // band edge, which the wider hit-tolerance would otherwise absorb).
    QVector<dsp::VfoMarker> markers;
    dsp::VfoMarker m;
    m.id = 1; m.freqHz = kF0; m.bandwidthHz = 100000.0;
    m.mode = "NFM"; m.selected = true;
    markers.append(m);
    w.setVfoMarkers(markers);

    QSignalSpy spy(&w, &ui::SpectrumDisplay::vfoMarkerCenterTuned);

    const QPoint from(w.xForFrequency(kF0), w.spectrumRect().center().y());
    const QPoint to(w.xForFrequency(peakF), w.spectrumRect().center().y());
    QTest::mousePress(&w, Qt::LeftButton, Qt::NoModifier, from);
    QTest::mouseMove(&w, to, Qt::LeftButton);
    QTest::mouseRelease(&w, Qt::LeftButton, Qt::NoModifier, to);

    QVERIFY2(spy.count() >= 1, "VFO body drag must emit a centre-tune");
    const QVariantList last = spy.takeLast();
    const int id = qvariant_cast<int>(last.at(0));
    const double tuned = qvariant_cast<double>(last.at(1));
    QCOMPARE(id, 1);
    const double binHz = kFs / kBins;
    QVERIFY2(std::abs(tuned - peakF) <= binHz,
             "dragged VFO frequency must land on the peak bin (xForFreq round-trip)");
}

// (b) The waterfall crop must walk the SAME visible window as the trace: the
//     source column it starts at maps back to visLoHz within one bin.
void TestSpectrumAutoRange::waterfallCropSharesTraceWindow() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setSpectrum(makeFrame(kBins, 256, -40.0f, -100.0f));

    const double binHz = kFs / kBins;
    const double bandLo = kF0 - kFs / 2.0;
    const int leftBin = w.waterfallCropLeftBin();
    // Frequency of that left source column (centre), compared to the trace's
    // visible left edge. They agree to within one bin -- no offset between the
    // trace and the waterfall.
    const double leftFreq = bandLo + (leftBin + 0.5) * binHz;
    QVERIFY2(std::abs(leftFreq - w.visLoHz()) <= binHz,
             "waterfall crop left edge must track the trace visible window");

    // Zoom in and re-check: both must move together.
    w.setZoomFactor(4.0);
    const int leftBin2 = w.waterfallCropLeftBin();
    const double leftFreq2 = bandLo + (leftBin2 + 0.5) * binHz;
    QVERIFY2(std::abs(leftFreq2 - w.visLoHz()) <= binHz,
             "after zoom, waterfall crop must still track the trace window");
}

// (c) A smooth strip-pan drag moves the waterfall start frequency monotonically,
//     never jumping more than one bin between consecutive mouse steps.
void TestSpectrumAutoRange::panDragWaterfallStartsSmoothly() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setSpectrum(makeFrame(kBins, 256, -40.0f, -100.0f));

    const double binHz = kFs / kBins;
    const QPoint strip = w.freqStripRect().center();
    QTest::mousePress(&w, Qt::LeftButton, Qt::NoModifier, strip);

    double prev = w.visLoHz();
    double prevLeft = (kF0 - kFs / 2.0) + (w.waterfallCropLeftBin() + 0.5) * binHz;
    bool monotonic = true;
    double maxStep = 0.0;
    // Five small (1 px) steps to the right.
    for (int i = 1; i <= 5; ++i) {
        const QPoint here = strip + QPoint(i, 0);
        QTest::mouseMove(&w, here, Qt::LeftButton);
        const double lo = w.visLoHz();
        const double left = (kF0 - kFs / 2.0) + (w.waterfallCropLeftBin() + 0.5) * binHz;
        // Dragging the strip right shifts the visible window left, so the start
        // frequency must move strictly DOWN each step (one consistent direction).
        if (lo >= prev) monotonic = false;
        maxStep = std::max(maxStep, std::fabs(left - prevLeft));
        prev = lo; prevLeft = left;
    }
    QTest::mouseRelease(&w, Qt::LeftButton, Qt::NoModifier, strip + QPoint(5, 0));

    QVERIFY2(monotonic, "panning must move the visible window monotonically");
    QVERIFY2(maxStep <= binHz + 1.0,
             "waterfall start frequency must jump <=1 bin per drag step (no teleport)");
}

QTEST_MAIN(TestSpectrumAutoRange)
#include "test_spectrum_autorange.moc"
