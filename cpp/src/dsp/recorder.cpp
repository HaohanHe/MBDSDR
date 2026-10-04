// SPDX-License-Identifier: MIT
#include "recorder.h"

#include <QJsonDocument>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QDebug>
#include <cstring>

namespace mbdsdr {
namespace dsp {

Recorder::Recorder() = default;
Recorder::~Recorder() { if (recording_) stop(); }

void Recorder::setMaxSegmentSeconds(double seconds) {
    maxSegmentSeconds_ = seconds;
    // Convert to a sample cap lazily once sampleRate_ is known; until then the
    // cap is 0 (no rotation). recompute in startWithBase.
    if (sampleRate_ > 0 && maxSegmentSeconds_ > 0)
        maxSegmentSamples_ = static_cast<std::uint64_t>(sampleRate_ * maxSegmentSeconds_);
    else
        maxSegmentSamples_ = 0;
}

void Recorder::setSegmentContext(const QString& dir, double freqHz,
                                 double gainDb, const QString& hardware) {
    segDir_ = dir;
    segFreq_ = freqHz;
    segGainDb_ = gainDb;
    segHardware_ = hardware;
}

bool Recorder::takeSegmentRotated() {
    return segmentRotated_.exchange(false);
}

bool Recorder::start(const QString& dir, double sr, double freq, double gainDb,
                     const QString& hardware) {
    setSegmentContext(dir, freq, gainDb, hardware);
    QDir().mkpath(dir);
    const QString stamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    QString base = QString("%1/%2_%3Hz").arg(dir, stamp).arg(
        static_cast<qint64>(freq));
    // Second-resolution stamp: two recordings in the same second on the same
    // frequency would collide.  Disambiguate with _2/_3... so a live stop/start
    // NEVER truncates an in-flight or previous capture.
    int n = 2;
    while (QFile::exists(base + ".sigmf-data") && n < 10000) {
        base = QString("%1_%2").arg(base).arg(n++);
    }
    return startWithBase(base, sr, freq, gainDb, hardware);
}

bool Recorder::startWithBase(const QString& base, double sr, double freq,
                             double gainDb, const QString& hardware) {
    if (recording_) return false;
    currentDataPath_ = base + ".sigmf-data";
    currentMetaPath_ = base + ".sigmf-meta";
    sampleRate_ = sr;
    centerFreq_ = freq;
    sampleCount_ = 0;
    // Cache the RF context so a rotated segment can name itself consistently,
    // even though the engine opened the first segment by explicit base path.
    if (segDir_.isEmpty()) segDir_ = QFileInfo(base).absolutePath();
    if (segFreq_ == 0.0) segFreq_ = freq;
    if (segGainDb_ == 0.0 && gainDb != 0.0) segGainDb_ = gainDb;
    if (segHardware_.isEmpty()) segHardware_ = hardware;
    // Recompute the sample cap now that sr is known.
    if (maxSegmentSeconds_ > 0 && sr > 0)
        maxSegmentSamples_ = static_cast<std::uint64_t>(sr * maxSegmentSeconds_);
    if (!openDataFileLocked()) return false;
    recording_ = true;
    segmentIndex_ = 1;
    qInfo() << "[Recorder] started" << currentDataPath_;
    return true;
}

// Open the current data file + (re)build the sidecar JSON for a fresh segment.
bool Recorder::openDataFileLocked() {
    dataFile_.open(currentDataPath_.toStdString(), std::ios::binary | std::ios::trunc);
    if (!dataFile_.is_open()) {
        qWarning() << "[Recorder] cannot open" << currentDataPath_;
        return false;
    }
    sampleCount_ = 0;

    QJsonObject global;
    global["core:datatype"] = "cf32_le";
    global["core:sample_rate"] = sampleRate_;
    global["core:version"] = "1.0.0";
    global["core:num_channels"] = 1;
    global["core:frequency"] = centerFreq_;
    global["core:hw"] = segHardware_;
    global["core:author"] = "MBDSDR";
    global["core:num_samples"] = 0;

    QJsonObject capture;
    capture["core:sample_start"] = 0;
    capture["core:frequency"] = centerFreq_;
    capture["core:datetime"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    capture["mbdsdr:gain_db"] = segGainDb_;
    QJsonArray captures;
    captures.append(capture);

    meta_ = QJsonObject();
    meta_["global"] = global;
    meta_["captures"] = captures;
    meta_["annotations"] = QJsonArray();
    return true;
}

// Finalise the current segment: patch num_samples, write meta, close the file.
// Leaves recording_ true (caller opens the next segment).
void Recorder::finalizeSegmentLocked() {
    dataFile_.close();
    QJsonObject global = meta_.value(QStringLiteral("global")).toObject();
    global[QStringLiteral("core:num_samples")] = static_cast<qint64>(sampleCount_);
    meta_[QStringLiteral("global")] = global;
    QJsonDocument doc(meta_);
    QFile f(currentMetaPath_);
    if (f.open(QIODevice::WriteOnly)) {
        f.write(doc.toJson(QJsonDocument::Indented));
    } else {
        qWarning() << "[Recorder] cannot write meta (non-fatal)";
    }
    qInfo() << "[Recorder] segment finished, samples:" << sampleCount_
            << "->" << currentDataPath_;
}

// Build a fresh collision-avoided base for the next rotated segment and open it.
bool Recorder::openNewSegmentLocked() {
    QDir().mkpath(segDir_);
    const QString stamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    QString base = QString("%1/%2_%3Hz").arg(segDir_, stamp)
                       .arg(static_cast<qint64>(segFreq_));
    int n = 2;
    while (QFile::exists(base + ".sigmf-data") && n < 10000) {
        base = QString("%1_%2").arg(base).arg(n++);
    }
    currentDataPath_ = base + ".sigmf-data";
    currentMetaPath_ = base + ".sigmf-meta";
    centerFreq_ = segFreq_;
    if (!openDataFileLocked()) return false;
    ++segmentIndex_;
    segmentRotated_.store(true);
    qInfo() << "[Recorder] rotated to segment" << segmentIndex_ << currentDataPath_;
    return true;
}

void Recorder::writeIQ(const std::vector<std::complex<float>>& data) {
    if (!recording_ || data.empty()) return;
    dataFile_.write(reinterpret_cast<const char*>(data.data()),
                    static_cast<std::streamsize>(data.size() * sizeof(std::complex<float>)));
    sampleCount_ += data.size();

    // Auto-segment: once this segment holds enough samples, finalise it and open
    // a fresh collision-avoided file. Real sample-clock cap (not a wall timer).
    if (maxSegmentSamples_ > 0 && sampleCount_ >= maxSegmentSamples_) {
        finalizeSegmentLocked();
        if (!openNewSegmentLocked()) {
            // Honest failure: stop rather than silently lose data.
            qWarning() << "[Recorder] segment rotate failed; stopping capture";
            recording_ = false;
        }
    }
}

void Recorder::stop() {
    if (!recording_) return;
    recording_ = false;
    finalizeSegmentLocked();
    qInfo() << "[Recorder] stopped, total segments:" << segmentIndex_;
}

} // namespace dsp
} // namespace mbdsdr
