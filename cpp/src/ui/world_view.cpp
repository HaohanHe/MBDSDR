// SPDX-License-Identifier: MIT
#include "world_view.h"
#include "core/tokens.h"
#include "coastline_data.h"

#include <QPainter>
#include <QMouseEvent>
#include <QWheelEvent>
#include <QToolTip>
#include <QRectF>
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace ui {

WorldView::WorldView(QWidget* parent) : QWidget(parent) {
    setMinimumSize(tokens::scaled(300), tokens::scaled(200));
    setMouseTracking(true);
    proj_.setSize(width(), height());
    proj_.setViewState({0.0, 0.0, 1.0});
}

// ---------------------------------------------------------------- data feed
void WorldView::setAircraft(QList<AircraftPoint> ac) {
    aircraft_ = std::move(ac);
    update();
}

void WorldView::addAircraft(const QString& icao, double lat, double lon) {
    for (auto& ac : aircraft_)
        if (ac.icao == icao) { ac.lat = lat; ac.lon = lon; update(); return; }
    AircraftPoint p; p.icao = icao; p.lat = lat; p.lon = lon;
    aircraft_.append(p);
    update();
}

void WorldView::setSatellites(QList<SatellitePoint> sat) {
    satellites_ = std::move(sat);
    update();
}

void WorldView::addSatellite(const QString& name, double lat, double lon) {
    for (auto& s : satellites_)
        if (s.name == name) { s.lat = lat; s.lon = lon; update(); return; }
    SatellitePoint p; p.name = name; p.lat = lat; p.lon = lon;
    satellites_.append(p);
    update();
}

void WorldView::setStation(double lat, double lon) {
    stationLat_ = lat;
    stationLon_ = lon;
    update();
}

void WorldView::setGnssFix(bool valid, double lat, double lon, int sats, double hdop) {
    gnssValid_ = valid;
    if (valid) { gnssLat_ = lat; gnssLon_ = lon; }
    gnssSats_ = sats;
    gnssHdop_ = hdop;
    update();
}

// ------------------------------------------------------------- layer toggles
void WorldView::setLayerVisible(MapLayer id, bool on) {
    layerOn_[static_cast<int>(id)] = on;
    update();
}
bool WorldView::layerVisible(MapLayer id) const {
    return layerOn_[static_cast<int>(id)];
}

void WorldView::resetView() {
    proj_.setViewState({0.0, 0.0, 1.0});
    update();
}

// ------------------------------------------------------------------- events
void WorldView::resizeEvent(QResizeEvent*) {
    proj_.setSize(width(), height());
}

void WorldView::mousePressEvent(QMouseEvent* e) {
    panning_ = true;
    moved_ = false;
    lastMouse_ = e->pos();
    pressPos_ = e->pos();
}

void WorldView::mouseMoveEvent(QMouseEvent* e) {
    if (!panning_) return;
    const double dx = e->position().x() - lastMouse_.x();
    const double dy = e->position().y() - lastMouse_.y();
    if ((e->pos() - pressPos_).manhattanLength() > tokens::scaled(4)) moved_ = true;
    lastMouse_ = e->pos();
    proj_.panByPixels(dx, dy);
    update();
}

void WorldView::mouseReleaseEvent(QMouseEvent* e) {
    panning_ = false;
    if (moved_) return;   // it was a drag, not a click
    Hit h = hitTest(e->position());
    if (h.type == Hit::None) {
        QToolTip::hideText();
        return;
    }
    QToolTip::showText(e->globalPosition().toPoint(), h.tooltip, this);
    if (h.type == Hit::Satellite) {
        setSelectedSatellite(h.id);          // update internal flags (no re-emit)
        emit satelliteSelected(h.id);          // external sync
    }
}

void WorldView::wheelEvent(QWheelEvent* e) {
    const double steps = e->angleDelta().y() / 120.0;
    const double factor = std::pow(1.25, steps);
    proj_.zoomAbout(e->position(), factor);
    update();
}

void WorldView::contextMenuEvent(QContextMenuEvent*) {
    resetView();
}

// ------------------------------------------------------------- selection slot
void WorldView::setSelectedSatellite(const QString& name) {
    for (auto& s : satellites_) s.selected = (s.name == name);
    update();
}

// --------------------------------------------------------------- hit testing
WorldView::Hit WorldView::hitTest(const QPointF& pos) const {
    Hit none;
    const double tol = tokens::scaled(10);
    auto near = [&](const QPointF& a) { return std::hypot(a.x() - pos.x(), a.y() - pos.y()) <= tol; };

    // Satellites first (click-to-select contract), then aircraft, then anchors.
    if (layerVisible(MapLayer::Satellite)) {
        for (const auto& s : satellites_) {
            QPointF p = proj_.project(s.lat, s.lon);
            if (near(p)) {
                Hit h; h.type = Hit::Satellite; h.id = s.name;
                h.tooltip = QString::fromUtf8("卫星 %1\n星下点 %2, %3")
                                .arg(s.name)
                                .arg(s.lat, 0, 'f', 3).arg(s.lon, 0, 'f', 3);
                return h;
            }
        }
    }
    if (layerVisible(MapLayer::Aircraft)) {
        for (const auto& ac : aircraft_) {
            QPointF p = proj_.project(ac.lat, ac.lon);
            if (near(p)) {
                Hit h; h.type = Hit::Aircraft; h.id = ac.icao;
                QString cs = ac.callsign.isEmpty() ? ac.icao : ac.callsign;
                h.tooltip = QString::fromUtf8("%1\n高度 %2 ft · 航向 %3°\n位置 %4, %5")
                                .arg(cs).arg(ac.altitudeFt)
                                .arg(ac.headingDeg, 0, 'f', 0)
                                .arg(ac.lat, 0, 'f', 3).arg(ac.lon, 0, 'f', 3);
                return h;
            }
        }
    }
    if (layerVisible(MapLayer::Gnss) && gnssValid_) {
        QPointF p = proj_.project(gnssLat_, gnssLon_);
        if (near(p)) {
            Hit h; h.type = Hit::Gnss; h.id = QStringLiteral("gnss");
            h.tooltip = QString::fromUtf8("GNSS 定位 %1, %2\n%3 颗星 · HDOP %4")
                            .arg(gnssLat_, 0, 'f', 4).arg(gnssLon_, 0, 'f', 4)
                            .arg(gnssSats_).arg(gnssHdop_, 0, 'f', 1);
            return h;
        }
    }
    if (layerVisible(MapLayer::Station) &&
        !std::isnan(stationLat_) && !std::isnan(stationLon_)) {
        QPointF p = proj_.project(stationLat_, stationLon_);
        if (near(p)) {
            Hit h; h.type = Hit::Station; h.id = QStringLiteral("station");
            h.tooltip = QString::fromUtf8("本站 %1, %2")
                            .arg(stationLat_, 0, 'f', 4).arg(stationLon_, 0, 'f', 4);
            return h;
        }
    }
    return none;
}

// ---------------------------------------------------------------- painting
void WorldView::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.fillRect(rect(), QColor(tokens::kBgMain));

    if (layerVisible(MapLayer::Graticule)) drawGraticule(p);
    if (layerVisible(MapLayer::Coastline)) drawCoastline(p);
    if (layerVisible(MapLayer::Station))   drawStation(p);
    if (layerVisible(MapLayer::Gnss))       drawGnss(p);
    if (layerVisible(MapLayer::Aircraft))   drawAircraft(p);
    if (layerVisible(MapLayer::Satellite))  drawSatellites(p);
    drawStatusChips(p);
}

