// SPDX-License-Identifier: MIT
#include "s_meter.h"
#include "core/tokens.h"

#include <QPainter>
#include <cmath>

namespace mbdsdr {
namespace ui {

SMeterWidget::SMeterWidget(QWidget* parent) : QWidget(parent) {
    setMinimumHeight(tokens::scaled(tokens::kSMeterH));
}

int SMeterWidget::sUnitsAboveNoise(double signalDbfs, double noiseFloorDbfs) {
    if (!std::isfinite(signalDbfs) || !std::isfinite(noiseFloorDbfs)) return -1;
    const double above = (signalDbfs - noiseFloorDbfs) / tokens::kSMeterDbPerUnit;
    int u = int(std::lround(above));
    if (u < 0) u = 0;
    if (u > tokens::kSMeterMaxUnits) u = tokens::kSMeterMaxUnits;
    return u;
}

SMeterWidget::SLabelPlacement SMeterWidget::labelPlacement(
        int index, int units, double trackLeft, double trackRight,
        double rowTop, double rowH) {
    const double w = trackRight - trackLeft;
    const double cellW = (units > 0) ? w / units : w;
    // S0 (left rail): flush-left so the glyph starts INSIDE the track edge.
    if (index <= 0)
        return {QRectF(trackLeft, rowTop, w, rowH),
                Qt::AlignLeft | Qt::AlignVCenter};
    // S9 (right rail): flush-right so the glyph ends INSIDE the track edge.
    if (index >= units)
        return {QRectF(trackLeft, rowTop, w, rowH),
                Qt::AlignRight | Qt::AlignVCenter};
    // Interior label: centered on its cell.
    const double x = trackLeft + cellW * index;
    return {QRectF(x - cellW / 2.0, rowTop, cellW, rowH), Qt::AlignCenter};
}

void SMeterWidget::setSignalDbfs(double dbfs) {
    signalDbfs_ = dbfs;
    if (!std::isfinite(dbfs)) { peakDbfs_ = qQNaN(); shownUnits_ = -1; update(); return; }
    // Peak hold: refresh on stronger, else left to tickDecay.
    if (!std::isfinite(peakDbfs_) || dbfs >= peakDbfs_) peakDbfs_ = dbfs;
    shownUnits_ = sUnitsAboveNoise(dbfs, noiseDbfs_);
    update();
}

void SMeterWidget::setNoiseFloorDbfs(double dbfs) {
    noiseDbfs_ = dbfs;
    shownUnits_ = sUnitsAboveNoise(signalDbfs_, noiseDbfs_);
    update();
}

void SMeterWidget::tickDecay(double dtSec) {
    if (!std::isfinite(peakDbfs_) || !std::isfinite(signalDbfs_)) return;
    const double decayed = peakDbfs_ - tokens::kSMeterPeakDecayDbPerSec * dtSec;
    peakDbfs_ = std::max(signalDbfs_, decayed);   // never below current signal
    update();
}

    QSize SMeterWidget::sizeHint() const {
    return QSize(tokens::scaled(tokens::kSMeterW), tokens::scaled(tokens::kSMeterH));
}

void SMeterWidget::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);
    const QRectF r = rect().adjusted(2, 2, -2, -2);
    const int units = tokens::kSMeterMaxUnits;

    // Track.
    p.setPen(QPen(tokens::cardEdge(), 1.0));
    p.setBrush(tokens::card1());
    p.drawRoundedRect(r, tokens::scaled(4), tokens::scaled(4));

    if (shownUnits_ < 0) {
        // Honest empty state: no device / no frame.
        p.setPen(tokens::rgbaA(tokens::kTextAlphaQuaternary));
        QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
        p.drawText(r, Qt::AlignCenter, QStringLiteral("S-meter  无设备"));
        return;
    }

    // Filled portion (0..shownUnits).
    const double uW = r.width() / units;
    QRectF fill(r.left(), r.top(), uW * shownUnits_, r.height());
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(tokens::kAccent));
    p.drawRoundedRect(fill, tokens::scaled(4), tokens::scaled(4));

    // S0..S9 tick labels. Elastic thinning (Phase42): the status bar can squeeze
    // the meter narrow enough that one cell barely fits a 2-char label, so the
    // glyphs would otherwise run together as "S1S2S3...". We measure the widest
    // label ("S9") and stride the LABELS so adjacent drawn labels keep at least
    // kSMeterTickLabelGap of breathing room: a wide meter draws every unit, a
    // narrow meter strides (S0 S2 S4 ... S9). The hairline tick marks stay dense.
    p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
    QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
    const QFontMetrics fm(f);
    const double labelW = fm.horizontalAdvance(QStringLiteral("S9"));  // widest label
    const double minGap = tokens::scaled(tokens::kSMeterTickLabelGap);
    const int stride = std::max(1,
        static_cast<int>(std::ceil((labelW + minGap) / uW)));
    for (int i = 0; i <= units; ++i) {
        const double x = r.left() + uW * i;
        p.setPen(QPen(tokens::rgbaA(tokens::kTickLabelAlpha), 1.0));
        p.drawLine(QPointF(x, r.bottom()), QPointF(x, r.bottom() - tokens::scaled(4)));
        const bool edgeCap = (i == 0 || i == units);   // S0/S9 always drawn
        // Interior label: keep it on the stride AND at least `stride` cells away
        // from the S9 end cap, else it would sit adjacent to the end label and
        // crowd it (the end cap is drawn regardless of the stride).
        if (!edgeCap && ((i % stride) != 0 || (units - i) < stride)) continue;
        p.setPen(tokens::rgbaA(tokens::kTextAlphaTertiary));
        const QString lab = QStringLiteral("S%1").arg(i);
        const auto lp = labelPlacement(i, units, r.left(), r.right(),
                                       r.top(), tokens::scaled(12));
        p.drawText(lp.rect, lp.align, lab);
    }
    // Peak-hold marker (slow decay).
    if (std::isfinite(peakDbfs_)) {
        const int pu = sUnitsAboveNoise(peakDbfs_, noiseDbfs_);
        const double px = r.left() + uW * pu;
        p.setPen(QPen(QColor(tokens::kWarning), 1.5));
        p.drawLine(QPointF(px, r.top()), QPointF(px, r.bottom()));
    }
}

} // namespace ui
} // namespace mbdsdr
