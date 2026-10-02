// SPDX-License-Identifier: MIT
//
// Real-time sound-card link health state machine. QtAudioSink feeds it the raw
// outcomes it observes from QAudioSink (writes, buffer-drained underruns, fatal
// state errors) and reads back a small state + a human status string for the
// status strip. It owns no device and no timer -- it is a pure state machine,
// exactly like StreamWatchdog, so the underrun / disconnect / bounded-reconnect
// logic is unit-testable offline without an audio device.
//
// HEADER-ONLY on purpose: QtAudioSink (compiled by ~20 test/screenshot targets)
// holds an AudioLinkHealth member, so an out-of-line .cpp would have to be added
// to every one of those targets. Inline methods keep the dependency at the
// header level.
//
// MECHANISM (learned clean-room from sdrpp GPLv3 sink_modules/audio_sink):
//   * sdrpp decouples the DSP thread from the sound-card interrupt with a Packer
//     and treats the sound card as the master clock; a starved buffer is an
//     underrun, not a crash.
//   * its RtAudio errorCallback handles RTAUDIO_DEVICE_DISCONNECT explicitly
//     instead of silently dying.
// We mirror those two behaviours (underrun accounting + explicit disconnect) but
// implement them as our own bounded state machine -- no code copied.
//
// True physical sound-card unplug / plug and real latency measurement are
// 「真机待验」; offline we only verify the state transitions and the bounded
// retry cadence.
#pragma once

#include <QString>

namespace mbdsdr {
namespace dsp {

class AudioLinkHealth {
public:
    enum class State {
        Healthy,    // rendering normally
        Underrun,   // buffer starved repeatedly (warning; audio may stutter)
        Dead,       // device error / failed writes: connection lost, reconnect pending
        GivenUp     // reconnect retries exhausted: honest "no audio device"
    };

    // One audio write outcome. ok=false (io_->write short-wrote / QAudioSink in
    // error) counts a hard glitch.
    void onWrite(bool ok) {
        if (state_ == State::Dead || state_ == State::GivenUp) return;
        if (ok) return;
        if (++errors_ >= kErrorDeadThreshold) state_ = State::Dead;
    }
    // Observed the device buffer drained completely dry after a write (underrun).
    void onUnderrun() {
        if (state_ == State::Dead || state_ == State::GivenUp) return;
        if (++underruns_ >= kUnderrunWarnThreshold) state_ = State::Underrun;
    }
    // QAudioSink reported FatalError / IOError (device pulled / gone).
    void onDeviceError() {
        if (state_ == State::GivenUp) return;
        state_ = State::Dead;
    }

    // Call periodically (~every audio block). Returns true exactly when a bounded
    // reconnect attempt should be made now. Retries are throttled and capped, so
    // a vanished device stops being hammered after a few seconds and honestly
    // reports GivenUp.
    bool tickReconnect() {
        if (state_ != State::Dead) return false;
        ++ticks_;
        if (retries_ >= kMaxRetries) { state_ = State::GivenUp; return false; }
        if (ticks_ < kRetryCooldownTicks) return false;
        ticks_ = 0;
        ++retries_;
        return true;
    }

    // Called after a successful sink rebuild: back to Healthy.
    void reset() {
        state_ = State::Healthy;
        underruns_ = 0; errors_ = 0; retries_ = 0; ticks_ = 0;
    }

    State state() const { return state_; }
    int underrunCount() const { return underruns_; }
    int errorCount() const { return errors_; }
    int reconnectAttempts() const { return retries_; }

    // Status strip text (Chinese, calm). Never empty.
    QString statusText() const {
        switch (state_) {
        case State::Healthy:  return QStringLiteral("声卡正常");
        case State::Underrun: return QStringLiteral("声卡欠载（缓冲不足）");
        case State::Dead:     return QStringLiteral("声卡断开，重连中…");
        case State::GivenUp:  return QStringLiteral("声卡不可用（请检查设备）");
        }
        return QStringLiteral("声卡状态未知");
    }

private:
    State state_ = State::Healthy;
    int underruns_  = 0;
    int errors_     = 0;
    int retries_    = 0;
    int ticks_      = 0;

    // Tolerances: many small underruns are tolerated before we call it Underrun;
    // a handful of hard errors flips to Dead.
    static constexpr int kUnderrunWarnThreshold = 20;
    static constexpr int kErrorDeadThreshold    = 3;
    // Reconnect cadence: one attempt every kRetryCooldownTicks ticks, at most
    // kMaxRetries before honest give-up.
    static constexpr int kRetryCooldownTicks = 10;
    static constexpr int kMaxRetries         = 20;
};

} // namespace dsp
} // namespace mbdsdr
