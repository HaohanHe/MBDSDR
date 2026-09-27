// SPDX-License-Identifier: MIT
// Offscreen screenshot utility -- NOT part of the main build target.
// Compile manually with g++ against the built object files.
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
    win.show();

    // Give the event loop a few frames to render test-signal spectrum,
    // then grab the window and save.
    QTimer::singleShot(800, [&]() {
        QPixmap pm = win.grab();
        pm.save("/tmp/mbdsdr_after.png", "PNG");
        qInfo("screenshot saved to /tmp/mbdsdr_after.png (%dx%d)",
              pm.width(), pm.height());
        app.quit();
    });

    return app.exec();
}
