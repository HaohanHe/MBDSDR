// SPDX-License-Identifier: MIT
#include "audio_output.h"

#include <QAudioDevice>
#include <QAudioFormat>
#include <QMediaDevices>
#include <QDebug>
#include <cmath>
#include <cstring>

namespace mbdsdr {
namespace dsp {

AudioOutput::AudioOutput(QObject* parent) : QObject(parent) {
    QAudioFormat fmt;
    fmt.setSampleRate(48000);
    fmt.setChannelCount(1);
    fmt.setSampleFormat(QAudioFormat::Float);

    const QAudioDevice dev = QMediaDevices::defaultAudioOutput();
    if (!dev.isFormatSupported(fmt)) {
        qWarning() << "[AudioOutput] default format not supported; audio disabled";
        available_ = false;
        return;
    }
    sink_ = std::make_unique<QAudioSink>(dev, fmt);
    QObject::connect(sink_.get(), &QAudioSink::stateChanged,
        this, [this](QAudio::State s) {
            if (s == QAudio::StoppedState && sink_->error() != QAudio::NoError) {
                qWarning() << "[AudioOutput] error:" << sink_->error();
            }
        });
    io_ = sink_->start();
    if (!io_) {
        qWarning() << "[AudioOutput] failed to start; audio disabled";
        available_ = false;
        sink_.reset();
        return;
    }
    available_ = true;
    qInfo() << "[AudioOutput] 48kHz mono float ready";
}

AudioOutput::~AudioOutput() {
    if (sink_) sink_->stop();
}

void AudioOutput::write(const std::vector<float>& audio, double sourceRateHz) {
    if (!available_ || muted_ || audio.empty()) return;

    // Linear resample to 48 kHz if needed
    std::vector<float> out;
    if (std::abs(sourceRateHz - 48000.0) < 1.0) {
        out = audio;
    } else {
        const double ratio = 48000.0 / sourceRateHz;
        const std::size_t outN = static_cast<std::size_t>(audio.size() * ratio);
        out.resize(outN);
        for (std::size_t i = 0; i < outN; ++i) {
            const double pos = i / ratio;
            const std::size_t i0 = static_cast<std::size_t>(pos);
            const std::size_t i1 = std::min(i0 + 1, audio.size() - 1);
            const double frac = pos - i0;
            out[i] = static_cast<float>(audio[i0] * (1-frac) + audio[i1] * frac);
        }
    }

    // Write raw float bytes
    const qint64 bytes = static_cast<qint64>(out.size() * sizeof(float));
    io_->write(reinterpret_cast<const char*>(out.data()), bytes);
}

} // namespace dsp
} // namespace mbdsdr
