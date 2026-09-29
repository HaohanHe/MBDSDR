// SPDX-License-Identifier: MIT
#include "bookmark_manager.h"

#include <QSettings>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonDocument>
#include <QSet>

#include <algorithm>

namespace mbdsdr {
namespace ui {

// Persistence key (unchanged from the original minimal implementation).
static const char* kKey = "ui/bookmarks";

bool BookmarkManager::lessThan(const Bookmark& a, const Bookmark& b) {
    if (a.group != b.group) return a.group < b.group;  // "" sorts first
    return a.frequencyHz < b.frequencyHz;
}

void BookmarkManager::insertSorted(QList<Bookmark>& items, const Bookmark& b) {
    int i = 0;
    for (; i < items.size(); ++i) {
        if (lessThan(b, items[i])) break;
    }
    items.insert(i, b);
}

void BookmarkManager::load() {
    items_.clear();
    QSettings s("MBDSDR", "MBDSDR");
    const QJsonDocument doc = QJsonDocument::fromJson(
        s.value(kKey).toByteArray());
    if (!doc.isArray()) return;  // no storage -> empty list, never built-in
    for (const auto& v : doc.array()) {
        const QJsonObject o = v.toObject();
        Bookmark b;
        b.frequencyHz = o.value("freq").toDouble(0.0);
        b.name        = o.value("name").toString();
        b.mode        = o.value("mode").toString("");
        b.bandwidthHz = o.value("bw").toDouble(0.0);
        b.group       = o.value("group").toString("");
        // Legacy migration: objects written by the old minimal shape
        // {freq, note} carry no "name" key -> map note to name and reset
        // the new optional fields to their "unspecified" defaults.
        if (!o.contains("name")) {
            b.name        = o.value("note").toString();
            b.mode        = "";
            b.bandwidthHz = 0.0;
            b.group       = "";
        }
        if (b.frequencyHz > 0.0) insertSorted(items_, b);
    }
}

void BookmarkManager::save() const {
    QJsonArray arr;
    for (const auto& b : items_) {
        QJsonObject o;
        o["name"]  = b.name;
        o["freq"]  = b.frequencyHz;
        o["mode"]  = b.mode;
        o["bw"]    = b.bandwidthHz;
        o["group"] = b.group;
        arr.append(o);
    }
    QSettings s("MBDSDR", "MBDSDR");
    s.setValue(kKey, QJsonDocument(arr).toJson(QJsonDocument::Compact));
}

int BookmarkManager::add(const Bookmark& b) {
    if (!(b.frequencyHz > 0.0)) return -1;  // reject non-positive frequencies
    int i = 0;
    for (; i < items_.size(); ++i) {
        if (lessThan(b, items_[i])) break;
    }
    items_.insert(i, b);
    save();
    return i;
}

void BookmarkManager::removeAt(int index) {
    if (index < 0 || index >= items_.size()) return;  // silent out-of-bounds
    items_.removeAt(index);
    save();
}

void BookmarkManager::update(int index, const Bookmark& b) {
    if (index < 0 || index >= items_.size()) return;  // silent out-of-bounds
    items_.removeAt(index);
    insertSorted(items_, b);  // keep (group, frequency) order
    save();
}

void BookmarkManager::clear() {
    items_.clear();
    save();
}

QStringList BookmarkManager::groups() const {
    QSet<QString> seen;
    QStringList result;
    for (const auto& b : items_) {
        if (!seen.contains(b.group)) {
            seen.insert(b.group);
            result.append(b.group);
        }
    }
    std::sort(result.begin(), result.end());  // ascending, "" first
    return result;
}

QList<Bookmark> BookmarkManager::byGroup(const QString& group) const {
    QList<Bookmark> out;
    for (const auto& b : items_) {
        if (b.group == group) out.append(b);
    }
    return out;
}

QList<double> BookmarkManager::frequencies() const {
    QList<double> out;
    out.reserve(items_.size());
    for (const auto& b : items_) out.append(b.frequencyHz);
    return out;
}

void BookmarkManager::sortByFrequency() {
    std::sort(items_.begin(), items_.end(), lessThan);
    save();
}

} // namespace ui
} // namespace mbdsdr
