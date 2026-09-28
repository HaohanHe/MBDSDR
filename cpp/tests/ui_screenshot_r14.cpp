// SPDX-License-Identifier: MIT
// Offscreen screenshot for UI round-14 DPI/size sweep. Not in CMake.
// Env: MBD_W, MBD_H (window size), MBD_OUT (png path). DPI scale via
// QT_SCALE_FACTOR set in the shell.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const int w = std::atoi(qgetenv("MBD_W").constData());
    const int h = std::atoi(qgetenv("MBD_H").constData());
    const QByteArray outBa = qgetenv("MBD_OUT");
    const QString out = QString::fromLocal8Bit(outBa);

    mbdsdr::MainWindow win;
    win.resize(w > 0 ? w : 1280, h > 0 ? h : 800);
    win.show();

    QTimer::singleShot(2500, [&]() {
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("screenshot saved to %s (%dx%d, dpr=%.2f)",
              out.toLocal8Bit().constData(), pm.width(), pm.height(),
              win.devicePixelRatioF());
        app.quit();
    });
    return app.exec();
}
