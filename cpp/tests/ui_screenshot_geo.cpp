// SPDX-License-Identifier: MIT
// Offscreen self-check: LEO PNT geometry readout + S-meter.
//   cpp/scratch/ui_geo.png     -- geometry availability label (prediction).
//   cpp/scratch/ui_smeter.png  -- S-meter bar (S0..S9 + peak hold).
// NOT in ctest; built as ui_shot_geo, run offscreen.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QLabel>
#include <QSettings>
#include <QDir>

#include "core/tokens.h"
#include "core/pnt_geometry.h"
#include "ui/main_window.h"
#include "ui/s_meter.h"

using namespace mbdsdr;

static const QString kOut =
    "/home/user/Doubao/chats/38438160041798146/cpp/scratch/";

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_geo");
    app.setStyleSheet(tokens::buildDarkQss());

    // ---- S-meter standalone (populated + empty state) -------------------
    {
        ui::SMeterWidget m;
        m.resize(320, 34);
        m.setNoiseFloorDbfs(-72.0);
        m.setSignalDbfs(-40.0);   // ~32 dB above noise -> S5
        m.show();
        QPixmap pm = m.grab();
        pm.save(kOut + "ui_smeter.png", "PNG");
        qInfo("ui_smeter.png %dx%d", pm.width(), pm.height());
    }

    // ---- Geometry readout from the real MainWindow -----------------------
    MainWindow win;
    win.resize(tokens::scaled(1280), tokens::scaled(800));
    win.show();
    QTimer::singleShot(1200, [&]() {
        if (QLabel* g = win.findChild<QLabel*>("geoReadout")) {
            // Seed a labelled geometry line (offline has no GNSS TLE).
            g->setText(QStringLiteral(
                "导航几何（预测，非定位）  可见 6 颗 · 简化DOP 2.1 · 可用性 好"));
            g->resize(700, g->sizeHint().height());
            QPixmap pm = g->grab();
            pm.save(kOut + "ui_geo.png", "PNG");
            qInfo("ui_geo.png %dx%d", pm.width(), pm.height());
        }
        app.quit();
    });
    return app.exec();
}
