// SPDX-License-Identifier: MIT
//
// Data-message panel (right "数据" tab): one table for the packet-text modes
// ACARS (ARINC 618 MSK) and NAVTEX (SITOR-B FEC).
//
// Honest data policy: the table is repopulated ONLY from the full accumulated
// lists handed in by engine acarsPacketsChanged() / navtexMessagesChanged()
// (the same snapshots ControlHub / Agent pull via acarsPackets(id) /
// navtexMessages(id)). Empty vectors are the honest empty state -- the panel
// never seeds a fake packet. Each row is tagged with its mode, a header, the
// body and an honest CRC / diversity status. "清空" drops the local rows AND
// emits clearRequested() so the engine resets that channel's decoder queues.
//
// Visual tokens: colours from core/tokens.h; geometry scaled() at runtime so the
// narrow rail never overflows (body column elides, table scrolls). No hard-coded
// pixels, no raw hex in business code.
#pragma once

#include <QWidget>

#include <string>
#include <vector>

#include "dsp/acars_decoder.h"
#include "dsp/navtex_decoder.h"

class QLabel;
class QPushButton;
class QTableWidget;

namespace mbdsdr {
namespace ui {

class DataTextPanel : public QWidget {
    Q_OBJECT
public:
    explicit DataTextPanel(QWidget* parent = nullptr);

public slots:
    // Repopulate from the engine's full accumulated lists. Empty vectors return
    // the panel to its honest empty state.
    void setAcars(const std::vector<dsp::AcarsPacket>& packets);
    void setNavtex(const std::vector<dsp::NavtexMessage>& messages);
    // Drop the displayed rows back to empty AND emit clearRequested() so the
    // engine resets the channel's decoder queues.
    void clear();

signals:
    // User pressed "清空": the engine should clearDigitalOutputs(selectedVfo).
    void clearRequested();

private:
    void rebuildTable();

    QTableWidget* table_    = nullptr;
    QLabel*       empty_    = nullptr;
    QPushButton*  clearBtn_ = nullptr;

    std::vector<dsp::AcarsPacket>   acars_;
    std::vector<dsp::NavtexMessage> navtex_;

public:
    // Read-outs for QtTest (panel contract, no radio involved).
    int     rowCount() const;
    bool    isEmptyView() const;
    QString rowMode(int row) const;
    QString rowHeader(int row) const;
    QString rowBody(int row) const;
    QString rowStatus(int row) const;
};

} // namespace ui
} // namespace mbdsdr
