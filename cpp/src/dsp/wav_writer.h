// SPDX-License-Identifier: MIT
// Simple uncompressed PCM WAV writer: streaming, length patched on stop.
// Used for continuous demod-audio recording (mono or stereo int16 @ 48 kHz).
#pragma once

#include <QString>
#include <QFile>
#include <vector>
#include <atomic>
#include <cstdint>

namespace mbdsdr {
namespace dsp {

class WavWriter {
public:
    WavWriter();
    ~WavWriter();

    /// Open `path` and write a placeholder RIFF header. `channels` is 1 (mono)
    /// or 2 (stereo; mono input is duplicated to L+R). Audio is int16.
    bool start(const QString& path, double sampleRateHz, int channels);
    /// Append one block of mono float samples in [-1,1]. No-op when stopped.
    void write(const std::vector<float>& samples);
    /// Patch the data-size fields in the header and close the file.
    void stop();

    bool isRecording() const { return recording_; }
    QString currentFilePath() const { return path_; }

private:
    void writeHeader(std::uint32_t dataBytes);

    QFile file_;
    QString path_;
    double sampleRate_ = 48000.0;
    int channels_ = 1;
    std::uint64_t frameCount_ = 0;   // per-channel frames written
    std::atomic<bool> recording_{false};
};

} // namespace dsp
} // namespace mbdsdr
