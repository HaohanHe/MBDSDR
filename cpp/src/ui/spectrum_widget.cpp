// SPDX-License-Identifier: MIT
#include "spectrum_widget.h"

#include <QPainter>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QComboBox>
#include <QLabel>
#include <QPen>
#include <QPainterPath>

#include "core/tokens.h"

#include <cmath>

namespace mbdsdr {
namespace ui {

SpectrumWidget::SpectrumWidget(QWidget* parent)
    : QWidget(parent)
{
    setMinimumSize(480, 320);
    setAutoFillBackground(true);

    auto* outer = new QVBoxLayout(this);
    outer->setContentsMargins(8, 8, 8, 8);
    outer->setSpacing(4);

    auto* topRow = new QHBoxLayout();
    testLabel_ = new QLabel("TEST SIGNAL — 非硬件实时数据 / NOT HARDWARE", this);
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
    infoLabel_->setText(QString("Fs=%1 MHz  F0=%2 MHz  N=%3%4")
                        .arg(fs / 1e6, 0, 'f', 1)
                        .arg(f0 / 1e6, 0, 'f', 1)
                        .arg(frame.fftSize)
                        .arg(frame.isTestSignal ? "  [TEST]" : ""));
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

    // Grid: kDivider
    QPen gridPen(QColor(QString::fromUtf8(tokens::kDivider)), 1, Qt::DotLine);
    p.setPen(gridPen);

    // dBFS range -- physical/algorithm constant, documented:
    // full-scale sine peak ≈ 0 dBFS; noise floor ~-80..-90 dBFS, so -100 is a sensible floor.
    const float yMin = -100.0f;
    const float yMax = 0.0f;
    for (float db = -20.0f; db >= yMin; db -= 20.0f) {
        int y = mT + static_cast<int>(plotH * (1.0f - (db - yMin) / (yMax - yMin)));
        p.drawLine(mL, y, w - mR, y);
        p.setPen(QColor(QString::fromUtf8(tokens::kTextWeak)));
        p.drawText(0, y - 6, mL - 4, 12, Qt::AlignRight | Qt::AlignVCenter,
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
        p.setPen(QColor(QString::fromUtf8(tokens::kTextWeak)));
        p.drawText(x - 40, mT + plotH + 4, 80, 16,
                   Qt::AlignCenter, QString("%1M").arg(freq / 1e6, 0, 'f', 1));
        p.setPen(gridPen);
    }

    p.setPen(QPen(QColor(QString::fromUtf8(tokens::kDivider)), 1));
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

    // Watermark: TEST SIGNAL (warning amber from tokens)
    p.setPen(QColor(QString::fromUtf8(tokens::kTestWarn)));
    QFont wf = font();
    wf.setBold(true);
    p.setFont(wf);
    p.drawText(mL + 8, mT + 16, "TEST SIGNAL - NOT HARDWARE");
}

} // namespace ui
} // namespace mbdsdr
