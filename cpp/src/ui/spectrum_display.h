// SPDX-License-Identifier: GPL-3.0-or-later
//
// Unified spectrum + frequency strip + waterfall canvas.
//
// Geometry ported from SDR++ (https://github.com/AlexandreRouma/SDRPlusPlus,
// Copyright (C) Alexandre Rouma / Ryzerth, GPL-3.0-or-later) ImGui::WaterFall
// and re-expressed with QPainter + DPI-scaled design tokens. The line spectrum,
// the shared tick/label strip and the scrolling waterfall all start at the same
// left x and share one width, so the frequency axes align by construction
// (not by coincidence):
//
//   top inset
//   |-- spectrum trace  (spectrumRect)
//   gap
//   |-- shared frequency strip (freqStripRect, ticks point up into the trace)
//   == draggable divider ==
//   |-- waterfall        (waterfallRect)
//   bottom inset
//
// A single SpectrumFrame (from dsp::SpectrumEngine::spectrumReady) drives both
// the trace and the waterfall history -- no synthetic data is ever invented.
#pragma once

#include <QWidget>
#include <QImage>
#include <QVector>
#include <QElapsedTimer>
#include <QRect>
#include <vector>

#include "core/spectrum_frame.h"
#include "dsp/peak_detector.h"
#include "dsp/vfo_manager.h"

namespace mbdsdr {
namespace ui {

class SpectrumDisplay : public QWidget {
    Q_OBJECT
public:
    explicit SpectrumDisplay(QWidget* parent = nullptr);

    // ---- Geometry accessors (for tests / layout; recomputed in resizeEvent) --
    void recomputeGeometry();
    QRect spectrumRect()  const { return g_.spectrum; }
    QRect freqStripRect() const { return g_.freqStrip; }
    QRect waterfallRect() const { return g_.waterfall; }
    QRect dividerHitRect() const { return g_.dividerHit; }
    int   dividerY()      const { return g_.dividerY; }

    // Visible frequency window (zoom/pan state).
    double visLoHz() const;
    double visHiHz() const;
    double zoomFactor() const { return zoomFactor_; }

    // ---- Waterfall read-out (tests) ----
    const QImage& history() const { return history_; }
    bool hasFrame() const { return haveFrame_; }

public slots:
    // Drive both the trace and the waterfall from the SAME real frame.
    void setSpectrum(const SpectrumFrame& frame);
    void setDbRange(float minDb, float maxDb);
    void setZoomFactor(double z);
    void resetZoom();
    void setBandwidthHz(double hz) { bwHz_ = hz; update(); }
    double bandwidthHz() const { return bwHz_; }
    void setStepHz(double hz) { stepHz_ = hz; }
    void setMaxHoldEnabled(bool on);
    void clearMaxHold() { maxHold_.clear(); update(); }

    // Waterfall controls.
    void setScrollSpeed(int linesPerFrame);   // write a row every N frames (1/2/4)
    void setPalette(int p);                   // 0 classic, 1 monochrome

    // Peak handling driven by the container's peak table.
    void setHighlightedPeak(int row);         // row index into the matured list
    void setPeakThresholdDb(float db) { peakThresholdDb_ = db; detectPeaks(); update(); }
    void tuneAndCenter(double hz);

    // Multi-VFO: replace the on-screen band boxes. When non-empty, these markers
    // replace the legacy single-VFO box; the legacy single-box path is kept for
    // back-compat tests.
    void setVfoMarkers(const QVector<mbdsdr::dsp::VfoMarker>& markers);

    // Band-box pixel geometry for a marker (edge-aligned for SSB). Public so
    // offscreen tests can assert USB/LSB side placement without pixel-peeping.
    // On return, bx0 <= bx1; vx is the dial/tuning line x.
    void vfoBoxGeometryFor(const mbdsdr::dsp::VfoMarker& m,
                           int& bx0, int& bx1, int& vx) const;

signals:
    void frequencyChanged(double newFreqHz);
    void bandwidthChanged(double newBandwidthHz);
    void visibleRangeChanged(double fLoHz, double fHiHz);
    /// Matured, tracked peak list changed (throttled to rounded-freq signature).
    void peaksUpdated(QList<mbdsdr::dsp::PeakInfo> peaks, QList<int> ids);
    /// Emitted when a view-changing control settles so the container can persist.
    void viewChanged();

