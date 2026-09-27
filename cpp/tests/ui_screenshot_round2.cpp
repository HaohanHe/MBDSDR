// SPDX-License-Identifier: MIT
// Offscreen screenshot utility for UI round-2 verification -- NOT in CMake.
// Compiled manually against the built object files (see build/objlist.txt).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication::setAttribute(Qt::AA_EnableHighDpiScaling);
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    mbdsdr::MainWindow win;
    win.resize(1280, 800);
    win.show();

    // Let the engine run ~2 s so the waterfall accumulates real frames and
    // its time axis has measured data, then grab and save.
    QTimer::singleShot(2000, [&]() {
        QPixmap pm = win.grab();
        pm.save("/tmp/mbdsdr_round2.png", "PNG");
        qInfo("screenshot saved to /tmp/mbdsdr_round2.png (%dx%d)",
              pm.width(), pm.height());
        app.quit();
    });

    return app.exec();
}
