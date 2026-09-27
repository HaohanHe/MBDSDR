// SPDX-License-Identifier: MIT
// Audio output via QAudioSink. If no audio device is available (headless CI),
// gracefully degrades to "drop on the floor" -- no crash, no fake audio.
#pragma once

#include <QObject>
#include <QAudioSink>
#include <QAudioFormat>
#include <QAudioDevice>
#include <QIODevice>
#include <QStringList>
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
    void write(const std::vector<float>& audio, double sourceRateHz);

    bool isAvailable() const { return available_; }
    void setMuted(bool m) { muted_ = m; }

    /// Hot-restart playback on the given output device. Pass a null QAudioDevice
    /// to fall back to the system default. Stops and discards the current sink_,
    /// then rebuilds against the new device. No-op-safe when no device exists.
    void setDevice(const QAudioDevice& dev);

    /// All user-visible output device descriptions (for a combo box). The first
    /// entry is always "系统默认" (index 0 = system default). Returns only the
    /// real hardware descriptions; the caller prepends the "default" row.
    static QStringList availableDevices();

    /// Description of the device currently bound to the sink, or "default" when
    /// the system default is in use / audio is disabled.
    QString currentDeviceName() const;

private:
    // Stop and tear down the active sink_ (if any). Safe to call repeatedly.
    void teardownSink();
    // Build a fresh sink_ against dev, negotiate a format, and start playback.
    // On any failure leaves available_ == false and sink_ == nullptr.
    void buildSink(const QAudioDevice& dev);

    std::unique_ptr<QAudioSink> sink_;
    QIODevice* io_ = nullptr;
    QAudioFormat fmt_;
    bool available_ = false;
    bool muted_ = false;
    QAudioDevice currentDev_;   // null == system default
};

} // namespace dsp
} // namespace mbdsdr
