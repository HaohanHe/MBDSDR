// SPDX-License-Identifier: MIT
#include "sky_view.h"
#include "core/tokens.h"

#include <QPainter>
#include <QRadialGradient>
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

void SkyView::setLiveSatellite(double az, double el, const QString& name) {
    liveValid_ = true;
    liveAz_ = az;
    liveEl_ = el;
    liveName_ = name;
    update();
}

void SkyView::clearLiveSatellite() {
    liveValid_ = false;
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
    p.setPen(QPen(QColor(tokens::kAccent), 1));
    const char* dirs[] = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"};
    for (int i = 0; i < 8; ++i) {
        double az = i * 45.0;
        double a = az * M_PI / 180.0;
        QPointF pos = center + QPointF((radius + 12) * sin(a), -(radius + 12) * cos(a));
        p.drawText(QRectF(pos.x()-15, pos.y()-10, 30, 20), Qt::AlignCenter, dirs[i]);
    }
    p.setPen(QPen(QColor(tokens::kAccent), 2));
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

    // Live satellite position: glowing green dot + label, drawn on top.
    if (liveValid_ && liveEl_ >= 0.0) {
        QPointF lp = polarToCart(liveAz_, liveEl_, radius, center);
        double r = tokens::scaled(5);
        QRadialGradient glow(lp, r * 3.0);
        glow.setColorAt(0.0, QColor(tokens::kSuccess));
        glow.setColorAt(1.0, QColor(tokens::kSuccess).lighter());
        p.setBrush(QBrush(glow));
        p.setPen(Qt::NoPen);
        p.drawEllipse(lp, r * 1.6, r * 1.6);
        p.setBrush(QColor(tokens::kSuccess));
        p.drawEllipse(lp, r, r);
        p.setPen(QPen(QColor(tokens::kSuccess)));
        QFont small = font();
        small.setPointSize(tokens::kFontAuxPt);
        p.setFont(small);
        p.drawText(lp + QPointF(r + 4, -r - 2), liveName_);
        p.drawText(lp + QPointF(r + 4, -r + 12),
                   QString("az=%1° el=%2°")
                       .arg(liveAz_, 0, 'f', 0).arg(liveEl_, 0, 'f', 1));
    }

    if (passes_.isEmpty()) {
        p.setPen(QColor(tokens::kTextWhite));
        p.drawText(rect(), Qt::AlignCenter, emptyText_);
    }
}

} // namespace ui
} // namespace mbdsdr
