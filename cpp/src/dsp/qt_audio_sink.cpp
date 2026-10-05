// SPDX-License-Identifier: MIT
#include "qt_audio_sink.h"

#include <QAudioDevice>
#include <QAudioFormat>
#include <QMediaDevices>
#include <QByteArray>
#include <QDebug>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>

namespace mbdsdr {
namespace dsp {

namespace {
// The DSP engine always delivers 48 kHz Float32 (mono or stereo) to this sink.
// Resampling below only adapts to a device whose native rate differs from 48 kHz.
constexpr double kInputSampleRateHz = 48000.0;

// When no default audio output device exists (headless CI / no PulseAudio),
// re-query QMediaDevices at most this often instead of on every 25 ms block.
constexpr int kNoDeviceRetryMs = 2000;

float clampUnit(float v) {
    if (v > 1.0f) return 1.0f;
    if (v < -1.0f) return -1.0f;
    return v;
}

// Append one unit-range sample, converted to the device's sample format, to bytes.
void appendSample(QByteArray& bytes, const QAudioFormat& fmt, float s) {
    s = clampUnit(s);
    switch (fmt.sampleFormat()) {
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
}
} // namespace

QtAudioSink::QtAudioSink() {
    // Sink is built lazily on the DSP worker thread (see ensureReady()).
}

QtAudioSink::~QtAudioSink() {
    teardownSink();
}

void QtAudioSink::setDevice(const QAudioDevice& dev) {
    // Called from the UI thread: just queue the request. The worker rebuilds
    // the sink in ensureReady(), keeping QAudioSink creation on its own thread.
    {
        QMutexLocker lk(&pendingMutex_);
        pendingDev_ = dev;
    }
    pendingRebuild_.store(true);
}

void QtAudioSink::ensureReady() {
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
        // No live sink. Two cases:
        //  (a) first run / healthy-but-no-device (headless): build immediately;
        //      buildSink() degrades to unavailable, exactly as before.
        //  (b) a previously healthy link died (state == Dead): attempt a BOUNDED
        //      rebuild through health_.tickReconnect() so a vanished device is not
        //      hammered, and stop honestly once it gives up (GivenUp).
        if (health_.state() == AudioLinkHealth::State::Dead) {
            if (health_.tickReconnect())
                buildSink(currentDev_.isNull() ? QAudioDevice() : currentDev_);
        } else if (health_.state() != AudioLinkHealth::State::GivenUp) {
            buildSink(QAudioDevice());   // null -> system default
        }
    }
}

void QtAudioSink::teardownSink() {
    if (sink_) {
        sink_->stop();
        sink_.reset();
    }
    io_ = nullptr;
    available_.store(false);
}

void QtAudioSink::buildSink(const QAudioDevice& dev) {
    teardownSink();

    // Null means system default.
    QAudioDevice d = dev;
    if (d.isNull()) {
        // Headless throttle: skip the QMediaDevices query entirely while a
        // previous probe found no device and the cooldown has not elapsed.
        if (std::chrono::steady_clock::now() < nextNoDeviceProbe_) return;
        d = QMediaDevices::defaultAudioOutput();
    }
    currentDev_ = d;

    if (d.isNull()) {
        qWarning() << "[QtAudioSink] no audio output device; audio disabled"
                      "(headless -- re-probing at most every" << kNoDeviceRetryMs << "ms)";
        nextNoDeviceProbe_ = std::chrono::steady_clock::now()
                           + std::chrono::milliseconds(kNoDeviceRetryMs);
        return;
    }

    QAudioFormat desired;
    desired.setSampleRate(48000);
    desired.setChannelCount(2);   // prefer true stereo; mono is handled by mapping at write time
    desired.setSampleFormat(QAudioFormat::Float);

    // Negotiate a real format. isFormatSupported() is conservative on the
    // Windows FFmpeg backend and rejects formats that would actually play,
    // so fall back to the device's preferred format instead of giving up.
    // The real channel count is read back from fmt_ at write time, so whether
    // we land on stereo, mono, or multi-channel is decided by the device.
    QAudioFormat fmt = desired;
    if (!d.isFormatSupported(fmt)) {
        fmt = d.preferredFormat();
    }

    sink_ = std::make_unique<QAudioSink>(d, fmt);
    // QtAudioSink is not a QObject, so no receiver context is used. The lambda
    // only ever runs while sink_ (its sender) is alive; the connection is
    // disconnected automatically when sink_ is destroyed in teardownSink().
    QObject::connect(sink_.get(), &QAudioSink::stateChanged,
        [this](QAudio::State s) {
            if (s == QAudio::StoppedState && sink_ && sink_->error() != QAudio::NoError) {
                qWarning() << "[QtAudioSink] error:" << sink_->error();
                // Device pulled / fatal: mark the link dead and flip available_
                // false honestly. We do NOT destroy the sender inside its own
                // callback; the worker's next ensureReady() teardowns + bounded
                // rebuilds through health_. (Real unplug/replug 真机待验.)
                health_.onDeviceError();
                available_.store(false);
            }
        });
    io_ = sink_->start();
    if (!io_) {
        qWarning() << "[QtAudioSink] failed to start; audio disabled";
        sink_.reset();
        return;
    }
    fmt_ = fmt;
    sink_->setVolume(volume_.load());
    available_.store(true);
    health_.reset();   // a fresh sink = healthy link
    qInfo() << "[QtAudioSink] ready on" << d.description() << ":" << fmt;
}

QStringList QtAudioSink::availableDevices() {
    QStringList names;
    const auto devs = QMediaDevices::audioOutputs();
    names.reserve(devs.size());
    for (const auto& d : devs) names << d.description();
    return names;
}

QStringList QtAudioSink::outputDevices() const {
    return availableDevices();
}

QString QtAudioSink::currentDeviceName() const {
    if (currentDev_.isNull()) return QStringLiteral("default");
    return currentDev_.description();
}

std::vector<float> QtAudioSink::resampleToDevice(const std::vector<float>& in) const {
    if (in.empty()) return {};
    const int outRate = fmt_.sampleRate();
    if (std::abs(kInputSampleRateHz - outRate) < 1.0) return in;

    // Linear resample 48k -> device rate.
    const double ratio = static_cast<double>(outRate) / kInputSampleRateHz;
    const std::size_t n = static_cast<std::size_t>(in.size() * ratio);
    std::vector<float> out(n);
    for (std::size_t i = 0; i < n; ++i) {
        const double pos = i / ratio;
        const std::size_t i0 = static_cast<std::size_t>(pos);
        const std::size_t i1 = std::min(i0 + 1, in.size() - 1);
        const double frac = pos - i0;
        out[i] = static_cast<float>(in[i0] * (1.0 - frac) + in[i1] * frac);
    }
    return out;
}

void QtAudioSink::feedAudioWrite(const QByteArray& bytes) {
    if (bytes.isEmpty() || !io_) return;
    const qint64 written = io_->write(bytes.constData(), bytes.size());
    health_.onWrite(written >= 0);
    // Underrun heuristic: after we just pushed a block, the hardware buffer is
    // already completely drained (bytesFree == bufferSize). Tolerated by the
    // state machine until it warrants the "欠载" warning.
    if (sink_) {
        const qint64 buf = sink_->bufferSize();
        if (buf > 0 && sink_->bytesFree() >= buf)
            health_.onUnderrun();
        if (sink_->error() != QAudio::NoError)
            health_.onDeviceError();
    }
}

void QtAudioSink::write(const std::vector<float>& audio) {
    ensureReady();
    if (!available_.load() || muted_.load() || audio.empty() || !io_) return;

    if (sink_) sink_->setVolume(volume_.load());

    const int channels = std::max(1, fmt_.channelCount());
    std::vector<float> mono = resampleToDevice(audio);

    // Mono: duplicate the same sample onto every output channel (L == R).
    QByteArray bytes;
    bytes.reserve(static_cast<int>(mono.size() * channels * fmt_.bytesPerSample()));
    for (float s : mono)
        for (int c = 0; c < channels; ++c)
            appendSample(bytes, fmt_, s);

    feedAudioWrite(bytes);
}

void QtAudioSink::writeStereo(const std::vector<float>& left,
                              const std::vector<float>& right) {
    ensureReady();
    if (!available_.load() || muted_.load() || left.empty() || right.empty() || !io_) return;

    if (sink_) sink_->setVolume(volume_.load());

    const int channels = std::max(1, fmt_.channelCount());

    // Resample each channel independently to the device rate, then interleave
    // per frame. The two channels use the same ratio so their lengths match.
    std::vector<float> L = resampleToDevice(left);
    std::vector<float> R = resampleToDevice(right);
    const std::size_t frames = std::min(L.size(), R.size());

    QByteArray bytes;
    bytes.reserve(static_cast<int>(frames * channels * fmt_.bytesPerSample()));
    for (std::size_t i = 0; i < frames; ++i) {
        const float l = L[i];
        const float r = R[i];
        for (int c = 0; c < channels; ++c) {
            float s;
            if (channels == 1)      s = (l + r) * 0.5f;   // mono device: downmix
            else if (c == 0)        s = l;                // front-left
            else                    s = r;                // front-right + extra channels
            appendSample(bytes, fmt_, s);
        }
    }

    feedAudioWrite(bytes);
}

} // namespace dsp
} // namespace mbdsdr
