// SPDX-License-Identifier: MIT
#include "sky_view.h"
#include "core/tokens.h"

#include <QPainter>
#include <cmath>

namespace mbdsdr {
namespace ui {

SkyView::SkyView(QWidget* parent) : QWidget(parent) {
    setMinimumSize(200, 200);
}

void SkyView::setPasses(QList<PassArc> passes) {
    passes_ = std::move(passes);
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

    p.setPen(QColor(tokens::kAccent));
    QFont f = font();
    f.setPointSize(tokens::kFontAuxPt);
    p.setFont(f);
    const char* dirs[] = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"};
    for (int i = 0; i < 8; ++i) {
        double az = i * 45.0;
        double a = az * M_PI / 180.0;
        QPointF pos = center + QPointF((radius + 12) * sin(a), -(radius + 12) * cos(a));
        p.drawText(QRectF(pos.x()-15, pos.y()-10, 30, 20), Qt::AlignCenter, dirs[i]);
    }

    p.setPen(QPen(QColor(tokens::kAccent), 2));
    p.drawPoint(center);

    QPen arcPen(QColor(tokens::kAccent), 2);
    p.setPen(arcPen);
    for (const auto& pass : passes_) {
        QPointF p0 = polarToCart(pass.azStart, pass.elStart, radius, center);
        QPointF p1 = polarToCart(pass.azMax, pass.elMax, radius, center);
        QPointF p2 = polarToCart(pass.azEnd, pass.elEnd, radius, center);
        p.drawLine(p0, p1);
        p.drawLine(p1, p2);
        p.setBrush(QColor(tokens::kAccent));
        p.drawEllipse(p0, 3, 3);
        p.drawEllipse(p2, 3, 3);
        p.drawText(p1 + QPointF(8, -8), pass.name);
        p.setPen(arcPen);
    }

    if (passes_.isEmpty()) {
        p.setPen(QColor(tokens::kTextWhite));
        p.drawText(rect(), Qt::AlignCenter, "无过境数据");
    }
}

} // namespace ui
} // namespace mbdsdr
