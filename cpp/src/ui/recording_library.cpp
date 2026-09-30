// SPDX-License-Identifier: MIT
#include "recording_library.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFileInfoList>
#include <QJsonDocument>
#include <QJsonObject>
#include <QDataStream>
#include <QDateTime>

#include <cmath>
#include <cstring>

namespace mbdsdr {
namespace ui {

namespace {
quint16 rd16(const uchar* p) { return quint16(p[0]) | (quint16(p[1]) << 8); }
quint32 rd32(const uchar* p) {
    return quint32(p[0]) | (quint32(p[1]) << 8) |
           (quint32(p[2]) << 16) | (quint32(p[3]) << 24);
}

// Known demod mode tokens, longest-first so "WFM" beats no false match.
QStringList knownModes() {
    return {QStringLiteral("NFM"), QStringLiteral("WFM"), QStringLiteral("AM"),
            QStringLiteral("LSB"), QStringLiteral("USB"), QStringLiteral("CW"),
            QStringLiteral("DSB"), QStringLiteral("SAM"), QStringLiteral("DRM")};
}

// Try to interpret a token as a timestamp stamp "yyyyMMdd_HHmmss".
QDateTime parseStamp(const QString& tok) {
    return QDateTime::fromString(tok, "yyyyMMdd_HHmmss");
}

QString isoToDisplay(const QString& iso) {
    QDateTime dt = QDateTime::fromString(iso, Qt::ISODate);
    if (!dt.isValid()) return QString();
    return dt.toLocalTime().toString("yyyy-MM-dd HH:mm:ss");
}
} // namespace

QVector<RecordingEntry> RecordingLibrary::scan(const QString& dir) {
    QVector<RecordingEntry> out;
    if (dir.isEmpty()) return out;
    QDir d(dir);
    if (!d.exists()) return out;   // honest empty: directory not created yet

    const QFileInfoList files =
        d.entryInfoList(QStringList{"*.wav"}, QDir::Files, QDir::Time);
    for (const QFileInfo& fi : files) {
        RecordingEntry e;
        e.wavPath = fi.absoluteFilePath();
        e.bytes   = fi.size();
        const QString base = fi.absolutePath() + "/" + fi.completeBaseName();
        e.jsonPath = base + ".json";

        // Prefer the real sidecar proof; fall back to the filename template.
        if (QFileInfo::exists(e.jsonPath)) {
            e.meta = readSidecar(e.jsonPath);
        }
        // Sidecar missing or incomplete: fill the gaps from the filename.
        const RecordingMeta fn = parseFileName(fi.completeBaseName());
        if (e.meta.time.isEmpty())       e.meta.time = fn.time;
        if (e.meta.frequencyHz == 0.0)   e.meta.frequencyHz = fn.frequencyHz;
        if (e.meta.mode.isEmpty())       e.meta.mode = fn.mode;
        out.append(e);
    }
    return out;
}

RecordingMeta RecordingLibrary::readSidecar(const QString& jsonPath) {
    RecordingMeta m;
    QFile f(jsonPath);
    if (!f.open(QIODevice::ReadOnly)) return m;
    QJsonParseError pe{};
    QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError || !doc.isObject()) return m;
    const QJsonObject o = doc.object();

    m.sidecarType = o.value("type").toString();

    // Continuous demod-audio recordings carry center_freq + datetime; gated
    // watch segments carry frequency (channel) + start_time/end_time.
    double center = o.value("center_freq").toDouble(-1.0);
    double channel = o.value("frequency").toDouble(-1.0);
    if (channel > 0.0)      m.frequencyHz = channel;
    else if (center > 0.0)  m.frequencyHz = center;

    m.mode = o.value("mode").toString();
    m.durationS = o.value("duration_s").toDouble(0.0);

    if (o.contains("trigger_threshold_db"))
        m.triggerThresholdDb = o.value("trigger_threshold_db").toDouble(0.0);

    // Prefer the precise start_time; fall back to the generic datetime stamp.
    QString t = o.value("start_time").toString();
    if (t.isEmpty()) t = o.value("datetime").toString();
    if (!t.isEmpty()) m.time = isoToDisplay(t);

    m.isHardware = o.value("is_hardware").toBool(false);
    return m;
}

RecordingMeta RecordingLibrary::parseFileName(const QString& baseName) {
    RecordingMeta m;
    const QStringList parts = baseName.split('_', Qt::SkipEmptyParts);
    if (parts.isEmpty()) return m;

    // The stamp "yyyyMMdd_HHmmss" ITSELF contains an underscore, so splitting
    // by '_' breaks it into [date, time]. Re-join the leading two tokens.
    int start = 0;
    if (parts.size() >= 2) {
        const QDateTime dt = parseStamp(parts[0] + "_" + parts[1]);
        if (dt.isValid()) {
            m.time = dt.toLocalTime().toString("yyyy-MM-dd HH:mm:ss");
            start = 2;
        }
    }
    if (m.time.isEmpty()) {
        const QDateTime dt = parseStamp(parts[0]);
        if (dt.isValid()) {
            m.time = dt.toLocalTime().toString("yyyy-MM-dd HH:mm:ss");
            start = 1;
        }
    }

    // Walk the remaining tokens looking for a frequency and a mode.
    const QStringList modes = knownModes();
    for (int i = start; i < parts.size(); ++i) {
        const QString tok = parts[i];

        // Watch template: "<mode>_<freq>Hz" -> e.g. "100500000Hz".
        if (tok.endsWith("Hz", Qt::CaseInsensitive)) {
            bool ok = false;
            const qlonglong hz = tok.chopped(2).toLongLong(&ok);
            if (ok && hz > 0) { m.frequencyHz = static_cast<double>(hz); continue; }
        }
        // Audio template: freq in MHz as a decimal string, e.g. "100.500".
        if (m.frequencyHz <= 0.0 && tok.contains('.')) {
            bool ok = false;
            const double mhz = tok.toDouble(&ok);
            if (ok && mhz > 0.0) { m.frequencyHz = mhz * 1e6; continue; }
        }
        // Mode token: exact, case-insensitive match against known modes.
        if (m.mode.isEmpty()) {
            for (const QString& md : modes) {
                if (tok.compare(md, Qt::CaseInsensitive) == 0) { m.mode = md; break; }
            }
        }
    }
    return m;
}

WavProbe RecordingLibrary::probeWav(const QString& path) {
    WavProbe p;
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) { p.error = "无法打开文件"; return p; }

