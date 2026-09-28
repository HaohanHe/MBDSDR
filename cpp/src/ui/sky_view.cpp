// SPDX-License-Identifier: MIT
#include "sky_view.h"
#include "core/tokens.h"

#include <QPainter>
#include <QRadialGradient>
#include <QFontMetrics>
#include <cmath>

namespace mbdsdr {
namespace ui {

SkyView::SkyView(QWidget* parent) : QWidget(parent) {
    setMinimumSize(200, 200);
}

void SkyView::setPasses(QList<PassArc> passes) {
    passes_ = std::move(passes);
    if (highlighted_ >= passes_.size()) highlighted_ = -1;
    update();
}

void SkyView::setHighlightedPass(int index) {
    highlighted_ = index;
    update();
}

void SkyView::setEmptyText(const QString& text) {
    emptyText_ = text;
    update();
}

void SkyView::setLiveSatellites(QList<LiveSat> sats) {
    liveSats_ = std::move(sats);
    update();
}

void SkyView::clearLiveSatellites() {
    liveSats_.clear();
    update();
}

static QPointF polarToCart(double azDeg, double elDeg, double radius, QPointF center) {
    double az = azDeg * M_PI / 180.0;
    double r = radius * (1.0 - elDeg / 90.0);
    return center + QPointF(r * sin(az), -r * cos(az));
}

void SkyView::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);

    QPointF center = rect().center();
    double radius = qMin(width(), height()) / 2.0 - 20;

    p.fillRect(rect(), QColor(tokens::kCard1));

    QPen gridPen(QColor(tokens::kCardEdge));
    gridPen.setWidthF(1.0);
    p.setPen(gridPen);
    for (int el = 0; el <= 90; el += 30) {
        double r = radius * (1.0 - el / 90.0);
        p.drawEllipse(center, r, r);
    }

    for (int az = 0; az < 360; az += 45) {
        double a = az * M_PI / 180.0;
        QPointF edge = center + QPointF(radius * sin(a), -radius * cos(a));
        p.drawLine(center, edge);
    }

    QFont f = font();
    f.setPointSize(tokens::kFontAuxPt);
    p.setFont(f);
    p.setPen(QPen(QColor(tokens::kTextSecondary), 1));
    const char* dirs[] = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"};
    for (int i = 0; i < 8; ++i) {
        double az = i * 45.0;
        double a = az * M_PI / 180.0;
        QPointF pos = center + QPointF((radius + 12) * sin(a), -(radius + 12) * cos(a));
        p.drawText(QRectF(pos.x()-15, pos.y()-10, 30, 20), Qt::AlignCenter, dirs[i]);
    }
    p.setPen(QPen(QColor(tokens::kTextSecondary), 2));
    p.drawPoint(center);

    // Draw each pass as a polyline through its sampled (az,el) track.
    for (int i = 0; i < passes_.size(); ++i) {
        const PassArc& pass = passes_[i];
        const bool hi = (i == highlighted_);
        QPen arcPen(hi ? QColor(tokens::kSuccess) : QColor(tokens::kAccent),
                    hi ? 3.0 : 1.5);
        p.setPen(arcPen);

        QPolygonF poly;
        for (const auto& pt : pass.track) {
            poly << polarToCart(pt.first, pt.second, radius, center);
        }
        p.drawPolyline(poly);

        // Start / end markers.
        if (!pass.track.isEmpty()) {
            p.setBrush(QColor(tokens::kAccent));
            QPointF s = polarToCart(pass.track.first().first,
                                   pass.track.first().second, radius, center);
            QPointF e = polarToCart(pass.track.last().first,
                                    pass.track.last().second, radius, center);
            p.drawEllipse(s, 3, 3);
            p.drawEllipse(e, 3, 3);
            // Label near the max-elevation point (highest el in track).
            int best = 0;
            for (int k = 1; k < pass.track.size(); ++k)
                if (pass.track[k].second > pass.track[best].second) best = k;
            QPointF m = polarToCart(pass.track[best].first,
                                    pass.track[best].second, radius, center);
            p.setPen(hi ? QPen(QColor(tokens::kSuccess))
                        : QPen(QColor(tokens::kTextWhite)));
            p.drawText(m + QPointF(8, -8), pass.name);
        }
    }

    // Live satellite positions. The selected sat always gets an accent glow +
    // name/az-el label. Other sats are small tertiary dots; their name labels
    // are only drawn when they do not collide with an already-placed label, so
    // several satellites in view never produce overlapping text.
    QFont small = font();
    small.setPointSize(tokens::kFontAuxPt);
    p.setFont(small);
    QFontMetrics fm(small);
    const qreal gap = tokens::scaled(4);
    QList<QRectF> occupied;

    // Draw the selected satellite's label first so it always wins space.
    for (const auto& ls : liveSats_) {
        if (!ls.selected || ls.el < 0.0) continue;
        QPointF lp = polarToCart(ls.az, ls.el, radius, center);
        double r = tokens::scaled(5);
        QRadialGradient glow(lp, r * 3.0);
        glow.setColorAt(0.0, QColor(tokens::kAccent));
        glow.setColorAt(1.0, QColor(tokens::kAccent).lighter());
        p.setBrush(QBrush(glow));
        p.setPen(Qt::NoPen);
        p.drawEllipse(lp, r * 1.6, r * 1.6);
        p.setBrush(QColor(tokens::kAccent));
        p.drawEllipse(lp, r, r);
        p.setPen(QPen(QColor(tokens::kAccent)));
        const QPointF namePos = lp + QPointF(r + gap, -r - 2);
        const QString ae = QString("az=%1° el=%2°")
                               .arg(ls.az, 0, 'f', 0).arg(ls.el, 0, 'f', 1);
        p.drawText(namePos, ls.name);
        p.drawText(lp + QPointF(r + gap, -r + 12), ae);
        occupied << QRectF(namePos, QSizeF(fm.horizontalAdvance(ls.name), fm.height()));
    }

    // Remaining sats: dot always, label only if it fits.
    for (const auto& ls : liveSats_) {
        if (ls.selected || ls.el < 0.0) continue;
        QPointF lp = polarToCart(ls.az, ls.el, radius, center);
        p.setBrush(QColor(tokens::kTextAlphaTertiary));
        p.setPen(Qt::NoPen);
        p.drawEllipse(lp, tokens::scaled(2.5), tokens::scaled(2.5));

        const QPointF namePos = lp + QPointF(gap, -gap);
        QRectF rect(namePos, QSizeF(fm.horizontalAdvance(ls.name), fm.height()));
        bool collision = false;
        for (const QRectF& o : occupied)
            if (rect.adjusted(-gap, -gap, gap, gap).intersects(o)) { collision = true; break; }
        if (!collision) {
            p.setPen(QPen(QColor(tokens::kTextAlphaTertiary)));
            p.drawText(namePos, ls.name);
            occupied << rect;
        }
    }

    // Empty-state caption is now a QLabel in the page layout (see MainWindow),
    // so it never paints over the polar compass / azimuth labels.
}

} // namespace ui
} // namespace mbdsdr
