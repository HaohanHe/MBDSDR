// SPDX-License-Identifier: MIT
// Pure-interaction-arithmetic test for the spectrum canvas. These are the rules
// that used to be inlined inside the mouse/wheel event bodies: click-vs-drag
// settle, wheel step-snap, edge-drag bandwidth clamp, and the divider share
// clamp. Assert them directly without a widget.
#include <QtTest/QtTest>
#include <cmath>
#include "ui/spectrum_tune.h"

using namespace mbdsdr::ui;

class TestSpectrumTune : public QObject {
    Q_OBJECT
private slots:
    void freqAtPixelCenterAndEdges();
    void dragTuneScalesWithPixels();
    void clickJumpsToPointer();
    void dragSettleEqualsOffsetModel();
    void wheelSnapLandsOnRoundGrid();
    void edgeDragBandwidthClamps();
    void dividerClampsNeitherPanelBelowMin();
};

// A 800px data area spanning 2.4 MHz centred on 98.5 MHz.
static constexpr int    kX0    = 60;
static constexpr int    kW     = 800;
static constexpr double  kLo    = 98.5e6 - 1.2e6;
static constexpr double  kSpan  = 2.4e6;

void TestSpectrumTune::freqAtPixelCenterAndEdges() {
    const int cx = kX0 + kW / 2;
    QCOMPARE(freqAtPixel(cx, kX0, kW, kLo, kSpan), 98.5e6);
    QCOMPARE(freqAtPixel(kX0, kX0, kW, kLo, kSpan), kLo);
    QCOMPARE(freqAtPixel(kX0 + kW, kX0, kW, kLo, kSpan), kLo + kSpan);
}

void TestSpectrumTune::dragTuneScalesWithPixels() {
    QCOMPARE(tuneFreqAfterDrag(98.5e6, 0, kW, kSpan), 98.5e6);
    // A full-width drag moves the dial by one full visible span.
    QCOMPARE(tuneFreqAfterDrag(98.5e6, kW, kW, kSpan), 98.5e6 + kSpan);
    // Half-width drag = half span.
    QCOMPARE(tuneFreqAfterDrag(98.5e6, kW / 2, kW, kSpan), 98.5e6 + kSpan / 2);
}

void TestSpectrumTune::clickJumpsToPointer() {
    // Press and release at the SAME pixel (a click): settle must jump the dial to
    // that pixel's frequency, NOT stay at the press dial.
    const int px = kX0 + kW / 2 + 100;   // 100px right of centre
    const double target = tuneSettleFreq(
        98.5e6, px, 100, px, 100, kX0, kW, kLo, kSpan);
    QCOMPARE(target, freqAtPixel(px, kX0, kW, kLo, kSpan));
    QVERIFY2(std::abs(target - 98.5e6) > 1000.0,
             "a click must move the dial to the pointer frequency");
}

void TestSpectrumTune::dragSettleEqualsOffsetModel() {
    // A real drag (press left, release 120px right): settle must equal the
    // offset model (idempotent with the last mouseMove), not jump to the pointer.
    const int downX = kX0 + kW / 2;
    const int upX   = downX + 120;
    const double target = tuneSettleFreq(
        98.5e6, downX, 200, upX, 200, kX0, kW, kLo, kSpan);
    QCOMPARE(target, tuneFreqAfterDrag(98.5e6, 120, kW, kSpan));
}

void TestSpectrumTune::wheelSnapLandsOnRoundGrid() {
    // Already on a 10 kHz grid -> step keeps it on the grid.
    QCOMPARE(snapFreqToStep(98.5e6 + 10000.0, 10000.0), 98.51e6);
    // Off-grid dial snaps to the nearest 10 kHz grid line.
    QCOMPARE(snapFreqToStep(98.505e6, 10000.0), 98.51e6);
    QCOMPARE(snapFreqToStep(98.504e6, 10000.0), 98.50e6);
    // No configured step -> pass through unchanged (never invent a grid).
    QCOMPARE(snapFreqToStep(98.505e6, 0.0), 98.505e6);
}

void TestSpectrumTune::edgeDragBandwidthClamps() {
    // Marker tuned at 100 MHz; edge dragged to 99.9 MHz -> 100 kHz half-width.
    QCOMPARE(bandwidthAfterEdgeDrag(100.0e6, 99.9e6, 500.0, 1.0e6), 100.0e3);
    // Below the minimum -> pinned to the minimum.
    QCOMPARE(bandwidthAfterEdgeDrag(100.0e6, 99.9999e6, 500.0, 1.0e6), 500.0);
    // Above the maximum -> pinned to the maximum.
    QCOMPARE(bandwidthAfterEdgeDrag(100.0e6, 90.0e6, 500.0, 1.0e6), 1.0e6);
}

void TestSpectrumTune::dividerClampsNeitherPanelBelowMin() {
    const int pool = 500, minTrace = 100, minFalls = 100;
    // Requested trace too tall -> capped so the waterfall keeps its minimum.
    QCOMPARE(clampTraceHeight(480, pool, minTrace, minFalls), pool - minFalls);
    // Requested trace too short -> floored at the minimum trace height.
    QCOMPARE(clampTraceHeight(20, pool, minTrace, minFalls), minTrace);
    // In-range request passes through untouched.
    QCOMPARE(clampTraceHeight(250, pool, minTrace, minFalls), 250);
}

QTEST_MAIN(TestSpectrumTune)
#include "test_spectrum_tune.moc"
