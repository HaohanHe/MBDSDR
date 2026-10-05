// SPDX-License-Identifier: MIT
#include "elevation_plot.h"
#include "core/tokens.h"

#include <QPainter>
#include <QFontMetrics>
#include <cmath>

namespace mbdsdr {
namespace ui {

ElevationPlot::ElevationPlot(QWidget* parent) : QWidget(parent) {
    setMinimumSize(tokens::scaled(tokens::kElevPlotW), tokens::scaled(tokens::kElevPlotH));
}

void ElevationPlot::setPass(const QString& name,
                            const QList<QPair<QDateTime, double>>& samples) {
    name_ = name;
    samples_ = samples;
    update();
}

void ElevationPlot::clear() {
    name_.clear();
    samples_.clear();
    update();
}

QDateTime ElevationPlot::aos() const {
    return samples_.isEmpty() ? QDateTime() : samples_.first().first;
}

QDateTime ElevationPlot::los() const {
    return samples_.isEmpty() ? QDateTime() : samples_.last().first;
}

double ElevationPlot::maxEl() const {
    double m = 0.0;
    for (const auto& s : samples_) m = std::max(m, s.second);
    return m;
}

int ElevationPlot::peakIndex() const {
    int best = -1;
    for (int i = 0; i < samples_.size(); ++i)
        if (best < 0 || samples_[i].second > samples_[best].second) best = i;
    return best;
}

QRectF ElevationPlot::plotRect() const {
    return QRectF(tokens::scaled(tokens::kElevMarginL),
                  tokens::scaled(tokens::kElevMarginT),
                  width()  - tokens::scaled(tokens::kElevMarginL) - tokens::scaled(tokens::kElevMarginR),
                  height() - tokens::scaled(tokens::kElevMarginT) - tokens::scaled(tokens::kElevMarginB));
}

QPointF ElevationPlot::pointAt(int i) const {
    if (i < 0 || i >= samples_.size()) return QPointF();
    QRectF pr = plotRect();
    qint64 total = samples_.first().first.msecsTo(samples_.last().first);
    double fx = 0.0;
    if (total > 0)
        fx = double(samples_.first().first.msecsTo(samples_[i].first)) / double(total);
    double x = pr.left() + fx * pr.width();
    double y = pr.top() + (1.0 - samples_[i].second / 90.0) * pr.height();
    return QPointF(x, y);
}

void ElevationPlot::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);
    p.fillRect(rect(), QColor(tokens::kCard1));

    QFont f = font();
    f.setPointSize(tokens::kFontAuxPt);
    p.setFont(f);
    const QFontMetrics fm(f);

    QRectF pr = plotRect();
    const qreal gutterL  = tokens::scaled(tokens::kElevMarginL);
    const qreal gutterB  = tokens::scaled(tokens::kElevMarginB);

    // --- Y frame + elevation grid + labels (0/30/60/90) ------------------
    p.setPen(QPen(tokens::rgbaA(tokens::kGraticuleAlpha), 1.0));
    p.drawRect(pr);
    for (int el = 0; el <= 90; el += 30) {
        double y = pr.top() + (1.0 - el / 90.0) * pr.height();
        if (el != 0 && el != 90) {
            QPen grid(tokens::rgbaA(tokens::kGraticuleAlpha), 1.0);
            grid.setStyle(Qt::DashLine);
            p.setPen(grid);
            p.drawLine(QPointF(pr.left(), y), QPointF(pr.right(), y));
        }
        p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
        p.drawText(QRectF(0, y - tokens::scaled(7), gutterL - tokens::scaled(6), tokens::scaled(14)),
                   Qt::AlignRight | Qt::AlignVCenter, QString("%1°").arg(el));
    }
    // Y axis unit.
    p.setPen(tokens::rgbaA(tokens::kTickLabelAlpha));
    p.drawText(QRectF(0, 0, gutterL, tokens::scaled(14)),
               Qt::AlignRight | Qt::AlignVCenter, QStringLiteral("仰角/°"));

    // --- Title (pass name), top-left -------------------------------------
    p.setPen(QColor(tokens::kTextPrimary));
    p.drawText(QRectF(pr.left(), 0, tokens::scaled(140), tokens::scaled(tokens::kElevMarginT) - tokens::scaled(4)),
               Qt::AlignLeft | Qt::AlignVCenter, name_);

    if (isEmpty()) {
        p.setPen(tokens::rgbaA(tokens::kTextAlphaTertiary));
        p.drawText(pr, Qt::AlignCenter, QStringLiteral("无仰角数据"));
        return;
    }

