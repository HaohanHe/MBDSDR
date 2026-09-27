// SPDX-License-Identifier: MIT
#pragma once

#include <QWidget>
#include <QList>
#include <QString>
#include <QPointF>

namespace mbdsdr {
namespace ui {

struct AircraftPoint {
    QString icao;
    QString callsign;
    double lat;
    double lon;
};

class WorldView : public QWidget {
    Q_OBJECT
public:
    explicit WorldView(QWidget* parent = nullptr);
    void setAircraft(QList<AircraftPoint> ac);

protected:
    void paintEvent(QPaintEvent*) override;

private:
    QList<AircraftPoint> aircraft_;
    QPointF latLonToPx(double lat, double lon);
};

} // namespace ui
} // namespace mbdsdr
