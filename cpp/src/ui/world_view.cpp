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
    setMinimumSize(tokens::scaled(tokens::kWorldViewMinW), tokens::scaled(tokens::kWorldViewMinH));
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

void WorldView::setGnssFix(bool valid, double lat, double lon, int sats, double hdop,
                           QDateTime fixTimeUtc) {
    gnssValid_ = valid;
    if (valid) { gnssLat_ = lat; gnssLon_ = lon; }
    gnssSats_ = sats;
    gnssHdop_ = hdop;
    gnssFixTime_ = fixTimeUtc;
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
            const QString t = gnssFixTime_.isValid()
                ? QString("定位时间 %1 UTC").arg(gnssFixTime_.toUTC().toString("yyyy-MM-dd HH:mm:ss"))
                : QStringLiteral("定位时间 --:--:--");
            h.tooltip = QString::fromUtf8("GNSS 定位\n%1, %2\n%3 颗星 · HDOP %4\n%5")
                            .arg(gnssLat_, 0, 'f', 4).arg(gnssLon_, 0, 'f', 4)
                            .arg(gnssSats_).arg(gnssHdop_, 0, 'f', 1)
                            .arg(t);
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
namespace {
// Collision-free single-line label placement. Tries the 8 compass offsets
// around `point` and returns the first candidate that (a) fits wholly inside
// `bounds` with `inset` margin and (b) does not intersect any already occupied
// rect (markers + earlier labels). Returns false when no clean spot exists --
// the caller drops the label (= priority thinning), the marker is still drawn.
bool placeMapLabel(const QPointF& point, const QString& text, const QFontMetrics& fm,
                   const QList<QRectF>& occupied, const QRectF& bounds,
                   int offset, int inset, QRectF& out) {
    const QSize ts = fm.size(Qt::TextSingleLine, text);
    const QRectF bb = bounds.adjusted(inset, inset, -inset, -inset);
    // Label top-left offset relative to the point, for 8 directions.
    const QPointF c[8] = {
        QPointF( offset,             -ts.height() * 0.5),            // E
        QPointF( offset,             -ts.height() - offset * 0.4),   // NE
        QPointF(-ts.width() * 0.5,   -ts.height() - offset),         // N
        QPointF(-ts.width() - offset, -ts.height() - offset * 0.4), // NW
        QPointF(-ts.width() - offset, -ts.height() * 0.5),          // W
        QPointF(-ts.width() - offset,  offset * 0.4),              // SW
        QPointF(-ts.width() * 0.5,    offset),                     // S
        QPointF( offset,              offset * 0.4),              // SE
    };
    for (int i = 0; i < 8; ++i) {
        QRectF r(point + c[i], ts);
        if (!bb.contains(r)) continue;
        bool clash = false;
        for (const QRectF& o : occupied)
            if (o.intersects(r)) { clash = true; break; }
        if (!clash) { out = r; return true; }
    }
    return false;
}
} // namespace

void WorldView::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setRenderHint(QPainter::SmoothPixmapTransform, true);
    p.fillRect(rect(), QColor(tokens::kBgMain));

    if (layerVisible(MapLayer::Graticule))  drawGraticule(p);
    if (layerVisible(MapLayer::Coastline)) drawCoastline(p);
    if (layerVisible(MapLayer::Station))   drawStation(p);
    if (layerVisible(MapLayer::Gnss))      drawGnss(p);
    if (layerVisible(MapLayer::Aircraft))  drawAircraft(p);
    if (layerVisible(MapLayer::Satellite)) drawSatellites(p);
    drawLabels(p);
    drawStatusChips(p);
    drawLegend(p);
}

