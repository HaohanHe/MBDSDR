// SPDX-License-Identifier: MIT
#include "about_dialog.h"

#include "core/tokens.h"
#include "mbdsdr_version.h"

#include <QVBoxLayout>
#include <QLabel>

namespace mbdsdr {
namespace ui {

AboutDialog::AboutDialog(QWidget* parent) : QDialog(parent) {
    setWindowTitle("关于");
    setMinimumWidth(tokens::kAboutMinW);

    auto* lay = new QVBoxLayout(this);
    lay->addWidget(new QLabel("<h2>MBDSDR C++</h2>", this));
    lay->addWidget(new QLabel(QStringLiteral("版本: %1").arg(QStringLiteral(MBDSDR_VERSION)), this));
    lay->addWidget(new QLabel("", this));
    lay->addWidget(new QLabel("<b>依赖</b>", this));
    lay->addWidget(new QLabel("Qt 6 · librtlsdr 2 · CMake · C++17", this));
    lay->addWidget(new QLabel("FFT 为内置实现（未使用 FFTW）", this));
    lay->addWidget(new QLabel("", this));
    lay->addWidget(new QLabel("<b>参考项目</b>", this));
    lay->addWidget(new QLabel("SDR++ · dump1090 · sigutils · SatDump", this));
    lay->addWidget(new QLabel("", this));
    lay->addWidget(new QLabel("MIT 许可证", this));
}

} // namespace ui
} // namespace mbdsdr
