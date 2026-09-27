// SPDX-License-Identifier: MIT
#pragma once

#include <QWidget>
#include <QList>
#include <QString>
#include <QPointF>
#include <limits>

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
    void setStation(double lat, double lon) { stationLat_ = lat; stationLon_ = lon; update(); }

protected:
    void paintEvent(QPaintEvent*) override;

private:
    QList<AircraftPoint> aircraft_;
    double stationLat_ = std::numeric_limits<double>::quiet_NaN();
    double stationLon_ = std::numeric_limits<double>::quiet_NaN();
    QPointF latLonToPx(double lat, double lon);
};

} // namespace ui
} // namespace mbdsdr
