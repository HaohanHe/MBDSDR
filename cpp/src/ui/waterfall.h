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

    // Time axis: derive elapsed seconds from the real frame count x smoothed
    // frame period (no hard-coded clock).
    QElapsedTimer frameClock_;
    int    frameCount_ = 0;
    qint64 lastElapsedMs_ = 0;
    double frameIntervalMs_ = 0.0;   // exponential moving average of period
};

} // namespace ui
} // namespace mbdsdr
