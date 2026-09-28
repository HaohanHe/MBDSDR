// SPDX-License-Identifier: MIT
#pragma once

#include <QWidget>
#include <QList>
#include "core/spectrum_frame.h"
#include "dsp/peak_detector.h"
#include "dsp/vfo_manager.h"

class QComboBox;
class QLabel;
class QSpinBox;
class QTableWidget;

namespace mbdsdr {
namespace ui {

class SpectrumDisplay;

// Container widget for the unified spectrum/waterfall view. It owns the compact
// tool strip (FFT size / window / average / Max / dB range / peak threshold /
// waterfall speed / palette), the honest "test signal" banner, the embedded
// SpectrumDisplay canvas and the bottom peak table. All tuning / zoom / band
// state lives in the canvas; this class only wires controls to it and forwards
// the existing public API so MainWindow's wiring stays unchanged.
class SpectrumWidget : public QWidget {
    Q_OBJECT
public:
    explicit SpectrumWidget(QWidget* parent = nullptr);

    /// Access to the embedded canvas (offscreen tests inject mouse/wheel
    /// events here while spying on this container's forwarded signals).
    SpectrumDisplay* displayCanvas() const { return canvas_; }

public slots:
    void setSpectrum(const SpectrumFrame& frame);
    void setDbRange(float minDb, float maxDb);
    void resetZoom();
    void setBandwidthHz(double hz);
    double bandwidthHz() const;
    void setStepHz(double hz);
    void setZoomFactor(double z);
    double zoomFactor() const;

    int  dbMinValue() const;
    int  dbMaxValue() const;
    void setDbSpinValues(int lo, int hi);

    int  fftSizeValue() const;
    void setFftSizeValue(int n);

    void setMaxHoldEnabled(bool on);

    // Waterfall controls (originally the standalone wfBar).
    void setScrollSpeed(int linesPerFrame);
    void setPalette(int p);

    // Multi-VFO band boxes.
    void setVfoMarkers(const QVector<mbdsdr::dsp::VfoMarker>& markers);

signals:
    void fftSizeRequested(int n);
    void windowTypeRequested(int w);
    void averageModeRequested(int a);
    void frequencyChanged(double newFreqHz);
    void bandwidthChanged(double newBandwidthHz);
    void visibleRangeChanged(double fLoHz, double fHiHz);
    void viewChanged();

    // Multi-VFO interaction.
    void vfoMarkerSelected(int id);
    void vfoMarkerCenterTuned(int id, double freqHz);
    void vfoMarkerBandwidthChanged(int id, double bwHz);

private:
    SpectrumDisplay* canvas_ = nullptr;

    QComboBox* fftCombo_   = nullptr;
    QSpinBox*  dbMinSpin_  = nullptr;
    QSpinBox*  dbMaxSpin_  = nullptr;
    QSpinBox*  peakThreshSpin_ = nullptr;
    QComboBox* scrollCombo_ = nullptr;
    QComboBox* paletteCombo_ = nullptr;
    QLabel*    testLabel_  = nullptr;
    QLabel*    infoLabel_   = nullptr;
    QTableWidget* peakTable_ = nullptr;

    // Rebuild the peak table from the canvas's matured, tracked peak list.
    void rebuildPeakTable(const QList<mbdsdr::dsp::PeakInfo>& peaks,
                          const QList<int>& ids);
};

} // namespace ui
} // namespace mbdsdr
