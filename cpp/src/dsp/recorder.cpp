// SPDX-License-Identifier: MIT
#include "recorder.h"

#include <QJsonDocument>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QDebug>
#include <cstring>

namespace mbdsdr {
namespace dsp {

Recorder::Recorder() = default;
Recorder::~Recorder() { if (recording_) stop(); }

bool Recorder::start(const QString& dir, double sr, double freq, double gainDb,
                     const QString& hardware) {
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

    dataFile_.open(currentDataPath_.toStdString(), std::ios::binary | std::ios::trunc);
    if (!dataFile_.is_open()) {
        qWarning() << "[Recorder] cannot open" << currentDataPath_;
        return false;
    }

    sampleRate_ = sr;
    centerFreq_ = freq;
    sampleCount_ = 0;

    QJsonObject global;
    global["core:datatype"] = "cf32_le";
    global["core:sample_rate"] = sr;
    global["core:version"] = "1.0.0";
    global["core:num_channels"] = 1;
    global["core:frequency"] = freq;
    global["core:hw"] = hardware;
    global["core:author"] = "MBDSDR";
    global["core:num_samples"] = 0;

    QJsonObject capture;
    capture["core:sample_start"] = 0;
    capture["core:frequency"] = freq;
    capture["core:datetime"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    capture["mbdsdr:gain_db"] = gainDb;
    QJsonArray captures;
    captures.append(capture);

    meta_ = QJsonObject();
    meta_["global"] = global;
    meta_["captures"] = captures;
    meta_["annotations"] = QJsonArray();

    recording_ = true;
    qInfo() << "[Recorder] started" << currentDataPath_;
    return true;
}

void Recorder::writeIQ(const std::vector<std::complex<float>>& data) {
    if (!recording_ || data.empty()) return;
    dataFile_.write(reinterpret_cast<const char*>(data.data()),
                    static_cast<std::streamsize>(data.size() * sizeof(std::complex<float>)));
    sampleCount_ += data.size();
}

void Recorder::stop() {
    if (!recording_) return;
    recording_ = false;
    dataFile_.close();

    // Patch num_samples and write meta. NOTE: meta_["global"].toObject() returns
    // a COPY -- the patch must be written back into meta_ or it is silently lost
    // (the sidecar would always read num_samples=0, a dishonest header).
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
    qInfo() << "[Recorder] stopped, samples:" << sampleCount_;
}

} // namespace dsp
} // namespace mbdsdr
