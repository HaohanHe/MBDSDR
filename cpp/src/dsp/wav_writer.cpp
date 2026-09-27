// SPDX-License-Identifier: MIT
#include "wav_writer.h"

#include <QDir>
#include <QFileInfo>
#include <QtEndian>
#include <cstring>
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

namespace {
// Write a 32-bit / 16-bit little-endian integer into the open QFile.
void writeLe32(QFile& f, quint32 v) {
    quint32 le = qToLittleEndian<quint32>(v);
    f.write(reinterpret_cast<const char*>(&le), sizeof(le));
}
void writeLe16(QFile& f, quint16 v) {
    quint16 le = qToLittleEndian<quint16>(v);
    f.write(reinterpret_cast<const char*>(&le), sizeof(le));
}
} // namespace

WavWriter::WavWriter() = default;
WavWriter::~WavWriter() { if (recording_) stop(); }

bool WavWriter::start(const QString& path, double sampleRateHz, int channels) {
    if (recording_) return false;
    QDir().mkpath(QFileInfo(path).absolutePath());

    path_ = path;
    sampleRate_ = sampleRateHz;
    channels_ = (channels == 2) ? 2 : 1;
    frameCount_ = 0;

    file_.setFileName(path_);
    if (!file_.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        return false;
    }
    // Placeholder header; patched on stop() once the size is known.
    writeHeader(0);
    recording_ = true;
    return true;
}

void WavWriter::writeHeader(std::uint32_t dataBytes) {
    // RIFF chunk descriptor
    file_.write("RIFF", 4);
    writeLe32(file_, 36u + dataBytes);   // file size - 8
    file_.write("WAVE", 4);
    // fmt sub-chunk
    file_.write("fmt ", 4);
    writeLe32(file_, 16u);               // PCM chunk size
    writeLe16(file_, 1);                 // audio format = PCM
    writeLe16(file_, static_cast<quint16>(channels_));
    writeLe32(file_, static_cast<quint32>(sampleRate_));
    const quint32 byteRate = static_cast<quint32>(sampleRate_) *
                             static_cast<quint32>(channels_) * sizeof(std::int16_t);
    writeLe32(file_, byteRate);
    writeLe16(file_, static_cast<quint16>(channels_) * sizeof(std::int16_t));
    writeLe16(file_, 16u);               // bits per sample
    // data sub-chunk
    file_.write("data", 4);
    writeLe32(file_, dataBytes);
}

void WavWriter::write(const std::vector<float>& samples) {
    if (!recording_ || samples.empty()) return;
    const std::size_t n = samples.size();
    // Mono float -> int16. For stereo, interleave L/R = s,s.
    const std::size_t frames = n;
    const std::size_t samplesOut = frames * static_cast<std::size_t>(channels_);
    std::vector<std::int16_t> pcm(samplesOut);
    for (std::size_t i = 0; i < n; ++i) {
        float v = std::clamp(samples[i], -1.0f, 1.0f);
        auto s = static_cast<std::int16_t>(v * 32767.0f);
        pcm[i * channels_] = s;
        if (channels_ == 2) pcm[i * channels_ + 1] = s;
    }
    file_.write(reinterpret_cast<const char*>(pcm.data()),
                static_cast<qint64>(pcm.size() * sizeof(std::int16_t)));
    frameCount_ += frames;
}

void WavWriter::stop() {
    if (!recording_) return;
    recording_ = false;
    const std::uint32_t dataBytes = static_cast<std::uint32_t>(
        frameCount_ * static_cast<std::size_t>(channels_) * sizeof(std::int16_t));
    // Rewind and patch the two length fields.
    if (file_.isOpen()) {
        file_.seek(0);
        writeHeader(dataBytes);
        file_.close();
    }
}

} // namespace dsp
} // namespace mbdsdr
