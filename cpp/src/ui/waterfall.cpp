// SPDX-License-Identifier: GPL-3.0-or-later
#include "waterfall.h"

#include <QPainter>
#include <cstring>
#include <cmath>
#include "core/tokens.h"

namespace mbdsdr {
namespace ui {

namespace {
// Internal buffer resolution, NOT a UI size: the image is scaled to whatever
// the widget currently measures. The time window it holds = rows * frame period.
constexpr int kDepthRows = 512;
constexpr float kDbMin = -100.0f;
constexpr float kDbMax = 0.0f;

struct Stop { float t; int r, g, b; };
// black -> deep blue -> blue -> cyan -> green -> yellow -> orange -> red
const Stop kStops[] = {
    {0.00f,   0,   0,   0},
    {0.12f,   0,   0,  90},
    {0.25f,   0,  40, 200},
    {0.42f,   0, 200, 235},
    {0.58f,  40, 220,  90},
    {0.75f, 255, 235,  60},
    {0.88f, 255, 120,   0},
    {1.00f, 255,  40,  30},
};
} // namespace

WaterfallWidget::WaterfallWidget(QWidget* parent)
    : QWidget(parent)
{
    setAutoFillBackground(true);
    setMinimumHeight(tokens::scaled(48));
    buildLut();
}

void WaterfallWidget::buildLut() {
    lut_.resize(256);
    const int nStops = static_cast<int>(sizeof(kStops) / sizeof(kStops[0]));
    for (int i = 0; i < 256; ++i) {
        const float t = i / 255.0f;
        int s = 0;
        while (s < nStops - 2 && kStops[s + 1].t < t) ++s;
        const Stop& a = kStops[s];
        const Stop& b = kStops[s + 1];
        const float f = (t - a.t) / (b.t - a.t);
        const int r = static_cast<int>(a.r + (b.r - a.r) * f);
        const int g = static_cast<int>(a.g + (b.g - a.g) * f);
        const int bl = static_cast<int>(a.b + (b.b - a.b) * f);
        lut_[i] = qRgb(r, g, bl);
    }
}

QRgb WaterfallWidget::colorForDb(float db) const {
    float t = (db - kDbMin) / (kDbMax - kDbMin);
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    return lut_[static_cast<int>(t * 255.0f)];
}

void WaterfallWidget::rebuildImage(int bins) {
    history_ = QImage(bins, kDepthRows, QImage::Format_RGB32);
    history_.fill(qRgb(0, 0, 0));
    bins_ = bins;
}

void WaterfallWidget::setSpectrum(const SpectrumFrame& frame) {
    const int bins = static_cast<int>(frame.dbfs.size());
    if (bins < 2) return;
    if (bins != bins_ || history_.isNull()) rebuildImage(bins);

    // Scroll existing rows down by one (single block move), then write new top row.
    const std::size_t rowBytes = static_cast<std::size_t>(history_.bytesPerLine());
    std::memmove(history_.scanLine(1), history_.constScanLine(0),
                 static_cast<std::size_t>(kDepthRows - 1) * rowBytes);
    QRgb* top = reinterpret_cast<QRgb*>(history_.scanLine(0));
    for (int i = 0; i < bins; ++i) top[i] = colorForDb(frame.dbfs[i]);

    haveFrame_ = true;
    update();
}

void WaterfallWidget::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.fillRect(rect(), QColor(QString::fromUtf8(tokens::kCard1)));

    if (!haveFrame_ || history_.isNull()) {
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaSecondary)));
        p.drawText(rect(), Qt::AlignCenter, QStringLiteral("等待频谱数据"));
        return;
    }

    // Align horizontally with the line spectrum's plot area so frequencies line up;
    // fill the full height. Nearest-neighbour keeps the bands crisp.
    const int mL = tokens::kPlotMarginL;
    const int mR = tokens::kPlotMarginR;
    const int plotW = width() - mL - mR;
    if (plotW <= 10) return;
    p.setRenderHint(QPainter::SmoothPixmapTransform, false);
    p.drawImage(QRectF(mL, 0, plotW, height()), history_);
}

} // namespace ui
} // namespace mbdsdr
