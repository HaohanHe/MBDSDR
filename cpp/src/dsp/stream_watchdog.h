// SPDX-License-Identifier: MIT
// Read-stream liveness watchdog: counts consecutive zero/failed IQ reads and
// declares the stream DEAD once a bounded threshold is reached. Pure state
// machine -- the source feeds it read results; no hardware, no timers.
//
// MECHANISM (learned clean-room from librtlsdr GPLv2 pull model): librtlsdr
// itself uses no libusb hotplug callback for the active stream; an honest
// signal that a device was pulled is consecutive rtlsdr_read_sync failures.
// This watchdog turns that raw failure stream into a bounded, testable state
// flip. The engine already has its own zero-read drop counter; this lives
// INSIDE the source so isConnected() stops lying the moment the stream dies.
//
// STATE MACHINE:
//   alive:  counting failures; a successful read resets the counter.
//   dead:   threshold reached; stays dead until reset() (the reconnect path).
#pragma once

namespace mbdsdr {
namespace dsp {

class StreamWatchdog {
public:
    /// maxConsecutiveFailures: how many bad reads in a row trigger dead.
    explicit StreamWatchdog(int maxConsecutiveFailures = 20);

    /// Feed one read result. ok=true means real samples were delivered.
    /// Returns true if the stream is now declared DEAD (threshold reached).
    /// A successful read resets the counter while alive; once DEAD it stays
    /// DEAD until reset() is called by the reconnect path.
    bool onRead(bool ok);

    /// True after the threshold was reached and not yet reset.
    bool isDead() const { return dead_; }

    /// Reset to alive (called on reconnect / after a successful reopen).
    void reset();

    /// Consecutive failures so far (diagnostic / telemetry).
    int failureCount() const { return failCount_; }

private:
    int  maxFail_;
    int  failCount_ = 0;
    bool dead_     = false;
};

} // namespace dsp
} // namespace mbdsdr
