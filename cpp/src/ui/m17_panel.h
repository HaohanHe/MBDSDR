// SPDX-License-Identifier: MIT
//
// M17 digital-call panel (right "m17" tab).
//
// Honest data policy: the table is repopulated ONLY from the accumulated call
// list pushed by engine m17CallsChanged() (the same snapshot ControlHub /
// Agent pull via m17Calls(id)). Voice-stream frames arrive with
// voiceUndecoded=true -- Codec2 is deliberately NOT bundled, so the panel
// marks those rows "语音 · 未解码" instead of playing invented audio. Data
// frames show the real raw payload bytes as hex. An empty vector is the honest
// empty state; no callsign is ever pre-seeded.
//
// tokens::scaled geometry + a stretching, eliding payload column keep the
// narrow right rail free of horizontal overflow.
#pragma once

#include <QWidget>

#include <string>
#include <vector>

#include "dsp/m17_decoder.h"

class QLabel;
class QPushButton;
class QTableWidget;

namespace mbdsdr {
namespace ui {

class M17Panel : public QWidget {
    Q_OBJECT
public:
    explicit M17Panel(QWidget* parent = nullptr);

public slots:
    // Repopulate from the engine's accumulated call list. Empty = honest empty.
    void setCalls(const std::vector<dsp::M17Call>& calls);
    // Drop rows to the empty state AND emit clearRequested() (engine reset).
    void clear();

signals:
    void clearRequested();

private:
    void rebuildTable();

    QTableWidget* table_   = nullptr;
    QLabel*       empty_   = nullptr;
    QPushButton*  clearBtn_ = nullptr;

    std::vector<dsp::M17Call> calls_;

public:
    // Read-outs for QtTest.
    int     callCount() const { return static_cast<int>(calls_.size()); }
    bool    isEmptyView() const { return calls_.empty(); }
    QString rowSrc(int row) const;
    QString rowType(int row) const;
    QString rowData(int row) const;
};

} // namespace ui
} // namespace mbdsdr
