// SPDX-License-Identifier: GPL-3.0-or-later
#include "waterfall.h"

#include <QPainter>
#include <QFont>
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
} // namespace

WaterfallWidget::WaterfallWidget(QWidget* parent)
    : QWidget(parent)
{
    setAutoFillBackground(true);
    setMinimumHeight(tokens::scaled(tokens::kWaterfallMinH));
    buildLut();
}

void WaterfallWidget::setScrollSpeed(int n) {
    scrollEvery_ = (n >= 1 && n <= 4) ? n : 1;
}

void WaterfallWidget::setPalette(int p) {
    palette_ = (p == 1) ? 1 : 0;
    buildLut();
    update();
}

void WaterfallWidget::buildLut() {
    lut_.resize(256);
    // Pre-resolve each hex stop to RGB once.
    struct RgbStop { float t; int r, g, b; };
    const bool mono = (palette_ == 1);
    const auto& stops = mono ? tokens::kWaterfallStopsMono : tokens::kWaterfallStops;
    const int nStops = mono
        ? static_cast<int>(sizeof(tokens::kWaterfallStopsMono)/sizeof(tokens::kWaterfallStopsMono[0]))
        : static_cast<int>(sizeof(tokens::kWaterfallStops)/sizeof(tokens::kWaterfallStops[0]));
    QVector<RgbStop> rgb(nStops);
    for (int i = 0; i < nStops; ++i) {
        QColor c(QString::fromUtf8(stops[i].hex));
        rgb[i] = {stops[i].t, c.red(), c.green(), c.blue()};
    }
    for (int i = 0; i < 256; ++i) {
        const float t = i / 255.0f;
        int s = 0;
        while (s < nStops - 2 && rgb[s + 1].t < t) ++s;
        const RgbStop& a = rgb[s];
        const RgbStop& b = rgb[s + 1];
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

    frameF0_ = frame.centerFreqHz;
    frameFs_ = frame.sampleRateHz;

    // Honor scroll speed: only push a new row every N frames.
    frameMod_ = (frameMod_ + 1) % scrollEvery_;
    if (frameMod_ != 0) { haveFrame_ = true; update(); return; }

    // Scroll existing rows down by one (single block move), then write new top row.
    const std::size_t rowBytes = static_cast<std::size_t>(history_.bytesPerLine());
    std::memmove(history_.scanLine(1), history_.constScanLine(0),
                 static_cast<std::size_t>(kDepthRows - 1) * rowBytes);
    QRgb* top = reinterpret_cast<QRgb*>(history_.scanLine(0));
    for (int i = 0; i < bins; ++i) top[i] = colorForDb(frame.dbfs[i]);

    haveFrame_ = true;

    // Time bookkeeping: the axis is derived from the real frame count and a
    // smoothed per-frame period, never a hard-coded value.
    if (frameCount_ == 0) {
        frameClock_.start();
        lastElapsedMs_ = 0;
        frameIntervalMs_ = 0.0;
    } else {
        const qint64 now = frameClock_.elapsed();
        const double dt = double(now - lastElapsedMs_);
        if (dt > 0.0) {
            frameIntervalMs_ = (frameIntervalMs_ <= 0.0)
                ? dt
                : 0.9 * frameIntervalMs_ + 0.1 * dt;   // light smoothing
        }
        lastElapsedMs_ = now;
    }
    ++frameCount_;

    update();
}

void WaterfallWidget::setVisibleRange(double fLoHz, double fHiHz) {
    if (fHiHz <= fLoHz) {
        hasVisRange_ = false;
    } else {
        visLo_ = fLoHz;
        visHi_ = fHiHz;
        hasVisRange_ = true;
    }
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

    // Align horizontally with the line spectrum's plot area so frequencies line up.
    // Reserve a strip at the bottom for the frequency axis. Nearest-neighbour keeps
    // the bands crisp.
    const int mL = tokens::kPlotMarginL;
    const int mR = tokens::kPlotMarginR;
    const int plotW = width() - mL - mR;
    if (plotW <= 10) return;
    const int bottomPad = tokens::scaled(tokens::kWaterfallBottomPad);
    const int plotH = std::max(1, height() - bottomPad);
    p.setRenderHint(QPainter::SmoothPixmapTransform, false);
    const QRectF dest(mL, 0, plotW, plotH);
    if (hasVisRange_ && bins_ > 1 && frameFs_ > 0.0) {
        // Map the visible frequency window to source columns in history_.
        // Column i corresponds to f = f0 - fs/2 + i*fs/(bins-1).
        const double fLowEdge = frameF0_ - frameFs_ / 2.0;
        double iLoF = (visLo_ - fLowEdge) / frameFs_ * (bins_ - 1);
        double iHiF = (visHi_ - fLowEdge) / frameFs_ * (bins_ - 1);
        int iLo = static_cast<int>(std::floor(iLoF));
        int iHi = static_cast<int>(std::ceil(iHiF));
        iLo = std::max(0, std::min(bins_ - 1, iLo));
        iHi = std::max(0, std::min(bins_ - 1, iHi));
        if (iHi > iLo) {
            p.drawImage(dest, history_, QRectF(iLo, 0, iHi - iLo + 1, kDepthRows));
        }
    } else {
        p.drawImage(dest, history_);
    }

    // Bottom frequency axis: only once a visible range is known (legacy no-range
    // behaviour leaves the strip blank). Ticks/span match the line spectrum.
    if (hasVisRange_) {
        const int n = tokens::kWaterfallFreqTicks;
        const int tickH = tokens::scaled(tokens::kWaterfallTickH);
        const int labelH = tokens::scaled(tokens::kFreqLabelH);
        const int labelW = tokens::scaled(tokens::kFreqLabelW);
        { QFont f = p.font(); f.setPointSize(tokens::kFontAuxPt); p.setFont(f); }
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        for (int i = 0; i < n; ++i) {
            const double frac = double(i) / (n - 1);
            const int x = mL + static_cast<int>(plotW * frac);
            p.drawLine(x, plotH, x, plotH + tickH);
            const double freq = visLo_ + (visHi_ - visLo_) * frac;
            p.drawText(x - labelW / 2, plotH + tickH, labelW, labelH,
                       Qt::AlignCenter,
                       QString("%1M").arg(freq / 1e6, 0, 'f', 1));
        }
    }

    // Time axis in the left margin: top (y=0) is the newest frame (0s),
    // bottom is the oldest (total elapsed). Only once we have >=2 frames and
    // a measured period; otherwise the empty-state text already covers it.
    if (frameCount_ > 1 && frameIntervalMs_ > 0.0) {
        const double totalSec = frameCount_ * frameIntervalMs_ / 1000.0;
        const int labelW = tokens::scaled(tokens::kTimeLabelW);
        const int labelH = tokens::scaled(tokens::kTimeLabelH);
        const int padY = tokens::scaled(tokens::kTimeLabelPadY);
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        auto drawT = [&](int y, const QString& txt) {
            p.drawText(0, y - labelH / 2, labelW, labelH,
                       Qt::AlignHCenter | Qt::AlignVCenter, txt);
        };
        drawT(padY + labelH / 2, QStringLiteral("0s"));
        drawT(plotH - padY - labelH / 2,
              QString("%1s").arg(totalSec, 0, 'f', 1));
        for (int k = 1; k <= tokens::kTimeTickCount; ++k) {
            const double frac = double(k) / (tokens::kTimeTickCount + 1);
            const int y = static_cast<int>(plotH * frac);
            const double age = totalSec * (1.0 - frac);
            drawT(y, QString("%1s").arg(age, 0, 'f', 1));
        }
    }
}

} // namespace ui
} // namespace mbdsdr
