// SPDX-License-Identifier: MIT
#include "shortcuts_dialog.h"
#include "core/tokens.h"

#include <QGridLayout>
#include <QLabel>
#include <QDialogButtonBox>

namespace mbdsdr {
namespace ui {

ShortcutsDialog::ShortcutsDialog(QWidget* parent) : QDialog(parent) {
    setWindowTitle("快捷键");
    setMinimumWidth(tokens::scaled(tokens::kSettingsMinW));
    auto* lay = new QGridLayout(this);
    const struct { const char* key; const char* desc; } rows[] = {
        {"← / →",        "中心频率 ± 步进"},
        {"Shift+← / →",  "中心频率 ± 步进/10（细调）"},
        {"↑ / ↓",        "带宽 ×2 / ÷2"},
        {"Space",        "静音切换"},
        {"Ctrl+R",       "录制 / 停止"},
    };
    int r = 0;
    for (const auto& row : rows) {
        auto* k = new QLabel(QString::fromLatin1(row.key), this);
        k->setObjectName("monoInfo");
        auto* d = new QLabel(QString::fromLatin1(row.desc), this);
        lay->addWidget(k, r, 0);
        lay->addWidget(d, r, 1);
        ++r;
    }
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Close, this);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::accept);
    lay->addWidget(buttons, r, 0, 1, 2);
}

} // namespace ui
} // namespace mbdsdr
