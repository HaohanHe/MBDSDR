// SPDX-License-Identifier: MIT
//
// Qt-flavoured facade around QtAudioSink. It is intentionally thin: the real
// QAudioSink logic lives in QtAudioSink (an IAudioSink), while this QObject
// keeps the legacy API the settings dialog / main window depend on
// (setDevice(QAudioDevice), static availableDevices(), setVolume(qreal)) so
// device hot-swap stays exactly as before.
//
// Threading model is inherited from QtAudioSink: the QAudioSink is created
// lazily on the DSP worker thread; UI-thread device requests are queued.
#pragma once

#include "iaudio_sink.h"
#include "qt_audio_sink.h"

#include <QObject>
#include <QAudioDevice>
#include <QStringList>

namespace mbdsdr {
namespace dsp {

class AudioOutput : public QObject, public IAudioSink {
    Q_OBJECT
public:
    explicit AudioOutput(QObject* parent = nullptr);
    ~AudioOutput() override;

    // ---- IAudioSink (delegated to the QtAudioSink backend) ---------------
    void write(const std::vector<float>& audio) override;
    void setVolume(float v) override;
    void setMuted(bool m) override;
    bool isAvailable() const override;
    QStringList outputDevices() const override;
    QString currentDeviceName() const override;

    // ---- Legacy UI API (unchanged; do NOT break the settings dialog) -----
    /// Volume 0.0..1.0. Thread-safe; applied to the sink on the worker thread.
    void setVolume(qreal v) { setVolume(static_cast<float>(v)); }
    qreal volume() const { return impl_.volume(); }
    /// Request hot-restart on the given output device. Null = system default.
    void setDevice(const QAudioDevice& dev) { impl_.setDevice(dev); }
    /// All user-visible output device descriptions (for a combo box). The
    /// caller prepends the "system default" row. Honest empty list when the
    /// machine has no audio hardware.
    static QStringList availableDevices() { return QtAudioSink::availableDevices(); }

private:
    QtAudioSink impl_;
};

} // namespace dsp
} // namespace mbdsdr
