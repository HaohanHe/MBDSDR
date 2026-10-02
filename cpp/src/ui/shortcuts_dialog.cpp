// SPDX-License-Identifier: MIT
#include "shortcuts_dialog.h"
#include "ui/shortcuts_catalog.h"
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
    // Rendered from the single-source catalog (ui/shortcuts_catalog.h) so the
    // table can never drift out of sync with the MainWindow wiring again.
    int r = 0;
    for (const auto& row : shortcutCatalog()) {
        auto* k = new QLabel(QString::fromLatin1(row.sequence), this);
        k->setObjectName("monoInfo");
        auto* d = new QLabel(QString::fromLatin1(row.description), this);
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
