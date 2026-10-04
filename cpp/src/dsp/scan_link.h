// SPDX-License-Identifier: MIT
//
// Scan <-> schedule linkage: turns the headless FrequencyScanner's bare
// "a carrier appeared" into an automatic "park on it, decode it, record it".
//
// Mechanism contrast (SDR++ scanner + scheduler, GPL -- read for mechanism
// only, re-implemented clean-room here):
//   * SDR++ scanner walks the band on a timer, retunes the *selected VFO*, waits
//     a settle time, then measures the FFT peak over the VFO passband. On a
//     signal it enters a "receiving" (dwell) state and only leaves after the
//     signal has been gone for a linger time. It stops there: it never starts a
//     decoder or a recorder.
//   * SDR++ scheduler is a separate, decoupled framework: a Task is an ordered
//     list of polymorphic Actions (tune-a-VFO, start-a-recorder, ...) fired on a
//     trigger. The scanner itself has no built-in "on signal -> act".
//
// This module is the thin, headless bridge between the two ideas, WITHOUT any
// GUI / module-registration / control-channel dependency (kept inside dsp on
// purpose, so it cannot collide with parallel control/agent work):
//   * it COMPOSES a FrequencyScanner (reused as-is, not modified);
//   * on the Scanning -> Hit edge it fires ONE "activity found" action -- the
//     caller (production engine or the offline test) uses that moment to retune
//     the parked VFO into a decode mode and arm a recorder;
//   * on the Hit -> Scanning/Idle edge (linger elapsed / FixedMs hold done) it
//     fires one "dwell ended" action so the caller can finalise the recording.
//
// Honesty contract (same as FrequencyScanner): this class owns no radio, opens
// no device, sleeps on no wall clock and fabricates neither a frequency nor an
// RSSI. The caller feeds the REAL measured RSSI for the currently tuned channel;
// a quiet band produces NO activity action. The action callbacks are plain
// std::function seams (the clean-room stand-in for SDR++'s Action objects) --
// binding them is what production does; the offline unit test binds them to a
// real VfoManager decoder + a real Recorder.
#pragma once

#include "frequency_scanner.h"

#include <QList>
#include <functional>

namespace mbdsdr {
namespace dsp {

// Action seams. Exactly one callback fires per edge, never more. A null
// callback is simply skipped (the link must not depend on a binding existing).
struct ScanLinkActions {
    // Scanner asked to retune to a new channel (including the very first tune
    // out of start()). The caller retunes the receiver; the real RSSI it then
    // measures on that channel is what the next tick feeds back in.
    std::function<void(double freqHz)> onRetune;
    // A NEW activity just opened (Scanning -> Hit edge). This is the moment to
    // dwell here: switch the parked VFO into a decode mode and arm recording.
    // Fires once per distinct dwell, never again until the link has left Hit.
    std::function<void(double freqHz, float levelDb)> onActivityFound;
    // The dwell has ended (signal gone past linger, or FixedMs hold elapsed):
    // finalise the recording and let the scan resume. freqHz is the channel we
    // were parked on.
    std::function<void(double freqHz)> onDwellEnded;
};

// The link's own lifecycle, normalised from the scanner's finer states.
enum class ScanLinkState { Idle, Scanning, Dwell };

class ScanActivityLink {
public:
    ScanActivityLink();
    ~ScanActivityLink();

    // Forwarded to the composed scanner (see FrequencyScanner).
    void setConfig(const ScanConfig& c);
    void setBookmarkFrequencies(const QList<double>& hz);
    const FrequencyScanner& scanner() const;

    // Bind the action seams. May be set once before start() or (idempotently)
    // replaced while idle.
    void setActions(ScanLinkActions a);

    // Start a fresh scan (clears dwell count). Fires onRetune for the first
    // channel on the next tick.
    void start();
    void stop();

    // Drive one tick. elapsedMs = time since the previous tick; rssiDb = the
    // REAL measured RSSI (dBFS) on the channel the scanner is currently tuned
    // to. Returns the frequency the receiver should be on. Fires the edge
    // callbacks described on ScanLinkActions.
    double tick(int elapsedMs, float rssiDb);

    // ---- Read-out ---------------------------------------------------------
    ScanLinkState state() const;
    int dwellCount() const;            // distinct activities parked on (this start)
    double parkedFrequency() const;    // channel currently parked on, else 0
    QList<double> retuneLog() const;  // every channel the scanner asked to retune to

private:
    FrequencyScanner scanner_;
    ScanLinkActions  actions_;

    ScanLinkState state_ = ScanLinkState::Idle;
    bool   wasHit_ = false;     // previous tick's scanner Hit edge latch
    int    dwellCount_ = 0;
    double parkedFreq_ = 0.0;
    QList<double> retuneLog_;
};

} // namespace dsp
} // namespace mbdsdr
