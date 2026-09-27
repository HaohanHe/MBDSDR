// SPDX-License-Identifier: MIT
#include "file_source.h"

#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QFileInfo>
#include <QDebug>

namespace mbdsdr {
namespace dsp {

FileSource::FileSource(const QString& base) : basePath_(base) {}
FileSource::~FileSource() { if (opened_) stop(); }

bool FileSource::start() {
    const QString metaPath = basePath_ + ".sigmf-meta";
    QFile mf(metaPath);
    if (!mf.open(QIODevice::ReadOnly)) {
        qWarning() << "[FileSource] cannot open meta" << metaPath;
        return false;
    }
    QJsonParseError pe;
    QJsonDocument doc = QJsonDocument::fromJson(mf.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError) {
        qWarning() << "[FileSource] meta parse error:" << pe.errorString();
        return false;
    }
    auto g = doc.object()["global"].toObject();
    sampleRate_ = g["core:sample_rate"].toDouble();
    centerFreq_ = g["core:frequency"].toDouble();
    totalSamples_ = static_cast<std::uint64_t>(g["core:num_samples"].toInt());

    const QString dataPath = basePath_ + ".sigmf-data";
    dataFile_.open(dataPath.toStdString(), std::ios::binary);
    if (!dataFile_.is_open()) {
        qWarning() << "[FileSource] cannot open data" << dataPath;
        return false;
    }
    readSamples_ = 0;
    opened_ = true;
    qInfo() << "[FileSource] opened" << basePath_ << "sr=" << sampleRate_;
    return true;
}

void FileSource::stop() {
    if (dataFile_.is_open()) dataFile_.close();
    opened_ = false;
}

std::size_t FileSource::readIQ(std::vector<std::complex<float>>& out) {
    if (!opened_) return 0;
    const std::size_t n = out.size();
    dataFile_.read(reinterpret_cast<char*>(out.data()), n * sizeof(std::complex<float>));
    const std::size_t got = static_cast<std::size_t>(dataFile_.gcount()) / sizeof(std::complex<float>);
    readSamples_ += got;
    // Loop at EOF
    if (got < n) {
        dataFile_.clear();
        dataFile_.seekg(0, std::ios::beg);
        readSamples_ = 0;
    }
    return got;
}

QString FileSource::name() const {
    return QString("File: %1").arg(QFileInfo(basePath_).fileName());
}

} // namespace dsp
} // namespace mbdsdr
