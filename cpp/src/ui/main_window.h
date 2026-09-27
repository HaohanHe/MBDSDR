// SPDX-License-Identifier: MIT
// Main window: top bar + horizontal 3-column splitter + bottom dock.
// Creates the SpectrumEngine (QThread) and wires it directly to the
// central SpectrumWidget via queued signal-slot -- no glue model layer.
#pragma once

#include <QMainWindow>

class QLabel;

namespace mbdsdr {
namespace dsp  { class SpectrumEngine; }
namespace ui   { class SpectrumWidget; }

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override;

private:
    dsp::SpectrumEngine* engine_   = nullptr;
    ui::SpectrumWidget*  spectrum_ = nullptr;
    QLabel*               statusLabel_ = nullptr;
};

} // namespace mbdsdr