    // Multi-VFO interaction.
    void vfoMarkerSelected(int id);
    void vfoMarkerCenterTuned(int id, double freqHz);
    void vfoMarkerBandwidthChanged(int id, double bwHz);

protected:
    void resizeEvent(QResizeEvent* event) override;
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void mouseDoubleClickEvent(QMouseEvent* event) override;
public:
    void wheelEvent(QWheelEvent* event) override;  // public for offscreen tests
protected:
    void leaveEvent(QEvent* event) override;

private:
    // The three painted panels + divider, all derived from one geometry call.
    struct Geometry {
        int x0 = 0, x1 = 0, dataWidth = 0;
        int contentTop = 0, contentBottom = 0;
        int freqH = 0, gap = 0;
        int panelsH = 0;
        int traceH = 0, wfH = 0;
        QRect spectrum;      // line trace
        QRect freqStrip;     // shared tick / label strip
        int dividerY = 0;    // hairline y
        QRect dividerHit;    // generous hit band around the divider
        QRect waterfall;     // scrolling spectrogram
    } g_;

    // Frequency -> pixel x, shared by the trace, the strip ticks and the
    // waterfall column crop. fLo/fHi/span come from zoom/pan state.
    void visibleRange(double& fLo, double& fHi, double& spanVis) const;
    int  xOfFreq(double f, double fLo, double spanVis) const {
        return g_.x0 + static_cast<int>(g_.dataWidth * (f - fLo) / spanVis);
    }
    void emitVisibleRange();

    // Peak detection + cross-frame tracking (moved from the old container).
    void detectPeaks();

    // ---- Waterfall history internals (ported from the old WaterfallWidget) --
    void rebuildImage(int bins);
    QRgb colorForDb(float db) const;
    void buildLut();

    SpectrumFrame frame_;
    bool haveFrame_ = false;

    // Drag / interaction state.
    bool dragging_ = false;
    bool panning_ = false;
    bool dividerDragging_ = false;
    bool dividerHover_ = false;
    enum class DragMode { None, Tune, Pan, BandL, BandR };
    DragMode dragMode_ = DragMode::None;
    QPoint hoverPos_;
    QPoint lastPanPos_;
    // Hover tooltip cache so setToolTip() is only re-called when the read-out
    // actually changes (avoids churning the tooltip system on every mouse move).
    int     toolTipVfoId_ = -1;
    QString toolTipText_;

    double bwHz_ = 12500.0;
    double stepHz_ = 1000.0;
    double vfoFreq_ = 0.0;

    // Multi-VFO band boxes. When non-empty these supersede the legacy single box.
    QVector<mbdsdr::dsp::VfoMarker> markers_;
    int dragVfoId_ = -1;   // marker currently being dragged, -1 = legacy/root
    // Hit-test a marker at pixel x (data area) -> index into markers_, or -1.
    int hitVfoMarker(double x, double fLo, double spanVis) const;

    // Band-box pixel geometry for a marker, accounting for sideband alignment.
    // USB: box [dial, dial+bw] with the dial/tuning line at the LEFT edge;
    // LSB/CW: box [dial-bw, dial] with the dial/tuning line at the RIGHT edge;
    // symmetric modes (AM/NFM/WFM/BPSK/QPSK): [dial-bw/2, dial+bw/2] centered.
    // Exposed for offscreen geometry tests.
    void vfoBoxGeometry(const mbdsdr::dsp::VfoMarker& m, double fLo, double spanVis,
                        int& bx0, int& bx1, int& vx) const;

    float  dbMin_ = -100.0f;
    float  dbMax_ = 0.0f;
    double zoomFactor_ = 1.0;
    double viewCenterHz_ = 0.0;

    // Divider position: share of (trace + waterfall) height given to the trace.
    double fraction_ = 0.5;

    // Peak tracking.
    QList<mbdsdr::dsp::PeakInfo> peaks_;
    QList<int> peakIds_;
    float peakThresholdDb_ = 15.0f;
    int   highlightedPeak_ = -1;
    QString lastPeakSignature_;
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

    // Max-hold envelope.
    std::vector<float> maxHold_;
    bool maxHoldEnabled_ = false;

    // ---- Waterfall history (ported) ----
    QImage history_;
    int bins_ = 0;
    QVector<QRgb> lut_;
    int scrollEvery_ = 1;
    int frameMod_ = 0;
    int palette_ = 0;
    double frameF0_ = 0.0;
    double frameFs_ = 0.0;
    QElapsedTimer frameClock_;
    int    frameCount_ = 0;
    qint64 lastElapsedMs_ = 0;
    double frameIntervalMs_ = 0.0;
};

} // namespace ui
} // namespace mbdsdr
