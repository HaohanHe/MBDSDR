// SPDX-License-Identifier: MIT
// Offscreen visual self-check: the enhanced permanent status strip now carries
// RSSI / SNR / squelch-state / GNSS readouts, ALL driven by the real engine
// readback signals (rssiLevel/snrLevel/squelchState/gnssFix). The GNSS field is
// intentionally empty (no receiver attached -- we never paint a fake fix).
// NOT in ctest; built as ui_shot_statusstrip, run offscreen. Saves
// cpp/scratch/ui_status_strip.png.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QStatusBar>
#include <QSettings>
#include <QDir>
#include <QCheckBox>
#include <QDebug>

#include "core/tokens.h"
#include "ui/main_window.h"

using namespace mbdsdr;

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QString tmpDir = QDir::tempPath() + "/mbdsdr_shot_statusstrip";
    QDir(tmpDir).removeRecursively();
    QDir().mkpath(tmpDir);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmpDir + "/cfg");
    app.setStyleSheet(tokens::buildDarkQss());

    MainWindow win;
    win.resize(tokens::scaled(1500), tokens::scaled(700));
    win.show();

    QTimer::singleShot(1600, [&]() {
        // Let the live test-source engine push a few real RSSI/SNR/squelch
        // readbacks, then enable the squelch gate so the strip shows OPEN/CLOSED
        // instead of OFF. No GNSS receiver -> the GNSS field stays empty (honest).
        for (auto* c : win.findChildren<QCheckBox*>()) {
            if (c->text() == QString::fromUtf8("启用静噪")) { c->setChecked(true); break; }
        }
        QApplication::processEvents();
    });

    QTimer::singleShot(2200, [&]() {
        const QString out =
            "/home/user/Doubao/chats/38438160041798146/cpp/scratch/ui_status_strip.png";
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("ui_status_strip saved %dx%d -> %s", pm.width(), pm.height(),
              qPrintable(out));
        app.quit();
    });
    return app.exec();
}
