// SPDX-License-Identifier: MIT
#include "ui/m17_panel.h"

#include "core/tokens.h"

#include <QHeaderView>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QTableWidget>
#include <QTableWidgetItem>
#include <QVBoxLayout>

namespace mbdsdr {
namespace ui {

namespace {
constexpr int kColSrc  = 0;
constexpr int kColDst  = 1;
constexpr int kColType = 2;
constexpr int kColData = 3;

// Honest frame-kind / payload-class wording. voiceUndecoded MUST surface as an
// explicit "未解码" note -- Codec2 is not bundled, the panel never pretends.
QString typeText(const dsp::M17Call& c) {
    QStringList parts;
    switch (c.frameKind) {
    case 1: parts << QStringLiteral("LSF"); break;
    case 3: parts << QStringLiteral("LICH"); break;
    default:
        parts << (c.isStream ? QStringLiteral("流") : QStringLiteral("包"));
        break;
    }
    switch (c.payloadClass) {
    case 1: parts << QStringLiteral("数据"); break;
    case 2: parts << QStringLiteral("语音"); break;
    case 3: parts << QStringLiteral("混合"); break;
    default: break;
    }
    if (c.voiceUndecoded) parts << QStringLiteral("语音·未解码");
    if (!c.crcOk)          parts << QStringLiteral("CRC错");
    return parts.join(QStringLiteral(" "));
}

// Raw payload bytes -> compact hex preview (honest; voice frames stay hex with
// the "未解码" type note rather than being decoded into fake speech).
QString dataText(const dsp::M17Call& c) {
    if (c.payload.empty()) return QStringLiteral("—");
    QString hex;
    const std::size_t n = std::min<std::size_t>(c.payload.size(), 24);
    for (std::size_t i = 0; i < n; ++i)
        hex += QStringLiteral("%1 ").arg(c.payload[i], 2, 16, QLatin1Char('0'));
    if (c.payload.size() > n) hex += QStringLiteral("…");
    return hex.trimmed().toUpper();
}
} // namespace

M17Panel::M17Panel(QWidget* parent) : QWidget(parent) {
    const int m = tokens::scaled(tokens::kSpacingM);
    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(m, m, m, m);
    root->setSpacing(tokens::scaled(tokens::kSpacingS));

    auto* header = new QHBoxLayout;
    header->setContentsMargins(0, 0, 0, 0);
    header->setSpacing(tokens::scaled(tokens::kSpacingM));
    auto* hint = new QLabel(QStringLiteral("数字呼叫 · m17 4800 4FSK"), this);
    hint->setObjectName("monoInfo");
    hint->setSizePolicy(QSizePolicy::Ignored, hint->sizePolicy().verticalPolicy());
    hint->setMinimumWidth(0);
    header->addWidget(hint, 1);
    clearBtn_ = new QPushButton(QStringLiteral("清空"), this);
    clearBtn_->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
    header->addWidget(clearBtn_);
    root->addLayout(header);

    empty_ = new QLabel(
        QStringLiteral("暂无 m17 呼叫\n切到 m17 解调，收到 LSF/数据流后在此列出\n语音帧仅显示元数据（Codec2 未解码）"), this);
    empty_->setObjectName("statusHint");
    empty_->setAlignment(Qt::AlignCenter);
    empty_->setWordWrap(true);
    root->addWidget(empty_);

    table_ = new QTableWidget(0, 4, this);
    table_->setHorizontalHeaderLabels({
        QStringLiteral("源"), QStringLiteral("目的"), QStringLiteral("类型"), QStringLiteral("数据")});
    table_->verticalHeader()->setVisible(false);
    table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table_->setSelectionMode(QAbstractItemView::NoSelection);
    // Narrow-rail elasticity: shrink the QHeaderView default 100 px min section
    // so Stretch columns actually contract (else a 4-col table forces ~440 px).
    table_->horizontalHeader()->setMinimumSectionSize(tokens::scaled(tokens::kTableMinSectionW));
    table_->horizontalHeader()->setSectionResizeMode(kColSrc,  QHeaderView::Stretch);
    table_->horizontalHeader()->setSectionResizeMode(kColDst,  QHeaderView::Stretch);
    table_->horizontalHeader()->setSectionResizeMode(kColType, QHeaderView::Stretch);
    table_->horizontalHeader()->setSectionResizeMode(kColData, QHeaderView::Stretch);
    root->addWidget(table_, 1);

    connect(clearBtn_, &QPushButton::clicked, this, &M17Panel::clear);
    rebuildTable();
}

void M17Panel::setCalls(const std::vector<dsp::M17Call>& calls) {
    calls_ = calls;
    rebuildTable();
}

void M17Panel::clear() {
    calls_.clear();
    rebuildTable();
    emit clearRequested();
}

void M17Panel::rebuildTable() {
    if (!table_) return;
    table_->setRowCount(0);
    const bool empty = calls_.empty();
    empty_->setVisible(empty);
    table_->setVisible(!empty);

    for (const auto& c : calls_) {
        const int row = table_->rowCount();
        table_->insertRow(row);
        auto* src  = new QTableWidgetItem(QString::fromStdString(c.src));
        auto* dst  = new QTableWidgetItem(QString::fromStdString(c.dst));
        auto* type = new QTableWidgetItem(typeText(c));
        auto* data = new QTableWidgetItem(dataText(c));
        for (auto* it : {src, dst, type, data}) {
            it->setToolTip(it->text());
            it->setFlags(it->flags() & ~Qt::ItemIsEditable & ~Qt::ItemIsSelectable);
        }
        table_->setItem(row, kColSrc, src);
        table_->setItem(row, kColDst, dst);
        table_->setItem(row, kColType, type);
        table_->setItem(row, kColData, data);
    }
}

QString M17Panel::rowSrc(int row) const {
    if (!table_ || row < 0 || row >= table_->rowCount()) return {};
    QTableWidgetItem* it = table_->item(row, kColSrc);
    return it ? it->text() : QString();
}

QString M17Panel::rowType(int row) const {
    if (!table_ || row < 0 || row >= table_->rowCount()) return {};
    QTableWidgetItem* it = table_->item(row, kColType);
    return it ? it->text() : QString();
}

QString M17Panel::rowData(int row) const {
    if (!table_ || row < 0 || row >= table_->rowCount()) return {};
    QTableWidgetItem* it = table_->item(row, kColData);
    return it ? it->text() : QString();
}

} // namespace ui
} // namespace mbdsdr
