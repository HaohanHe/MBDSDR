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
    /// Reset horizontal zoom back to the full capture span, re-centered on f0.
    void resetZoom();
    /// Restore a persisted zoom factor (1.0 = full span). Clamped internally.
    void setZoomFactor(double z);
    double zoomFactor() const { return zoomFactor_; }

    /// Read/write the dB spinboxes (used by MainWindow for QSettings).
    int  dbMinValue() const;
    int  dbMaxValue() const;
    void setDbSpinValues(int lo, int hi);

    /// Read/write the selected FFT size (points) for QSettings persistence.
    int  fftSizeValue() const;
    void setFftSizeValue(int n);

signals:
    void fftSizeRequested(int n);
    void frequencyChanged(double newFreqHz);
    /// Emitted whenever the visible frequency window changes (zoom/pan/reset).
    void visibleRangeChanged(double fLoHz, double fHiHz);
    /// Emitted when an internal control (dB spinboxes, FFT combo) changes so
    /// MainWindow can persist settings immediately.
    void viewChanged();

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
    // Visible frequency window derived from zoomFactor_ around viewCenterHz_.
    void visibleRange(double& fLo, double& fHi, double& spanVis) const;
    void emitVisibleRange();

    SpectrumFrame frame_;
    QComboBox* fftCombo_   = nullptr;
    QSpinBox*  dbMinSpin_ = nullptr;
    QSpinBox*  dbMaxSpin_ = nullptr;
    QLabel*    testLabel_  = nullptr;
    QLabel*    infoLabel_  = nullptr;
    bool dragging_ = false;
    bool panning_ = false;          // Shift+drag: pan view, do not retune f0
    double vfoFreq_ = 0;
    QPoint hoverPos_ = QPoint(-1, -1);
    QPoint lastPanPos_ = QPoint(-1, -1);

    float  dbMin_ = -100.0f;   // vertical scale bounds
    float  dbMax_ = 0.0f;
    double zoomFactor_ = 1.0;  // 1 = full span, kZoomMax = max zoom-in
    double viewCenterHz_ = 0.0; // visible-window center; pans away from f0
};

} // namespace ui
} // namespace mbdsdr