    // --- X time ticks across AOS..LOS: ends anchored so nothing clipped --
    QDateTime t0 = samples_.first().first;
    QDateTime t1 = samples_.last().first;
    const int ticks = 5;
    const qreal labW = tokens::scaled(64);
    for (int i = 0; i < ticks; ++i) {
        double fx = double(i) / double(ticks - 1);
        QDateTime t = t0.addMSecs(int(fx * t0.msecsTo(t1)));
        double x = pr.left() + fx * pr.width();
        p.setPen(QPen(tokens::rgbaA(tokens::kGraticuleAlpha), 1.0));
        p.drawLine(QPointF(x, pr.bottom()), QPointF(x, pr.bottom() + tokens::scaled(4)));
        const QString lab = t.toUTC().toString("HH:mm:ss");
        QRectF tb;
        Qt::Alignment al;
        if (i == 0)             { tb = QRectF(x, pr.bottom() + tokens::scaled(6), labW, tokens::scaled(14)); al = Qt::AlignLeft; }
        else if (i == ticks-1)  { tb = QRectF(x - labW, pr.bottom() + tokens::scaled(6), labW, tokens::scaled(14)); al = Qt::AlignRight; }
        else                    { tb = QRectF(x - labW/2, pr.bottom() + tokens::scaled(6), labW, tokens::scaled(14)); al = Qt::AlignHCenter; }
        p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
        p.drawText(tb, al | Qt::AlignTop, lab);
    }
    // X axis caption.
    p.setPen(tokens::rgbaA(tokens::kTickLabelAlpha));
    p.drawText(QRectF(pr.left(), height() - gutterB + tokens::scaled(20), pr.width(), tokens::scaled(14)),
               Qt::AlignHCenter | Qt::AlignTop, QStringLiteral("UTC 时间（AOS → LOS）"));

    // --- The elevation curve ---------------------------------------------
    QPolygonF poly;
    for (int i = 0; i < samples_.size(); ++i) poly << pointAt(i);
    p.setPen(QPen(QColor(tokens::kAccent), 1.6));
    p.setBrush(Qt::NoBrush);
    p.drawPolyline(poly);

    // AOS / LOS endpoint tags.
    p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
    QPointF pa = pointAt(0), pe = pointAt(samples_.size()-1);
    p.drawText(QRectF(pa.x(), pa.y() - tokens::scaled(16), tokens::scaled(60), tokens::scaled(12)),
               Qt::AlignLeft | Qt::AlignBottom, QStringLiteral("AOS"));
    p.drawText(QRectF(pe.x() - tokens::scaled(60), pe.y() - tokens::scaled(16), tokens::scaled(60), tokens::scaled(12)),
               Qt::AlignRight | Qt::AlignBottom, QStringLiteral("LOS"));

    // --- Max-elevation marker --------------------------------------------
    int peak = peakIndex();
    if (peak >= 0) {
        QPointF q = pointAt(peak);
        QPen dash(QColor(tokens::kWarning), 1.0);
        dash.setStyle(Qt::DashLine);
        p.setPen(dash);
        p.drawLine(QPointF(q.x(), pr.bottom()), QPointF(q.x(), q.y()));
        p.setBrush(QColor(tokens::kWarning));
        p.setPen(Qt::NoPen);
        p.drawEllipse(q, tokens::scaled(4), tokens::scaled(4));
        p.setPen(QPen(QColor(tokens::kWarning)));
        p.drawText(QRectF(q.x() + tokens::scaled(6), q.y() - tokens::scaled(16),
                           tokens::scaled(90), tokens::scaled(14)),
                   Qt::AlignLeft | Qt::AlignBottom,
                   QString("max %1°").arg(samples_[peak].second, 0, 'f', 1));
    }

    // --- Legend (top-right inside plot) ----------------------------------
    const qreal lx = pr.right() - tokens::scaled(110);
    qreal ly = pr.top() + tokens::scaled(4);
    p.setPen(QPen(QColor(tokens::kAccent), 1.6));
    p.drawLine(QPointF(lx, ly + tokens::scaled(5)), QPointF(lx + tokens::scaled(16), ly + tokens::scaled(5)));
    p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
    p.drawText(QRectF(lx + tokens::scaled(20), ly, tokens::scaled(40), tokens::scaled(12)),
               Qt::AlignLeft | Qt::AlignVCenter, QStringLiteral("仰角"));
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(tokens::kWarning));
    p.drawEllipse(QPointF(lx + tokens::scaled(66), ly + tokens::scaled(5)), tokens::scaled(3), tokens::scaled(3));
    p.setPen(tokens::rgbaA(tokens::kTextAlphaSecondary));
    p.drawText(QRectF(lx + tokens::scaled(72), ly, tokens::scaled(30), tokens::scaled(12)),
               Qt::AlignLeft | Qt::AlignVCenter, QStringLiteral("顶点"));
}

} // namespace ui
} // namespace mbdsdr
