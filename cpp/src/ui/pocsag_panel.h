// SPDX-License-Identifier: MIT
//
// POCSAG pager-message panel (right "寻呼" tab).
//
// Honest data policy: the table is repopulated ONLY from the full accumulated
// list handed in by engine pocsagMessagesChanged() (the same diff-pushed
// snapshot ControlHub / Agent pull via pocsagMessages(id)). An empty vector is
// the honest empty state -- the panel never seeds a fake pager message. Rows
// show the real decoded RIC address + function bits + payload text; the time
// column stamps the WALL-CLOCK moment the panel first saw a row (the decoder
// emits no timestamps of its own). The "清空" button drops the local list AND
// emits clearRequested() so the engine resets that channel's POCSAG queue.
//
// Visual tokens: every colour comes from core/tokens.h; every geometry value is
// scaled() at runtime so the narrow right rail never overflows (the text column
// elides, the table scrolls). No raw hex, no hard-coded pixels in business code.
#pragma once

#include <QWidget>

#include <cstdint>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include "dsp/pocsag_decoder.h"

class QLabel;
class QPushButton;
class QTableWidget;

namespace mbdsdr {
namespace ui {

class PocsagPanel : public QWidget {
    Q_OBJECT
public:
    explicit PocsagPanel(QWidget* parent = nullptr);

public slots:
    // Repopulate the table from the engine's full accumulated list. An empty
    // vector returns the panel to its honest empty state.
    void setMessages(const std::vector<dsp::PocsagMessage>& messages);
    // Drop the displayed rows back to the empty state AND emit clearRequested()
    // so the engine resets the channel's decoder queue.
    void clear();

signals:
    // User pressed "清空": the engine should clearDigitalOutputs(selectedVfo).
    void clearRequested();

private:
    void rebuildTable();

    QTableWidget* table_  = nullptr;
    QLabel*       empty_  = nullptr;
    QPushButton*  clearBtn_ = nullptr;

    // The engine re-pushes the WHOLE list on every change; keep the first-seen
    // wall-clock stamp per (address, payload) so a rebuild never re-times rows.
    std::map<std::pair<uint32_t, std::string>, QString> arrivalTimes_;
    std::vector<dsp::PocsagMessage> messages_;

public:
    // Read-outs for QtTest (panel contract, no radio involved).
    int     messageCount() const { return static_cast<int>(messages_.size()); }
    bool    isEmptyView() const;
    QString rowAddress(int row) const;
    QString rowPayload(int row) const;
};

} // namespace ui
} // namespace mbdsdr
