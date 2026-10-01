// SPDX-License-Identifier: MIT
#include "activity_log.h"
#include "core/tokens.h"

#include <QSettings>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QFile>

namespace mbdsdr {
namespace ui {

int ActivityLog::maxEntries() { return tokens::kActivityLogMaxEntries; }

void ActivityLog::load() {
    items_.clear();
    QSettings s;
    const QJsonDocument doc = QJsonDocument::fromJson(
        s.value("ui/activityLog").toByteArray());
    if (!doc.isArray()) return;
    for (const auto& v : doc.array()) {
        const QJsonObject o = v.toObject();
        SignalActivity a;
        a.timeUtc = QDateTime::fromString(o.value("time").toString(), Qt::ISODate);
        a.frequencyHz = o.value("freq").toDouble();
        a.levelDbfs = o.value("level").toDouble();
        a.mode = o.value("mode").toString();
        a.source = o.value("source").toString();
        if (a.frequencyHz > 0.0) items_.append(a);
    }
}

void ActivityLog::save() const {
    QJsonArray arr;
    for (const SignalActivity& a : items_) {
        QJsonObject o;
        o["time"] = a.timeUtc.toUTC().toString(Qt::ISODate);
        o["freq"] = a.frequencyHz;
        o["level"] = a.levelDbfs;
        o["mode"] = a.mode;
        o["source"] = a.source;
        arr.append(o);
    }
    QSettings s;
    s.setValue("ui/activityLog", QJsonDocument(arr).toJson(QJsonDocument::Compact));
}

int ActivityLog::append(const SignalActivity& a) {
    if (a.frequencyHz <= 0.0) return -1;   // honest: no invented frequency
    items_.prepend(a);                       // newest first
    while (items_.size() > maxEntries()) items_.removeLast();
    save();
    return 0;
}

void ActivityLog::clear() {
    items_.clear();
    save();
}

bool ActivityLog::exportToFile(const QString& path) const {
    QJsonArray arr;
    for (const SignalActivity& a : items_) {
        QJsonObject o;
        o["time"] = a.timeUtc.toUTC().toString(Qt::ISODate);
        o["freq"] = a.frequencyHz;
        o["level"] = a.levelDbfs;
        o["mode"] = a.mode;
        o["source"] = a.source;
        arr.append(o);
    }
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) return false;
    f.write(QJsonDocument(arr).toJson(QJsonDocument::Indented));
    return true;
}

} // namespace ui
} // namespace mbdsdr
