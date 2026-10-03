// SPDX-License-Identifier: MIT
#include "ui/pocsag_panel.h"

#include "core/tokens.h"

#include <QHeaderView>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QTableWidget>
#include <QTableWidgetItem>
#include <QTime>
#include <QVBoxLayout>

namespace mbdsdr {
namespace ui {

namespace {
// Table columns (narrow rail: the payload column stretches + elides).
constexpr int kColAddress = 0;
constexpr int kColPayload = 1;
constexpr int kColTime    = 2;

QString typeTag(dsp::PocsagMessage::Type t) {
    switch (t) {
    case dsp::PocsagMessage::Type::Numeric: return QStringLiteral("数字");
    case dsp::PocsagMessage::Type::Alpha:  return QStringLiteral("字母");
    default:                               return QStringLiteral("未知");
    }
}
} // namespace

PocsagPanel::PocsagPanel(QWidget* parent) : QWidget(parent) {
    const int m = tokens::scaled(tokens::kSpacingM);
    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(m, m, m, m);
    root->setSpacing(tokens::scaled(tokens::kSpacingS));

    // Header row: title-ish status on the left, clear on the right.
    auto* header = new QHBoxLayout;
    header->setContentsMargins(0, 0, 0, 0);
    header->setSpacing(tokens::scaled(tokens::kSpacingM));
    auto* hint = new QLabel(QStringLiteral("寻呼解码 · POCSAG 1200"), this);
    hint->setObjectName("monoInfo");
    header->addWidget(hint, 1);
    clearBtn_ = new QPushButton(QStringLiteral("清空"), this);
    clearBtn_->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
    header->addWidget(clearBtn_);
    root->addLayout(header);

    empty_ = new QLabel(
        QStringLiteral("暂无可视寻呼消息\n切到 POCSAG 解调，收到呼号帧后在此列出"), this);
    empty_->setObjectName("statusHint");
    empty_->setAlignment(Qt::AlignCenter);
    empty_->setWordWrap(true);
    root->addWidget(empty_);

    table_ = new QTableWidget(0, 3, this);
    table_->setHorizontalHeaderLabels({
        QStringLiteral("地址"), QStringLiteral("消息"), QStringLiteral("时间")});
    table_->verticalHeader()->setVisible(false);
    table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table_->setSelectionMode(QAbstractItemView::NoSelection);
    // Narrow-rail elasticity: default QHeaderView min section is ~100 px, which
    // would force a ~284 px table and overflow the right rail; shrink it so
    // Stretch columns actually contract. Long payloads elide (tooltips keep
    // them readable).
    table_->horizontalHeader()->setMinimumSectionSize(tokens::scaled(36));
    table_->horizontalHeader()->setSectionResizeMode(kColAddress, QHeaderView::Stretch);
    table_->horizontalHeader()->setSectionResizeMode(kColPayload, QHeaderView::Stretch);
    table_->horizontalHeader()->setSectionResizeMode(kColTime, QHeaderView::Stretch);
    root->addWidget(table_, 1);

    connect(clearBtn_, &QPushButton::clicked, this, &PocsagPanel::clear);
    rebuildTable();
}

void PocsagPanel::setMessages(const std::vector<dsp::PocsagMessage>& messages) {
    // Stamp first-seen wall-clock time for rows the engine's growing list adds;
    // rows already known keep their original stamp (the list is never re-timed).
    const QString now = QTime::currentTime().toString(QStringLiteral("hh:mm:ss"));
    for (const auto& m : messages) {
        const auto key = std::make_pair(m.address, m.text);
        if (arrivalTimes_.find(key) == arrivalTimes_.end())
            arrivalTimes_.emplace(key, now);
    }
    messages_ = messages;
    rebuildTable();
}

void PocsagPanel::clear() {
    messages_.clear();
    arrivalTimes_.clear();
    rebuildTable();
    emit clearRequested();
}

bool PocsagPanel::isEmptyView() const {
    return messages_.empty();
}

void PocsagPanel::rebuildTable() {
    if (!table_) return;
    table_->setRowCount(0);
    const bool empty = messages_.empty();
    empty_->setVisible(empty);
    table_->setVisible(!empty);

    for (const auto& m : messages_) {
        const int row = table_->rowCount();
        table_->insertRow(row);
        const QString addr =
            QStringLiteral("%1 · F%2 [%3]")
                .arg(m.address)
                .arg(m.function)
                .arg(typeTag(m.type));
        auto* addrItem = new QTableWidgetItem(addr);
        QString text = QString::fromStdString(m.text);
        if (text.isEmpty()) text = QStringLiteral("—");
        auto* payloadItem = new QTableWidgetItem(text);
        const auto key = std::make_pair(m.address, m.text);
        const QString time =
            arrivalTimes_.count(key) ? arrivalTimes_.at(key) : QStringLiteral("--:--:--");
        auto* timeItem = new QTableWidgetItem(time);
        for (QTableWidgetItem* it : {addrItem, payloadItem, timeItem}) {
            it->setToolTip(it->text());   // elided text remains readable on hover
            it->setFlags(it->flags() & ~Qt::ItemIsEditable & ~Qt::ItemIsSelectable);
        }
        table_->setItem(row, kColAddress, addrItem);
        table_->setItem(row, kColPayload, payloadItem);
        table_->setItem(row, kColTime, timeItem);
    }
}

QString PocsagPanel::rowAddress(int row) const {
    if (!table_ || row < 0 || row >= table_->rowCount()) return {};
    QTableWidgetItem* it = table_->item(row, kColAddress);
    return it ? it->text() : QString();
}

QString PocsagPanel::rowPayload(int row) const {
    if (!table_ || row < 0 || row >= table_->rowCount()) return {};
    QTableWidgetItem* it = table_->item(row, kColPayload);
    return it ? it->text() : QString();
}

} // namespace ui
} // namespace mbdsdr
