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
    double gutter = tokens::scaled(22); // room for azimuth/angle labels
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

    // --- Elevation rings: el = 0, 30, 60 (90 is the center point) -------
    QPen gridPen(QColor(tokens::kCardEdge));
    gridPen.setWidthF(1.0);
    p.setPen(gridPen);
    for (int el = 0; el <= 60; el += 30) {
        double r = R * (1.0 - el / 90.0);
        p.drawEllipse(c, r, r);
    }
    // Ring elevation captions on the north ray.
    p.setPen(QPen(QColor(tokens::kTextSecondary)));
    for (int el = 0; el <= 60; el += 30) {
        double r = R * (1.0 - el / 90.0);
        p.drawText(QRectF(c.x() + tokens::scaled(4), c.y() - r - tokens::scaled(10),
                          tokens::scaled(34), tokens::scaled(12)),
                   Qt::AlignLeft, QString::number(el));
    }
    p.setPen(QPen(QColor(tokens::kTextSecondary), 2));
    p.drawPoint(c); // zenith (el = 90)

    // --- Azimuth rays every 45°, labelled N/NE/.../NW + angle ------------
    p.setPen(gridPen);
    for (int az = 0; az < 360; az += 45) {
        double a = az * M_PI / 180.0;
        QPointF edge = c + QPointF(R * std::sin(a), -R * std::cos(a));
        p.drawLine(c, edge);
    }
    p.setPen(QPen(QColor(tokens::kTextSecondary)));
    const double labelR = R + tokens::scaled(14);
    for (int i = 0; i < 8; ++i) {
        double az = i * 45.0;
        double a = az * M_PI / 180.0;
        QPointF pos = c + QPointF(labelR * std::sin(a), -labelR * std::cos(a));
        p.drawText(QRectF(pos.x() - tokens::scaled(20), pos.y() - tokens::scaled(10),
                          tokens::scaled(40), tokens::scaled(14)),
                   Qt::AlignCenter,
                   QString("%1 %2°").arg(QLatin1String(kDirNames[i])).arg(az, 0, 'f', 0));
    }

    // --- Pass arcs -------------------------------------------------------
    for (int i = 0; i < passes_.size(); ++i) {
        const PassArc& pass = passes_[i];
        if (pass.track.size() < 2) continue;
        const bool sel = (!selectedName_.isEmpty() && pass.name == selectedName_);
        QPen arcPen(sel ? QColor(tokens::kSuccess) : QColor(tokens::kAccent),
                    sel ? 2.6 : 1.4);
        p.setPen(arcPen);
        QPolygonF poly;
        for (const auto& pt : pass.track)
            poly << polarToXY(pt.first, pt.second, R, c);
        p.drawPolyline(poly);

        // Start (AOS) / end (LOS) horizon markers + time labels.
        QPointF s = polarToXY(pass.track.first().first, pass.track.first().second, R, c);
        QPointF e = polarToXY(pass.track.last().first,  pass.track.last().second,  R, c);
        p.setBrush(sel ? QColor(tokens::kSuccess) : QColor(tokens::kAccent));
        p.setPen(Qt::NoPen);
        p.drawEllipse(s, tokens::scaled(3), tokens::scaled(3));
        p.drawEllipse(e, tokens::scaled(3), tokens::scaled(3));

        p.setPen(QPen(QColor(tokens::kTextSecondary)));
        auto fmt = [](const QDateTime& t) -> QString {
            if (!t.isValid()) return QStringLiteral("--:--:--");
            return QString("%1/%2").arg(t.toUTC().toString("HH:mm:ss"),
                                        t.toLocalTime().toString("HH:mm:ss"));
        };
        const int labW = tokens::scaled(150);
        p.drawText(QRectF(s.x() - tokens::scaled(8) - labW, s.y() - tokens::scaled(8),
                          labW, tokens::scaled(14)),
                   Qt::AlignRight | Qt::AlignVCenter,
                   QString("AOS %1").arg(fmt(pass.aosUtc)));
        p.drawText(QRectF(e.x() + tokens::scaled(8), e.y() - tokens::scaled(7),
                          labW, tokens::scaled(14)),
                   Qt::AlignLeft | Qt::AlignVCenter,
                   QString("LOS %1").arg(fmt(pass.losUtc)));

        // Name near the max-elevation point.
        int best = 0;
        for (int k = 1; k < pass.track.size(); ++k)
            if (pass.track[k].second > pass.track[best].second) best = k;
        QPointF m = polarToXY(pass.track[best].first, pass.track[best].second, R, c);
        p.setPen(QPen(sel ? QColor(tokens::kSuccess) : QColor(tokens::kTextWhite)));
        p.drawText(m + QPointF(tokens::scaled(8), -tokens::scaled(6)), pass.name);
    }

    // --- Live orbit satellites -------------------------------------------
    for (const LiveSat& l : liveSats_) {
        if (l.el < 0.0 || l.el > 90.0) continue;
        QPointF q = polarToXY(l.az, l.el, R, c);
        const bool sel = (!selectedName_.isEmpty() &&
                          (l.name == selectedName_ || l.selected));
        if (sel) {
            qreal r = tokens::scaled(5);
            QRadialGradient glow(q, r * 3.0);
            glow.setColorAt(0.0, QColor(tokens::kAccent));
            glow.setColorAt(1.0, QColor(tokens::kAccent).lighter());
            p.setBrush(QBrush(glow));
            p.setPen(Qt::NoPen);
            p.drawEllipse(q, r * 1.6, r * 1.6);
            p.setBrush(QColor(tokens::kAccent));
            p.drawEllipse(q, r, r);
            p.setPen(QPen(QColor(tokens::kAccent)));
            p.drawText(q + QPointF(tokens::scaled(8), -tokens::scaled(4)), l.name);
        } else {
            p.setBrush(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
            p.setPen(Qt::NoPen);
            p.drawEllipse(q, tokens::scaled(2.5), tokens::scaled(2.5));
        }
    }

    // --- GNSS satellites: diamonds, distinct from orbit dots -------------
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
                          : QColor(tokens::textRgba(tokens::kTextAlphaQuaternary)));
        p.setPen(g.used ? QPen(QColor(tokens::kWarning).darker())
                        : QPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary))));
        p.drawPolygon(d);
        p.setPen(QPen(QColor(tokens::kTextSecondary)));
        p.drawText(q + QPointF(tokens::scaled(6), -tokens::scaled(4)), g.prn);
    }

    // --- Current UTC / local time (授时) ---------------------------------
    if (nowUtc_.isValid()) {
        p.setPen(QPen(QColor(tokens::kTextSecondary)));
        const QFontMetrics fm(small);
        const QString utc = QString("UTC %1").arg(nowUtc_.toUTC().toString("yyyy-MM-dd HH:mm:ss"));
        const QString loc = QString("本地 %1").arg(nowUtc_.toLocalTime().toString("HH:mm:ss t"));
        int x = tokens::scaled(8);
        int y = tokens::scaled(6) + fm.ascent();
        p.drawText(x, y, utc);
        p.drawText(x, y + fm.height(), loc);
    }

    // --- Non-hardware (synthetic data) badge -----------------------------
    if (nonHardware_) {
        p.setPen(QPen(QColor(tokens::kWarning)));
        p.drawText(rect().adjusted(0, tokens::scaled(4), -tokens::scaled(8), 0),
                   Qt::AlignRight | Qt::AlignTop,
                   QStringLiteral("非硬件 NOT HARDWARE"));
    }

    // --- Honest empty state ---------------------------------------------
    if (isEmpty()) {
        p.setPen(QPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary))));
        p.drawText(rect(), Qt::AlignCenter, emptyText_);
    }
}

} // namespace ui
} // namespace mbdsdr