void WorldView::drawGraticule(QPainter& p) {
    const double step = proj_.suggestGridStep();
    const double left  = proj_.centerLon() - proj_.lonSpan() / 2.0;
    const double right = proj_.centerLon() + proj_.lonSpan() / 2.0;
    const double top   = proj_.centerLat() + proj_.latSpan() / 2.0;
    const double bot   = proj_.centerLat() - proj_.latSpan() / 2.0;

    QPen grid(QColor(tokens::kCardEdge));
    grid.setWidthF(1.0);
    p.setPen(grid);
    p.setFont(QFont(QString::fromUtf8(tokens::kFontFamily), tokens::scaled(8)));

    for (double lon = std::ceil(left / step) * step; lon <= right; lon += step) {
        QLineF l(proj_.project(top, lon), proj_.project(bot, lon));
        p.drawLine(l);
        p.drawText(QPointF(l.x1() + 2, height() - 4),
                   QString::number(std::lround(lon)) + QLatin1String("°"));
    }
    for (double lat = std::ceil(bot / step) * step; lat <= top; lat += step) {
        QLineF l(proj_.project(lat, left), proj_.project(lat, right));
        p.drawLine(l);
        p.drawText(QPointF(2, l.y1() - 2),
                   QString::number(std::lround(lat)) + QLatin1String("°"));
    }
}

