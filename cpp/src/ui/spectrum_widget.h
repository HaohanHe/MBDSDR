// SPDX-License-Identifier: MIT
// SpectrumWidget: pure view. Receives SpectrumFrame from the engine
// (via queued signal-slot connection across threads) and paints with QPainter.
// It owns NO data source, NO timer, NO DSP -- just renders what it is given.
//
// The on-canvas and top-bar labels explicitly mark the data as TEST SIGNAL.
#pragma once

#include <QWidget>
#include "core/spectrum_frame.h"

class QComboBox;
class QLabel;

namespace mbdsdr {
namespace ui {

class SpectrumWidget : public QWidget {
    Q_OBJECT
public:
    explicit SpectrumWidget(QWidget* parent = nullptr);

public slots:
    /// Called by SpectrumEngine::spectrumReady (queued, GUI thread).
    void setSpectrum(const SpectrumFrame& frame);

signals:
    /// Emitted when the user changes the FFT size in the combo box.
    void fftSizeRequested(int n);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    SpectrumFrame frame_;          // last frame received (GUI-thread copy)
    QComboBox* fftCombo_   = nullptr;
    QLabel*    testLabel_  = nullptr;
    QLabel*    infoLabel_  = nullptr;
};

} // namespace ui
} // namespace mbdsdr
