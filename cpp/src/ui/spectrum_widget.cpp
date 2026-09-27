// SPDX-License-Identifier: MIT
#include "spectrum_widget.h"

#include <QPainter>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QComboBox>
#include <QSpinBox>
#include <QLabel>
#include <QPen>
#include <QPainterPath>
#include <QMenu>

#include <QMouseEvent>
#include <QWheelEvent>
#include <QContextMenuEvent>
#include <QtGlobal>
#include "core/tokens.h"

#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace ui {

SpectrumWidget::SpectrumWidget(QWidget* parent)
    : QWidget(parent)
{
    setMinimumSize(tokens::scaled(tokens::kSpectrumMinW),
                   tokens::scaled(tokens::kSpectrumMinH));
    setAutoFillBackground(true);

    auto* outer = new QVBoxLayout(this);
    outer->setContentsMargins(tokens::scaled(tokens::kSpectrumPad),
                              tokens::scaled(tokens::kSpectrumPad),
                              tokens::scaled(tokens::kSpectrumPad),
                              tokens::scaled(tokens::kSpectrumPad));
    outer->setSpacing(tokens::scaled(tokens::kSpectrumSpacing));

    auto* topRow = new QHBoxLayout();
    testLabel_ = new QLabel("", this);
    testLabel_->setObjectName("testBanner");
    topRow->addWidget(testLabel_);
    topRow->addStretch();

    topRow->addWidget(new QLabel("FFT:", this));
    fftCombo_ = new QComboBox(this);
    fftCombo_->addItems({"1024", "2048", "4096"});
    fftCombo_->setCurrentIndex(1);
    connect(fftCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                switch (idx) {
                    case 0: emit fftSizeRequested(1024); break;
                    case 1: emit fftSizeRequested(2048); break;
                    case 2: emit fftSizeRequested(4096); break;
                }
            });
    topRow->addWidget(fftCombo_);

    // Adjustable dB range (vertical scale).
    topRow->addWidget(new QLabel("dB", this));
    dbMinSpin_ = new QSpinBox(this);
    dbMinSpin_->setRange(tokens::kDbSpinLowerMin, tokens::kDbSpinLowerMax);
    dbMinSpin_->setValue(tokens::kDbLowerDefault);
    dbMinSpin_->setSuffix("");
    topRow->addWidget(dbMinSpin_);
    dbMaxSpin_ = new QSpinBox(this);
    dbMaxSpin_->setRange(tokens::kDbSpinUpperMin, tokens::kDbSpinUpperMax);
    dbMaxSpin_->setValue(tokens::kDbUpperDefault);
    topRow->addWidget(dbMaxSpin_);
    auto applyDb = [this]() {
        int lo = dbMinSpin_->value();
        int hi = dbMaxSpin_->value();
        if (lo >= hi) {  // keep a sane range; nudge the other bound
            hi = lo + 10;
            if (hi > tokens::kDbSpinUpperMax) { hi = tokens::kDbSpinUpperMax; lo = hi - 10; }
            dbMaxSpin_->blockSignals(true); dbMaxSpin_->setValue(hi); dbMaxSpin_->blockSignals(false);
            dbMinSpin_->blockSignals(true); dbMinSpin_->setValue(lo); dbMinSpin_->blockSignals(false);
        }
        setDbRange(static_cast<float>(lo), static_cast<float>(hi));
    };
    connect(dbMinSpin_, QOverload<int>::of(&QSpinBox::valueChanged), this, applyDb);
    connect(dbMaxSpin_, QOverload<int>::of(&QSpinBox::valueChanged), this, applyDb);

    infoLabel_ = new QLabel(this);
    infoLabel_->setObjectName("monoInfo");
    topRow->addWidget(infoLabel_);

    outer->addLayout(topRow);
    outer->addStretch();
}

void SpectrumWidget::setDbRange(float minDb, float maxDb) {
    if (maxDb <= minDb) maxDb = minDb + 1.0f;
    dbMin_ = minDb;
    dbMax_ = maxDb;
    update();
}

void SpectrumWidget::resetZoom() {
    zoomFactor_ = tokens::kZoomMin;
    update();
}

