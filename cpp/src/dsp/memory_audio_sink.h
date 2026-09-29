// SPDX-License-Identifier: GPL-3.0-or-later
//
// In-memory IAudioSink: every 48 kHz mono Float32 block written to it is
// captured (after volume/mute, exactly as the speaker would have rendered it)
// into an internal buffer for unit-test assertions. It is ALSO the honest
// "no device" backend: isAvailable() == true (the buffer is always writable)
// but outputDevices() is an empty list -- it never pretends to be hardware.
#pragma once

#include "iaudio_sink.h"

#include <atomic>
#include <vector>

namespace mbdsdr {
namespace dsp {

class MemoryAudioSink : public IAudioSink {
public:
    MemoryAudioSink();
    ~MemoryAudioSink() override;

    // ---- IAudioSink -------------------------------------------------------
    void write(const std::vector<float>& audio) override;
    void writeStereo(const std::vector<float>& left,
                     const std::vector<float>& right) override;
    void setVolume(float v) override;
    void setMuted(bool m) override;
    bool isAvailable() const override { return true; }
    QStringList outputDevices() const override;
    QString currentDeviceName() const override;

    // ---- Test accessors ---------------------------------------------------
    /// Captured post-volume/post-mute mono samples ([-1,1]) from write().
    const std::vector<float>& buffer() const { return buffer_; }
    std::size_t frames() const { return buffer_.size(); }
    /// Captured post-volume/post-mute stereo samples ([-1,1]) from writeStereo().
    /// left/right are captured independently and stay frame-aligned.
    const std::vector<float>& stereoLeft() const { return stereoLeft_; }
    const std::vector<float>& stereoRight() const { return stereoRight_; }
    std::size_t stereoFrames() const { return stereoLeft_.size(); }
    void clear() { buffer_.clear(); stereoLeft_.clear(); stereoRight_.clear(); }
    bool muted() const { return muted_.load(); }
    float volume() const { return volume_.load(); }

private:
    std::vector<float> buffer_;
    std::vector<float> stereoLeft_;
    std::vector<float> stereoRight_;
    std::atomic<float> volume_{1.0f};
    std::atomic<bool> muted_{false};
};

} // namespace dsp
} // namespace mbdsdr
