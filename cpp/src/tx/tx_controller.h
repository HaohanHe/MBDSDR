// SPDX-License-Identifier: MIT
// TX safety interlock. Wraps a backend + modulator and enforces the rules that
// make transmission safe:
//   - nothing is emitted without an explicit user PTT request;
//   - a PTT timeout watchdog forces the transmitter off after maxPttSeconds,
//     measured from the number of samples actually pushed (deterministic);
//   - releasing PTT (or destruction) stops emission immediately.
#pragma once

#include "itx_backend.h"
#include "modulator.h"

#include <QString>
#include <vector>

namespace mbdsdr {
namespace tx {

class TxController {
public:
    TxController(ITxBackend& backend, IModulator& modulator, double sampleRate);
    ~TxController();

    void setMaxPttSeconds(double s) { maxPttSeconds_ = s; }
    double maxPttSeconds() const { return maxPttSeconds_; }

    // Explicit user PTT. Returns false when the backend refuses or the watchdog
    // is already tripped (call resetWatchdog() after a trip to re-arm).
    bool requestPtt(bool on);
    bool transmitting() const { return tx_; }

    // Modulate a real-audio block and push it. Returns samples accepted; zero
    // when not transmitting or when the watchdog trips on this block.
    int transmitAudio(const std::vector<float>& audio);

    double segmentElapsedSeconds() const;
    double totalTxSeconds() const;
    bool watchdogTripped() const { return tripped_; }
    void resetWatchdog() { tripped_ = false; }

    QString status() const;

private:
    ITxBackend& backend_;
    IModulator& modulator_;
    double rate_;
    double maxPttSeconds_ = 120.0;
    long long totalSamples_ = 0;
    long long segmentStart_ = 0;
    bool tx_ = false;
    bool tripped_ = false;
};

} // namespace tx
} // namespace mbdsdr
