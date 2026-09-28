// SPDX-License-Identifier: MIT
// Throwaway offscreen screenshot generator for the integrated map (3 layers)
// and sky (timing annotation + elevation curve). All data fed here is
// hand-built SYNTHETIC sample data -- every frame is stamped
// "录制样例·非硬件 NOT HARDWARE". No real hardware / live position.
#include "ui/world_view.h"
#include "ui/sky_view.h"
#include "ui/elevation_plot.h"

#include <QApplication>
#include <QWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QDateTime>
#include <QImage>
#include <cmath>

using namespace mbdsdr::ui;

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    const QString outDir = QString::fromLatin1(argv[1]);

    // ---------------- MAP: station + GNSS fix + ADS-B + satellites ---------
    {
        WorldView v;
        v.resize(1000, 620);
        v.setSynthetic(true);                 // stamps 非硬件 NOT HARDWARE
        v.setStation(39.9, 116.4);            // hand-entered station (sample)
        v.setGnssFix(true, 39.91, 116.39, 14, 0.8);

        AircraftPoint ac1; ac1.icao = "ABC123"; ac1.callsign = "CSN5101";
        ac1.lat = 40.2; ac1.lon = 116.6; ac1.altitudeFt = 35000; ac1.headingDeg = 270;
        ac1.track = {{39.8, 116.1}, {39.95, 116.25}, {40.05, 116.45}, {40.2, 116.6}};
        AircraftPoint ac2; ac2.icao = "DEF456"; ac2.callsign = "CCA1501";
        ac2.lat = 39.4; ac2.lon = 116.0; ac2.altitudeFt = 28000; ac2.headingDeg = 90;
        ac2.track = {{39.3, 115.9}, {39.35, 115.95}, {39.4, 116.0}};
        v.setAircraft({ac1, ac2});

        SatellitePoint s1; s1.name = "ISS"; s1.lat = 25.0; s1.lon = 115.0; s1.selected = true;
        s1.track = {{20.0, 110.0}, {22.5, 112.5}, {25.0, 115.0}, {27.5, 117.5}, {30.0, 119.0}};
        SatellitePoint s2; s2.name = "NOAA-19"; s2.lat = 45.0; s2.lon = 120.0;
        s2.track = {{42.0, 118.0}, {43.5, 119.0}, {45.0, 120.0}, {46.0, 121.0}};
        v.setSatellites({s1, s2});

        v.setViewState({36.0, 116.0, 3.0});
        v.show();
        v.grab().save(outDir + "/map_3layers_nonhw.png");
    }

    // ---------------- SKY: polar passes + GNSS sats + elevation curve -----
    {
        auto* holder = new QWidget;
        auto* lay = new QVBoxLayout(holder);
        lay->setContentsMargins(8, 8, 8, 8);
        lay->setSpacing(6);
        SkyView* sky = new SkyView;
        sky->setNonHardware(true);
        ElevationPlot* ep = new ElevationPlot;
        holder->resize(520, 860);

        QDateTime base(QDate(2026, 9, 28), QTime(14, 0, 0), Qt::UTC);
        PassArc arc; arc.name = "ISS";
        const int steps = 60;
        QList<QPair<QDateTime, double>> samples;
        for (int i = 0; i <= steps; ++i) {
            double u = double(i) / steps;
            double el = 62.0 * std::sin(M_PI * u);
            double az = 40.0 + (150.0 - 40.0) * u;
            arc.track.append({az, el});
            samples.append({base.addSecs(int(u * 600)), el});
        }
        arc.aosUtc = base;
        arc.losUtc = base.addSecs(600);
        arc.maxEl = 62.0;
        sky->setPasses({arc});
        sky->setSelectedSatellite("ISS");

        LiveSat orb; orb.name = "ISS"; orb.az = 95.0; orb.el = 58.0; orb.selected = true;
        sky->setLiveSatellites({orb});

        GnssSkySat g1; g1.prn = "G15"; g1.az = 45; g1.el = 55; g1.snr = 48; g1.used = true;
        GnssSkySat g2; g2.prn = "G22"; g2.az = 200; g2.el = 30; g2.snr = 41; g2.used = true;
        GnssSkySat g3; g3.prn = "G31"; g3.az = 300; g3.el = 70; g3.snr = 0; g3.used = false;
        sky->setGnssSatellites({g1, g2, g3});
        sky->setCurrentTime(base.addSecs(300));

        ep->setPass("ISS", samples);

        auto* clockLbl = new QLabel(
            "GNSS UTC 14:05:00.000   系统 UTC 14:05:00.123   本地 22:05:00   "
            "偏差 +0.123 s（系统−GNSS，未改钟）  [录制样例·非硬件 NOT HARDWARE]");
        clockLbl->setWordWrap(true);

        lay->addWidget(sky, 3);
        lay->addWidget(ep, 2);
        lay->addWidget(clockLbl);
        holder->show();
        QImage shot = holder->grab().toImage();
        shot.save(outDir + "/sky_clock_elevation_nonhw.png");
    }
    return 0;
}
