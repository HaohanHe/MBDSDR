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

// Radar-style convergence limiter for the 1 Hz Doppler retune.  Call
// reset(target) once (on capture / switch-on), then advance(liveTarget) every
// tick.  The returned frequency moves toward liveTarget by at most
// kDopplerMaxStepHz Hz, so the loop never dithers the tuner: a slowly-moving
// real track (tens of Hz/s) always lands on target immediately, while a big
// discrete jump (pass start, capture, f0 correction) steps in bounded slices.
class DopplerStepLimiter {
public:
    explicit DopplerStepLimiter(double maxStepHz = tokens::kDopplerMaxStepHz)
        : maxStepHz_(maxStepHz) {}

    // Bind to a starting VFO frequency (no step).
    void reset(double vfoHz) { current_ = vfoHz; armed_ = true; }
    // Stop tracking (checkbox off / pass ended); next advance() re-binds.
    void disarm() { armed_ = false; }
    bool   armed() const { return armed_; }
    double current() const { return current_; }
    double maxStep() const { return maxStepHz_; }

    // Move from current_ toward targetHz by at most maxStepHz_.  Returns the
    // frequency the caller should actually set on the VFO this tick.
    double advance(double targetHz) {
        if (!armed_) { current_ = targetHz; armed_ = true; return current_; }
        const double diff = targetHz - current_;
        if (std::fabs(diff) <= maxStepHz_) {
            current_ = targetHz;                 // within one step: land exactly
        } else {
            current_ += (diff > 0.0 ? maxStepHz_ : -maxStepHz_);   // bounded step
        }
        return current_;
    }

private:
    double maxStepHz_;
    double current_ = 0.0;
    bool   armed_   = false;
};

} // namespace core
} // namespace mbdsdr
