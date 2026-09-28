// SPDX-License-Identifier: MIT
#include "ui/constellation_view.h"

#include "core/tokens.h"

#include <QPainter>
#include <QPaintEvent>
#include <QResizeEvent>
#include <QFont>
#include <QFontMetrics>

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace ui {

namespace {
// Local geometry constants (base px, scaled() at runtime per DESIGN_RULES).
constexpr int kPadSide   = 14;   // inset from widget edges
constexpr int kPadTop    = 18;
constexpr int kPadBottom = 22;
constexpr int kDotR      = 2;    // scatter dot radius
constexpr int kRefR      = 4;    // ideal reference point radius
constexpr int kGridRings = 2;    // extra concentric rings inside unit circle
} // namespace

ConstellationView::ConstellationView(QWidget* parent) : QWidget(parent) {
    setMinimumSize(tokens::scaled(180), tokens::scaled(180));
    recomputeLayout();
}

void ConstellationView::setMode(dsp::DigMode m) {
    mode_ = m;
    update();
}

void ConstellationView::recomputeLayout() {
    int w = width();
    int h = height();
    if (w < 10) w = 10;
    if (h < 10) h = 10;

    int padL = tokens::scaled(kPadSide);
    int padR = tokens::scaled(kPadSide);
    int padT = tokens::scaled(kPadTop);
    int padB = tokens::scaled(kPadBottom);

    int plotW = w - padL - padR;
    int plotH = h - padT - padB;
    int r = std::min(plotW, plotH) / 2;
    if (r < 10) r = 10;

    center_ = QPointF(padL + plotW / 2.0, padT + plotH / 2.0);
    radius_ = r;
}

void ConstellationView::resizeEvent(QResizeEvent* /*e*/) {
    recomputeLayout();
}

int ConstellationView::xOfI(float i) const {
    return static_cast<int>(center_.x() + i * radius_ / rmsRadius_);
}
int ConstellationView::yOfQ(float q) const {
    // Q positive upward -> screen y down.
    return static_cast<int>(center_.y() - q * radius_ / rmsRadius_);
}
float ConstellationView::iOfX(int x) const {
    return static_cast<float>((x - center_.x()) * rmsRadius_ / radius_);
}
float ConstellationView::qOfY(int y) const {
    return static_cast<float>((center_.y() - y) * rmsRadius_ / radius_);
}

void ConstellationView::clear() {
    points_.clear();
    showNotHwTag_ = false;
    evmPct_ = 0.0f;
    evmN_   = 0;
    rmsRadius_ = 1.0f;
    update();
}

float ConstellationView::nearestIdealDist(std::complex<float> p) const {
    auto refs = dsp::DigitalDemod::idealPoints(mode_);
    float best = 1e9f;
    for (auto r : refs) {
        float d = std::abs(p - r);
        if (d < best) best = d;
    }
    return best;
}

void ConstellationView::pushPoint(std::complex<float> p) {
    // age existing points
    for (auto& ap : points_) ap.age++;
    points_.push_back({p, 0});
    while (static_cast<int>(points_.size()) > kMaxPoints) points_.pop_front();

    // update RMS radius (leaky average)
    float mag = std::abs(p);
    if (mag > 1e-4f) {
        rmsRadius_ += 0.05f * (mag - rmsRadius_);
        if (rmsRadius_ < 0.1f) rmsRadius_ = 0.1f;
    }

    // EVM vs nearest ideal point, as % of ideal unit radius.
    float d = nearestIdealDist(p);
    evmPct_ = evmPct_ * 0.995f + (100.0f * d) * 0.005f;
}

void ConstellationView::feedSymbols(const std::vector<std::complex<float>>& symbols,
                                    bool isHardware) {
    if (!isHardware) showNotHwTag_ = true;
    for (auto s : symbols) pushPoint(s);
    update();
}

QString ConstellationView::evmText() const {
    if (points_.empty()) return QString::fromUtf8("—");
    return QString::asprintf("EVM %.1f%%", evmPct_);
}

void ConstellationView::paintEvent(QPaintEvent* /*event*/) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);

    // Background
    p.fillRect(rect(), QColor(tokens::kSpectrumBg));

    QPen gridPen(QColor(tokens::textRgba(tokens::kTextAlphaQuaternary)));
    gridPen.setWidthF(1.0);
    p.setPen(gridPen);

    // Axes through center
    p.drawLine(static_cast<int>(center_.x() - radius_), static_cast<int>(center_.y()),
               static_cast<int>(center_.x() + radius_), static_cast<int>(center_.y()));
    p.drawLine(static_cast<int>(center_.x()), static_cast<int>(center_.y() - radius_),
               static_cast<int>(center_.x()), static_cast<int>(center_.y() + radius_));

    // Concentric rings (unit circle highlighted)
    for (int k = 1; k <= kGridRings + 1; ++k) {
        double r = radius_ * k / (kGridRings + 1);
        if (k == kGridRings + 1) {
            QPen unitPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary2)));
            unitPen.setWidthF(1.2);
            p.setPen(unitPen);
        } else {
            p.setPen(gridPen);
        }
        p.drawEllipse(center_, r, r);
    }

    // Ideal reference points
    auto refs = dsp::DigitalDemod::idealPoints(mode_);
    QPen refPen(QColor(tokens::kAccent));
    refPen.setWidthF(1.4);
    p.setPen(refPen);
    p.setBrush(Qt::NoBrush);
    for (auto r : refs) {
        int x = xOfI(r.real());
        int y = yOfQ(r.imag());
        p.drawRect(x - tokens::scaled(kRefR), y - tokens::scaled(kRefR),
                    tokens::scaled(kRefR) * 2, tokens::scaled(kRefR) * 2);
    }

    // Scatter points (age-faded)
    int dotR = tokens::scaled(kDotR);
    for (const auto& ap : points_) {
        float a = std::max(0.12f, 1.0f - static_cast<float>(ap.age) / kMaxPoints);
        QColor c(tokens::kSuccess);
        c.setAlphaF(a);
        p.setPen(Qt::NoPen);
        p.setBrush(c);
        p.drawEllipse(QPointF(xOfI(ap.value.real()), yOfQ(ap.value.imag())),
                      dotR, dotR);
    }

    // Empty-state hint (centered small text)
    if (points_.empty()) {
        QFont f = font();
        f.setPointSizeF(tokens::kFontAuxPt);
        p.setFont(f);
        p.setPen(QColor(tokens::kTextSecondary));
        p.drawText(rect(), Qt::AlignCenter, emptyHintText());
    } else {
        // EVM read-out (top-left)
        QFont f = font();
        f.setPointSizeF(tokens::kFontAuxPt);
        p.setFont(f);
        p.setPen(QColor(tokens::kTextSecondary));
        int pad = tokens::scaled(tokens::kSpacingS);
        p.drawText(pad, pad + tokens::scaled(10), evmText());
    }

    // NOT HARDWARE tag (top-right)
    if (showNotHwTag_) {
        QFont f = font();
        f.setPointSizeF(tokens::kFontAuxPt);
        p.setFont(f);
        QString tag = nonHardwareTagText();
        QFontMetrics fm(f);
        int tw = fm.horizontalAdvance(tag) + tokens::scaled(tokens::kSpacingM);
        int th = fm.height() + tokens::scaled(tokens::kSpacingS);
        QRect tagRect(width() - tw - tokens::scaled(tokens::kSpacingM),
                      tokens::scaled(tokens::kSpacingS), tw, th);
        QColor bg(tokens::kWarning);
        bg.setAlphaF(0.18);
        p.setPen(Qt::NoPen);
        p.setBrush(bg);
        p.drawRoundedRect(tagRect, tokens::scaled(2), tokens::scaled(2));
        p.setPen(QColor(tokens::kWarning));
        p.drawText(tagRect, Qt::AlignCenter, tag);
    }
}

} // namespace ui
} // namespace mbdsdr
