// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <QWidget>
#include <QImage>
#include <QVector>
#include <QElapsedTimer>
#include "core/spectrum_frame.h"

namespace mbdsdr {
namespace ui {

// Scrolling time-frequency display. Fed by the SAME SpectrumFrame the line
// spectrum consumes (no extra FFT). Newest row is at the top.
class WaterfallWidget : public QWidget {
    Q_OBJECT
public:
    explicit WaterfallWidget(QWidget* parent = nullptr);

public slots:
    void setSpectrum(const SpectrumFrame& frame);
    /// Crop the display to a visible frequency window (zoomed/panned spectrum).
    /// Pass fLo>fHi or call with hasVisRange=false to revert to full span.
    void setVisibleRange(double fLoHz, double fHiHz);
    /// Scroll speed: write a new row every N frames (1/2/4).
    void setScrollSpeed(int linesPerFrame);
    /// Palette: 0 = classic color, 1 = monochrome blue-scale.
    void setPalette(int p);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    void rebuildImage(int bins);
    QRgb colorForDb(float db) const;
    void buildLut();

    QImage history_;           // bins x kDepthRows, RGB32, newest row at y=0
    int bins_ = 0;
    bool haveFrame_ = false;
    QVector<QRgb> lut_;        // dB -> color lookup table
    int scrollEvery_ = 1;       // write a row every N frames
    int frameMod_ = 0;
    int palette_ = 0;           // 0 classic, 1 mono

    // Current visible frequency window (from the spectrum widget). When set,
    // only the columns whose bin frequency falls in [visLo_, visHi_] are drawn,
    // stretched to the plot width. !hasVisRange_ => draw full span (legacy).
    double visLo_ = 0.0, visHi_ = 0.0;
    bool hasVisRange_ = false;
    double frameF0_ = 0.0;     // center freq of the latest frame (for bin map)
    double frameFs_ = 0.0;     // sample rate of the latest frame

    // Time axis: derive elapsed seconds from the real frame count x smoothed
    // frame period (no hard-coded clock).
    QElapsedTimer frameClock_;
    int    frameCount_ = 0;
    qint64 lastElapsedMs_ = 0;
    double frameIntervalMs_ = 0.0;   // exponential moving average of period
};

} // namespace ui
} // namespace mbdsdr
