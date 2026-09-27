// SPDX-License-Identifier: MIT
#include "about_dialog.h"

#include <QVBoxLayout>
#include <QLabel>

namespace mbdsdr {
namespace ui {

AboutDialog::AboutDialog(QWidget* parent) : QDialog(parent) {
    setWindowTitle("关于");
    setMinimumWidth(380);

    auto* lay = new QVBoxLayout(this);
    lay->addWidget(new QLabel("<h2>MBDSDR C++</h2>", this));
    lay->addWidget(new QLabel("版本: 0.1.0 (Phase 9)", this));
    lay->addWidget(new QLabel("", this));
    lay->addWidget(new QLabel("<b>依赖</b>", this));
    lay->addWidget(new QLabel("Qt 6.2.4 · librtlsdr 0.6.0 · CMake · C++17", this));
    lay->addWidget(new QLabel("", this));
    lay->addWidget(new QLabel("<b>参考项目</b>", this));
    lay->addWidget(new QLabel("SDR++ · dump1090 · sigutils · SatDump", this));
    lay->addWidget(new QLabel("", this));
    lay->addWidget(new QLabel("GPL-3.0", this));
}

} // namespace ui
} // namespace mbdsdr
