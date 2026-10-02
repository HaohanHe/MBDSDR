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

// ============================================================================
// Wheel cascade / strip-pan / tune-follow (Phase12-E gap L2, clean-room).
// ============================================================================
// Upstream mechanism studied (SDR++ core/src/gui/main_window.cpp:580-597,
// widgets/waterfall.cpp:411-435, tuner.cpp:22-109 -- GPLv3, mechanism only):
//   * wheel on the trace/waterfall step-tunes by snapInterval, widened x10 while
//     Shift is held (coarse) and narrowed x0.1 while Alt is held (fine);
//   * wheel on the frequency STRIP pans the view by viewBandwidth/20 per notch,
//     a different region from the tuning wheel;
//   * after a long tune the view follows the VFO so it never leaves the screen:
//     if the VFO is still comfortably inside the viewport the view stays put, if
//     it crossed out the viewport the view pans to re-seat it one viewport margin
//     inside the crossed edge, and pan overflow past the capture band edge is
//     clamped (the upstream LO re-tune on overflow is the engine's job, already
//     owned by MBDSDR's vfoSetOffset -- this layer only decides the viewport move).
// All three are plain-number functions so they can be unit-tested with no
// QWidget, no Qt event state and no radio hardware.

// Which modifier cascade a wheel notch carries. Ctrl is deliberately absent:
// the canvas reserves Ctrl+wheel for zoom.
enum class WheelTier {
    Fine,    // Alt   -> step x0.1 (fine trim)
    Normal,  // none  -> step x1
    Coarse,  // Shift -> step x10  (coarse sweep)
};

// Effective per-notch step after the modifier cascade. A non-positive base step
// collapses to 0 (a disabled step never invents a grid).
inline double wheelStepSize(double baseStepHz, WheelTier tier) {
    if (!(baseStepHz > 0.0)) return 0.0;
    double mult = 1.0;
    switch (tier) {
        case WheelTier::Fine:   mult = 0.1; break;
        case WheelTier::Coarse: mult = 10.0; break;
        case WheelTier::Normal: mult = 1.0;  break;
    }
    return baseStepHz * mult;
}

// One wheel notch on the tuning dial. `dialHz` is the current dial, `notches`
// the signed accumulated notch count (angleDelta.y()/120, so a fast flick is
// several notches at once), `baseStepHz` the configured step, `tier` the held
// modifier. The result is snapped onto a 0-anchored grid whose pitch is the
// EFFECTIVE step, so repeated coarse notches walk a round x10 dial grid and
// repeated fine notches walk a round x0.1 grid -- instead of a slowly drifting
// offset. A disabled (non-positive) step returns the dial unchanged.
inline double wheelStepFreq(double dialHz, double notches, double baseStepHz,
                            WheelTier tier) {
    const double step = wheelStepSize(baseStepHz, tier);
    if (!(step > 0.0) || notches == 0.0) return dialHz;
    return snapFreqToStep(dialHz + notches * step, step);
}

// A contiguous capture/FFT band on the frequency axis (the live LO span).
struct FftBand {
    double centerHz = 0.0;
    double widthHz  = 0.0;
    double lo() const { return centerHz - widthHz / 2.0; }
    double hi() const { return centerHz + widthHz / 2.0; }
};

// Wheel on the shared tick/label STRIP pans the view (it does NOT tune).
// `deltaHz` is the signed per-notch displacement the event already computed
// (notches * viewSpan/20). The new view centre is clamped so it cannot roam
// outside the capture band -- panning STOPS at either band edge rather than
// sliding off into FFT data that does not exist. A degenerate (non-positive)
// band width disables the clamp (free roam, e.g. before the first frame).
inline double wheelPanView(double viewCenterHz, double deltaHz, const FftBand& band) {
    double next = viewCenterHz + deltaHz;
    if (band.widthHz > 0.0) next = std::clamp(next, band.lo(), band.hi());
    return next;
}

// The visible viewport on the frequency axis.
struct ViewWindow {
    double centerHz = 0.0;
    double spanHz   = 0.0;
    double lo() const { return centerHz - spanHz / 2.0; }
    double hi() const { return centerHz + spanHz / 2.0; }
};

// After a tune (wheel step / arrow nudge / click on a distant peak) decide
// whether the canvas must follow the VFO so it does not leave the screen.
// MBDSDR's engine already owns the two other legs of SDR++'s normalTuning --
// the in-band channelizer offset slide and the out-of-band LO re-tune -- so
// this implements only the viewport-follow leg:
//   * VFO already sits one `marginFrac` inside BOTH viewport edges -> the view
//     is already comfortably on it: return the current view centre unchanged;
//   * VFO crossed the left edge -> pan the view so the VFO re-enters exactly
//     one margin inside the left edge;
//   * VFO crossed the right edge -> pan so it re-enters one margin inside the
//     right edge;
//   * the panned view centre is then clamped inside the capture band (overflow
//     past the band edge is the engine's LO-re-tune responsibility, out of
//     scope here).
inline double followCenterAfterTune(double vfoHz, const ViewWindow& view,
                                    const FftBand& band,
                                    double marginFrac = 0.10) {
    if (!(view.spanHz > 0.0)) return view.centerHz;
    const double margin = view.spanHz * marginFrac;
    const double lo = view.lo(), hi = view.hi();
    double target = view.centerHz;
    if (vfoHz < lo + margin) {
        // Crossed the left edge: park the VFO one margin inside the left edge.
        target = vfoHz - margin + view.spanHz / 2.0;
    } else if (vfoHz > hi - margin) {
        // Crossed the right edge: park the VFO one margin inside the right edge.
        target = vfoHz + margin - view.spanHz / 2.0;
    } else {
        return view.centerHz;   // comfortably on screen: do not move the canvas.
    }
    if (band.widthHz > 0.0) target = std::clamp(target, band.lo(), band.hi());
    return target;
}

} // namespace ui
} // namespace mbdsdr
