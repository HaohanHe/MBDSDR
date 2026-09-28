// SPDX-License-Identifier: MIT
// Frequency bookmarks: user-saved frequencies with a free-text note.
// Persisted as a JSON array in QSettings. No pre-filled stations.
#pragma once

#include <QList>
#include <QString>

namespace mbdsdr {
namespace ui {

struct Bookmark {
    double freqHz = 0.0;
    QString note;
};

class BookmarkManager {
public:
    void load();   // from QSettings "ui/bookmarks"
    void save() const;
    void add(double freqHz, const QString& note);
    void remove(int index);
    const QList<Bookmark>& list() const { return items_; }

private:
    QList<Bookmark> items_;
};

} // namespace ui
} // namespace mbdsdr