void SpectrumWidget::visibleRange(double& fLo, double& fHi, double& spanVis) const {
    const double fs = frame_.sampleRateHz;
    const double f0 = frame_.centerFreqHz;
    spanVis = fs / zoomFactor_;
    fLo = f0 - spanVis / 2.0;
    fHi = f0 + spanVis / 2.0;
}

void SpectrumWidget::setSpectrum(const SpectrumFrame& frame) {
    frame_ = frame;
    const double fs  = frame.sampleRateHz;
    const double f0  = frame.centerFreqHz;
    testLabel_->setText(frame.isTestSignal ? "测试信号（非硬件）" : "");
    infoLabel_->setText(QString("%1  Fs=%2 MHz  F0=%3 MHz  N=%4")
                        .arg(frame.sourceName.isEmpty() ? "?" : frame.sourceName)
                        .arg(fs / 1e6, 0, 'f', 1)
                        .arg(f0 / 1e6, 0, 'f', 1)
                        .arg(frame.fftSize));
    update();
}

void SpectrumWidget::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, false);

    // Background: kCard1 (from tokens)
    p.fillRect(rect(), QColor(QString::fromUtf8(tokens::kCard1)));

    const int w = width();
    const int h = height();
    const int mL = tokens::kPlotMarginL;
    const int mR = tokens::kPlotMarginR;
    const int mT = tokens::kPlotMarginT;
    const int mB = tokens::kPlotMarginB;
    const int plotW = w - mL - mR;
    const int plotH = h - mT - mB;
    if (plotW <= 10 || plotH <= 10) return;

    // Grid: kCardEdge
    QPen gridPen(QColor(QString::fromUtf8(tokens::kCardEdge)), 1, Qt::DotLine);
    p.setPen(gridPen);

    // dBFS range -- adjustable via the top-row spinboxes.
    const float yMin = dbMin_;
    const float yMax = dbMax_;
    const int step = tokens::kDbGridStep;
    for (int db = (std::ceil(yMin / step) * step); db <= static_cast<int>(yMax); db += step) {
        int y = mT + static_cast<int>(plotH * (1.0f - (db - yMin) / (yMax - yMin)));
        p.drawLine(mL, y, w - mR, y);
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        p.drawText(0, y - tokens::scaled(tokens::kDbLabelOffsetY),
                   mL - tokens::scaled(tokens::kDbLabelPadR),
                   tokens::scaled(tokens::kDbLabelH),
                   Qt::AlignRight | Qt::AlignVCenter,
                   QString("%1").arg(db));
        p.setPen(gridPen);
    }

    // Visible frequency window (zoom shrinks the span around center f0).
    double fLo, fHi, spanVis;
    visibleRange(fLo, fHi, spanVis);
    const double fs = frame_.sampleRateHz;
    const double f0 = frame_.centerFreqHz;

    // Frequency grid -- recomputed for the zoomed-in window.
    for (int i = 0; i <= 4; ++i) {
        int x = mL + static_cast<int>(plotW * i / 4.0);
        p.drawLine(x, mT, x, mT + plotH);
        double freq = fLo + spanVis * i / 4.0;
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        p.drawText(x - tokens::scaled(tokens::kFreqLabelHalfW),
                   mT + plotH + tokens::scaled(tokens::kFreqLabelOffsetY),
                   tokens::scaled(tokens::kFreqLabelW),
                   tokens::scaled(tokens::kFreqLabelH),
                   Qt::AlignCenter,
                   QString("%1M").arg(freq / 1e6, 0, 'f', 1));
        p.setPen(gridPen);
    }

    p.setPen(QPen(QColor(QString::fromUtf8(tokens::kCardEdge)), 1));
    p.drawRect(mL, mT, plotW, plotH);

    // Spectrum trace: kAccent -- draw only the bins inside the visible window.
    const std::size_t n = frame_.dbfs.size();
    if (n >= 2 && fs > 0.0) {
        const double fLowEdge = f0 - fs / 2.0;   // bin 0 frequency (already fft-shifted)
        const double iLoF = (fLo - fLowEdge) / fs * (n - 1);
        const double iHiF = (fHi - fLowEdge) / fs * (n - 1);
        int iLo = static_cast<int>(std::floor(iLoF));
        int iHi = static_cast<int>(std::ceil(iHiF));
        iLo = std::max(0, std::min(static_cast<int>(n) - 1, iLo));
        iHi = std::max(0, std::min(static_cast<int>(n) - 1, iHi));

        QPen tracePen(QColor(QString::fromUtf8(tokens::kAccent)), 1);
        p.setPen(tracePen);
        QPainterPath path;
        for (int i = iLo; i <= iHi; ++i) {
            const double fi = fLowEdge + fs * i / (n - 1);
            int x = mL + static_cast<int>(plotW * (fi - fLo) / spanVis);
            float v = frame_.dbfs[i];
            if (v < yMin) v = yMin;
            if (v > yMax) v = yMax;
            int y = mT + static_cast<int>(plotH * (1.0f - (v - yMin) / (yMax - yMin)));
            if (i == iLo) path.moveTo(x, y);
            else path.lineTo(x, y);
        }
        p.drawPath(path);
    }

    // VFO center line -- always at the widget center (f0 stays the view center;
    // zoom only enlarges the view, never pans it).
    QPen vfoPen(QColor(tokens::kAccent));
    vfoPen.setWidthF(tokens::kVfoLineWidth);
    p.setPen(vfoPen);
    p.drawLine(mL + plotW / 2, mT, mL + plotW / 2, mT + plotH);

    // Crosshair tooltip
    if (hoverPos_.x() >= 0) {
        p.setPen(QPen(QColor(tokens::kAccent), 1, Qt::DashLine));
        p.drawLine(hoverPos_.x(), mT, hoverPos_.x(), mT + plotH);
        p.drawLine(mL, hoverPos_.y(), w - mR, hoverPos_.y());
        const double frac = (hoverPos_.x() - mL) / static_cast<double>(plotW);
        const double freq = fLo + frac * spanVis;
        p.drawText(hoverPos_ + QPointF(tokens::kTooltipOffset, -tokens::kTooltipOffset),
                   QString("%1 MHz").arg(freq / 1e6, 0, 'f', 3));
    }
}

