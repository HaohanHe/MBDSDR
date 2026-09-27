// SPDX-License-Identifier: MIT
// Offscreen screenshot utility for UI round-3 verification -- NOT in CMake.
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

    QTimer::singleShot(2000, [&]() {
        QPixmap pm = win.grab();
        pm.save("/tmp/mbdsdr_round3.png", "PNG");
        qInfo("screenshot saved to /tmp/mbdsdr_round3.png (%dx%d)",
              pm.width(), pm.height());
        app.quit();
    });

    return app.exec();
}
