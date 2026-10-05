// SPDX-License-Identifier: MIT
//
// Satellite-pass "one-tap capture" + live Doppler auto-compensation logic.
//
// This header is deliberately UI-free (only QString) so the decision logic can
// be unit-tested offscreen without a MainWindow or a radio:
//
//   * recommendSatelliteMode(f0Hz) -- frequency-domain -> default demod mode /
//     IF bandwidth recommendation.  It maps by the DOWNLINK CARRIER's band
//     (public satellite downlink allocations), NEVER by a hard-coded satellite
//     name.  Unknown f0 (0) falls back to a conservative analog default but the
//     caller is expected to refuse capture when f0 is unknown.
//
//   * captureTargetHz(f0, dopplerAtPeak) -- the frequency to retune the active
//     VFO to when the user presses 捕获: nominal downlink + the predicted peak
//     Doppler suggestion.  Returns 0 when the carrier is unknown (honest).
//
//   * DopplerStepLimiter -- radar-style convergence throttle for the 1 Hz live
//     retune.  Each tick it moves the VFO at most kDopplerMaxStepHz toward the
//     fresh f0+fd target, so a real (slowly moving) LEO track always keeps up
//     while a one-shot jump / pass start settles in bounded steps instead of
//     slamming the local oscillator and dithering.
//
// Honesty: every value here is derived from the real SatPass (f0DownlinkHz,
// dopplerAtPeakHz) and a real propagateAt() range-rate.  Nothing is invented.
#pragma once

#include <QString>
#include <cmath>

#include "core/tokens.h"   // kDopplerMaxStepHz (named throttle token)

