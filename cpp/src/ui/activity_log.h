// SPDX-License-Identifier: MIT
// Signal activity log (data + persistence only, no QWidget).
//
// DISTINCT from bookmarks: a bookmark is a USER-CURATED favourite; this log is
// an AUTOMATIC, append-only record of signals the scanner / sweep actually
// observed (frequency / time / level / mode / source). It has its own storage
// key and its own UI area, and is never merged into bookmarks.
//
// Persisted as a JSON array in QSettings("MBDSDR","MBDSDR") under
// "ui/activityLog", capped at a fixed number of entries (newest first) so a
// long scan session cannot grow storage unbounded.
#pragma once

#include <QList>
#include <QString>
#include <QDateTime>

namespace mbdsdr {
namespace ui {

struct SignalActivity {
    QDateTime timeUtc;        // when observed
    double  frequencyHz = 0.0;
    double  levelDbfs = 0.0;  // peak level reported by the real scan
    QString mode;             // demod mode in effect (may be empty)
    QString source;           // "scan_band" / "scanner" / ... -- where it came from
};

class ActivityLog {
public:
    void load();              // read JSON array from QSettings "ui/activityLog"
    void save() const;        // write JSON array (newest first)

    // Append one observed signal (newest first).  Drops entries beyond the cap
    // (oldest). Auto-saves.  Returns index in the list (0 = newest).
    int append(const SignalActivity& a);
    void clear();             // drop all + save
    bool exportToFile(const QString& path) const;  // JSON array dump; true on write

    const QList<SignalActivity>& list() const { return items_; }  // newest first
    int count() const { return items_.size(); }

    static int maxEntries();   // token-capped history length

private:
    QList<SignalActivity> items_;
};

} // namespace ui
} // namespace mbdsdr