void WorldView::drawGraticule(QPainter& p) {
    const double step = proj_.suggestGridStep();
    const double left  = proj_.centerLon() - proj_.lonSpan() / 2.0;
    const double right = proj_.centerLon() + proj_.lonSpan() / 2.0;
    const double top   = proj_.centerLat() + proj_.latSpan() / 2.0;
    const double bot   = proj_.centerLat() - proj_.latSpan() / 2.0;

    QPen grid(tokens::rgbaA(tokens::kGraticuleAlpha));
    grid.setWidthF(1.0);
    p.setPen(grid);
    for (double lon = std::ceil(left / step) * step; lon <= right; lon += step) {
        QLineF l(proj_.project(top, lon), proj_.project(bot, lon));
        p.drawLine(l);
    }
    for (double lat = std::ceil(bot / step) * step; lat <= top; lat += step) {
        QLineF l(proj_.project(lat, left), proj_.project(lat, right));
        p.drawLine(l);
    }

    // Faint tick captions inside the edge gutters -- never clipped, never loud.
    QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
    const QFontMetrics fm(f);
    p.setPen(tokens::rgbaA(tokens::kTickLabelAlpha));
    const qreal inset = tokens::scaled(tokens::kLabelMinInset);
    const qreal gxB = tokens::scaled(tokens::kMapGutterB);
    const qreal gxL = tokens::scaled(tokens::kMapGutterL);

    for (double lon = std::ceil(left / step) * step; lon <= right; lon += step) {
        const QPointF x = proj_.project(bot, lon);
        const QString s = QString::number(std::lround(lon)) + QChar(0xB0);
        QRectF r(x.x() - tokens::scaled(20), height() - gxB, tokens::scaled(40), fm.height());
        r.moveLeft(std::clamp(r.left(), inset, qreal(width()) - inset - r.width()));
        p.drawText(r, Qt::AlignHCenter | Qt::AlignBottom, s);
    }
    for (double lat = std::ceil(bot / step) * step; lat <= top; lat += step) {
        const QPointF y = proj_.project(lat, left);
        const QString s = QString::number(std::lround(lat)) + QChar(0xB0);
        QRectF r(inset, y.y() - fm.height() / 2.0, gxL - tokens::scaled(4), fm.height());
        r.moveTop(std::clamp(r.top(), inset, qreal(height()) - inset - r.height()));
        p.drawText(r, Qt::AlignRight | Qt::AlignVCenter, s);
    }
}

void WorldView::drawCoastline(QPainter& p) {
    QPen coast(tokens::rgbaA(tokens::kCoastlineAlpha));
    coast.setWidthF(1.0);
    coast.setJoinStyle(Qt::RoundJoin);
    coast.setCapStyle(Qt::RoundCap);
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
    const int h = tokens::scaled(tokens::kMapStationHalf);
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(tokens::kSuccess));
    p.drawRect(QRectF(pos.x() - h, pos.y() - h, 2.0 * h, 2.0 * h));
}

void WorldView::drawGnss(QPainter& p) {
    if (!gnssValid_) return;   // honest: no dot without a fix
    QPointF pos = proj_.project(gnssLat_, gnssLon_);
    const int ro = tokens::scaled(tokens::kMapGnssOuterR);
    p.setPen(QPen(QColor(tokens::kAccent), 1.4));
    p.setBrush(Qt::NoBrush);
    p.drawEllipse(pos, ro, ro);
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(tokens::kAccent));
    p.drawEllipse(pos, tokens::scaled(tokens::kMapGnssInnerR), tokens::scaled(tokens::kMapGnssInnerR));
}

void WorldView::drawAircraft(QPainter& p) {
    // Fading ground track: oldest tail faint -> current clearer (segment ramp).
    for (const auto& ac : aircraft_) {
        if (ac.track.size() < 2) continue;
        QPolygonF poly;
        for (auto [lat, lon] : ac.track) poly << proj_.project(lat, lon);
        poly << proj_.project(ac.lat, ac.lon);
        for (int i = 1; i < poly.size(); ++i) {
            const double u = double(i) / double(poly.size() - 1);
            p.setPen(QPen(tokens::rgbaA(0.10 + 0.28 * u), 1.0));
            p.drawLine(poly[i - 1], poly[i]);
        }
    }
    // Amber triangle markers.
    const int h = tokens::scaled(tokens::kMapAcHalf);
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(tokens::kWarning));
    for (const auto& ac : aircraft_) {
        QPointF pos = proj_.project(ac.lat, ac.lon);
        QPolygonF tri;
        tri << QPointF(pos.x(), pos.y() - h)
            << QPointF(pos.x() + h * 0.9, pos.y() + h * 0.7)
            << QPointF(pos.x() - h * 0.9, pos.y() + h * 0.7);
        p.drawPolygon(tri);
    }
}

void WorldView::drawSatellites(QPainter& p) {
    // Faint ground track.
    for (const auto& s : satellites_) {
        if (s.track.size() < 2) continue;
        QPolygonF poly;
        for (auto [lat, lon] : s.track) poly << proj_.project(lat, lon);
        poly << proj_.project(s.lat, s.lon);
        for (int i = 1; i < poly.size(); ++i) {
            const double u = double(i) / double(poly.size() - 1);
            p.setPen(QPen(tokens::rgbaA(0.06 + 0.18 * u), 1.0));
            p.drawLine(poly[i - 1], poly[i]);
        }
    }
    for (const auto& s : satellites_) {
        QPointF pos = proj_.project(s.lat, s.lon);
        if (s.selected) {
            p.setPen(QPen(QColor(tokens::kAccent), 1.4));
            p.setBrush(QColor(tokens::kAccent));
            p.drawEllipse(pos, tokens::scaled(tokens::kMapSatSelR), tokens::scaled(tokens::kMapSatSelR));
        } else {
            p.setPen(Qt::NoPen);
            p.setBrush(tokens::rgbaA(tokens::kTextAlphaTertiary));
            p.drawEllipse(pos, tokens::scaled(tokens::kMapSatDotR), tokens::scaled(tokens::kMapSatDotR));
        }
    }
}