namespace mbdsdr {
namespace core {

// Recommended demodulator + IF bandwidth for a downlink carrier, chosen purely
// from the frequency band it lives in.  These match the public satellite
// downlink allocations the app already knows about (NOAA/Meteor APT, amateur
// LEO telemetry, GOES LRIT, ADS-B); they are a sensible *default*, not a claim
// that every signal in-band decodes perfectly.
struct SatChannelMode {
    QString mode;          // one of the demodCombo_ entries (NFM/WFM/BPSK/...)
    double  bandwidthHz = 0.0;
    QString bandNote;      // human-readable band label for the status line
};

inline SatChannelMode recommendSatelliteMode(double f0Hz) {
    // ADS-B squitter band (1090 MHz): wideband capture, the dedicated decoder.
    if (f0Hz >= 1088.0e6 && f0Hz <= 1092.0e6)
        return {"ADS-B", 2.0e6, QStringLiteral("ADS-B 1090")};

    // GOES/MetOp LRIT/HRIT L-band (1690-1710 MHz): wide digital downlink.
    if (f0Hz >= 1690.0e6 && f0Hz <= 1710.0e6)
        return {"BPSK", 30000.0, QStringLiteral("L 波段 LRIT")};

    // NOAA/Meteor APT VHF weather-sat broadcast (136-138 MHz): wide-FM image.
    if (f0Hz >= 136.0e6 && f0Hz <= 138.0e6)
        return {"WFM", 60000.0, QStringLiteral("VHF APT 137")};

    // Amateur satellite VHF voice/repeater sub-band (144-148 MHz): narrow FM.
    if (f0Hz >= 144.0e6 && f0Hz <= 148.0e6)
        return {"NFM", 12500.0, QStringLiteral("VHF 语音")};

    // LEO telemetry / digipeater UHF sub-bands (400-401 ISS, 435-438 amateur):
    // digital BPSK telemetry (the engine's 2400-baud path).
    if ((f0Hz >= 400.15e6 && f0Hz <= 401.0e6) ||
        (f0Hz >= 435.0e6 && f0Hz <= 438.0e6))
        return {"BPSK", 12000.0, QStringLiteral("UHF 遥测")};

    // Unknown band: conservative analog narrow-FM default.  Capture is still
    // gated by f0DownlinkHz>0, so this only fires for a known-but-unmapped
    // carrier.
    return {"NFM", 12500.0, QStringLiteral("通用")};
}

// Frequency to retune the active VFO to on capture: nominal downlink carrier +
// the predicted peak-elevation Doppler suggestion.  Returns 0 when the carrier
// is unknown (f0Hz == 0) -- the caller must disable the button in that case.
inline double captureTargetHz(double f0DownlinkHz, double dopplerAtPeakHz) {
    if (f0DownlinkHz <= 0.0) return 0.0;   // honest unknown carrier
    return f0DownlinkHz + dopplerAtPeakHz;
}

// =====================================================================
// Doppler compensation chain -- division of labour (so the three loops do
// not fight over the same VFO):
//
//   * TLE compensation (this limiter, 1 Hz, desktop C++): the SLOW, large-
//     scale Doppler ramp of a real LEO pass.  propagateAt() gives the
//     topocentric range-rate -> dopplerHz(f0, vr) -> a desired OFFSET in Hz
//     on top of the nominal downlink carrier f0DownlinkHz.  This limiter is
//     the ONLY thing that retunes the VFO in the sky-tab path; it moves the
//     offset by at most kDopplerMaxStepHz per second.
//   * Blind CFO / Gardner timing recovery (Python, mbdsdr_ai/ssdv_phy): the
//     FAST residual carrier-frequency offset and the symbol clock, closed
//     inside the demodulator on the baseband stream.  The desktop app never
//     talks to it per-sample; it rides on whatever in-band offset this
//     limiter leaves the channeliser at.  Boundary: this limiter's bandwidth
//     budget (a few kHz of slow ramp) stays WELL inside the demodulator's
//     blind-acquisition pull range, so the two loops are non-overlapping.
//
// Honest empty state: with no station / no captured pass there is no TLE
// propagation, so this limiter stays Idle at offset 0 and the UI disables
// the checkbox.  Nothing here invents a TLE or a fake fd.
// =====================================================================

// State machine for the 1 Hz Doppler offset tracker.
//   Idle      : not tracking (offset 0).  advance() one-shot re-binds.
//   Tracking  : normal -- bounded step toward the live range-rate offset.
//   Frozen    : target went non-finite (range-rate lost / stale TLE).  The
//               offset is HELD at its last valid value; we never walk the
//               tuner on a NaN target (that would be a blind scan).  A
//               finite target later resumes tracking.
//   Returning : user switched compensation off -- the offset glides back to
//               0 by the same per-tick clamp, never an instant jump to f0.
enum class DopplerLimiterState { Idle, Tracking, Frozen, Returning };

// Radar-style convergence limiter for the 1 Hz Doppler retune, expressed as a
// pure OFFSET in Hz (the caller applies VFO = f0DownlinkHz + offset()).  Call
// reset(initialOffsetHz) once on capture, then advance(liveOffsetHz) every
// tick.  Three deterministic safety behaviours, all unit-testable off a
// synthetic pass curve:
//
//   1. Step clamp   -- advance() moves offset_ by at most kDopplerMaxStepHz
//                      per tick.  A slowly-moving real track (tens of Hz/s)
//                      lands on target immediately; a big discrete jump
//                      (capture / AOS) converges in bounded slices instead of
//                      slamming the local oscillator.
//   2. Freeze-hold  -- a non-finite target (target lost / range-rate invalid)
//                      freezes the offset at its last valid value and sets
//                      frozen(); the tuner is NOT walked.  A finite target
//                      resumes tracking.
//   3. Smooth return-- requestReturnToZero() (checkbox off) glides the offset
//                      back to 0 within the step limit over the next ticks.
class DopplerStepLimiter {
public:
    explicit DopplerStepLimiter(double maxStepHz = tokens::kDopplerMaxStepHz)
        : maxStepHz_(maxStepHz) {}

