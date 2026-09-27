// SPDX-License-Identifier: MIT
#include "world_view.h"
#include "core/tokens.h"

#include <QPainter>
#include <cmath>

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

    // Simplified landmasses (schematic, non-overlapping rectangles)
    auto drawLand = [&](double latN, double lonW, double latS, double lonE) {
        QRectF r(latLonToPx(latN, lonW), latLonToPx(latS, lonE));
        p.drawRect(r.normalized());
    };
    p.setBrush(QColor(tokens::kCard2));
    p.setPen(Qt::NoPen);
    drawLand(70, -170, 15, -60);    // North America
    drawLand(15, -85, -55, -35);    // South America
    drawLand(70, -10, 35, 40);      // Europe
    drawLand(35, -20, -35, 50);     // Africa
    drawLand(70, 40, 10, 180);      // Asia
    drawLand(-10, 110, -40, 155);   // Australia

    const bool hasStation = !std::isnan(stationLat_) && !std::isnan(stationLon_);
    if (hasStation) {
        QPointF station = latLonToPx(stationLat_, stationLon_);
        p.setPen(QPen(QColor(tokens::kSuccess), 2));
        p.setBrush(QColor(tokens::kSuccess));
        p.drawEllipse(station, 5, 5);
        p.drawText(station + QPointF(8, -8), "本站");
    }

    // Aircraft
    p.setPen(QPen(QColor(tokens::kAccent), 2));
    p.setBrush(QColor(tokens::kAccent));
    for (const auto& ac : aircraft_) {
        QPointF pos = latLonToPx(ac.lat, ac.lon);
        p.drawEllipse(pos, 3, 3);
        p.drawText(pos + QPointF(6, 6), ac.callsign);
    }

    // At most ONE centered hint, so labels can never overlap into garble.
    if (!hasStation) {
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        p.drawText(rect(), Qt::AlignCenter,
                   "未设置本站位置（请在“设置”中配置经纬度）");
    } else if (aircraft_.isEmpty()) {
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaFaint)));
        p.drawText(QRectF(0, height() - 34, width(), 24),
                   Qt::AlignCenter, "等待 ADS-B 位置数据");
    }
}

} // namespace ui
} // namespace mbdsdr
