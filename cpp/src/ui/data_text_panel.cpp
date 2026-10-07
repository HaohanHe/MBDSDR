// SPDX-License-Identifier: MIT
#include "ui/data_text_panel.h"

#include "core/tokens.h"

#include <QHeaderView>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QString>
#include <QTableWidget>
#include <QTableWidgetItem>
#include <QVBoxLayout>

namespace mbdsdr {
namespace ui {

namespace {
// Table columns (narrow rail: the body column stretches + elides).
constexpr int kColMode   = 0;
constexpr int kColHeader = 1;
constexpr int kColBody   = 2;
constexpr int kColStatus = 3;

QString acarsStatus(const dsp::AcarsPacket& p) {
    return p.crcOk ? QStringLiteral("CRC 通过") : QStringLiteral("CRC 失败");
}

QString navtexStatus(const dsp::NavtexMessage& m) {
    QString s = m.diversityOk ? QStringLiteral("分集通过")
                              : QStringLiteral("分集 %1 误").arg(m.diversityErrors);
    if (!m.phasingOk) s += QStringLiteral(" · 定相存疑");
    return s;
}
} // namespace

DataTextPanel::DataTextPanel(QWidget* parent) : QWidget(parent) {
    const int m = tokens::scaled(tokens::kSpacingM);
    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(m, m, m, m);
    root->setSpacing(tokens::scaled(tokens::kSpacingS));

    // Header row: title on the left, clear on the right.
    auto* header = new QHBoxLayout;
    header->setContentsMargins(0, 0, 0, 0);
    header->setSpacing(tokens::scaled(tokens::kSpacingM));
    auto* hint = new QLabel(QStringLiteral("数据报文 · ACARS / NAVTEX"), this);
    hint->setObjectName("monoInfo");
    // Narrow-rail elasticity: let the title shrink/elide (mirrors PocsagPanel).
    hint->setSizePolicy(QSizePolicy::Ignored, hint->sizePolicy().verticalPolicy());
    hint->setMinimumWidth(0);
    header->addWidget(hint, 1);
    clearBtn_ = new QPushButton(QStringLiteral("清空"), this);
    clearBtn_->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
    header->addWidget(clearBtn_);
    root->addLayout(header);

    empty_ = new QLabel(
        QStringLiteral("暂无数据报文\n切到 ACARS / NAVTEX 解调，真实收到帧后在此列出"), this);
    empty_->setObjectName("statusHint");
    empty_->setAlignment(Qt::AlignCenter);
    empty_->setWordWrap(true);
    root->addWidget(empty_);

    table_ = new QTableWidget(0, 4, this);
    table_->setHorizontalHeaderLabels({
        QStringLiteral("模式"), QStringLiteral("头部"),
        QStringLiteral("正文"), QStringLiteral("状态")});
    table_->verticalHeader()->setVisible(false);
    table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table_->setSelectionMode(QAbstractItemView::NoSelection);
    table_->horizontalHeader()->setMinimumSectionSize(tokens::scaled(36));
    table_->horizontalHeader()->setSectionResizeMode(kColMode, QHeaderView::Stretch);
    table_->horizontalHeader()->setSectionResizeMode(kColHeader, QHeaderView::Stretch);
    table_->horizontalHeader()->setSectionResizeMode(kColBody, QHeaderView::Stretch);
    table_->horizontalHeader()->setSectionResizeMode(kColStatus, QHeaderView::Stretch);
    root->addWidget(table_, 1);

    connect(clearBtn_, &QPushButton::clicked, this, &DataTextPanel::clear);
    rebuildTable();
}

void DataTextPanel::setAcars(const std::vector<dsp::AcarsPacket>& packets) {
    acars_ = packets;
    rebuildTable();
}

void DataTextPanel::setNavtex(const std::vector<dsp::NavtexMessage>& messages) {
    navtex_ = messages;
    rebuildTable();
}

void DataTextPanel::clear() {
    acars_.clear();
    navtex_.clear();
    rebuildTable();
    emit clearRequested();
}

int DataTextPanel::rowCount() const {
    return static_cast<int>(acars_.size() + navtex_.size());
}

bool DataTextPanel::isEmptyView() const {
    return acars_.empty() && navtex_.empty();
}

void DataTextPanel::rebuildTable() {
    if (!table_) return;
    table_->setRowCount(0);
    const bool empty = isEmptyView();
    empty_->setVisible(empty);
    table_->setVisible(!empty);

    auto addRow = [this](const QString& mode, const QString& head,
                         const QString& body, const QString& status) {
        const int row = table_->rowCount();
        table_->insertRow(row);
        auto cells = {
            new QTableWidgetItem(mode), new QTableWidgetItem(head),
            new QTableWidgetItem(body.isEmpty() ? QStringLiteral("—") : body),
            new QTableWidgetItem(status)};
        for (QTableWidgetItem* it : cells) {
            it->setToolTip(it->text());
            it->setFlags(it->flags() & ~Qt::ItemIsEditable & ~Qt::ItemIsSelectable);
        }
        int col = 0;
        for (QTableWidgetItem* it : cells) table_->setItem(row, col++, it);
    };

    for (const auto& p : acars_) {
        const QString head = QString::fromStdString(
            p.label + "/" + p.blockId + p.ack);
        addRow(QStringLiteral("ACARS"), head,
               QString::fromStdString(p.text), acarsStatus(p));
    }
    for (const auto& m : navtex_) {
        const QString head = QString::fromStdString(
            m.stationB1 + m.typeB2 + "/" + m.numberB3B4);
        addRow(QStringLiteral("NAVTEX"), head,
               QString::fromStdString(m.text), navtexStatus(m));
    }
}

QString DataTextPanel::rowMode(int row) const {
    if (!table_ || row < 0 || row >= table_->rowCount()) return {};
    QTableWidgetItem* it = table_->item(row, kColMode);
    return it ? it->text() : QString();
}

QString DataTextPanel::rowHeader(int row) const {
    if (!table_ || row < 0 || row >= table_->rowCount()) return {};
    QTableWidgetItem* it = table_->item(row, kColHeader);
    return it ? it->text() : QString();
}

QString DataTextPanel::rowBody(int row) const {
    if (!table_ || row < 0 || row >= table_->rowCount()) return {};
    QTableWidgetItem* it = table_->item(row, kColBody);
    return it ? it->text() : QString();
}

QString DataTextPanel::rowStatus(int row) const {
    if (!table_ || row < 0 || row >= table_->rowCount()) return {};
    QTableWidgetItem* it = table_->item(row, kColStatus);
    return it ? it->text() : QString();
}

} // namespace ui
} // namespace mbdsdr
