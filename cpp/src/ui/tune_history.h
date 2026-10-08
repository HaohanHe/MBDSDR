// SPDX-License-Identifier: MIT
//
// Recent-tune history: the bounded, deduped, QSettings-serialisable list of
// real centre frequencies the receiver has actually settled on. Deliberately a
// header-only, widget-free value object (same pattern as status_format.h):
//   - maybePush() records a read-back frequency and dedups against both the
//     newest entry and any earlier duplicate within tokens::kTuneDedupHz;
//   - to/fromVariantList() round-trip the QSettings key;
//   - formatting to a user label goes through ui::formatFrequencyAutoHz.
// MainWindow owns one instance, feeds it from the ~1 Hz telemetry tick (the
// single non-invasive choke through which EVERY real centre read-back flows),
// and renders it in the 频率 group's "最近" combo. Empty list = honest
// "无调谐记录" empty state; there is no demo seed.
#pragma once

#include <QtCore/QString>
#include <QtCore/QVector>
#include <QtCore/QVariant>
#include <QtCore/QVariantList>

#include <algorithm>
#include <cmath>

#include "core/tokens.h"
#include "status_format.h"

namespace mbdsdr {
namespace ui {

class TuneHistory {
public:
    explicit TuneHistory(int cap = tokens::kTuneHistoryMax,
                         double epsilonHz = tokens::kTuneDedupHz)
        : cap_(cap > 0 ? cap : tokens::kTuneHistoryMax), epsilonHz_(epsilonHz) {}

    // Record a real read-back centre frequency. Returns true iff the list
    // changed (so the caller can repaint + debounce-persist). Non-positive or
    // sub-epsilon values are ignored -- a spinbox drag produces one settled
    // entry, not a trail of intermediates.
    bool maybePush(double hz) {
        if (!(hz > 0.0)) return false;
        if (!entries_.isEmpty() && std::abs(entries_.first() - hz) <= epsilonHz_)
            return false;   // same as the current tuning, nothing new
        int dup = -1;
        for (int i = 0; i < entries_.size(); ++i)
            if (std::abs(entries_[i] - hz) <= epsilonHz_) { dup = i; break; }
        if (dup >= 0) entries_.removeAt(dup);   // move the existing entry to front
        entries_.prepend(hz);
        while (entries_.size() > cap_) entries_.removeLast();
        return true;
    }

    bool            isEmpty() const { return entries_.isEmpty(); }
    int             size()    const { return entries_.size(); }
    const QVector<double>& entries() const { return entries_; }

    // Human label for entry i (used by the combo). Out of range -> "--".
    QString labelAt(int i) const {
        return (i >= 0 && i < entries_.size()) ? formatFrequencyAutoHz(entries_[i])
                                               : QStringLiteral("--");
    }

    QVariantList toVariantList() const {
        QVariantList out;
        out.reserve(entries_.size());
        for (double hz : entries_) out.append(hz);
        return out;
    }

    // Rebuild from the persisted list (most-recent first). Malformed /
    // non-positive entries are skipped; cap honoured on restore.
    void fromVariantList(const QVariantList& in) {
        entries_.clear();
        for (const QVariant& v : in) {
            bool ok = false;
            const double hz = v.toDouble(&ok);
            if (ok && hz > 0.0) {
                entries_.append(hz);
                if (entries_.size() >= cap_) break;
            }
        }
    }

private:
    int    cap_;
    double epsilonHz_;
    QVector<double> entries_;
};

} // namespace ui
} // namespace mbdsdr
