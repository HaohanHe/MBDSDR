// SPDX-License-Identifier: MIT
//
// Offline world map canvas (QPainter + plate-carree projection).
//
// Honest-data policy: the widget ONLY paints positions fed in through its
// setters. It never synthesizes fake "live-looking" points. When GNSS has no
// fix it explicitly states "GNSS 无定位" and draws no receiver dot; when
// aircraft/satellite lists are empty it draws nothing for them. When fed
// hand-built / synthetic fixtures (setSynthetic(true)) it stamps a small
// "非硬件 NOT HARDWARE" tag so a test frame can never be mistaken for live
// hardware.
//
// Base map = offline embedded Natural Earth 110m coastline (public domain),
// no network required. Online OSM tiles are an optional, off-by-good look
// hook (setOnlineTilesEnabled) and never block the offline core.
#pragma once

#include <QWidget>
#include <QList>
#include <QString>
#include <QPointF>
#include <QPair>
#include <QDateTime>
#include <limits>

#include "map_projection.h"

namespace mbdsdr {
namespace ui {

// ADS-B aircraft fix: callsign, barometric altitude (ft), true heading deg,
// and an optional recent position tail (track) for a small trailing streak.
struct AircraftPoint {
    QString icao;
    QString callsign;
    double lat = 0.0;
    double lon = 0.0;
    int    altitudeFt = 0;
    double headingDeg = 0.0;
    QList<QPair<double, double>> track;   // (lat, lon) tail, oldest..newest
};

// Live satellite sub-satellite point: name (TLE name = sync key with sky
// view), ground-track polyline, and selection flag.
struct SatellitePoint {
    QString name;
    double lat = 0.0;
    double lon = 0.0;
    bool selected = false;
    QList<QPair<double, double>> track;   // (lat, lon) ground track
};

enum class MapLayer {
    Coastline = 0,
    Graticule,
    Station,
    Gnss,
    Aircraft,
    Satellite
};

class WorldView : public QWidget {
    Q_OBJECT
public:
    explicit WorldView(QWidget* parent = nullptr);

    // ---- data feeders (real data only) -----------------------------------
    void setAircraft(QList<AircraftPoint> ac);
    // Incremental ADS-B hook: upsert by icao. Extra fields stay at defaults
    // unless the richer setAircraft() form is used.
    void addAircraft(const QString& icao, double lat, double lon);
    void setSatellites(QList<SatellitePoint> sat);
    // Incremental TLE hook: upsert by name (name = sky-view sync key).
    void addSatellite(const QString& name, double lat, double lon);
    void setStation(double lat, double lon);   // NaN to clear the station.
    // GNSS receiver fix. valid=false => no receiver dot, honest "GNSS 无定位".
    // fixTimeUtc is the real NMEA (GGA/RMC) fix timestamp; an invalid time is
    // shown as "--" (never fabricated).
    void setGnssFix(bool valid, double lat, double lon, int sats, double hdop,
                    QDateTime fixTimeUtc = QDateTime());

    // Mark whether the current data is synthetic/offline (stamps the
    // "非硬件 NOT HARDWARE" tag). Default false.
    void setSynthetic(bool s) { synthetic_ = s; update(); }
    bool isSynthetic() const { return synthetic_; }

    // ---- layer visibility ------------------------------------------------
    void setLayerVisible(MapLayer id, bool on);
    bool layerVisible(MapLayer id) const;

    // Online OSM tiles are optional and off by default; the offline vector
    // coastline is the always-complete base. No API key is bundled.
    void setOnlineTilesEnabled(bool /*on*/) { /* hook: offline core is default */ }

    // ---- view state (persistence) ---------------------------------------
    MapProjection::ViewState viewState() const { return proj_.viewState(); }
    void setViewState(const MapProjection::ViewState& v) { proj_.setViewState(v); update(); }
    void resetView();

    // Geometry exposed for tests.
    const MapProjection& projection() const { return proj_; }
    QPointF project(double lat, double lon) const { return proj_.project(lat, lon); }

    // Test helper: whether a GNSS fix is currently accepted (no fix => no dot).
    bool gnssHasFixForTest() const { return gnssValid_; }

    // Test / host readback: the legend row captions (colored swatch + label).
    // Exposed so a test can assert the legend文案 exists without OCR-ing pixels.
    QStringList legendItems() const;

    // ---- hit testing -----------------------------------------------------
    struct Hit {
        enum Type { None, Station, Gnss, Aircraft, Satellite } type = None;
        QString id;          // icao / satellite name / "station" / "gnss"
        QString tooltip;     // real fields, human readable
    };
    Hit hitTest(const QPointF& pos) const;

signals:
    // Emitted only when the user clicks a satellite (external sync to sky view).
    // The TLE name is the unique key; empty string means "nothing selected".
    void satelliteSelected(const QString& name);

public slots:
    // External selection sync (e.g. from sky view). Empty string clears.
    // Does NOT re-emit satelliteSelected (avoids signal loops).
    void setSelectedSatellite(const QString& name);

protected:
    void paintEvent(QPaintEvent*) override;
    void resizeEvent(QResizeEvent*) override;
    void mousePressEvent(QMouseEvent* e) override;
    void mouseMoveEvent(QMouseEvent* e) override;
    void mouseReleaseEvent(QMouseEvent* e) override;
    void wheelEvent(QWheelEvent* e) override;
    void contextMenuEvent(QContextMenuEvent* e) override;

private:
    void drawGraticule(QPainter& p);
    void drawCoastline(QPainter& p);
    void drawStation(QPainter& p);
    void drawGnss(QPainter& p);
    void drawAircraft(QPainter& p);
    void drawSatellites(QPainter& p);
    void drawLabels(QPainter& p);
    void drawStatusChips(QPainter& p);
    void drawLegend(QPainter& p);
    void applySelectionFlags();

    MapProjection proj_;

    QList<AircraftPoint> aircraft_;
    QList<SatellitePoint> satellites_;
    double stationLat_ = std::numeric_limits<double>::quiet_NaN();
    double stationLon_ = std::numeric_limits<double>::quiet_NaN();

    bool gnssValid_ = false;
    double gnssLat_ = 0.0, gnssLon_ = 0.0;
    int    gnssSats_ = 0;
    double gnssHdop_ = 0.0;
    QDateTime gnssFixTime_;   // real NMEA fix time (invalid => show "--")

    bool synthetic_ = false;
    bool layerOn_[6] = {true, true, true, true, true, true};

    bool panning_ = false;
    QPoint lastMouse_;
    QPoint pressPos_;
    bool moved_ = false;
};

} // namespace ui
} // namespace mbdsdr
