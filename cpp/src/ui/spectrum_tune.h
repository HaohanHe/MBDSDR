// SPDX-License-Identifier: MIT
//
// Pure, widget-free spectrum interaction arithmetic.
// ============================================================================
// The canvas (spectrum_display.cpp) used to inline these rules directly inside
// its QMouseEvent / QWheelEvent bodies, so the "click to tune" settle, the wheel
// snap, the edge-drag bandwidth clamp and the trace/waterfall divider clamp
// could only be exercised through a full offscreen widget. They are now
// header-only functions of plain numbers -- no QWidget, no Qt event state --
// unit-tested directly in test_spectrum_tune.cpp, and the event handlers
// delegate to them. One source of truth; the event code stays thin.
//
// Behaviour contract preserved from the inline versions:
//   * a near-stationary press (< 3 px manhattan) is a CLICK -> jump the dial to
//     the release pixel's frequency (SDR++ "click a peak to tune");
//   * a drag keeps the offset model, so the release settle equals the last
//     mouseMove emission (idempotent -- no frequency jump on a finished drag);
//   * plain wheel steps by the configured step, then snaps onto a 0-anchored
//     step grid so repeated taps land on round dial values;
//   * dragging a VFO edge sets half-bandwidth = |markerTune - edgeFreq|, clamped
//     to the legal band;
//   * dragging the trace<->waterfall divider clamps the trace height so neither
//     panel drops below its minimum.
#pragma once

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace ui {

// Manhattan distance (px) below which a press+release counts as a click (not a
// drag). Matches the long-standing event threshold.
inline constexpr int kClickSettlePx = 3;

// Frequency (Hz) under a canvas x pixel in the data strip.
//   plotX0 = left edge of the data area, plotW = data-area width in px.
inline double freqAtPixel(int x, int plotX0, int plotW, double fLoHz, double spanHz) {
    const double w = (plotW > 0) ? static_cast<double>(plotW) : 1.0;
    return fLoHz + (x - plotX0) / w * spanHz;
}

// Drag-tune: dial frequency after a horizontal drag of dx px from the press.
inline double tuneFreqAfterDrag(double downDialHz, int dxDragPx, int plotW,
                                double spanHz) {
    const double w = (plotW > 0) ? static_cast<double>(plotW) : 1.0;
    return downDialHz + dxDragPx / w * spanHz;
}

// Settle frequency for a press+release on the bare tuning area (no VFO box).
//   * click (press and release within kClickSettlePx) -> jump the dial to the
//     release pixel's frequency;
//   * drag -> offset model, identical to the last mouseMove emission.
inline double tuneSettleFreq(double downDialHz, int downX, int downY,
                             int upX, int upY, int plotX0, int plotW,
                             double fLoHz, double spanHz) {
    const int moved = std::abs(upX - downX) + std::abs(upY - downY);
    if (moved < kClickSettlePx)
        return freqAtPixel(upX, plotX0, plotW, fLoHz, spanHz);
    return tuneFreqAfterDrag(downDialHz, upX - downX, plotW, spanHz);
}

// Snap a tuned frequency onto a step grid anchored at 0 Hz. Repeated wheel-step
// taps then land on round-number dial values instead of a slowly drifting
// offset. stepHz <= 0 (no configured step) => pass through unchanged.
inline double snapFreqToStep(double freqHz, double stepHz) {
    if (!(stepHz > 0.0)) return freqHz;
    return std::round(freqHz / stepHz) * stepHz;
}

// New VFO bandwidth after dragging one edge to edgeFreqHz: half-bandwidth from
// the marker centre, clamped to the legal band. A non-positive bound disables
// that side of the clamp.
inline double bandwidthAfterEdgeDrag(double markerTuneHz, double edgeFreqHz,
                                     double minBwHz, double maxBwHz) {
    double bw = std::abs(markerTuneHz - edgeFreqHz);
    if (minBwHz > 0.0 && bw < minBwHz) bw = minBwHz;
    if (maxBwHz > 0.0 && bw > maxBwHz) bw = maxBwHz;
    return bw;
}

// Divider (trace<->waterfall) drag: clamp the requested trace height so neither
// the trace nor the waterfall drops below its minimum. `pool` is the vertical
// pixel pool shared between them. A degenerate pool (hi < lo) favours the
// minimum trace height rather than inverting the panels.
inline int clampTraceHeight(int traceHpixels, int pool, int minTrace, int minFalls) {
    int lo = minTrace;
    int hi = pool - minFalls;
    if (hi < lo) hi = lo;
    return std::clamp(traceHpixels, lo, hi);
}

} // namespace ui
} // namespace mbdsdr
