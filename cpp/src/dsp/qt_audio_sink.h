// SPDX-License-Identifier: MIT
//
// IAudioSink backend backed by Qt's QAudioSink. Extracted verbatim from the
// old AudioOutput so the DSP engine depends only on IAudioSink while the real
// playback semantics (and their threading model) are preserved exactly.
//
// Threading model: the QAudioSink (and its internal timers) must be created
// and driven from the DSP worker thread. No sink is built in the constructor;
// it is lazily created on the worker in ensureReady(), called from write().
// Device changes requested from the UI thread are queued and consumed on the
// worker so no timer is ever started from the wrong thread.
//
// With no audio device (headless CI) it degrades gracefully: isAvailable()
// stays false and output is dropped -- no crash, no fake audio.
#pragma once

#include "iaudio_sink.h"
#include "audio_link_health.h"

#include <QAudioDevice>
#include <QAudioFormat>
#include <QAudioSink>
#include <QIODevice>
#include <QMutex>

#include <atomic>
#include <memory>
#include <vector>

namespace mbdsdr {
namespace dsp {

class QtAudioSink : public IAudioSink {
public:
    QtAudioSink();
    ~QtAudioSink() override;

    // ---- IAudioSink (48 kHz mono/stereo Float32 in [-1,1]) ---------------
    void write(const std::vector<float>& audio) override;
    void writeStereo(const std::vector<float>& left,
                     const std::vector<float>& right) override;
    void setVolume(float v) override { volume_.store(v); }
    void setMuted(bool m) override { muted_.store(m); }
    bool isAvailable() const override { return available_.load(); }
    QStringList outputDevices() const override;
    QString currentDeviceName() const override;

    // ---- Qt-specific, unchanged from the legacy AudioOutput ---------------
    /// Request hot-restart on the given output device. A null QAudioDevice
    /// resolves to the system default. Safe to call from any thread: the
    /// request is queued and the sink rebuild happens on the worker.
    void setDevice(const QAudioDevice& dev);
    /// All user-visible output device descriptions. Returns only the real
    /// hardware descriptions; the caller prepends the "system default" row.
    static QStringList availableDevices();
    float volume() const { return volume_.load(); }

    // ---- Link health (underrun / disconnect / bounded reconnect) -----------
    // Status-strip text driven by the worker thread's observations of QAudioSink.
    // Never fabricated: on a headless box with no device the sink simply stays
    // unavailable and the state reads accordingly.
    QString healthStatus() const { return health_.statusText(); }
    AudioLinkHealth::State healthState() const { return health_.state(); }

private:
    // Worker-thread: consume any queued device request, then lazily build the
    // sink against the default device if none exists yet.
    void ensureReady();
    // Stop and tear down the active sink_ (if any). Safe to call repeatedly.
    void teardownSink();
    // Build a fresh sink_ against dev, negotiate a format, and start playback.
    // A null dev resolves to the system default. On failure leaves available_
    // == false and sink_ == nullptr. Must run on the worker thread.
    void buildSink(const QAudioDevice& dev);
    // Linearly resample one 48 kHz Float32 channel to the negotiated device
    // rate (fmt_.sampleRate()). Worker-thread only; fmt_ is worker state.
    std::vector<float> resampleToDevice(const std::vector<float>& in) const;

    // Write one prepared byte block to io_ and feed the result to health_
    // (write outcome, buffer-drained underrun, post-write sink error). Worker-thread only.
    void feedAudioWrite(const QByteArray& bytes);

    std::unique_ptr<QAudioSink> sink_;
    QIODevice* io_ = nullptr;
    QAudioFormat fmt_;
    std::atomic<bool> available_{false};
    std::atomic<bool> muted_{false};
    std::atomic<float> volume_{0.8f};
    QAudioDevice currentDev_;   // null == system default (worker thread only)

    // Pending device request from the UI thread.
    QMutex pendingMutex_;
    QAudioDevice pendingDev_;
    std::atomic<bool> pendingRebuild_{false};

    // Link-health state machine (worker-thread observations only). Feeds
    // underrun counting, fatal-error disconnect, and the bounded reconnect cadence.
    AudioLinkHealth health_;
};

} // namespace dsp
} // namespace mbdsdr
