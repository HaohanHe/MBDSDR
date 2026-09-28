// SPDX-License-Identifier: MIT
#pragma once

#include <QWidget>
#include <QList>
#include <QString>
#include <QPointF>
#include <QDateTime>

namespace mbdsdr {
namespace ui {

// Elevation-vs-time curve for the selected pass.
// Y axis: elevation 0..90 deg. X axis: time, spanning EXACTLY
// [samples.first().time, samples.last().time] == AOS .. LOS (UTC).
class ElevationPlot : public QWidget {
    Q_OBJECT
public:
    explicit ElevationPlot(QWidget* parent = nullptr);

    // Feed the pass to plot. samples = parallel (UTC time, elevation deg).
    // The first sample is the AOS edge, the last is the LOS edge. An empty
    // list clears the plot.
    void setPass(const QString& name, const QList<QPair<QDateTime, double>>& samples);
    void clear();

    bool isEmpty() const { return samples_.isEmpty(); }
    QDateTime aos() const;
    QDateTime los() const;
    double maxEl() const;
    int peakIndex() const;           // index of the max-elevation sample
    QPointF pointAt(int i) const;    // mapped pixel of sample i
    QRectF plotRect() const;         // inner plot area (axes)

protected:
    void paintEvent(QPaintEvent*) override;

private:
    QString name_;
    QList<QPair<QDateTime, double>> samples_;
};

} // namespace ui
} // namespace mbdsdr