void WorldView::drawLabels(QPainter& p) {
    QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
    const QFontMetrics fm(f);

    struct Item { QPointF pos; QString text; QColor color; int prio; };
    QList<Item> items;
    if (layerVisible(MapLayer::Satellite))
        for (const auto& s : satellites_)
            if (s.selected)
                items.push_back({proj_.project(s.lat, s.lon), s.name,
                                 QColor(tokens::kTextPrimary), 1});
    if (layerVisible(MapLayer::Gnss) && gnssValid_)
        items.push_back({proj_.project(gnssLat_, gnssLon_), QString::fromUtf8("GNSS"),
                         QColor(tokens::kTextSecondary), 2});
    if (layerVisible(MapLayer::Station) && !std::isnan(stationLat_))
        items.push_back({proj_.project(stationLat_, stationLon_), QString::fromUtf8("本站"),
                         QColor(tokens::kTextSecondary), 3});
    if (layerVisible(MapLayer::Aircraft))
        for (const auto& ac : aircraft_)
            if (!ac.callsign.isEmpty())
                items.push_back({proj_.project(ac.lat, ac.lon), ac.callsign,
                                 tokens::rgbaA(tokens::kTextAlphaSecondary), 4});

    // Priority: selected satellite > GNSS > station > aircraft.
    std::sort(items.begin(), items.end(),
              [](const Item& a, const Item& b) { return a.prio < b.prio; });

    QList<QRectF> occupied;
    const int inset = tokens::scaled(tokens::kLabelMinInset);
    const int off   = tokens::scaled(tokens::kLabelOffset);
    for (const auto& it : items)
        occupied.append(QRectF(it.pos.x() - tokens::scaled(6), it.pos.y() - tokens::scaled(6),
                               tokens::scaled(12), tokens::scaled(12)));

    p.setBrush(Qt::NoBrush);
    for (const auto& it : items) {
        QRectF r;
        if (!placeMapLabel(it.pos, it.text, fm, occupied, rect(), off, inset, r))
            continue;   // no clean spot -> drop this label (thinning)
        occupied.append(r);
        // Thin leader line from the marker EDGE to the label's nearest edge,
        // so the marker's own centre pixels are never overwritten.
        const QPointF edge(std::clamp(it.pos.x(), r.left(), r.right()),
                           std::clamp(it.pos.y(), r.top(), r.bottom()));
        QPointF dir = edge - it.pos;
        const double len = std::hypot(dir.x(), dir.y());
        if (len > tokens::scaled(6)) {
            dir /= len;
            p.setPen(QPen(tokens::rgbaA(tokens::kLeaderLineAlpha), 1.0));
            p.drawLine(it.pos + dir * tokens::scaled(6), edge);
        }
        p.setPen(it.color);
        p.drawText(r, Qt::AlignVCenter | Qt::AlignLeft, it.text);
    }
}

