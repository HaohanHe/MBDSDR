// SPDX-License-Identifier: MIT
#include "s_meter.h"
#include "core/tokens.h"

#include <QPainter>
#include <cmath>

namespace mbdsdr {
namespace ui {

SMeterWidget::SMeterWidget(QWidget* parent) : QWidget(parent) {
    setMinimumHeight(tokens::scaled(34));
}

int SMeterWidget::sUnitsAboveNoise(double signalDbfs, double noiseFloorDbfs) {
    if (!std::isfinite(signalDbfs) || !std::isfinite(noiseFloorDbfs)) return -1;
    const double above = (signalDbfs - noiseFloorDbfs) / tokens::kSMeterDbPerUnit;
    int u = int(std::lround(above));
    if (u < 0) u = 0;
    if (u > tokens::kSMeterMaxUnits) u = tokens::kSMeterMaxUnits;
    return u;
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
    return QSize(tokens::scaled(220), tokens::scaled(34));
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

    // S0..S9 tick labels.
    p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
    QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
    const QFontMetrics fm(f);
    for (int i = 0; i <= units; ++i) {
        const double x = r.left() + uW * i;
        p.setPen(QPen(tokens::rgbaA(tokens::kTickLabelAlpha), 1.0));
        p.drawLine(QPointF(x, r.bottom()), QPointF(x, r.bottom() - tokens::scaled(4)));
        p.setPen(tokens::rgbaA(tokens::kTextAlphaTertiary));
        const QString lab = (i == units) ? QStringLiteral("S9") : QStringLiteral("S%1").arg(i);
        p.drawText(QRectF(x - uW / 2, r.top(), uW, tokens::scaled(12)),
                   Qt::AlignCenter, lab);
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
