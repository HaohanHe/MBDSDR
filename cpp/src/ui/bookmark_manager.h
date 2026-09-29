// SPDX-License-Identifier: MIT
// SDR++-style frequency bookmark manager (data + persistence only, no QWidget).
//
// Bookmarks are persisted as a JSON array in QSettings("MBDSDR","MBDSDR")
// under the key "ui/bookmarks". Each object uses the keys:
//   "name" (string), "freq" (number, Hz), "mode" (string),
//   "bw"   (number, Hz), "group" (string; empty = default group).
//
// IMPORTANT: This manager NEVER pre-fills or builds in any FM / broadcast
// station. When there is no stored data, load() yields an empty list by
// design. Legacy storage of the shape {freq, note} is migrated on read:
// "note" becomes "name", mode="" / bw=0 / group="".
#pragma once

#include <QList>
#include <QString>
#include <QStringList>

namespace mbdsdr {
namespace ui {

struct Bookmark {
    QString name;             // bookmark name (legacy "note" migrates to name)
    double  frequencyHz = 0.0;
    QString mode;             // "NFM"/"WFM"/"AM"/"LSB"/"USB" etc.; empty = unspecified
    double  bandwidthHz = 0.0;// 0 = unspecified
    QString group;            // group/list name; empty = default group
                              // (UI may render as "默认", storage keeps "")
};

// Kept ordered by (group, frequencyHz) ascending at all times.
class BookmarkManager {
public:
    void load();                  // read JSON array from QSettings "ui/bookmarks"
    void save() const;            // write JSON array (Compact)
    int  add(const Bookmark& b);  // freq must be >0 else ignored and returns -1;
                                  // keeps order; returns landing index; auto save
    void removeAt(int index);     // erase + save (out-of-bounds silent)
    void update(int index, const Bookmark& b); // replace fields, re-sort + save
    void clear();                 // drop all + save

    const QList<Bookmark>& list() const { return items_; }
    int  count() const { return items_.size(); }
    QStringList groups() const;                 // distinct group names, asc (incl. "")
    QList<Bookmark> byGroup(const QString& group) const;
    QList<double> frequencies() const;          // ordered Hz, for scanner bookmark mode
    void sortByFrequency();                     // re-sort by (group, freq) + save

private:
    static bool lessThan(const Bookmark& a, const Bookmark& b);
    static void insertSorted(QList<Bookmark>& items, const Bookmark& b);
    QList<Bookmark> items_;
};

} // namespace ui
} // namespace mbdsdr