void WorldView::drawCoastline(QPainter& p) {
    QPen coast(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
    coast.setWidthF(1.2);
    p.setPen(coast);
    p.setBrush(Qt::NoBrush);
    for (int i = 0; i < kCoastlineLineCount; ++i) {
        const int a = kCoastlineOffsets[i];
        const int b = kCoastlineOffsets[i + 1];
        QPolygonF poly;
        poly.reserve(b - a);
        for (int j = a; j < b; ++j) {
            const float lat = kCoastlineLatLon[2 * j];
            const float lon = kCoastlineLatLon[2 * j + 1];
            poly << proj_.project(lat, lon);
        }
        p.drawPolyline(poly);
    }
}

void WorldView::drawStation(QPainter& p) {
    if (std::isnan(stationLat_) || std::isnan(stationLon_)) return;
    QPointF pos = proj_.project(stationLat_, stationLon_);
    const int r = tokens::scaled(5);
    p.setPen(QPen(QColor(tokens::kSuccess), 2));
    p.setBrush(QColor(tokens::kSuccess));
    p.drawEllipse(pos, r, r);
    p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaSecondary)));
    p.drawText(pos + QPointF(r + 3, -r - 2), QString::fromUtf8("本站"));
}

void WorldView::drawGnss(QPainter& p) {
    if (!gnssValid_) return;   // honest: no dot without a fix
    QPointF pos = proj_.project(gnssLat_, gnssLon_);
    const int r = tokens::scaled(6);
    p.setPen(QPen(QColor(tokens::kAccent), 2));
    p.setBrush(Qt::NoBrush);
    p.drawEllipse(pos, r, r);
    p.setPen(QPen(QColor(tokens::kAccent), 1));
    p.setBrush(QColor(tokens::kAccent));
    p.drawEllipse(pos, tokens::scaled(2), tokens::scaled(2));
}

void WorldView::drawAircraft(QPainter& p) {
    // ground-track tails first, faint.
    p.setPen(QPen(QColor(tokens::textRgba(tokens::kTextAlphaFaint)), 1.0));
    for (const auto& ac : aircraft_) {
        if (ac.track.size() < 2) continue;
        QPolygonF poly;
        for (auto [lat, lon] : ac.track) poly << proj_.project(lat, lon);
        poly << proj_.project(ac.lat, ac.lon);
        p.drawPolyline(poly);
    }
    p.setPen(QPen(QColor(tokens::kWarning), 2));
    p.setBrush(QColor(tokens::kWarning));
    QFontMetrics fm(p.font());
    for (const auto& ac : aircraft_) {
        QPointF pos = proj_.project(ac.lat, ac.lon);
        p.drawEllipse(pos, tokens::scaled(3), tokens::scaled(3));
        if (!ac.callsign.isEmpty()) {
            p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaSecondary)));
            p.drawText(pos + QPointF(tokens::scaled(6), -tokens::scaled(4)), ac.callsign);
            p.setPen(QPen(QColor(tokens::kWarning), 2));
        }
    }
    (void)fm;
}

