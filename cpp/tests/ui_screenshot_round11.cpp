// SPDX-License-Identifier: MIT
// Offscreen screenshot for UI round-11 verification (peak list). Not in CMake.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    mbdsdr::MainWindow win;
    win.resize(1280, 800);
    win.show();

    // Run ~2.5 s so the test-tone spectrum stabilises and peak detection
    // populates the list, then grab.
    QTimer::singleShot(2500, [&]() {
        QPixmap pm = win.grab();
        pm.save("/tmp/mbdsdr_round11.png", "PNG");
        qInfo("screenshot saved to /tmp/mbdsdr_round11.png (%dx%d)",
              pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
