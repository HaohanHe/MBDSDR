// SPDX-License-Identifier: MIT
#include "gated_recorder.h"

#include <QDir>
#include <QDateTime>
#include <QFile>
#include <QDataStream>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTimeZone>
#include <QtGlobal>
#include <cstdint>
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

namespace {
// Defaults for unattended watch recording.
constexpr double kDefaultPreRollMs = 400.0;   // 0.4 s before the trigger
constexpr double kDefaultHangMs    = 1500.0;  // 1.5 s end-delay
} // namespace

GatedRecorder::GatedRecorder(double sr) : sr_(sr) {
    preRollMs_ = kDefaultPreRollMs;
    hangMs_ = kDefaultHangMs;
    attackAlpha_  = static_cast<float>(1.0 - std::exp(-10.0 / (sr_ * 10e-3)));
    releaseAlpha_ = static_cast<float>(1.0 - std::exp(-10.0 / (sr_ * 60e-3)));
}

void GatedRecorder::setPreRollMs(double ms) {
    preRollMs_ = std::clamp(ms, kPreRollMinMs, kPreRollMaxMs);
}

void GatedRecorder::setHangMs(double ms) {
    hangMs_ = std::clamp(ms, kHangMinMs, kHangMaxMs);
}

void GatedRecorder::startSegment() {
    segmentBuf_.clear();
    // Copy pre-roll
    for (float s : preRoll_) segmentBuf_.push_back(s);
    segmentLenMs_ = preRollMs_;
    hangLeftMs_ = hangMs_;
    env_ = 0;
    triggerEpochMs_ = QDateTime::currentMSecsSinceEpoch();
    state_ = State::REC;
}

bool GatedRecorder::endSegment() {
    auto abandon = [&]() {
        state_ = State::IDLE;
        segmentBuf_.clear();
        return false;
    };
    if (segmentLenMs_ < kMinSegMs) return abandon();

    float peak = 0;
    for (float s : segmentBuf_) peak = std::max(peak, std::abs(s));
    if (peak < 1e-4) return abandon();
    const float threshold = peak * 0.01f;

    // Head trim (symmetric with the tail trim): drop leading quiet samples so
    // the segment starts at the real signal onset. The pre-roll carries the
    // un-gated audio fed by the engine, so genuine pre-onset floor is kept.
    std::size_t begin = 0;
    while (begin < segmentBuf_.size() && std::abs(segmentBuf_[begin]) < threshold) ++begin;
    std::size_t end = segmentBuf_.size();
    while (end > begin && std::abs(segmentBuf_[end-1]) < threshold) --end;
    if (end <= begin) return abandon();
    segmentBuf_.erase(segmentBuf_.begin(), segmentBuf_.begin() + begin);
    segmentBuf_.resize(end - begin);

    // Short fade-in at the new head so the trimmed onset does not click.
    {
        const std::size_t fadeN = std::min<std::size_t>(
            static_cast<std::size_t>(sr_ * 10e-3), segmentBuf_.size());
        for (std::size_t i = 0; i < fadeN; ++i)
            segmentBuf_[i] *= static_cast<float>(i + 1) / static_cast<float>(fadeN);
    }

    float newPeak = 0;
    for (float s : segmentBuf_) newPeak = std::max(newPeak, std::abs(s));
    if (newPeak > 1e-4) {
        float g = std::min(0.9f / newPeak, 8.0f);
        for (auto& s : segmentBuf_) s *= g;
    }

    const QString base = uniqueBasePath();
    if (base.isEmpty()) return abandon();
    const qint64 endEpochMs = QDateTime::currentMSecsSinceEpoch();
    // The file starts (pre-roll) this far before the trigger opened.
    const qint64 startEpochMs = triggerEpochMs_ -
            static_cast<qint64>(preRollMs_);

    lastSavedPath_ = writeWav(segmentBuf_, base);
    if (lastSavedPath_.isEmpty()) return abandon();
    writeSidecar(lastSavedPath_, segmentBuf_, startEpochMs, endEpochMs);
    ++segmentCount_;
    state_ = State::IDLE;
    segmentBuf_.clear();
    return true;
}

std::vector<QString> GatedRecorder::feed(const std::vector<float>& audio, bool gate) {
    std::vector<QString> saved;
    if (!enabled_) return saved;

    const double blockMs = audio.size() * 1000.0 / sr_;

    // Always push to pre-roll
    const std::size_t maxPreRoll = static_cast<std::size_t>(sr_ * preRollMs_ / 1000.0);
    for (float s : audio) {
        preRoll_.push_back(s);
        if (preRoll_.size() > maxPreRoll) preRoll_.pop_front();
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
        hangLeftMs_ = hangMs_;
    } else {
        hangLeftMs_ -= blockMs;
    }

    if (hangLeftMs_ <= 0 || segmentLenMs_ >= kMaxSegMs) {
        if (endSegment()) saved.push_back(lastSavedPath_);
    }
    return saved;
}

