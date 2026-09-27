// SPDX-License-Identifier: MIT
#include "world_view.h"
#include "core/tokens.h"

#include <QPainter>

namespace mbdsdr {
namespace ui {

WorldView::WorldView(QWidget* parent) : QWidget(parent) {
    setMinimumSize(300, 200);
}

void WorldView::setAircraft(QList<AircraftPoint> ac) {
    aircraft_ = std::move(ac);
    update();
}

QPointF WorldView::latLonToPx(double lat, double lon) {
    double x = (lon + 180.0) / 360.0 * width();
    double y = (90.0 - lat) / 180.0 * height();
    return QPointF(x, y);
}

void WorldView::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.fillRect(rect(), QColor(tokens::kCard1));

    // Grid lines
    QPen gridPen(QColor(tokens::kCardEdge));
    gridPen.setWidthF(1);
    p.setPen(gridPen);
    for (int lon = -180; lon <= 180; lon += 30) {
        QPointF top = latLonToPx(90, lon);
        QPointF bot = latLonToPx(-90, lon);
        p.drawLine(top, bot);
    }
    for (int lat = -90; lat <= 90; lat += 30) {
        QPointF left = latLonToPx(lat, -180);
        QPointF right = latLonToPx(lat, 180);
        p.drawLine(left, right);
    }

    // Simplified landmasses (rough rectangles for visual feel)
    p.setBrush(QColor(tokens::kCard2));
    p.setPen(Qt::NoPen);
    // North America
    p.drawRect(latLonToPx(70, -170).x(), latLonToPx(70, -170).y(),
               latLonToPx(15, -60).x() - latLonToPx(70, -170).x(),
               latLonToPx(15, -60).y() - latLonToPx(70, -170).y());
    // Europe
    p.drawRect(latLonToPx(70, -10).x(), latLonToPx(70, -10).y(),
               latLonToPx(35, 40).x() - latLonToPx(70, -10).x(),
               latLonToPx(35, 40).y() - latLonToPx(70, -10).y());
    // Asia
    p.drawRect(latLonToPx(70, 40).x(), latLonToPx(70, 40).y(),
               latLonToPx(10, 180).x() - latLonToPx(70, 40).x(),
               latLonToPx(10, 180).y() - latLonToPx(70, 40).y());
    // Africa
    p.drawRect(latLonToPx(35, -20).x(), latLonToPx(35, -20).y(),
               latLonToPx(-35, 50).x() - latLonToPx(35, -20).x(),
               latLonToPx(-35, 50).y() - latLonToPx(35, -20).y());

    // Station marker (Beijing 116.4E, 39.9N)
    QPointF station = latLonToPx(39.9, 116.4);
    p.setPen(QPen(QColor(tokens::kSuccess), 2));
    p.setBrush(QColor(tokens::kSuccess));
    p.drawEllipse(station, 5, 5);
    p.drawText(station + QPointF(8, -8), "本站");

    // Aircraft
    p.setPen(QPen(QColor(tokens::kAccent), 2));
    p.setBrush(QColor(tokens::kAccent));
    for (const auto& ac : aircraft_) {
        QPointF pos = latLonToPx(ac.lat, ac.lon);
        p.drawEllipse(pos, 3, 3);
        p.drawText(pos + QPointF(6, 6), ac.callsign);
    }

    // Empty state
    if (aircraft_.isEmpty()) {
        p.setPen(QColor(tokens::kAccent));
        p.drawText(rect(), Qt::AlignCenter, "等待 ADS-B 数据");
    }
}

} // namespace ui
} // namespace mbdsdr