void SpectrumWidget::mousePressEvent(QMouseEvent* e) {
    dragging_ = true;
    mouseMoveEvent(e);
}

void SpectrumWidget::mouseMoveEvent(QMouseEvent* e) {
    hoverPos_ = e->pos();
    if (!dragging_ || frame_.sampleRateHz <= 0) { update(); return; }
    const int mL = tokens::kPlotMarginL;
    const int mR = tokens::kPlotMarginR;
    const int plotW = width() - mL - mR;
    if (plotW <= 10) { update(); return; }
    double fLo, fHi, spanVis;
    visibleRange(fLo, fHi, spanVis);
    const double frac = (e->position().x() - mL) / plotW;
    const double freq = fLo + frac * spanVis;
    vfoFreq_ = freq;
    emit frequencyChanged(freq);
    update();
}

void SpectrumWidget::mouseReleaseEvent(QMouseEvent*) {
    dragging_ = false;
}

void SpectrumWidget::mouseDoubleClickEvent(QMouseEvent*) {
    resetZoom();
}

void SpectrumWidget::wheelEvent(QWheelEvent* e) {
    if (frame_.sampleRateHz <= 0.0) { e->ignore(); return; }
    // Wheel up = zoom in (shrink the visible span), down = zoom out.
    const double steps = e->angleDelta().y() / 120.0;
    double f = zoomFactor_ * std::pow(2.0, steps);
    zoomFactor_ = std::clamp(f, tokens::kZoomMin, tokens::kZoomMax);
    update();
    e->accept();
}

void SpectrumWidget::contextMenuEvent(QContextMenuEvent* e) {
    QMenu menu(this);
    QAction* reset = menu.addAction("重置 zoom");
    QAction* chosen = menu.exec(e->globalPos());
    if (chosen == reset) resetZoom();
}

void SpectrumWidget::leaveEvent(QEvent*) {
    hoverPos_ = QPoint(-1, -1);
    update();
}

} // namespace ui
} // namespace mbdsdr
