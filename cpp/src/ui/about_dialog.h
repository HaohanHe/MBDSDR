// SPDX-License-Identifier: MIT
#pragma once

#include <QDialog>

namespace mbdsdr {
namespace ui {

class AboutDialog : public QDialog {
    Q_OBJECT
public:
    explicit AboutDialog(QWidget* parent = nullptr);
};

} // namespace ui
} // namespace mbdsdr
