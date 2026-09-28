// SPDX-License-Identifier: MIT
#include "elevation_plot.h"
#include "core/tokens.h"

#include <QPainter>
#include <QFontMetrics>
#include <cmath>

namespace mbdsdr {
namespace ui {

ElevationPlot::ElevationPlot(QWidget* parent) : QWidget(parent) {
    setMinimumSize(tokens::scaled(280), tokens::scaled(160));
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
    return QRectF(tokens::scaled(tokens::kPlotMarginL),
                  tokens::scaled(tokens::kPlotMarginT),
                  width()  - tokens::scaled(tokens::kPlotMarginL) - tokens::scaled(tokens::kPlotMarginR),
                  height() - tokens::scaled(tokens::kPlotMarginT) - tokens::scaled(tokens::kPlotMarginB));
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
    QFontMetrics fm(f);

    QRectF pr = plotRect();

    // --- Y grid + elevation labels (0/30/60/90) -------------------------
    p.setPen(QPen(QColor(tokens::kCardEdge), 1.0));
    p.drawRect(pr);
    p.setPen(QPen(QColor(tokens::kTextSecondary)));
    for (int el = 0; el <= 90; el += 30) {
        double y = pr.top() + (1.0 - el / 90.0) * pr.height();
        if (el != 0 && el != 90) {
            QPen grid(QColor(tokens::kCardEdge), 1.0);
            grid.setStyle(Qt::DashLine);
            p.setPen(grid);
            p.drawLine(QPointF(pr.left(), y), QPointF(pr.right(), y));
        }
        p.setPen(QPen(QColor(tokens::kTextSecondary)));
        p.drawText(QRectF(pr.left() - tokens::scaled(tokens::kPlotMarginL), y - tokens::scaled(7),
                          tokens::scaled(tokens::kPlotMarginL) - tokens::scaled(6), tokens::scaled(14)),
                   Qt::AlignRight | Qt::AlignVCenter, QString("%1°").arg(el));
    }

    if (isEmpty()) {
        p.setPen(QPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary))));
        p.drawText(rect(), Qt::AlignCenter, QStringLiteral("无仰角数据"));
        return;
    }

    // --- X time ticks across AOS..LOS -----------------------------------
    QDateTime t0 = samples_.first().first;
    QDateTime t1 = samples_.last().first;
    p.setPen(QPen(QColor(tokens::kTextSecondary)));
    const int ticks = 5;
    for (int i = 0; i < ticks; ++i) {
        double fx = double(i) / double(ticks - 1);
        QDateTime t = t0.addMSecs(int(fx * t0.msecsTo(t1)));
        double x = pr.left() + fx * pr.width();
        p.drawLine(QPointF(x, pr.bottom()), QPointF(x, pr.bottom() + tokens::scaled(4)));
        QString lab = t.toUTC().toString("HH:mm:ss");
        QRectF tb(x - tokens::scaled(40), pr.bottom() + tokens::scaled(5),
                  tokens::scaled(80), tokens::scaled(14));
        p.drawText(tb, Qt::AlignHCenter | Qt::AlignTop, lab);
    }
    // Axis caption
    p.drawText(QRectF(pr.left(), pr.bottom() + tokens::scaled(20), pr.width(), tokens::scaled(14)),
               Qt::AlignHCenter | Qt::AlignTop, QStringLiteral("UTC 时间 (AOS → LOS)"));

    // --- The elevation curve --------------------------------------------
    QPolygonF poly;
    for (int i = 0; i < samples_.size(); ++i) poly << pointAt(i);
    p.setPen(QPen(QColor(tokens::kAccent), 1.6));
    p.setBrush(Qt::NoBrush);
    p.drawPolyline(poly);

    // --- Max-elevation marker ------------------------------------------
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
        p.drawText(q + QPointF(tokens::scaled(6), -tokens::scaled(6)),
                   QString("max %1°").arg(samples_[peak].second, 0, 'f', 1));
    }

    // --- Title (pass name) ----------------------------------------------
    p.setPen(QPen(QColor(tokens::kTextPrimary)));
    p.drawText(QRectF(pr.left(), 0, pr.width(), tokens::scaled(tokens::kPlotMarginT) - tokens::scaled(4)),
               Qt::AlignCenter | Qt::AlignVCenter, name_);
}

} // namespace ui
} // namespace mbdsdr
