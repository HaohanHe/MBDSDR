// SPDX-License-Identifier: MIT
#include "file_source.h"

#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QDataStream>
#include <QtEndian>
#include <QDebug>

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace dsp {

namespace {
quint16 rd16(const uchar* p) { return quint16(p[0]) | (quint16(p[1]) << 8); }
quint32 rd32(const uchar* p) {
    return quint32(p[0]) | (quint32(p[1]) << 8) |
           (quint32(p[2]) << 16) | (quint32(p[3]) << 24);
}
} // namespace

FileSource::FileSource(const QString& base) : basePath_(base) {}
FileSource::~FileSource() { if (opened_) stop(); }

bool FileSource::openSigmf(const QString& base) {
    basePath_ = base;
    fmt_ = Fmt::Sigmf;
    const QString metaPath = base + ".sigmf-meta";
    QFile mf(metaPath);
    if (!mf.open(QIODevice::ReadOnly)) {
        error_ = QStringLiteral("无法打开 SigMF meta: %1").arg(metaPath);
        qWarning() << "[FileSource]" << error_;
        return false;
    }
    QJsonParseError pe;
    QJsonDocument doc = QJsonDocument::fromJson(mf.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError) {
        error_ = QStringLiteral("SigMF meta 解析失败: %1").arg(pe.errorString());
        return false;
    }
    const QJsonObject g = doc.object().value("global").toObject();
    sampleRate_ = g.value("core:sample_rate").toDouble(0.0);
    centerFreq_ = g.value("core:frequency").toDouble(0.0);
    const QString dtype = g.value("core:datatype").toString();
    if (dtype != QLatin1String("cf32_le")) {
        error_ = QStringLiteral("不支持的 SigMF 数据类型 %1 (仅 cf32_le)").arg(dtype);
        return false;
    }
    totalSamples_ = static_cast<qint64>(g.value("core:num_samples").toDouble(0.0));

    dataPath_ = base + ".sigmf-data";
    dataStartOffset_ = 0;
    if (totalSamples_ <= 0) {
        // Honest fallback: derive from the data file size.
        QFileInfo di(dataPath_);
        if (di.exists()) totalSamples_ = di.size() / 8;
    }
    return true;
}

bool FileSource::openWav(const QString& wavPath, double centerFreqHint,
                         const QString& mode) {
    fmt_ = Fmt::Wav;
    dataPath_ = wavPath;
    centerFreq_ = centerFreqHint;
    mode_ = mode;

    QFile f(wavPath);
    if (!f.open(QIODevice::ReadOnly)) {
        error_ = QStringLiteral("无法打开 WAV: %1").arg(wavPath);
        return false;
    }
    const QByteArray hdr = f.read(12);
    if (hdr.size() < 12 || memcmp(hdr.data(), "RIFF", 4) != 0 ||
        memcmp(hdr.data() + 8, "WAVE", 4) != 0) {
        error_ = QStringLiteral("不是 WAV (RIFF/WAVE) 文件");
        return false;
    }
    bool haveFmt = false;
    quint16 channels = 0, bits = 0;
    while (true) {
        const QByteArray head = f.read(8);
        if (head.size() < 8) break;
        const quint32 size = rd32(reinterpret_cast<const uchar*>(head.constData() + 4));
        if (memcmp(head.constData(), "fmt ", 4) == 0) {
            const QByteArray body = f.read(size);
            if (body.size() < 16) break;
            const uchar* b = reinterpret_cast<const uchar*>(body.constData());
            const quint16 fmtTag = rd16(b + 0);
            channels = rd16(b + 2);
            sampleRate_ = rd32(b + 4);
            bits = rd16(b + 14);
            haveFmt = true;
            if (size & 1) f.seek(f.pos() + 1);
        } else if (memcmp(head.constData(), "data", 4) == 0) {
            dataStartOffset_ = f.pos();
            totalSamples_ = channels > 0 ? size / (channels * 2) : 0;
            break;
        } else {
            f.seek(f.pos() + size + (size & 1));
        }
    }
    if (!haveFmt) { error_ = "WAV 缺少 fmt 块"; return false; }
    if (bits != 16) {
        error_ = QStringLiteral("不支持 %1-bit WAV (仅 16-bit PCM)").arg(bits);
        return false;
    }
    if (channels < 1) { error_ = "WAV 声道数非法"; return false; }
    return true;
}

bool FileSource::openRaw(const QString& path, double sampleRateHz) {
    fmt_ = Fmt::Raw;
    dataPath_ = path;
    sampleRate_ = sampleRateHz;
    mode_ = QStringLiteral("原始·用户指定参数");
    dataStartOffset_ = 0;
    QFileInfo fi(path);
    if (!fi.exists()) { error_ = QStringLiteral("文件不存在: %1").arg(path); return false; }
    totalSamples_ = fi.size() / 8;   // cf32_le = 8 bytes per complex frame
    if (sampleRateHz <= 0) {
        error_ = "原始文件需要用户指定采样率";
        return false;
    }
    return true;
}

bool FileSource::start() {
    if (dataPath_.isEmpty()) {
        // Back-compat: bare base path -> SigMF.
        if (!openSigmf(basePath_)) return false;
    }
    dataFile_.open(dataPath_.toStdString(), std::ios::binary);
    if (!dataFile_.is_open()) {
        error_ = QStringLiteral("无法打开数据文件: %1").arg(dataPath_);
        return false;
    }
    dataFile_.seekg(dataStartOffset_);
    playedSamples_ = 0;
    paused_.store(false);
    opened_ = true;
    qInfo() << "[FileSource] opened" << dataPath_ << "sr=" << sampleRate_
            << "frames=" << totalSamples_;
    return true;
}

void FileSource::stop() {
    if (dataFile_.is_open()) dataFile_.close();
    opened_ = false;
}

long FileSource::dataOffsetForSample(qint64 sample) const {
    long bytesPer = (fmt_ == Fmt::Wav) ? 2 : 8;   // WAV mono int16 | cf32
    if (fmt_ == Fmt::Wav) {
        // Mono WAV: 2 bytes per frame.
        return dataStartOffset_ + static_cast<long>(sample * 2);
    }
    return dataStartOffset_ + static_cast<long>(sample * 8);
}

std::size_t FileSource::readIQ(std::vector<std::complex<float>>& out) {
    if (!opened_ || paused_.load()) return 0;
    const std::size_t n = out.size();
    if (n == 0) return 0;

    if (fmt_ == Fmt::Wav) {
        // Stream int16 mono -> complex (real, 0 imag). Constant-size block only.
        const std::size_t bytes = n * 2;
        std::vector<uchar> buf(bytes);
        dataFile_.read(reinterpret_cast<char*>(buf.data()), bytes);
        const std::size_t got = static_cast<std::size_t>(dataFile_.gcount()) / 2;
        for (std::size_t i = 0; i < got; ++i) {
            const qint16 s = static_cast<qint16>(rd16(buf.data() + i * 2));
            out[i] = std::complex<float>(static_cast<float>(s) / 32768.0f, 0.0f);
        }
        playedSamples_ += static_cast<qint64>(got);
        if (got < n) {   // EOF: loop back to the start
            dataFile_.clear();
            dataFile_.seekg(dataStartOffset_);
            playedSamples_ = 0;
        }
        return got;
    }

    // SigMF / raw cf32_le: direct complex float read.
    dataFile_.read(reinterpret_cast<char*>(out.data()), n * sizeof(std::complex<float>));
    const std::size_t got = static_cast<std::size_t>(dataFile_.gcount()) /
                            sizeof(std::complex<float>);
    playedSamples_ += static_cast<qint64>(got);
    if (got < n) {
        dataFile_.clear();
        dataFile_.seekg(dataStartOffset_);
        playedSamples_ = 0;
    }
    return got;
}

void FileSource::seekFraction(double f01) {
    if (!opened_ || totalSamples_ <= 0) return;
    const double f = std::clamp(f01, 0.0, 1.0);
    const qint64 target = static_cast<qint64>(f * totalSamples_);
    dataFile_.clear();
    dataFile_.seekg(dataOffsetForSample(target));
    playedSamples_ = target;
}

double FileSource::progressFraction() const {
    if (totalSamples_ <= 0) return 0.0;
    return std::clamp(static_cast<double>(playedSamples_) /
                      static_cast<double>(totalSamples_), 0.0, 1.0);
}

QString FileSource::name() const {
    return QString("File: %1").arg(QFileInfo(dataPath_).fileName());
}

} // namespace dsp
} // namespace mbdsdr
