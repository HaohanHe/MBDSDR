// SPDX-License-Identifier: MIT
#include "bookmark_manager.h"

#include <QSettings>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonDocument>

namespace mbdsdr {
namespace ui {

static const char* kKey = "ui/bookmarks";

void BookmarkManager::load() {
    items_.clear();
    QSettings s("MBDSDR", "MBDSDR");
    const QJsonDocument doc = QJsonDocument::fromJson(
        s.value(kKey).toByteArray());
    if (!doc.isArray()) return;
    for (const auto& v : doc.array()) {
        const QJsonObject o = v.toObject();
        Bookmark b;
        b.freqHz = o.value("freq").toDouble(0.0);
        b.note = o.value("note").toString();
        if (b.freqHz > 0.0) items_.append(b);
    }
}

void BookmarkManager::save() const {
    QJsonArray arr;
    for (const auto& b : items_) {
        QJsonObject o;
        o["freq"] = b.freqHz;
        o["note"] = b.note;
        arr.append(o);
    }
    QSettings s("MBDSDR", "MBDSDR");
    s.setValue(kKey, QJsonDocument(arr).toJson(QJsonDocument::Compact));
}

void BookmarkManager::add(double freqHz, const QString& note) {
    items_.append({freqHz, note});
    save();
}

void BookmarkManager::remove(int index) {
    if (index < 0 || index >= items_.size()) return;
    items_.removeAt(index);
    save();
}

} // namespace ui
} // namespace mbdsdr
