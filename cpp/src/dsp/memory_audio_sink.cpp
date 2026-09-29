// SPDX-License-Identifier: MIT
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

void MemoryAudioSink::writeStereo(const std::vector<float>& left,
                                  const std::vector<float>& right) {
    // Frame-aligned independent capture. Contract guarantees left/right equal
    // length; min() makes it robust to a caller that gets the tail wrong.
    const std::size_t n = std::min(left.size(), right.size());
    if (n == 0) return;
    const float v = volume_.load();
    const bool muted = muted_.load();
    stereoLeft_.reserve(stereoLeft_.size() + n);
    stereoRight_.reserve(stereoRight_.size() + n);
    for (std::size_t i = 0; i < n; ++i) {
        float l = muted ? 0.0f : left[i] * v;
        float r = muted ? 0.0f : right[i] * v;
        if (l > 1.0f) l = 1.0f;
        if (l < -1.0f) l = -1.0f;
        if (r > 1.0f) r = 1.0f;
        if (r < -1.0f) r = -1.0f;
        stereoLeft_.push_back(l);
        stereoRight_.push_back(r);
    }
}

} // namespace dsp
} // namespace mbdsdr
