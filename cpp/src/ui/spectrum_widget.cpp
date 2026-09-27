// SPDX-License-Identifier: MIT
#include "spectrum_widget.h"

#include <QPainter>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QComboBox>
#include <QLabel>
#include <QPen>
#include <QPainterPath>

#include <QMouseEvent>
#include "core/tokens.h"

#include <cmath>

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

    infoLabel_ = new QLabel(this);
    infoLabel_->setObjectName("monoInfo");
    topRow->addWidget(infoLabel_);

    outer->addLayout(topRow);
    outer->addStretch();
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

    // dBFS range -- physical/algorithm constant, documented:
    // full-scale sine peak ≈ 0 dBFS; noise floor ~-80..-90 dBFS, so -100 is a sensible floor.
    const float yMin = -100.0f;
    const float yMax = 0.0f;
    for (float db = -20.0f; db >= yMin; db -= 20.0f) {
        int y = mT + static_cast<int>(plotH * (1.0f - (db - yMin) / (yMax - yMin)));
        p.drawLine(mL, y, w - mR, y);
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        p.drawText(0, y - tokens::scaled(tokens::kDbLabelOffsetY),
                   mL - tokens::scaled(tokens::kDbLabelPadR),
                   tokens::scaled(tokens::kDbLabelH),
                   Qt::AlignRight | Qt::AlignVCenter,
                   QString("%1").arg(db, 0, 'f', 0));
        p.setPen(gridPen);
    }

    // Frequency grid
    const double fs = frame_.sampleRateHz;
    const double f0 = frame_.centerFreqHz;
    for (int i = 0; i <= 4; ++i) {
        int x = mL + static_cast<int>(plotW * i / 4.0);
        p.drawLine(x, mT, x, mT + plotH);
        double freq = f0 - fs / 2.0 + fs * i / 4.0;
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

    // Spectrum trace: kAccent
    const std::size_t n = frame_.dbfs.size();
    if (n >= 2) {
        QPen tracePen(QColor(QString::fromUtf8(tokens::kAccent)), 1);
        p.setPen(tracePen);
        QPainterPath path;
        for (std::size_t i = 0; i < n; ++i) {
            int x = mL + static_cast<int>(plotW * i / (n - 1));
            float v = frame_.dbfs[i];
            if (v < yMin) v = yMin;
            if (v > yMax) v = yMax;
            int y = mT + static_cast<int>(plotH * (1.0f - (v - yMin) / (yMax - yMin)));
            if (i == 0) path.moveTo(x, y);
            else path.lineTo(x, y);
        }
        p.drawPath(path);
    }

    // VFO center line
    QPen vfoPen(QColor(tokens::kAccent));
    vfoPen.setWidthF(tokens::kVfoLineWidth);
    p.setPen(vfoPen);
    p.drawLine(width() / 2, mT, width() / 2, mB);

    // Crosshair tooltip
    if (hoverPos_.x() >= 0) {
        p.setPen(QPen(QColor(tokens::kAccent), 1, Qt::DashLine));
        p.drawLine(hoverPos_.x(), mT, hoverPos_.x(), mB);
        p.drawLine(mL, hoverPos_.y(), mR, hoverPos_.y());
        double xRatio = static_cast<double>(hoverPos_.x()) / width();
        double freq = frame_.centerFreqHz + (xRatio - 0.5) * frame_.sampleRateHz;
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
    double xRatio = static_cast<double>(e->position().x()) / width();
    double freq = frame_.centerFreqHz + (xRatio - 0.5) * frame_.sampleRateHz;
    vfoFreq_ = freq;
    emit frequencyChanged(freq);
    update();
}

void SpectrumWidget::mouseReleaseEvent(QMouseEvent*) {
    dragging_ = false;
}

void SpectrumWidget::leaveEvent(QEvent*) {
    hoverPos_ = QPoint(-1, -1);
    update();
}

} // namespace ui
} // namespace mbdsdr
