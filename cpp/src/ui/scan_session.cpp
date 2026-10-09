// SPDX-License-Identifier: MIT
#include "scan_session.h"

#include <QFile>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonDocument>
#include <QJsonParseError>

namespace mbdsdr {
namespace ui {

QByteArray scanSessionToJson(const ScanSession& s) {
    QJsonObject o;
    o["app"]        = QStringLiteral("MBDSDR");
    o["kind"]       = QStringLiteral("scan-session");
    o["version"]    = 1;
    o["startMHz"]   = s.startMHz;
    o["stopMHz"]    = s.stopMHz;
    o["stepIndex"]  = s.stepIndex;
    o["dwellMs"]    = s.dwellMs;
    o["thresholdDb"]= s.thresholdDb;
    o["dirIndex"]   = s.dirIndex;
    o["holdIndex"]  = s.holdIndex;
    o["lingerMs"]   = s.lingerMs;
    o["holdMs"]     = s.holdMs;
    o["bmOnly"]     = s.bmOnly;
    o["mode"]       = s.mode;
    o["bwHz"]       = s.bwHz;

    QJsonArray arr;
    for (const ScanSessionHit& h : s.hits) {
        QJsonObject ho;
        ho["freqHz"]  = h.freqHz;
        ho["levelDb"] = h.levelDb;
        ho["mode"]    = h.mode;
        ho["bwHz"]    = h.bwHz;
        arr.append(ho);
    }
    o["hits"] = arr;
    return QJsonDocument(o).toJson(QJsonDocument::Compact);
}

namespace {
// Require a numeric (int-or-double) field; reject missing / string / bool.
double reqNum(const QJsonObject& o, const char* key, QString* err) {
    const QJsonValue v = o.value(QString::fromUtf8(key));
    if (v.isUndefined() || v.isNull()) {
        if (err) *err = QStringLiteral("缺少字段 \"%1\"").arg(QString::fromUtf8(key));
        return 0.0;
    }
    if (!v.isDouble()) {
        if (err) *err = QStringLiteral("字段 \"%1\" 不是数字").arg(QString::fromUtf8(key));
        return 0.0;
    }
    return v.toDouble();
}
QString reqStr(const QJsonObject& o, const char* key, QString* err) {
    const QJsonValue v = o.value(QString::fromUtf8(key));
    if (v.isUndefined() || v.isNull()) {
        if (err) *err = QStringLiteral("缺少字段 \"%1\"").arg(QString::fromUtf8(key));
        return QString();
    }
    if (!v.isString()) {
        if (err) *err = QStringLiteral("字段 \"%1\" 不是字符串").arg(QString::fromUtf8(key));
        return QString();
    }
    return v.toString();
}
bool reqBool(const QJsonObject& o, const char* key, QString* err, bool* out) {
    const QJsonValue v = o.value(QString::fromUtf8(key));
    if (v.isUndefined() || v.isNull()) {
        if (err) *err = QStringLiteral("缺少字段 \"%1\"").arg(QString::fromUtf8(key));
        return false;
    }
    if (!v.isBool()) {
        if (err) *err = QStringLiteral("字段 \"%1\" 不是布尔值").arg(QString::fromUtf8(key));
        return false;
    }
    *out = v.toBool();
    return true;
}
} // namespace

bool scanSessionFromJson(const QByteArray& bytes, ScanSession& out, QString* err) {
    QJsonParseError pe{};
    const QJsonDocument doc = QJsonDocument::fromJson(bytes, &pe);
    if (doc.isNull() || !doc.isObject()) {
        if (err) *err = QStringLiteral("不是有效的 JSON 对象：%1").arg(pe.errorString());
        return false;
    }
    const QJsonObject o = doc.object();
    if (o.value(QLatin1String("kind")).toString() != QLatin1String("scan-session")) {
        if (err) *err = QStringLiteral("kind 字段不是 \"scan-session\"（文件可能不是 MBDSDR 扫描会话）");
        return false;
    }

    ScanSession s;
    s.startMHz    = reqNum(o, "startMHz", err);
    s.stopMHz     = reqNum(o, "stopMHz", err);
    s.stepIndex   = static_cast<int>(reqNum(o, "stepIndex", err));
    s.dwellMs     = static_cast<int>(reqNum(o, "dwellMs", err));
    s.thresholdDb = reqNum(o, "thresholdDb", err);
    s.dirIndex    = static_cast<int>(reqNum(o, "dirIndex", err));
    s.holdIndex   = static_cast<int>(reqNum(o, "holdIndex", err));
    s.lingerMs    = static_cast<int>(reqNum(o, "lingerMs", err));
    s.holdMs      = static_cast<int>(reqNum(o, "holdMs", err));
    if (err && !err->isEmpty()) return false;

    if (!reqBool(o, "bmOnly", err, &s.bmOnly)) return false;
    s.mode        = reqStr(o, "mode", err);
    if (err && !err->isEmpty()) return false;
    s.bwHz        = reqNum(o, "bwHz", err);
    if (err && !err->isEmpty()) return false;

    const QJsonValue hitsVal = o.value(QLatin1String("hits"));
    if (hitsVal.isUndefined() || hitsVal.isNull()) {
        if (err) *err = QStringLiteral("缺少字段 \"hits\"");
        return false;
    }
    if (!hitsVal.isArray()) {
        if (err) *err = QStringLiteral("字段 \"hits\" 不是数组");
        return false;
    }
    for (const QJsonValue& v : hitsVal.toArray()) {
        if (!v.isObject()) {
            if (err) *err = QStringLiteral("hits 数组中有条目不是对象");
            return false;
        }
        const QJsonObject ho = v.toObject();
        ScanSessionHit h;
        h.freqHz  = reqNum(ho, "freqHz", err);
        if (err && !err->isEmpty()) return false;
        h.levelDb = static_cast<float>(reqNum(ho, "levelDb", err));
        if (err && !err->isEmpty()) return false;
        h.mode    = reqStr(ho, "mode", err);
        if (err && !err->isEmpty()) return false;
        h.bwHz    = reqNum(ho, "bwHz", err);
        if (err && !err->isEmpty()) return false;
        s.hits.append(h);
    }

    out = s;
    if (err) err->clear();
    return true;
}

bool scanSessionSaveFile(const QString& path, const ScanSession& s, QString* err) {
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        if (err) *err = QStringLiteral("无法写入文件：%1").arg(f.errorString());
        return false;
    }
    const QByteArray bytes = scanSessionToJson(s);
    const qint64 n = f.write(bytes);
    f.close();
    if (n != bytes.size()) {
        if (err) *err = QStringLiteral("写入不完整（%1/%2 字节）").arg(n).arg(bytes.size());
        return false;
    }
    return true;
}

bool scanSessionLoadFile(const QString& path, ScanSession& out, QString* err) {
    QFile f(path);
    if (!f.exists()) {
        if (err) *err = QStringLiteral("文件不存在：%1").arg(path);
        return false;
    }
    if (!f.open(QIODevice::ReadOnly)) {
        if (err) *err = QStringLiteral("无法读取文件：%1").arg(f.errorString());
        return false;
    }
    const QByteArray bytes = f.readAll();
    f.close();
    return scanSessionFromJson(bytes, out, err);
}

} // namespace ui
} // namespace mbdsdr
