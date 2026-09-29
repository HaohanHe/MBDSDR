// SPDX-License-Identifier: MIT
#include "audio_output.h"

namespace mbdsdr {
namespace dsp {

AudioOutput::AudioOutput(QObject* parent) : QObject(parent) {
    // The QtAudioSink (impl_) is built lazily on the DSP worker thread.
}

AudioOutput::~AudioOutput() = default;

void AudioOutput::write(const std::vector<float>& audio) {
    impl_.write(audio);
}

void AudioOutput::setVolume(float v) {
    impl_.setVolume(v);
}

void AudioOutput::setMuted(bool m) {
    impl_.setMuted(m);
}

bool AudioOutput::isAvailable() const {
    return impl_.isAvailable();
}

QStringList AudioOutput::outputDevices() const {
    return impl_.outputDevices();
}

QString AudioOutput::currentDeviceName() const {
    return impl_.currentDeviceName();
}

} // namespace dsp
} // namespace mbdsdr
