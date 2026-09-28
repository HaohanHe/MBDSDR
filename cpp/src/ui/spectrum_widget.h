// SPDX-License-Identifier: MIT
#pragma once

#include <QWidget>
#include <QPoint>
#include <QList>
#include "core/spectrum_frame.h"
#include "dsp/peak_detector.h"

class QComboBox;
class QLabel;
class QSpinBox;
class QTableWidget;

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
    // Run peak detection on the latest frame and refresh the list + markers.
    void detectPeaks();
    // Retune to hz; if hz is outside the current visible window, pan the view
    // so it is centered (keeping zoomFactor_), then notify the waterfall.
    void tuneAndCenter(double hz);

    SpectrumFrame frame_;
    QComboBox* fftCombo_   = nullptr;
    QSpinBox*  dbMinSpin_ = nullptr;
    QSpinBox*  dbMaxSpin_ = nullptr;
    QSpinBox*  peakThreshSpin_ = nullptr;
    QTableWidget* peakTable_ = nullptr;
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

    QList<mbdsdr::dsp::PeakInfo> peaks_;   // displayed peaks (tracked, matured)
    float peakThresholdDb_ = 15.0f;        // dB above median (set from tokens in ctor)
    int   highlightedPeak_ = -1;           // selected table row -> peak index (-1 none)
    QString lastPeakSignature_;            // cheap throttle for table rebuild

    // Cross-frame peak tracking: a detected peak that survives N frames gets a
    // stable ID and only then shows up, so the list stops jittering.
    struct TrackedPeak {
        int    id = 0;
        double freqHz = 0;
        float  dbfs = 0;
        double bandwidthHz = 0;
        int    seenFrames = 0;
        int    missFrames = 0;
        bool   matchedThisFrame = false;
    };
    QList<TrackedPeak> tracked_;
    int nextPeakId_ = 1;
};

} // namespace ui
} // namespace mbdsdr