    QByteArray hdr = f.read(12);
    if (hdr.size() < 12) { p.error = "文件过小"; return p; }
    if (memcmp(hdr.data() + 0, "RIFF", 4) != 0 || memcmp(hdr.data() + 8, "WAVE", 4) != 0) {
        p.error = "不是 WAV (RIFF/WAVE) 文件"; return p;
    }

    // Walk sub-chunks generically (don't assume fmt immediately precedes data).
    bool haveFmt = false;
    while (true) {
        QByteArray head = f.read(8);
        if (head.size() < 8) break;   // truncated
        const char* id = head.constData();
        const quint32 size = rd32(reinterpret_cast<const uchar*>(head.constData() + 4));
        if (memcmp(id, "fmt ", 4) == 0) {
            QByteArray body = f.read(size);
            if (body.size() < 16) break;
            const uchar* b = reinterpret_cast<const uchar*>(body.constData());
            p.audioFormat   = rd16(b + 0);
            p.channels      = rd16(b + 2);
            p.sampleRate    = rd32(b + 4);
            p.bitsPerSample = rd16(b + 14);
            haveFmt = true;
            if (size & 1) f.read(1);   // chunk word-align padding
        } else if (memcmp(id, "data", 4) == 0) {
            p.dataBytes = size;
            break;   // payload location is at f.pos(); we need only header facts
        } else {
            f.seek(f.pos() + size + (size & 1));   // skip unknown chunk
        }
    }

    if (!haveFmt) { p.error = "缺少 fmt 块"; return p; }
    if (p.audioFormat != 1) {
        p.error = QString("不支持的格式 (audioFormat=%1, 仅支持 PCM)").arg(p.audioFormat);
        return p;
    }
    if (p.channels < 1 || p.bitsPerSample != 16) {
        p.error = QString("不支持 %1-bit/%2声道 (仅 16-bit PCM)")
                      .arg(p.bitsPerSample).arg(p.channels);
        return p;
    }
    const quint32 bytesPerFrame = p.channels * (p.bitsPerSample / 8);
    p.frames = bytesPerFrame ? p.dataBytes / bytesPerFrame : 0;
    p.ok = true;
    return p;
}

bool RecordingLibrary::decodePcmMonoToFloat(const WavProbe& probe, const QString& path,
                                             std::vector<float>& out) {
    out.clear();
    if (!probe.ok || probe.dataBytes == 0) return false;
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) return false;

    // Relocate the data chunk (probeWav leaves the file at the fmt end; re-walk
    // so decode is self-contained and order-independent).
    if (!f.seek(12)) return false;
    qint64 dataStart = -1;
    while (true) {
        QByteArray head = f.read(8);
        if (head.size() < 8) break;
        const quint32 size = rd32(reinterpret_cast<const uchar*>(head.constData() + 4));
        if (memcmp(head.constData(), "data", 4) == 0) { dataStart = f.pos(); break; }
        f.seek(f.pos() + size + (size & 1));
    }
    if (dataStart < 0 || !f.seek(dataStart)) return false;

    const int channels = probe.channels;
    const qint64 frames = probe.dataBytes / (channels * 2);
    out.reserve(static_cast<std::size_t>(frames));
    QByteArray block;
    const qint64 want = frames * channels * 2;
    block = f.read(want);
    if (block.size() < static_cast<int>(want)) return false;
    const uchar* b = reinterpret_cast<const uchar*>(block.constData());
    for (qint64 fr = 0; fr < frames; ++fr) {
        // Take the first channel; int16 little-endian -> [-1,1] float.
        const qint64 off = fr * channels * 2;
        const qint16 s = static_cast<qint16>(rd16(b + off));
        out.push_back(static_cast<float>(s) / 32768.0f);
    }
    return true;
}

bool RecordingLibrary::removeEntry(const RecordingEntry& e) {
    bool ok = QFile::remove(e.wavPath);
    // Sidecar is best-effort: a capture recorded without proof must still
    // delete cleanly.
    if (!e.jsonPath.isEmpty() && QFileInfo::exists(e.jsonPath))
        QFile::remove(e.jsonPath);
    return ok;
}

} // namespace ui
} // namespace mbdsdr