    // Bind to a starting Doppler offset (Hz) and start tracking.  Called on
    // capture with the predicted peak-Doppler suggestion (dopplerAtPeakHz).
    void reset(double offsetHz) {
        offset_  = offsetHz;
        frozen_  = false;
        state_   = DopplerLimiterState::Tracking;
    }

    // User switched compensation OFF.  Leave the offset where it is but flip
    // to Returning so the next advance() calls glide it home to 0.  Idempotent;
    // an already-Idle limiter stays Idle.
    void requestReturnToZero() {
        if (state_ == DopplerLimiterState::Idle) return;
        state_ = DopplerLimiterState::Returning;
    }

    // Re-arm to Tracking from the CURRENT offset (used when the user switches
    // compensation back on while a smooth-return is still in flight).  The
    // offset is left where it is; the next advance() steps toward the live
    // target by the clamp -- never a jump.  Idle stays Idle (nothing to resume).
    void rearm() {
        if (state_ == DopplerLimiterState::Idle) return;
        frozen_ = false;
        state_  = DopplerLimiterState::Tracking;
    }

    // Hard stop (pass ended / TLE refetch / station lost / new row selected).
    // Release the offset; the VFO itself is left where the caller parked it
    // (the caller stops driving us).  The next reset() re-binds cleanly.
    void disarm() {
        offset_ = 0.0;
        frozen_ = false;
        state_  = DopplerLimiterState::Idle;
    }

    // 1 Hz tick.  Steps offset_ toward targetOffsetHz by at most maxStepHz_.
    // Returns the offset the caller should apply this tick (VFO = f0 + ret).
    double advance(double targetOffsetHz) {
        switch (state_) {
        case DopplerLimiterState::Idle:
            // Not tracking: one-shot re-bind (safety net; the UI path always
            // reset()s on capture before checking the box).  A non-finite
            // target here must NOT be bound (would poison the offset with NaN);
            // stay Idle at 0.
            if (!std::isfinite(targetOffsetHz)) return offset_;
            offset_ = targetOffsetHz;
            state_  = DopplerLimiterState::Tracking;
            return offset_;

        case DopplerLimiterState::Returning: {
            // Glide toward 0 regardless of the (ignored) target.
            const double diff = 0.0 - offset_;
            if (std::fabs(diff) <= maxStepHz_) {
                offset_ = 0.0;
                state_  = DopplerLimiterState::Idle;   // arrived home
            } else {
                offset_ += (diff > 0.0 ? maxStepHz_ : -maxStepHz_);
            }
            return offset_;
        }

        case DopplerLimiterState::Frozen:
        case DopplerLimiterState::Tracking: {
            // Behaviour 2: a non-finite target means the live range-rate just
            // became unavailable.  HOLD the last valid offset; do NOT step the
            // tuner (a NaN diff would otherwise walk it by the clamp each tick).
            if (!std::isfinite(targetOffsetHz)) {
                frozen_ = true;
                state_  = DopplerLimiterState::Frozen;
                return offset_;
            }
            // A finite target from Frozen resumes tracking.
            frozen_ = false;
            state_  = DopplerLimiterState::Tracking;
            const double diff = targetOffsetHz - offset_;
            if (std::fabs(diff) <= maxStepHz_) {
                offset_ = targetOffsetHz;             // within one step: land
            } else {
                offset_ += (diff > 0.0 ? maxStepHz_ : -maxStepHz_);   // bounded
            }
            return offset_;
        }
        }
        return offset_;
    }

    // The applied Doppler offset (Hz) the caller should add to f0DownlinkHz.
    // This is the PURE, real cumulative value surfaced on the status line.
    double offset()   const { return offset_; }
    // True while a non-finite target has the loop holding its last offset.
    bool   frozen()   const { return frozen_; }
    DopplerLimiterState state() const { return state_; }
    double maxStep()  const { return maxStepHz_; }

private:
    double maxStepHz_;
    double offset_ = 0.0;
    bool   frozen_ = false;
    DopplerLimiterState state_ = DopplerLimiterState::Idle;
};

} // namespace core
} // namespace mbdsdr
