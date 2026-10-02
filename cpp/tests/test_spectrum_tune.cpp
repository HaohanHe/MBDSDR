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
    void wheelCascadeModifierMapping();
    void wheelStripPanClampsToBand();
    void tuneFollowThreeStates();
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

// ---- Wheel modifier cascade (SDR++ Shift x10 coarse / Alt x0.1 fine) -------
void TestSpectrumTune::wheelCascadeModifierMapping() {
    const double dial = 98.5e6;
    const double step = 1000.0;   // 1 kHz configured step
    // No modifier: one forward notch walks +1 kHz onto the 1 kHz grid.
    QCOMPARE(wheelStepFreq(dial, +1.0, step, WheelTier::Normal), 98.501e6);
    // Shift (coarse x10): +10 kHz per notch, snapped onto the 10 kHz grid.
    QCOMPARE(wheelStepFreq(dial, +1.0, step, WheelTier::Coarse), 98.510e6);
    QCOMPARE(wheelStepFreq(dial, -1.0, step, WheelTier::Coarse), 98.490e6);
    // Alt (fine x0.1): +100 Hz per notch, snapped onto the 100 Hz grid.
    QCOMPARE(wheelStepFreq(dial, +1.0, step, WheelTier::Fine), 98.5001e6);
    QCOMPARE(wheelStepFreq(dial, -1.0, step, WheelTier::Fine), 98.4999e6);
    // A fast flick = several notches at once, still on the round grid.
    QCOMPARE(wheelStepFreq(dial, +3.0, step, WheelTier::Coarse), 98.530e6);
    // A dial that is off-grid steps +1k to 98.5065 MHz, then snaps to the nearest
    // 1 kHz grid line (round-half-up -> 98.507 MHz).
    QCOMPARE(wheelStepFreq(98.5055e6, +1.0, step, WheelTier::Normal), 98.507e6);
    // A disabled (non-positive) step never invents a grid: dial unchanged.
    QCOMPARE(wheelStepFreq(dial, +1.0, 0.0, WheelTier::Normal), dial);
    QCOMPARE(wheelStepSize(-5.0, WheelTier::Coarse), 0.0);
}

// ---- Wheel on the tick strip pans the view, clamped to the capture band ----
void TestSpectrumTune::wheelStripPanClampsToBand() {
    // Capture band: 98.5 MHz +/- 1.2 MHz => [97.3, 99.7] MHz.
    const FftBand band{98.5e6, 2.4e6};
    // View centred on the band; pan a small amount right -> centre moves by delta.
    QCOMPARE(wheelPanView(98.5e6, +0.1e6, band), 98.6e6);
    QCOMPARE(wheelPanView(98.5e6, -0.1e6, band), 98.4e6);
    // Pan past the LEFT edge -> pinned at the band lo edge (boundary clamp).
    QCOMPARE(wheelPanView(98.0e6, -5.0e6, band), band.lo());
    // Pan past the RIGHT edge -> pinned at the band hi edge.
    QCOMPARE(wheelPanView(99.0e6, +5.0e6, band), band.hi());
    // Degenerate band (no frame yet) disables the clamp: free roam.
    QCOMPARE(wheelPanView(98.5e6, +1.0e6, FftBand{98.5e6, 0.0}), 99.5e6);
}

// ---- Tune-follow: in-viewport / out-viewport / out-of-band three states ---
void TestSpectrumTune::tuneFollowThreeStates() {
    // View: 2.4 MHz span centred on 98.5 MHz => [97.3, 99.7] MHz, 10% margin.
    const ViewWindow view{98.5e6, 2.4e6};
    const FftBand   band{98.5e6, 2.4e6};   // view == band here
    const double margin = view.spanHz * 0.10;  // 0.24 MHz
    // State 1: VFO comfortably INSIDE the viewport (>= margin from both edges)
    // -> the canvas already has it: follow is a no-op, view centre unchanged.
    QCOMPARE(followCenterAfterTune(98.5e6, view, band), 98.5e6);
    QCOMPARE(followCenterAfterTune(98.0e6, view, band), 98.5e6);
    // State 2: VFO crossed the RIGHT edge (beyond hi - margin = 99.46 MHz) ->
    // pan so the VFO re-enters one margin inside the right edge.
    const double rightMoved = followCenterAfterTune(99.9e6, view, band);
    // New view centre 98.94 MHz => view [97.74, 100.14]; VFO sits one margin
    // inside the right edge.
    QCOMPARE(rightMoved, 99.9e6 + margin - view.spanHz / 2.0);
    // State 2b: VFO crossed the LEFT edge (below lo + margin = 97.54 MHz).
    const double leftMoved = followCenterAfterTune(97.0e6, view, band);
    QCOMPARE(leftMoved, 97.0e6 - margin + view.spanHz / 2.0);
    // State 3: VFO far OUTSIDE the capture band -> the desired pan would escape
    // the band, so the view pins to the band edge (LO re-tune is engine's job).
    QCOMPARE(followCenterAfterTune(200.0e6, view, band), band.hi());
    QCOMPARE(followCenterAfterTune(50.0e6, view, band), band.lo());
    // Degenerate view (no span) -> no follow possible, stay put.
    QCOMPARE(followCenterAfterTune(99.9e6, ViewWindow{98.5e6, 0.0}, band), 98.5e6);
}

QTEST_MAIN(TestSpectrumTune)
#include "test_spectrum_tune.moc"
