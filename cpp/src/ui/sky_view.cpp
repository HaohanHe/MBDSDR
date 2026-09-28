// SPDX-License-Identifier: MIT
#include "sky_view.h"
#include "core/tokens.h"

#include <QPainter>
#include <QPolygonF>
#include <QToolTip>
#include <QMouseEvent>
#include <QFontMetrics>
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace ui {

namespace {

constexpr const char* kDirNames[8] = {
    "N", "NE", "E", "SE", "S", "SW", "W", "NW"
};

// Distance from point p to segment ab (screen px).
double distToSegment(const QPointF& p, const QPointF& a, const QPointF& b) {
    QPointF ab = b - a;
    double len2 = ab.x() * ab.x() + ab.y() * ab.y();
    if (len2 < 1e-9) return std::sqrt(QPointF::dotProduct(p - a, p - a));
    double t = QPointF::dotProduct(p - a, ab) / len2;
    t = std::clamp(t, 0.0, 1.0);
    QPointF proj = a + t * ab;
    return std::sqrt(QPointF::dotProduct(p - proj, p - proj));
}

// Collision-free single-line label placement around a sky point. Tries the 8
// compass offsets; returns the first candidate wholly inside `bounds` (with
// `inset`) and not intersecting `occupied`. On failure returns false so the
// caller can thin the label (markers are always still drawn).
bool placeSkyLabel(const QPointF& point, const QString& text, const QFontMetrics& fm,
                   const QList<QRectF>& occupied, const QRectF& bounds,
                   int offset, int inset, QRectF& out) {
    const QSize ts = fm.size(Qt::TextSingleLine, text);
    const QRectF bb = bounds.adjusted(inset, inset, -inset, -inset);
    const QPointF c[8] = {
        QPointF( offset,             -ts.height() * 0.5),
        QPointF( offset,             -ts.height() - offset * 0.4),
        QPointF(-ts.width() * 0.5,   -ts.height() - offset),
        QPointF(-ts.width() - offset, -ts.height() - offset * 0.4),
        QPointF(-ts.width() - offset, -ts.height() * 0.5),
        QPointF(-ts.width() - offset,  offset * 0.4),
        QPointF(-ts.width() * 0.5,    offset),
        QPointF( offset,              offset * 0.4),
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

SkyView::SkyView(QWidget* parent) : QWidget(parent) {
    setMinimumSize(tokens::scaled(220), tokens::scaled(220));
    setMouseTracking(true);
}

QPointF SkyView::polarToXY(double azDeg, double elDeg, double radius, QPointF center) {
    double az = azDeg * M_PI / 180.0;
    double r = radius * (1.0 - elDeg / 90.0);
    return center + QPointF(r * std::sin(az), -r * std::cos(az));
}

QPointF SkyView::compassCenter() const {
    return rect().center();
}

double SkyView::compassRadius() const {
    double gutter = tokens::scaled(tokens::kSkyGutter); // room for azimuth captions
    return qMin(width(), height()) / 2.0 - gutter;
}

void SkyView::setPasses(QList<PassArc> passes) {
    passes_ = std::move(passes);
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

void SkyView::setGnssSatellites(QList<GnssSkySat> sats) {
    gnss_ = std::move(sats);
    update();
}

void SkyView::clearGnssSatellites() {
    gnss_.clear();
    update();
}

void SkyView::setCurrentTime(QDateTime utc) {
    nowUtc_ = utc;
    update();
}

void SkyView::setEmptyText(const QString& text) {
    emptyText_ = text;
    update();
}

void SkyView::setNonHardware(bool on) {
    nonHardware_ = on;
    update();
}

void SkyView::setSelectedSatellite(const QString& name) {
    if (selectedName_ == name) return;
    selectedName_ = name;
    update();
}

QString SkyView::tooltipAt(const QPointF& p) const {
    Hit h = hitTest(p);
    if (h.kind == Hit::None) return QString();
    return tooltipFor(h);
}

SkyView::Hit SkyView::hitTest(const QPointF& p) const {
    const QPointF c = compassCenter();
    const double R = compassRadius();
    const double tol = tokens::scaled(8);

    // GNSS diamonds (topmost interactive layer).
    for (int i = 0; i < gnss_.size(); ++i) {
        const GnssSkySat& g = gnss_[i];
        if (g.el < 0.0 || g.el > 90.0) continue;
        QPointF q = polarToXY(g.az, g.el, R, c);
        if (std::sqrt(QPointF::dotProduct(p - q, p - q)) <= tol)
            return {Hit::Gnss, i};
    }

    // Orbit live satellites.
    for (int i = 0; i < liveSats_.size(); ++i) {
        const LiveSat& l = liveSats_[i];
        if (l.el < 0.0 || l.el > 90.0) continue;
        QPointF q = polarToXY(l.az, l.el, R, c);
        if (std::sqrt(QPointF::dotProduct(p - q, p - q)) <= tol)
            return {Hit::Orbit, i};
    }

    // Pass arcs: distance to the polyline.
    for (int i = 0; i < passes_.size(); ++i) {
        const PassArc& pass = passes_[i];
        if (pass.track.size() < 2) continue;
        double best = 1e18;
        for (int k = 1; k < pass.track.size(); ++k) {
            QPointF a = polarToXY(pass.track[k-1].first, pass.track[k-1].second, R, c);
            QPointF b = polarToXY(pass.track[k].first,   pass.track[k].second,   R, c);
            best = std::min(best, distToSegment(p, a, b));
        }
        if (best <= tol) return {Hit::Pass, i};
    }
    return {Hit::None, -1};
}

QString SkyView::tooltipFor(const Hit& h) const {
    const QPointF c = compassCenter();
    const double R = compassRadius();
    switch (h.kind) {
    case Hit::Orbit: {
        const LiveSat& l = liveSats_[h.index];
        return QString("%1\n方位 %2°  仰角 %3°")
            .arg(l.name)
            .arg(l.az, 0, 'f', 0).arg(l.el, 0, 'f', 1);
    }
    case Hit::Gnss: {
        const GnssSkySat& g = gnss_[h.index];
        return QString("PRN %1\n方位 %2°  仰角 %3°\nSNR %4 dB-Hz  [%5]")
            .arg(g.prn)
            .arg(g.az, 0, 'f', 0).arg(g.el, 0, 'f', 0)
            .arg(g.snr, 0, 'f', 0)
            .arg(g.used ? QStringLiteral("已用于定位") : QStringLiteral("未使用"));
    }
    case Hit::Pass: {
        const PassArc& p = passes_[h.index];
        QString aos = p.aosUtc.isValid()
            ? QString("AOS  %1 UTC / 本地 %2")
                  .arg(p.aosUtc.toUTC().toString("HH:mm:ss"),
                       p.aosUtc.toLocalTime().toString("HH:mm:ss"))
            : QStringLiteral("AOS  --:--:--");
        QString los = p.losUtc.isValid()
            ? QString("LOS  %1 UTC / 本地 %2")
                  .arg(p.losUtc.toUTC().toString("HH:mm:ss"),
                       p.losUtc.toLocalTime().toString("HH:mm:ss"))
            : QStringLiteral("LOS  --:--:--");
        return QString("%1\n%2\n%3\n最大仰角 %4°")
            .arg(p.name, aos, los).arg(p.maxEl, 0, 'f', 1);
    }
    default:
        return QString();
    }
}

void SkyView::mousePressEvent(QMouseEvent* e) {
    Hit h = hitTest(e->position());
    if (h.kind == Hit::Orbit) {
        const LiveSat& l = liveSats_[h.index];
        setSelectedSatellite(l.name);
        emit satelliteSelected(l.name);
    }
    if (h.kind != Hit::None) {
        QToolTip::showText(e->globalPosition().toPoint(), tooltipFor(h), this);
    }
    QWidget::mousePressEvent(e);
}

void SkyView::mouseMoveEvent(QMouseEvent* e) {
    Hit h = hitTest(e->position());
    if (h.kind != Hit::None) {
        QToolTip::showText(e->globalPosition().toPoint(), tooltipFor(h), this);
    }
    QWidget::mouseMoveEvent(e);
}

void SkyView::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);

    p.fillRect(rect(), QColor(tokens::kCard1));

    const QPointF c = compassCenter();
    const double R = compassRadius();

    QFont small = font();
    small.setPointSize(tokens::kFontAuxPt);
    p.setFont(small);

    // --- Elevation rings el = 0/30/60 (90 is the zenith point) ----------
    QPen gridPen(tokens::rgbaA(tokens::kGraticuleAlpha));
    gridPen.setWidthF(1.0);
    p.setPen(gridPen);
    for (int el = 0; el <= 60; el += 30) {
        double r = R * (1.0 - el / 90.0);
        p.drawEllipse(c, r, r);
    }
    p.setPen(QPen(tokens::rgbaA(tokens::kTextAlphaSecondary), 2));
    p.drawPoint(c); // zenith (el = 90)

    const QFontMetrics fm(small);

    // Occupied rects data labels must never cover: corner bands, the fixed
    // ring captions, and the outer azimuth captions.
    const int inset = tokens::scaled(tokens::kLabelMinInset);
    const int off   = tokens::scaled(tokens::kLabelOffset);
    QList<QRectF> occupied;
    occupied.append(QRectF(0, 0, qreal(width()), fm.height() * 2 + inset));
    occupied.append(QRectF(width() - tokens::scaled(160), 0,
                           qreal(tokens::scaled(160)), fm.height() + inset));

    // Ring elevation captions at a fixed, non-blocking spot: just inside the
    // east (right) spoke. Never on the north ray, never a stray "0" under N.
    p.setPen(tokens::rgbaA(tokens::kTickLabelAlpha));
    for (int el = 0; el <= 60; el += 30) {
        double r = R * (1.0 - el / 90.0);
        QRectF lb(c.x() + r - tokens::scaled(26), c.y() - tokens::scaled(7),
                  tokens::scaled(22), tokens::scaled(14));
        p.drawText(lb, Qt::AlignRight | Qt::AlignVCenter, QString("%1°").arg(el));
        occupied.append(lb);
    }

    // --- Azimuth rays every 45°, captions outside the compass ----------
    p.setPen(gridPen);
    for (int az = 0; az < 360; az += 45) {
        double a = az * M_PI / 180.0;
        QPointF edge = c + QPointF(R * std::sin(a), -R * std::cos(a));
        p.drawLine(c, edge);
    }
    p.setPen(tokens::rgbaA(tokens::kTickLabelAlpha));
    const double labelR = R + tokens::scaled(12);
    for (int i = 0; i < 8; ++i) {
        double az = i * 45.0;
        double a = az * M_PI / 180.0;
        QPointF pos = c + QPointF(labelR * std::sin(a), -labelR * std::cos(a));
        QRectF lb(pos.x() - tokens::scaled(22), pos.y() - tokens::scaled(8),
                  tokens::scaled(44), tokens::scaled(14));
        p.drawText(lb, Qt::AlignCenter,
                   QString("%1 %2°").arg(QLatin1String(kDirNames[i])).arg(az, 0, 'f', 0));
        occupied.append(lb);
    }

    // --- Label jobs (placed later, collision-free, priority ordered) -----
    struct Lab { QPointF anchor; QString text; QColor color; int prio; };
    QList<Lab> labs;

    // --- Pass arcs ------------------------------------------------------
    for (int i = 0; i < passes_.size(); ++i) {
        const PassArc& pass = passes_[i];
        if (pass.track.size() < 2) continue;
        const bool sel = (!selectedName_.isEmpty() && pass.name == selectedName_);
        QPen arcPen(sel ? QColor(tokens::kSuccess) : QColor(tokens::kAccent),
                    sel ? tokens::kArcSelWidth : tokens::kArcWidth);
        p.setPen(arcPen);
        QPolygonF poly;
        for (const auto& pt : pass.track)
            poly << polarToXY(pt.first, pt.second, R, c);
        p.drawPolyline(poly);

        QPointF s = polarToXY(pass.track.first().first, pass.track.first().second, R, c);
        QPointF e = polarToXY(pass.track.last().first,  pass.track.last().second,  R, c);
        p.setBrush(sel ? QColor(tokens::kSuccess) : QColor(tokens::kAccent));
        p.setPen(Qt::NoPen);
        p.drawEllipse(s, tokens::scaled(3), tokens::scaled(3));
        p.drawEllipse(e, tokens::scaled(3), tokens::scaled(3));

        // Small time tick dots along the arc (quarters).
        for (double u : {0.25, 0.5, 0.75}) {
            int idx = int(u * (pass.track.size() - 1) + 0.5);
            QPointF qq = polarToXY(pass.track[idx].first, pass.track[idx].second, R, c);
            p.setBrush(tokens::rgbaA(tokens::kTextAlphaSecondary));
            p.drawEllipse(qq, tokens::scaled(tokens::kSkyRingDotR), tokens::scaled(tokens::kSkyRingDotR));
        }

        auto fmt = [](const QDateTime& t) -> QString {
            return t.isValid() ? t.toUTC().toString("HH:mm:ss") : QStringLiteral("--:--:--");
        };
        labs.push_back({s, QString("AOS %1").arg(fmt(pass.aosUtc)),
                        tokens::rgbaA(tokens::kTextAlphaSecondary), 1});
        labs.push_back({e, QString("LOS %1").arg(fmt(pass.losUtc)),
                        tokens::rgbaA(tokens::kTextAlphaSecondary), 1});

        int best = 0;
        for (int k = 1; k < pass.track.size(); ++k)
            if (pass.track[k].second > pass.track[best].second) best = k;
        QPointF m = polarToXY(pass.track[best].first, pass.track[best].second, R, c);
        labs.push_back({m, pass.name,
                        QColor(sel ? tokens::kSuccess : tokens::kTextPrimary), 2});
    }

    // --- Live orbit satellites ------------------------------------------
    for (const LiveSat& l : liveSats_) {
        if (l.el < 0.0 || l.el > 90.0) continue;
        QPointF q = polarToXY(l.az, l.el, R, c);
        const bool sel = (!selectedName_.isEmpty() &&
                          (l.name == selectedName_ || l.selected));
        if (sel) {
            qreal r = tokens::scaled(5);
            p.setPen(QPen(tokens::rgbaA(tokens::kLeaderLineAlpha), 1.0));
            p.setBrush(Qt::NoBrush);
            p.drawEllipse(q, r * 1.8, r * 1.8);
            p.setPen(Qt::NoPen);
            p.setBrush(QColor(tokens::kAccent));
            p.drawEllipse(q, r, r);
            labs.push_back({q, l.name, QColor(tokens::kTextPrimary), 3});
        } else {
            p.setBrush(tokens::rgbaA(tokens::kTextAlphaTertiary));
            p.setPen(Qt::NoPen);
            p.drawEllipse(q, tokens::scaled(2), tokens::scaled(2));
        }
    }

    // --- GNSS satellites: amber diamonds (contract), distinct -----------
    for (const GnssSkySat& g : gnss_) {
        if (g.el < 0.0 || g.el > 90.0) continue;
        QPointF q = polarToXY(g.az, g.el, R, c);
        const qreal half = tokens::scaled(4);
        QPolygonF d;
        d << QPointF(q.x(), q.y() - half)
          << QPointF(q.x() + half, q.y())
          << QPointF(q.x(), q.y() + half)
          << QPointF(q.x() - half, q.y());
        p.setBrush(g.used ? QColor(tokens::kWarning)
                          : tokens::rgbaA(tokens::kTextAlphaQuaternary));
        p.setPen(g.used ? QPen(QColor(tokens::kWarning).darker())
                        : QPen(tokens::rgbaA(tokens::kTextAlphaTertiary)));
        p.drawPolygon(d);
        labs.push_back({q, g.prn, QColor(tokens::kTextSecondary), 4});
    }

    // --- Place labels: reserve marker footprints, then priority order ---
    for (const Lab& L : labs)
        occupied.append(QRectF(L.anchor.x() - tokens::scaled(6), L.anchor.y() - tokens::scaled(6),
                               tokens::scaled(12), tokens::scaled(12)));
    std::sort(labs.begin(), labs.end(), [](const Lab& a, const Lab& b) { return a.prio < b.prio; });
    p.setBrush(Qt::NoBrush);
    for (const Lab& L : labs) {
        QRectF r;
        if (!placeSkyLabel(L.anchor, L.text, fm, occupied, rect(), off, inset, r))
            continue;   // no clean spot -> drop label, marker stays
        occupied.append(r);
        // Thin leader line from the marker EDGE to the label's nearest edge,
        // so the marker's own centre pixels are never overwritten.
        const QPointF edge(std::clamp(L.anchor.x(), r.left(), r.right()),
                           std::clamp(L.anchor.y(), r.top(), r.bottom()));
        QPointF dir = edge - L.anchor;
        const double len = std::hypot(dir.x(), dir.y());
        if (len > tokens::scaled(6)) {
            dir /= len;
            p.setPen(QPen(tokens::rgbaA(tokens::kLeaderLineAlpha), 1.0));
            p.drawLine(L.anchor + dir * tokens::scaled(6), edge);
        }
        p.setPen(L.color);
        p.drawText(r, Qt::AlignVCenter | Qt::AlignLeft, L.text);
    }

    // --- Current UTC / local time (授时), top-left -----------------------
    if (nowUtc_.isValid()) {
        p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
        const QString utc = QString("UTC %1").arg(nowUtc_.toUTC().toString("yyyy-MM-dd HH:mm:ss"));
        const QString loc = QString("本地 %1").arg(nowUtc_.toLocalTime().toString("HH:mm:ss t"));
        int x = tokens::scaled(tokens::kSpacingM);
        int y = tokens::scaled(tokens::kSpacingM) + fm.ascent();
        p.drawText(x, y, utc);
        p.drawText(x, y + fm.height(), loc);
    }

    // --- Non-hardware badge: quiet corner chip, top-right ---------------
    if (nonHardware_) {
        const QString tag = QStringLiteral("非硬件 · NOT HARDWARE");
        const int padX = tokens::scaled(tokens::kSpacingM);
        const int w = fm.horizontalAdvance(tag) + 2 * padX;
        const int chipH = fm.height() + 2 * tokens::scaled(tokens::kSpacingS);
        QRectF r(width() - w - tokens::scaled(tokens::kSpacingM),
                 tokens::scaled(tokens::kSpacingM), w, chipH);
        p.setPen(QPen(tokens::cardEdge(), 1.0));
        p.setBrush(tokens::card1());
        p.drawRoundedRect(r, tokens::scaled(tokens::kRadiusSmall),
                          tokens::scaled(tokens::kRadiusSmall));
        p.setPen(tokens::rgbaA(tokens::kTextAlphaQuaternary));
        p.drawText(r, Qt::AlignCenter, tag);
    }

    // --- Honest empty state ---------------------------------------------
    if (isEmpty()) {
        p.setPen(tokens::rgbaA(tokens::kTextAlphaTertiary));
        p.drawText(rect(), Qt::AlignCenter, emptyText_);
    }
}

} // namespace ui
} // namespace mbdsdr
