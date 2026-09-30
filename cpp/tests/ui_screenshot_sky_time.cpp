// SPDX-License-Identifier: MIT
// Offscreen visual self-check for the new space-time deepening:
//   1. cpp/scratch/ui_sky_drag.png    -- sky in "preview" state (scrubbed clock +
//                                        selected-satellite dashed trajectory).
//   2. cpp/scratch/ui_gis_legend.png -- world map corner legend + GNSS fix dot.
//   3. cpp/scratch/ui_clock_domain.png -- clock-domain readout (GNSS 授时 / 系统 / Δt).
// NOT in ctest; built as ui_shot_skytime, run offscreen.
//
// The orbital/GNSS points fed here are labelled "非硬件 NOT HARDWARE" sample
// fixtures (same convention as the other ui_shot_* self-checks); the layout /
// legend / clock-domain behavior is what is being checked visually.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QLabel>
#include <QSlider>
#include <QDateTime>
#include <QSettings>
#include <QDir>
#include <cmath>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "ui/sky_view.h"
#include "ui/world_view.h"

using namespace mbdsdr;

static const QString kOut =
    "/home/user/Doubao/chats/38438160041798146/cpp/scratch/";

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_skytime");
    app.setStyleSheet(tokens::buildDarkQss());

    // ---- 1) Sky preview state + selected trajectory (standalone, full size) --
    {
        ui::SkyView sky;
        sky.setNonHardware(true);
        sky.resize(560, 560);
        sky.show();
        const QDateTime preview =
            QDateTime::currentDateTimeUtc().addSecs(5 * 60);  // +5 min preview
        sky.setCurrentTime(preview);

        ui::LiveSat a; a.name = "ORB_A"; a.az = 120; a.el = 55; a.selected = true;
        ui::LiveSat b; b.name = "ORB_B"; b.az = 300; b.el = 25;
        sky.setLiveSatellites({a, b});

        QList<QPair<double,double>> traj;
        for (int k = 0; k < 61; ++k) {
            double u = double(k) / 60.0;
            traj.append({100.0 + 60.0 * u, 15.0 + 55.0 * std::sin(M_PI * u)});
        }
        sky.setSelectedTrajectory(traj);
        QCoreApplication::processEvents();
        QPixmap pm = sky.grab();
        pm.save(kOut + "ui_sky_drag.png", "PNG");
        qInfo("ui_sky_drag.png %dx%d", pm.width(), pm.height());
    }

    // ---- 2) World map legend + GNSS fix dot (standalone, full size) -------
    {
        ui::WorldView wv;
        wv.setSynthetic(true);
        wv.resize(900, 560);
        wv.show();
        wv.setViewState({35.0, 116.0, 2.5});
        wv.setStation(39.9, 116.4);
        QDateTime fixTime(QDate(2026, 9, 30), QTime(10, 20, 30, 120), Qt::UTC);
        wv.setGnssFix(true, 39.91, 116.39, 14, 0.8, fixTime);
        ui::AircraftPoint ac; ac.icao = "ABC123"; ac.callsign = "CSN5101";
        ac.lat = 40.5; ac.lon = 115.5; ac.altitudeFt = 35000; ac.headingDeg = 270;
        wv.setAircraft({ac});
        ui::SatellitePoint sp; sp.name = "ISS"; sp.lat = 28.0; sp.lon = 113.0;
        wv.setSatellites({sp});
        QCoreApplication::processEvents();
        QPixmap pm = wv.grab();
        pm.save(kOut + "ui_gis_legend.png", "PNG");
        qInfo("ui_gis_legend.png %dx%d", pm.width(), pm.height());
    }

    // ---- 3) Clock-domain readout (real MainWindow, honest empty state) --
    MainWindow win;
    win.resize(tokens::scaled(1280), tokens::scaled(800));
    win.show();
    QTimer::singleShot(1200, [&]() {
        for (QLabel* l : win.findChildren<QLabel*>()) {
            if (l->objectName() == "monoInfo" &&
                l->text().contains(QStringLiteral("时钟域"))) {
                QPixmap pm = l->grab();
                pm.save(kOut + "ui_clock_domain.png", "PNG");
                qInfo("ui_clock_domain.png %dx%d  text=%s",
                      pm.width(), pm.height(), qPrintable(l->text()));
                break;
            }
        }
        app.quit();
    });
    return app.exec();
}
