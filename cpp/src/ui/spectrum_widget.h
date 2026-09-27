// SPDX-License-Identifier: MIT
#pragma once

#include <QWidget>
#include <QPoint>
#include "core/spectrum_frame.h"

class QComboBox;
class QLabel;
class QSpinBox;

namespace mbdsdr {
namespace ui {

class SpectrumWidget : public QWidget {
    Q_OBJECT
public:
    explicit SpectrumWidget(QWidget* parent = nullptr);

public slots:
    void setSpectrum(const SpectrumFrame& frame);
    /// Adjust the vertical (dB) scale. Clamped internally.
    void setDbRange(float minDb, float maxDb);
    /// Reset horizontal zoom back to the full capture span.
    void resetZoom();

signals:
    void fftSizeRequested(int n);
    void frequencyChanged(double newFreqHz);

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void mouseDoubleClickEvent(QMouseEvent* event) override;
    void wheelEvent(QWheelEvent* event) override;
    void contextMenuEvent(QContextMenuEvent* event) override;
    void leaveEvent(QEvent* event) override;

private:
    // Visible frequency window derived from zoomFactor_ around center f0.
    void visibleRange(double& fLo, double& fHi, double& spanVis) const;

    SpectrumFrame frame_;
    QComboBox* fftCombo_   = nullptr;
    QSpinBox*  dbMinSpin_ = nullptr;
    QSpinBox*  dbMaxSpin_ = nullptr;
    QLabel*    testLabel_  = nullptr;
    QLabel*    infoLabel_  = nullptr;
    bool dragging_ = false;
    double vfoFreq_ = 0;
    QPoint hoverPos_ = QPoint(-1, -1);

    float  dbMin_ = -100.0f;   // vertical scale bounds
    float  dbMax_ = 0.0f;
    double zoomFactor_ = 1.0;  // 1 = full span, kZoomMax = max zoom-in
};

} // namespace ui
} // namespace mbdsdr