std::vector<QString> GatedRecorder::flush() {
    std::vector<QString> saved;
    // Only report a path that is finalised here; never re-report a segment that
    // feed() already returned (avoids double counting at shutdown).
    if (state_ == State::REC && endSegment()) saved.push_back(lastSavedPath_);
    return saved;
}

QString GatedRecorder::uniqueBasePath() const {
    QDir().mkpath(outDir_);
    const QString stamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    const QString first = QString("%1/%2_%3_%4Hz")
                              .arg(outDir_, stamp, ctx_.mode)
                              .arg(static_cast<qint64>(ctx_.channelFreqHz));
    QString base = first;
    for (int n = 2; n < 10000 && QFile::exists(base + ".wav"); ++n) {
        base = first + QString("_%1").arg(n);
    }
    return base;
}

QString GatedRecorder::writeWav(const std::vector<float>& samples,
                                  const QString& basePath) {
    const QString path = basePath + ".wav";

    // Convert float [-1,1] to int16
    std::vector<std::int16_t> pcm(samples.size());
    for (std::size_t i = 0; i < samples.size(); ++i) {
        float v = std::clamp(samples[i], -1.0f, 1.0f);
        pcm[i] = static_cast<std::int16_t>(v * 32767.0f);
    }

    QFile f(path);
    if (!f.open(QIODevice::WriteOnly)) return {};
    QDataStream ds(&f);
    ds.setByteOrder(QDataStream::LittleEndian);

    const std::int32_t dataBytes = static_cast<std::int32_t>(pcm.size() * sizeof(std::int16_t));
    // RIFF header
    ds.writeRawData("RIFF", 4);
    ds << static_cast<quint32>(36 + dataBytes);
    ds.writeRawData("WAVE", 4);
    // fmt chunk
    ds.writeRawData("fmt ", 4);
    ds << static_cast<quint32>(16);
    ds << static_cast<quint16>(1);
    ds << static_cast<quint16>(1);
    ds << static_cast<quint32>(static_cast<quint32>(sr_));
    ds << static_cast<quint32>(static_cast<quint32>(sr_ * 2));
    ds << static_cast<quint16>(2);
    ds << static_cast<quint16>(16);
    // data chunk
    ds.writeRawData("data", 4);
    ds << dataBytes;
    ds.writeRawData(reinterpret_cast<const char*>(pcm.data()), dataBytes);

    return path;
}

void GatedRecorder::writeSidecar(const QString& wavPath,
                                  const std::vector<float>& samples,
                                  qint64 startEpochMs, qint64 endEpochMs) {
    QJsonObject meta;
    meta["type"] = "mbdsdr-watch-recording";
    meta["sample_rate"] = sr_;
    meta["samples"] = static_cast<qint64>(samples.size());
    // Honest, measured duration from the actual written sample count.
    const double durationS = samples.size() / sr_;
    meta["duration_s"] = durationS;
    meta["center_freq"] = ctx_.centerFreqHz;
    meta["frequency"] = ctx_.channelFreqHz;
    meta["gain_db"] = ctx_.gainDb;
    meta["mode"] = ctx_.mode;
    meta["trigger_threshold_db"] = ctx_.triggerThresholdDb;
    meta["preroll_ms"] = preRollMs_;
    meta["end_delay_ms"] = hangMs_;
    meta["start_time"] =
        QDateTime::fromMSecsSinceEpoch(startEpochMs, QTimeZone("UTC")).toString(Qt::ISODate);
    meta["end_time"] =
        QDateTime::fromMSecsSinceEpoch(endEpochMs, QTimeZone("UTC")).toString(Qt::ISODate);
    meta["hardware"] = ctx_.hardware;
    meta["is_hardware"] = ctx_.hardwareConnected;
    if (ctx_.hardwareConnected) {
        meta["note"] = QString("Real RF capture via %1").arg(ctx_.hardware);
    } else {
        // Offline / injected input must never be mistaken for a real capture.
        meta["note"] = QStringLiteral(
            "非硬件 / NOT HARDWARE -- offline synthesized or injected input");
    }

    const QString jsonPath = wavPath;
    QFile f(jsonPath.chopped(4) + ".json");
    if (f.open(QIODevice::WriteOnly))
        f.write(QJsonDocument(meta).toJson(QJsonDocument::Indented));
}

} // namespace dsp
} // namespace mbdsdr
