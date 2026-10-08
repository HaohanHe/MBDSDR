// SPDX-License-Identifier: MIT
#include "rssi_trend.h"
#include "core/tokens.h"

#include <QPainter>
#include <cmath>

namespace mbdsdr {
namespace ui {

RssiTrendWidget::RssiTrendWidget(QWidget* parent) : QWidget(parent) {
    // Same strip height as the S-meter so the two permanent status widgets line
    // up vertically in the status bar.
    setMinimumHeight(tokens::scaled(tokens::kSMeterH));
}

void RssiTrendWidget::pushDbfs(double dbfs) {
    if (!std::isfinite(dbfs)) { clear(); return; }   // link down -> honest empty
    hist_.push_back(static_cast<float>(dbfs));
    // Trim the rolling buffer to the named depth (drop the oldest). The depth is
    // small (~120 samples) so an erase-from-front is cheap and keeps the order
    // invariant: back = newest, front = oldest.
    while (static_cast<int>(hist_.size()) > tokens::kRssiTrendMaxSamples)
        hist_.erase(hist_.begin());
    update();
}

void RssiTrendWidget::clear() {
    if (!hist_.empty()) { hist_.clear(); update(); }
}

QSize RssiTrendWidget::sizeHint() const {
    return QSize(tokens::scaled(tokens::kRssiTrendW), tokens::scaled(tokens::kSMeterH));
}

void RssiTrendWidget::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);
    const QRectF r = rect().adjusted(2, 2, -2, -2);

    // Shared card track (matches the S-meter framing).
    p.setPen(QPen(tokens::cardEdge(), 1.0));
    p.setBrush(tokens::card1());
    p.drawRoundedRect(r, tokens::scaled(4), tokens::scaled(4));

    if (hist_.empty()) {
        // Honest empty state: no real sample yet. Faint caption only -- never a
        // baseline or ghost line that would look like history.
        p.setPen(tokens::rgbaA(tokens::kTextAlphaQuaternary));
        QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
        p.drawText(r, Qt::AlignCenter, QStringLiteral("RSSI 趋势"));
        return;
    }

    // Visible dB window = real min..max of the buffered samples (auto-scale to
    // the actual recent range). A flat buffer (min==max) gets a 2 dB band so the
    // line still renders mid-height instead of degenerating to a zero-span map.
    float lo = hist_[0], hi = hist_[0];
    for (float v : hist_) { if (v < lo) lo = v; if (v > hi) hi = v; }
    if (!(hi > lo)) { lo -= 1.0f; hi += 1.0f; }
    const double span = double(hi) - double(lo);

    auto yOf = [&](float v) {
        const double t = (double(v) - double(lo)) / span;   // 0..1 (low->high)
        return r.bottom() - t * r.height();
    };

    const int n = static_cast<int>(hist_.size());
    // Inset the line a little on each side so the newest-sample dot (right end)
    // sits fully INSIDE the card rather than being clipped by its right border.
    const double pad = tokens::scaled(2.0);
    const double x0 = r.left() + pad, x1 = r.right() - pad;
    QPolygonF line;
    for (int i = 0; i < n; ++i) {
        // Oldest (front) -> left; newest (back) -> right.
        const double x = (n == 1) ? (x0 + x1) / 2.0
                                  : x0 + (x1 - x0) * double(i) / double(n - 1);
        line << QPointF(x, yOf(hist_[i]));
    }

    QColor trace(QString::fromUtf8(tokens::kRssiTrendColor));
    trace.setAlphaF(tokens::kRssiTrendLineAlpha);
    p.setPen(QPen(trace, 1.2));
    p.setBrush(Qt::NoBrush);
    p.drawPolyline(line);

    // Newest-sample marker at the right edge so the current level is readable.
    p.setPen(Qt::NoPen);
    p.setBrush(trace);
    const QPointF newest = line.last();
    p.drawEllipse(newest, tokens::scaled(1.5), tokens::scaled(1.5));
}

} // namespace ui
} // namespace mbdsdr
