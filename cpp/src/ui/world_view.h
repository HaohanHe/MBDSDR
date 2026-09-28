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

struct SatellitePoint {
    QString name;
    double lat;
    double lon;
    bool selected = false;
};

class WorldView : public QWidget {
    Q_OBJECT
public:
    explicit WorldView(QWidget* parent = nullptr);
    void setAircraft(QList<AircraftPoint> ac);
    // Incremental hooks for ADS-B / downlink wiring.
    void addAircraft(const QString& icao, double lat, double lon);
    void setSatellites(QList<SatellitePoint> sat);
    void addSatellite(const QString& name, double lat, double lon);
    void setStation(double lat, double lon) { stationLat_ = lat; stationLon_ = lon; update(); }

protected:
    void paintEvent(QPaintEvent*) override;
    void mousePressEvent(QMouseEvent* e) override;
    void mouseMoveEvent(QMouseEvent* e) override;
    void mouseReleaseEvent(QMouseEvent*) override;
    void wheelEvent(QWheelEvent* e) override;
    void contextMenuEvent(QContextMenuEvent* e) override;

private:
    QList<AircraftPoint> aircraft_;
    QList<SatellitePoint> satellites_;
    double stationLat_ = std::numeric_limits<double>::quiet_NaN();
    double stationLon_ = std::numeric_limits<double>::quiet_NaN();
    QPointF latLonToPx(double lat, double lon);
    double zoom_ = 1.0;
    double panLat_ = 0.0;
    double panLon_ = 0.0;
    bool panning_ = false;
    QPoint lastPan_;
};

} // namespace ui
} // namespace mbdsdr
