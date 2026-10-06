// SPDX-License-Identifier: MIT
//
// Null audio sink: a backend that discards every sample. It is used for
// headless / automated runs (offscreen CI, explicit MBDSDR_NULL_AUDIO) so the
// receiver can process a full IQ stream without ever opening a real render
// device or emitting demod noise to a speaker.
//
// It is deliberately honest: isAvailable() is false and outputDevices() is
// empty -- it never claims to be playing sound. Squelch/demod behaviour is
// untouched; only the render endpoint is a no-op.
#pragma once

#include "iaudio_sink.h"

namespace mbdsdr {
namespace dsp {

class NullAudioSink : public IAudioSink {
public:
    void write(const std::vector<float>&) override {}
    using IAudioSink::writeStereo;   // default downmix routes to write()
    void writeStereo(const std::vector<float>&,
                     const std::vector<float>&) override {}
    void setVolume(float) override {}
    void setMuted(bool) override {}
    bool isAvailable() const override { return false; }   // no render device
    QStringList outputDevices() const override { return {}; }
    QString currentDeviceName() const override {
        return QStringLiteral("null (discard)");
    }
};

} // namespace dsp
} // namespace mbdsdr
