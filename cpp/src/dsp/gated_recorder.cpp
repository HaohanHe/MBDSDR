// SPDX-License-Identifier: MIT
#include "gated_recorder.h"

#include <QDir>
#include <QDateTime>
#include <QFile>
#include <QDataStream>
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

GatedRecorder::GatedRecorder(double sr) : sr_(sr) {
    attackAlpha_  = static_cast<float>(1.0 - std::exp(-10.0 / (sr_ * 10e-3)));
    releaseAlpha_ = static_cast<float>(1.0 - std::exp(-10.0 / (sr_ * 60e-3)));
    preRoll_.set_capacity(static_cast<std::size_t>(sr_ * kPreRollMs / 1000.0));
}

void GatedRecorder::startSegment() {
    segmentBuf_.clear();
    // Copy pre-roll
    for (float s : preRoll_) segmentBuf_.push_back(s);
    segmentLenMs_ = kPreRollMs;
    hangLeftMs_ = kHangMs;
    env_ = 0;
    state_ = State::REC;
}

void GatedRecorder::endSegment() {
    if (segmentLenMs_ < kMinSegMs) {
        state_ = State::IDLE;
        segmentBuf_.clear();
        return;
    }
    // Trim trailing silence (below 1% of peak)
    float peak = 0;
    for (float s : segmentBuf_) peak = std::max(peak, std::abs(s));
    if (peak < 1e-4) { state_ = State::IDLE; segmentBuf_.clear(); return; }
    const float threshold = peak * 0.01f;
    std::size_t end = segmentBuf_.size();
    while (end > 0 && std::abs(segmentBuf_[end-1]) < threshold) --end;
    segmentBuf_.resize(end);

    // Gentle normalize to 0.9 peak, max gain 8x
    float newPeak = 0;
    for (float s : segmentBuf_) newPeak = std::max(newPeak, std::abs(s));
    if (newPeak > 1e-4) {
        float g = std::min(0.9f / newPeak, 8.0f);
        for (auto& s : segmentBuf_) s *= g;
    }

    writeWav(segmentBuf_, currentMode_, currentFreq_);
    state_ = State::IDLE;
    segmentBuf_.clear();
}

std::vector<QString> GatedRecorder::feed(const std::vector<float>& audio, bool gate) {
    std::vector<QString> saved;
    if (!enabled_) return saved;

    const double blockMs = audio.size() * 1000.0 / sr_;

    // Always push to pre-roll
    for (float s : audio) {
        preRoll_.push_back(s);
        if (preRoll_.size() > preRoll_.capacity()) preRoll_.pop_front();
    }

    if (state_ == State::IDLE) {
        if (gate) startSegment();
        else return saved;
    }

    // State == REC
    // Envelope shaper
    for (float s : audio) {
        const float target = gate ? 1.0f : 0.0f;
        const float a = gate ? attackAlpha_ : releaseAlpha_;
        env_ += a * (target - env_);
        segmentBuf_.push_back(s * env_);
    }
    segmentLenMs_ += blockMs;

    if (gate) {
        hangLeftMs_ = kHangMs;
    } else {
        hangLeftMs_ -= blockMs;
    }

    if (hangLeftMs_ <= 0 || segmentLenMs_ >= kMaxSegMs) {
        endSegment();
    }
    return saved;
}

std::vector<QString> GatedRecorder::flush() {
    std::vector<QString> saved;
    if (state_ == State::REC) endSegment();
    return saved;
}

QString GatedRecorder::writeWav(const std::vector<float>& samples,
                                  const QString& mode, double freq) {
    QDir().mkpath(outDir_);
    const QString stamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    const QString path = QString("%1/%2_%3_%4Hz.wav").arg(outDir_, stamp, mode)
                             .arg(static_cast<qint64>(freq));

    // Convert float [-1,1] to int16
    std::vector<qint16_t> pcm(samples.size());
    for (std::size_t i = 0; i < samples.size(); ++i) {
        float v = std::clamp(samples[i], -1.0f, 1.0f);
        pcm[i] = static_cast<qint16_t>(v * 32767.0f);
    }

    QFile f(path);
    if (!f.open(QIODevice::WriteOnly)) return {};
    QDataStream ds(&f);
    ds.setByteOrder(QDataStream::LittleEndian);

    const qint32 dataBytes = static_cast<qint32>(pcm.size() * sizeof(qint16_t));
    // RIFF header
    ds.writeRawData("RIFF", 4);
    ds << static_cast<quint32>(36 + dataBytes);
    ds.writeRawData("WAVE", 4);
    // fmt chunk
    ds.writeRawData("fmt ", 4);
    ds << static_cast<quint32>(16);           // chunk size
    ds << static_cast<quint16>(1);            // PCM
    ds << static_cast<quint16>(1);            // mono
    ds << static_cast<quint32>(static_cast<quint32>(sr_));
    ds << static_cast<quint32>(static_cast<quint32>(sr_ * 2)); // byte rate
    ds << static_cast<quint16>(2);            // block align
    ds << static_cast<quint16>(16);           // bits per sample
    // data chunk
    ds.writeRawData("data", 4);
    ds << dataBytes;
    ds.writeRawData(reinterpret_cast<const char*>(pcm.data()), dataBytes);

    return path;
}

} // namespace dsp
} // namespace mbdsdr
