// SPDX-License-Identifier: MIT
#include "audio_output.h"

#include <QAudioDevice>
#include <QAudioFormat>
#include <QMediaDevices>
#include <QDebug>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>

namespace mbdsdr {
namespace dsp {

namespace {
float clampUnit(float v) {
    if (v > 1.0f) return 1.0f;
    if (v < -1.0f) return -1.0f;
    return v;
}
} // namespace

AudioOutput::AudioOutput(QObject* parent) : QObject(parent) {
    // Sink is built lazily on the DSP worker thread (see ensureReady()).
}

AudioOutput::~AudioOutput() {
    teardownSink();
}

void AudioOutput::setDevice(const QAudioDevice& dev) {
    // Called from the UI thread: just queue the request. The worker rebuilds
    // the sink in ensureReady(), keeping QAudioSink creation on its own thread.
    {
        QMutexLocker lk(&pendingMutex_);
        pendingDev_ = dev;
    }
    pendingRebuild_.store(true);
}

void AudioOutput::ensureReady() {
    QAudioDevice requested;
    bool havePending = false;
    if (pendingRebuild_.exchange(false)) {
        QMutexLocker lk(&pendingMutex_);
        requested = pendingDev_;
        pendingDev_ = QAudioDevice();
        havePending = true;
    }

    if (havePending) {
        buildSink(requested);
    } else if (!sink_) {
        buildSink(QAudioDevice());   // null -> system default
    }
}

void AudioOutput::teardownSink() {
    if (sink_) {
        sink_->stop();
        sink_.reset();
    }
    io_ = nullptr;
    available_.store(false);
}

void AudioOutput::buildSink(const QAudioDevice& dev) {
    teardownSink();

    // Null means system default.
    QAudioDevice d = dev;
    if (d.isNull()) d = QMediaDevices::defaultAudioOutput();
    currentDev_ = d;

    if (d.isNull()) {
        qWarning() << "[AudioOutput] no audio output device; audio disabled";
        return;
    }

    QAudioFormat desired;
    desired.setSampleRate(48000);
    desired.setChannelCount(1);
    desired.setSampleFormat(QAudioFormat::Float);

    // Negotiate a real format. isFormatSupported() is conservative on the
    // Windows FFmpeg backend and rejects formats that would actually play,
    // so fall back to the device's preferred format instead of giving up.
    QAudioFormat fmt = desired;
    if (!d.isFormatSupported(fmt)) {
        fmt = d.preferredFormat();
        QAudioFormat mono = fmt;
        mono.setChannelCount(1);
        if (d.isFormatSupported(mono)) fmt = mono;
    }

    sink_ = std::make_unique<QAudioSink>(d, fmt);
    QObject::connect(sink_.get(), &QAudioSink::stateChanged,
        this, [this](QAudio::State s) {
            if (s == QAudio::StoppedState && sink_ && sink_->error() != QAudio::NoError)
                qWarning() << "[AudioOutput] error:" << sink_->error();
        });
    io_ = sink_->start();
    if (!io_) {
        qWarning() << "[AudioOutput] failed to start; audio disabled";
        sink_.reset();
        return;
    }
    fmt_ = fmt;
    sink_->setVolume(volume_.load());
    available_.store(true);
    qInfo() << "[AudioOutput] ready on" << d.description() << ":" << fmt;
}

QStringList AudioOutput::availableDevices() {
    QStringList names;
    const auto devs = QMediaDevices::audioOutputs();
    names.reserve(devs.size());
    for (const auto& d : devs) names << d.description();
    return names;
}

QString AudioOutput::currentDeviceName() const {
    if (currentDev_.isNull()) return QStringLiteral("default");
    return currentDev_.description();
}

void AudioOutput::write(const std::vector<float>& audio, double sourceRateHz) {
    ensureReady();
    if (!available_.load() || muted_.load() || audio.empty() || !io_) return;

    if (sink_) sink_->setVolume(volume_.load());

    const int outRate = fmt_.sampleRate();
    const int channels = std::max(1, fmt_.channelCount());

    // 1) Linear resample mono to the device rate.
    std::vector<float> mono;
    if (std::abs(sourceRateHz - outRate) < 1.0) {
        mono = audio;
    } else {
        const double ratio = static_cast<double>(outRate) / sourceRateHz;
        const std::size_t n = static_cast<std::size_t>(audio.size() * ratio);
        mono.resize(n);
        for (std::size_t i = 0; i < n; ++i) {
            const double pos = i / ratio;
            const std::size_t i0 = static_cast<std::size_t>(pos);
            const std::size_t i1 = std::min(i0 + 1, audio.size() - 1);
            const double frac = pos - i0;
            mono[i] = static_cast<float>(audio[i0] * (1.0 - frac)
                                        + audio[i1] * frac);
        }
    }

    // 2) Expand to the channel count and convert to the device sample format.
    const std::size_t frames = mono.size();
    QByteArray bytes;
    bytes.reserve(static_cast<int>(frames * channels
                                   * fmt_.bytesPerSample()));

    auto appendFrame = [&](float s) {
        s = clampUnit(s);
        switch (fmt_.sampleFormat()) {
        case QAudioFormat::Float: {
            float f = s;
            bytes.append(reinterpret_cast<const char*>(&f), sizeof(float));
            break;
        }
        case QAudioFormat::Int16: {
            auto v = static_cast<int16_t>(std::lround(s * 32767.0f));
            bytes.append(reinterpret_cast<const char*>(&v), sizeof(int16_t));
            break;
        }
        case QAudioFormat::Int32: {
            auto v = static_cast<int32_t>(std::llround(s * 2147483647.0f));
            bytes.append(reinterpret_cast<const char*>(&v), sizeof(int32_t));
            break;
        }
        case QAudioFormat::UInt8: {
            auto v = static_cast<uint8_t>(std::lround((s * 0.5f + 0.5f) * 255.0f));
            bytes.append(reinterpret_cast<const char*>(&v), 1);
            break;
        }
        default:
            break; // unknown format: nothing to write
        }
    };

    for (float s : mono) {
        for (int c = 0; c < channels; ++c) appendFrame(s);
    }

    if (!bytes.isEmpty()) io_->write(bytes.constData(), bytes.size());
}

} // namespace dsp
} // namespace mbdsdr