void WorldView::drawStatusChips(QPainter& p) {
    const int padX = tokens::scaled(tokens::kSpacingM);
    const int padY = tokens::scaled(tokens::kSpacingS);
    QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
    const QFontMetrics fm(f);
    const int chipH = fm.height() + 2 * padY;

    // One restrained chip style: faint card fill, 1px hairline edge, secondary
    // text, optional 4px status dot. No solid mustard sticker.
    auto chip = [&](int x, int y, const QString& text, const QColor& dot) {
        const int dotW = dot.isValid() ? tokens::scaled(6) + tokens::scaled(4) : 0;
        const int w = fm.horizontalAdvance(text) + 2 * padX + dotW;
        QRectF r(x, y, w, chipH);
        p.setPen(QPen(tokens::cardEdge(), 1.0));
        p.setBrush(tokens::card1());
        p.drawRoundedRect(r, tokens::scaled(tokens::kRadiusSmall), tokens::scaled(tokens::kRadiusSmall));
        int tx = x + padX;
        if (dot.isValid()) {
            p.setPen(Qt::NoPen);
            p.setBrush(dot);
            const int d = tokens::scaled(4);
            p.drawEllipse(QPointF(x + padX + d / 2.0, y + chipH / 2.0), d / 2.0, d / 2.0);
            tx = x + padX + d + tokens::scaled(6);
        }
        p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
        p.drawText(QRectF(tx, y, w - (tx - x), chipH), Qt::AlignVCenter | Qt::AlignLeft, text);
    };

    int y = tokens::scaled(tokens::kSpacingM);
    if (layerVisible(MapLayer::Gnss)) {
        if (gnssValid_)
            chip(tokens::scaled(tokens::kSpacingM), y,
                 QString::fromUtf8("GNSS 定位 · %1 星 · HDOP %2")
                     .arg(gnssSats_).arg(gnssHdop_, 0, 'f', 1),
                 QColor(tokens::kSuccess));
        else
            chip(tokens::scaled(tokens::kSpacingM), y,
                 QString::fromUtf8("GNSS 无定位"),
                 tokens::rgbaA(tokens::kTextAlphaDisabled));
        y += chipH + tokens::scaled(tokens::kSpacingS);
    }
    if (std::isnan(stationLat_))
        chip(tokens::scaled(tokens::kSpacingM), y,
             QString::fromUtf8("未设置本站位置"),
             tokens::rgbaA(tokens::kTextAlphaDisabled));

    // Synthetic / non-hardware tag: a quiet instrument corner badge, top-right.
    if (synthetic_) {
        const QString tag = QString::fromUtf8("非硬件 · NOT HARDWARE");
        const int w = fm.horizontalAdvance(tag) + 2 * padX;
        QRectF r(width() - w - tokens::scaled(tokens::kSpacingM),
                 tokens::scaled(tokens::kSpacingM), w, chipH);
        p.setPen(QPen(tokens::cardEdge(), 1.0));
        p.setBrush(tokens::card1());
        p.drawRoundedRect(r, tokens::scaled(tokens::kRadiusSmall),
                          tokens::scaled(tokens::kRadiusSmall));
        p.setPen(tokens::rgbaA(tokens::kTextAlphaQuaternary));
        p.drawText(r, Qt::AlignCenter, tag);
    }
}

// Restrained corner legend: one tiny colored swatch + caption per layer, bottom
// right. Colors are read straight from the same tokens the layers paint with,
// so the legend can never drift from the actual markers. Faint, small, quiet.
namespace {
struct LegendRow { QColor color; QString text; };
QList<LegendRow> legendRows() {
    return {
        { QColor(tokens::kAccent),  QStringLiteral("GNSS 定位点") },
        { QColor(tokens::kWarning), QStringLiteral("ADS-B 飞机") },
        { tokens::rgbaA(tokens::kTextAlphaTertiary), QStringLiteral("卫星星下点") },
        { QColor(tokens::kSuccess), QStringLiteral("本站") },
    };
}
} // namespace

QStringList WorldView::legendItems() const {
    QStringList out;
    for (const auto& r : legendRows()) out << r.text;
    return out;
}

void WorldView::drawLegend(QPainter& p) {
    QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
    const QFontMetrics fm(f);
    const int lineH = fm.height();
    const int padX = tokens::scaled(tokens::kSpacingM);
    const int padY = tokens::scaled(tokens::kSpacingS);
    const int swatch = tokens::scaled(tokens::kMapSatDotR);

    const QList<LegendRow> items = legendRows();

    int maxW = 0;
    for (const auto& it : items)
        maxW = std::max(maxW, fm.horizontalAdvance(it.text));
    const int boxW = swatch + tokens::scaled(tokens::kSpacingS) + maxW + 2 * padX;
    const int boxH = int(items.size()) * lineH + 2 * padY;

    QRectF box(width() - boxW - tokens::scaled(tokens::kSpacingM),
               height() - boxH - tokens::scaled(tokens::kMapGutterB) - tokens::scaled(tokens::kSpacingM),
               boxW, boxH);
    p.setPen(QPen(tokens::cardEdge(), 1.0));
    p.setBrush(tokens::card1());
    p.drawRoundedRect(box, tokens::scaled(tokens::kRadiusSmall), tokens::scaled(tokens::kRadiusSmall));

    int y = box.top() + padY;
    for (const auto& it : items) {
        const QPointF sc(box.left() + padX + swatch / 2.0, y + lineH / 2.0);
        p.setPen(Qt::NoPen);
        p.setBrush(it.color);
        p.drawEllipse(sc, swatch / 2.0, swatch / 2.0);
        p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
        p.drawText(QRectF(box.left() + padX + swatch + tokens::scaled(tokens::kSpacingS),
                          y, maxW, lineH),
                   Qt::AlignVCenter | Qt::AlignLeft, it.text);
        y += lineH;
    }
}

} // namespace ui
} // namespace mbdsdr
