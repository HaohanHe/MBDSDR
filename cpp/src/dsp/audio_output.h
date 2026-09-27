// SPDX-License-Identifier: MIT
// Audio output via QAudioSink. If no audio device is available (headless CI),
// gracefully degrades to "drop on the floor" -- no crash, no fake audio.
#pragma once

#include <QObject>
#include <QAudioSink>
#include <QAudioFormat>
#include <QIODevice>
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

private:
    std::unique_ptr<QAudioSink> sink_;
    QIODevice* io_ = nullptr;
    QAudioFormat fmt_;
    bool available_ = false;
    bool muted_ = false;
};

} // namespace dsp
} // namespace mbdsdr