void WorldView::drawSatellites(QPainter& p) {
    // ground tracks, very faint.
    p.setPen(QPen(QColor(tokens::textRgba(tokens::kTextAlphaDisabled)), 1.0));
    for (const auto& s : satellites_) {
        if (s.track.size() < 2) continue;
        QPolygonF poly;
        for (auto [lat, lon] : s.track) poly << proj_.project(lat, lon);
        poly << proj_.project(s.lat, s.lon);
        p.drawPolyline(poly);
    }
    for (const auto& s : satellites_) {
        QPointF pos = proj_.project(s.lat, s.lon);
        if (s.selected) {
            p.setPen(QPen(QColor(tokens::kAccent), 2));
            p.setBrush(QColor(tokens::kAccent));
            p.drawEllipse(pos, tokens::scaled(6), tokens::scaled(6));
            p.setPen(QColor(tokens::kTextPrimary));
            p.drawText(pos + QPointF(tokens::scaled(9), -tokens::scaled(6)), s.name);
        } else {
            p.setPen(QPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)), 1));
            p.setBrush(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
            p.drawEllipse(pos, tokens::scaled(3), tokens::scaled(3));
        }
    }
}

void WorldView::drawStatusChips(QPainter& p) {
    const int pad = tokens::scaled(tokens::kSpacingM);
    const int h = tokens::scaled(20);
    int y = tokens::scaled(tokens::kSpacingM);

    auto chip = [&](const QString& text, const QColor& color) {
        QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
        QFontMetrics fm(f);
        QRectF r(tokens::scaled(tokens::kSpacingM), y,
                 fm.horizontalAdvance(text) + 2 * pad, h);
        p.setPen(Qt::NoPen);
        p.setBrush(QColor(QString::fromUtf8(tokens::kCard2)));
        p.drawRoundedRect(r, tokens::scaled(tokens::kRadiusSmall),
                          tokens::scaled(tokens::kRadiusSmall));
        p.setPen(color);
        p.drawText(r, Qt::AlignCenter, text);
        y += h + tokens::scaled(tokens::kSpacingS);
    };

    // GNSS status chip (always, when layer visible).
    if (layerVisible(MapLayer::Gnss)) {
        if (gnssValid_) {
            chip(QString::fromUtf8("GNSS 定位 · %1 星 · HDOP %2")
                     .arg(gnssSats_).arg(gnssHdop_, 0, 'f', 1),
                 QColor(tokens::kSuccess));
        } else {
            chip(QString::fromUtf8("GNSS 无定位"),
                 QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        }
    }
    if (std::isnan(stationLat_)) {
        chip(QString::fromUtf8("未设置本站位置"),
             QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
    }
    if (aircraft_.isEmpty() && layerVisible(MapLayer::Aircraft)) {
        chip(QString::fromUtf8("等待 ADS-B 位置数据"),
             QColor(tokens::textRgba(tokens::kTextAlphaFaint)));
    }

    // Synthetic / non-hardware tag, top-right.
    if (synthetic_) {
        QString tag = QString::fromUtf8("非硬件 NOT HARDWARE");
        QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); f.setBold(true);
        p.setFont(f);
        QFontMetrics fm(f);
        QRectF r(width() - fm.horizontalAdvance(tag) - 2 * pad - tokens::scaled(tokens::kSpacingM),
                 tokens::scaled(tokens::kSpacingM),
                 fm.horizontalAdvance(tag) + 2 * pad, h);
        p.setPen(Qt::NoPen);
        p.setBrush(QColor(QString::fromUtf8(tokens::kWarning)));
        p.drawRoundedRect(r, tokens::scaled(tokens::kRadiusSmall),
                          tokens::scaled(tokens::kRadiusSmall));
        p.setPen(QColor(tokens::kBgBar));
        p.drawText(r, Qt::AlignCenter, tag);
    }
}

} // namespace ui
} // namespace mbdsdr
