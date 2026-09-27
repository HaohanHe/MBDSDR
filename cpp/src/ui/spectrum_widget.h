// SPDX-License-Identifier: MIT
#pragma once

#include <QWidget>
#include <QPoint>
#include "core/spectrum_frame.h"

class QComboBox;
class QLabel;

namespace mbdsdr {
namespace ui {

class SpectrumWidget : public QWidget {
    Q_OBJECT
public:
    explicit SpectrumWidget(QWidget* parent = nullptr);

public slots:
    void setSpectrum(const SpectrumFrame& frame);

signals:
    void fftSizeRequested(int n);
    void frequencyChanged(double newFreqHz);

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void leaveEvent(QEvent* event) override;

private:
    SpectrumFrame frame_;
    QComboBox* fftCombo_   = nullptr;
    QLabel*    testLabel_  = nullptr;
    QLabel*    infoLabel_  = nullptr;
    bool dragging_ = false;
    double vfoFreq_ = 0;
    QPoint hoverPos_ = QPoint(-1, -1);
};

} // namespace ui
} // namespace mbdsdr
