// SPDX-License-Identifier: GPL-3.0-or-later
#include "memory_audio_sink.h"

#include <algorithm>

namespace mbdsdr {
namespace dsp {

MemoryAudioSink::MemoryAudioSink() = default;
MemoryAudioSink::~MemoryAudioSink() = default;

void MemoryAudioSink::setVolume(float v) {
    volume_.store(std::clamp(v, 0.0f, 1.0f));
}

void MemoryAudioSink::setMuted(bool m) {
    muted_.store(m);
}

QStringList MemoryAudioSink::outputDevices() const {
    // Honest: this backend is not hardware and reports no output devices.
    return QStringList();
}

QString MemoryAudioSink::currentDeviceName() const {
    return QStringLiteral("memory");
}

void MemoryAudioSink::write(const std::vector<float>& audio) {
    if (audio.empty()) return;
    const float v = volume_.load();
    const bool muted = muted_.load();
    buffer_.reserve(buffer_.size() + audio.size());
    for (float s : audio) {
        float out = muted ? 0.0f : s * v;
        if (out > 1.0f) out = 1.0f;
        if (out < -1.0f) out = -1.0f;
        buffer_.push_back(out);
    }
}

} // namespace dsp
} // namespace mbdsdr
