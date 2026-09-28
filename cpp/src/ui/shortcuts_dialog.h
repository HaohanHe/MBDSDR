// SPDX-License-Identifier: MIT
// Simple read-only shortcuts reference dialog. No logic, just a two-column table.
#pragma once
#include <QDialog>

namespace mbdsdr {
namespace ui {

class ShortcutsDialog : public QDialog {
public:
    explicit ShortcutsDialog(QWidget* parent = nullptr);
};

} // namespace ui
} // namespace mbdsdr
