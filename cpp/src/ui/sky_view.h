// SPDX-License-Identifier: MIT
#pragma once

#include <QWidget>
#include <QList>
#include <QString>
#include <QPointF>
#include <QDateTime>

namespace mbdsdr {
namespace ui {

// A sampled orbital pass across the sky. track[] runs horizon -> horizon as
// (azimuth deg, elevation deg). az: 0=N, clockwise. el: 0..90.
struct PassArc {
    QString name;
    QList<QPair<double, double>> track; // sampled (az, el) degrees
    QDateTime aosUtc;                  // acquisition of signal (UTC)
    QDateTime losUtc;                  // loss of signal (UTC)
    double maxEl = 0.0;                // peak elevation over the pass (deg)
};

// One live orbit-satellite position at the current instant.
struct LiveSat {
    double az = 0.0;
    double el = 0.0;
    QString name;
    bool selected = false; // initial hint; the authoritative selection is the
                           // widget's selectedName_ (setSelectedSatellite()).
};

// One GNSS (GSV) satellite. Independent constellation layer: rendered as
// diamonds, never participates in the orbit-satellite selection sync.
struct GnssSkySat {
    QString prn;
    double az = 0.0;
    double el = 0.0;
    double snr = 0.0; // dB-Hz
    bool used = false; // used in the navigation solution
};

// Real azimuth/elevation polar "new space-time" sky view.
// Projection convention (kept from the original widget):
//   az=0 points North (top of widget), increases clockwise;
//   r = R * (1 - el/90): el=0 -> outer ring, el=90 -> center.
class SkyView : public QWidget {
    Q_OBJECT
public:
    explicit SkyView(QWidget* parent = nullptr);

    // Pure projection: (azDeg, elDeg) -> widget pixel for the given compass
    // radius / center. Exposed so tests can assert the polar mapping without
    // relying on a painted image.
    static QPointF polarToXY(double azDeg, double elDeg, double radius, QPointF center);

    // Compas geometry for the current widget size (tests / integration).
    QPointF compassCenter() const;
    double compassRadius() const;

    void setPasses(QList<PassArc> passes);
    void setLiveSatellites(QList<LiveSat> sats);
    void clearLiveSatellites();
    void setGnssSatellites(QList<GnssSkySat> sats);
    void clearGnssSatellites();
    // Predicted (TLE/SGP4) navigation satellites currently above the horizon.
    // Distinct from real GSV diamonds: hollow rings, explicitly marked "预测" --
    // we do NOT claim these are being received. Empty list clears.
    void setPredictedNavSats(QList<LiveSat> sats);
    QList<LiveSat> predictedNavSats() const { return predictedNav_; }
    // Real propagated trajectory of the SELECTED satellite around the displayed
    // moment: sampled (az, el) deg, drawn as a dashed overlay that coexists with
    // the predicted pass arcs. Empty list clears the overlay.
    void setSelectedTrajectory(QList<QPair<double,double>> azEl);
    QList<QPair<double,double>> selectedTrajectory() const { return selectedTraj_; }
    // utc must be a UTC time; the view paints it alongside its local conversion.
    void setCurrentTime(QDateTime utc);
    void setEmptyText(const QString& text);
    // Tag the on-screen data as synthetic (annotates "非硬件/NOT HARDWARE").
    // Production feeders leave this false (default).
    void setNonHardware(bool on);

    QList<PassArc> passes() const { return passes_; }
    QList<LiveSat> liveSatellites() const { return liveSats_; }
    QList<GnssSkySat> gnssSatellites() const { return gnss_; }
    QString selectedSatellite() const { return selectedName_; }
    QDateTime currentTimeUtc() const { return nowUtc_; }
    bool isEmpty() const {
        return passes_.isEmpty() && liveSats_.isEmpty() && gnss_.isEmpty();
    }
    // Tooltip text for whatever sits under widget point p (empty if nothing).
    // Exposed for tests and for host code that wants the same strings.
    QString tooltipAt(const QPointF& p) const;

public slots:
    // Select/highlight the orbit satellite named `name`. An empty string
    // clears the selection. Does NOT re-emit satelliteSelected().
    void setSelectedSatellite(const QString& name);

signals:
    // Fired when the user clicks an orbit (TLE) satellite. The GNSS layer is
    // excluded: it only shows PRN info on hover, never syncs selection.
    void satelliteSelected(const QString& name);

protected:
    void paintEvent(QPaintEvent*) override;
    void mousePressEvent(QMouseEvent* e) override;
    void mouseMoveEvent(QMouseEvent* e) override;

private:
    struct Hit {
        enum Kind { None, Pass, Orbit, Gnss } kind = None;
        int index = -1;
    };
    Hit hitTest(const QPointF& p) const;
    QString tooltipFor(const Hit& h) const;

    QList<PassArc> passes_;
    QString emptyText_ = QStringLiteral("无过境数据");
    QList<LiveSat> liveSats_;
    QList<GnssSkySat> gnss_;
    QList<QPair<double,double>> selectedTraj_; // real propagated overlay (selected sat)
    QList<LiveSat> predictedNav_;              // TLE-predicted nav sats (hollow)
    QDateTime nowUtc_;
    QString selectedName_;
    bool nonHardware_ = false;
};

} // namespace ui
} // namespace mbdsdr
