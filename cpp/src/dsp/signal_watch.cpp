// SPDX-License-Identifier: MIT
#include "signal_watch.h"
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

namespace {
// Default trigger: a real signal must lift the total-power RSSI to this dBFS
// level. Chosen above a typical quiet-floor RSSI but well below a talk-spurt;
// the user can move it (and the UI shows the live RSSI as a reference).
constexpr float kDefaultThresholdDb = -50.0f;
// Fast attack so the onset is not missed (the recorder also keeps a pre-roll);
// slower decay so a momentary fade does not retrigger/segment a call.
constexpr double kAttackTauMs = 15.0;
constexpr double kDecayTauMs  = 120.0;
// The smoothed level must hold at/above the threshold for this long before the
// trigger opens -- rejects single-block clicks / transients.
constexpr double kConfirmMs = 50.0;
} // namespace

SignalWatch::SignalWatch(double blockMs)
    : thresholdDb_(kDefaultThresholdDb), blockMs_(blockMs) {
    recomputeAlphas();
}

void SignalWatch::setBlockMs(double ms) {
    ms = std::max(0.1, ms);
    if (std::abs(ms - blockMs_) < 1e-6) return;
    blockMs_ = ms;
    recomputeAlphas();
}

void SignalWatch::recomputeAlphas() {
    attackAlpha_ = static_cast<float>(1.0 - std::exp(-blockMs_ / kAttackTauMs));
    decayAlpha_  = static_cast<float>(1.0 - std::exp(-blockMs_ / kDecayTauMs));
}

void SignalWatch::reset() {
    smoothDb_ = -150.0f;
    aboveHeldMs_ = 0.0;
    active_ = false;
}

bool SignalWatch::update(float levelDb) {
    // Fast-attack / slow-decay smoothing of the REAL measured RSSI.
    const float alpha = (levelDb > smoothDb_) ? attackAlpha_ : decayAlpha_;
    smoothDb_ += alpha * (levelDb - smoothDb_);

    if (!active_) {
        if (smoothDb_ >= thresholdDb_) {
            aboveHeldMs_ += blockMs_;
            if (aboveHeldMs_ >= kConfirmMs) active_ = true;
        } else {
            aboveHeldMs_ = 0.0;
        }
    } else if (smoothDb_ < thresholdDb_) {
        // The decay smoothing already bridges brief fades; once the smoothed
        // level is back under the threshold the trigger closes. The recorder
        // applies its own (longer) end-delay before finalising the file.
        active_ = false;
        aboveHeldMs_ = 0.0;
    }
    return active_;
}

} // namespace dsp
} // namespace mbdsdr
