// SPDX-License-Identifier: MIT
// Discrete tuner gain table: snaps a continuous UI gain request to the nearest
// legal hardware step and reports the ACTUAL applied gain honestly.
//
// MECHANISM (learned clean-room from librtlsdr GPLv2 src/librtlsdr.c:956
// rtlsdr_get_tuner_gains -- we do NOT copy that driver's arrays): the C driver
// returns a per-tuner fixed array of legal gain levels in tenths of dB
// (R82xx 29 levels, E4000 14 levels, ...). This class takes that table as an
// injected value (production: filled from rtlsdr_get_tuner_gains; tests: a
// fixed fake table) and provides the pure snapping logic.
//
// HONESTY MODEL:
//   * Empty table  -> "no discrete info available". setGain passes the request
//     through unchanged; actualGainDb() equals the request. We never invent a
//     step.
//   * Non-empty    -> snap to the nearest legal step; out-of-range clamps to
//     the min/max step; half-step ties resolve to the higher step
//     (deterministic). The driver then receives the snapped tenths value.
#pragma once

#include <vector>

namespace mbdsdr {
namespace dsp {

class TunerGainTable {
public:
    /// Replace the discrete legal levels (tenths of dB). Ascending order is
    /// expected for readability but snap() is order-independent. An empty
    /// vector means "unknown / passthrough" (honest empty state).
    void setTable(std::vector<int> gainsDb10);

    /// True when a real discrete table has been loaded.
    bool empty() const { return tableDb10_.empty(); }

    /// Number of legal steps.
    int size() const { return static_cast<int>(tableDb10_.size()); }

    /// Snap a requested gain (dB) to the nearest legal step, returned in dB.
    /// Out-of-range requests clamp to min/max. Half-step ties -> higher step.
    /// With an empty table the value is returned unchanged (passthrough).
    double snap(double gainDb) const;

    /// All legal steps in dB (for a UI step combo). Empty when unknown.
    std::vector<double> availableGainsDb() const;

    /// Record the gain ACTUALLY accepted by the driver (dB). Called after the
    /// hardware accepts a snapped level; gain() then reports this real number
    /// rather than the continuous UI request.
    void setActualGainDb(double db) { actualDb_ = db; }
    double actualGainDb() const { return actualDb_; }

private:
    std::vector<int> tableDb10_;   // legal levels, tenths of dB
    double actualDb_ = 0.0;        // last applied (snapped) gain, dB
};

} // namespace dsp
} // namespace mbdsdr
