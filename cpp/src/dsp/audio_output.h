// SPDX-License-Identifier: MIT
// Audio output via QAudioSink. If no audio device is available (headless CI),
// gracefully degrades to "drop on the floor" -- no crash, no fake audio.
//
// Threading model: the QAudioSink (and its internal timers) must be created and
// driven from the DSP worker thread. The constructor no longer builds a sink;
// it is lazily created on the worker in ensureReady(), called from write().
// Device changes requested from the UI thread are queued and consumed on the
// worker so no timer is ever started from the wrong thread.
#pragma once

#include <QObject>
#include <QAudioSink>
#include <QAudioFormat>
#include <QAudioDevice>
#include <QIODevice>
#include <QStringList>
#include <QMutex>
#include <atomic>
#include <vector>
#include <memory>

namespace mbdsdr {
namespace dsp {

class AudioOutput : public QObject {
    Q_OBJECT
public:
    explicit AudioOutput(QObject* parent = nullptr);
    ~AudioOutput() override;

    /// Write a block of mono float audio (range [-1,1]) at the given source
    /// sample rate. Resamples to 48 kHz internally before playback.
    /// Must be called from the DSP worker thread.
    void write(const std::vector<float>& audio, double sourceRateHz);

    bool isAvailable() const { return available_.load(); }
    /// Thread-safe; applied on the worker thread.
    void setMuted(bool m) { muted_.store(m); }
    /// Volume 0.0..1.0. Thread-safe; applied to the sink on the worker thread.
    void setVolume(qreal v) { volume_.store(static_cast<float>(v)); }
    qreal volume() const { return volume_.load(); }

    /// Request hot-restart on the given output device. Pass a null QAudioDevice
    /// to fall back to the system default. Safe to call from any thread: the
    /// request is queued and the actual sink rebuild happens on the worker.
    void setDevice(const QAudioDevice& dev);

    /// All user-visible output device descriptions (for a combo box). The first
    /// entry is always "系统默认" (index 0 = system default). Returns only the
    /// real hardware descriptions; the caller prepends the "default" row.
    static QStringList availableDevices();

    /// Description of the device currently bound to the sink, or "default" when
    /// the system default is in use / audio is disabled.
    QString currentDeviceName() const;

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
};

} // namespace dsp
} // namespace mbdsdr
