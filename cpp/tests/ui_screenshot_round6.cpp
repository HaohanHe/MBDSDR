// SPDX-License-Identifier: MIT
// Offscreen screenshot utility for UI round-6 verification -- NOT in CMake.
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
        pm.save("/tmp/mbdsdr_round6.png", "PNG");
        qInfo("screenshot saved to /tmp/mbdsdr_round6.png (%dx%d)",
              pm.width(), pm.height());
        app.quit();
    });

    return app.exec();
}
