// SPDX-License-Identifier: MIT
// Offscreen screenshot harness for the "克制化 + 专注模式" UI enhancement.
//
// NOTE: this file is registered as the `ui_shot_focus` target by the
// integration stage (the organizer wires SHOT_SRCS + HEADERS into CMake; we
// do not touch CMakeLists.txt here). It is not built as part of `mbdsdr`.
//
// Two captures, back to back:
//   1. At 2500 ms after show: the ordinary layout (left rail + right tabs +
//      spectrum + quiet source banner)  -> MBD_OUT  (default scratch/focus_off.png)
//   2. We then programmatically toggle the 专注 button (objectName "focusBtn"),
//      wait for the 220 ms OutCubic rail-collapse to settle, and grab again ->
//      MBD_OUT2 (default scratch/focus_on.png): rails hidden, spectrum full
//      width, focus button accent-highlighted.
//
// Env: MBD_W, MBD_H (window size), MBD_OUT, MBD_OUT2 (png paths).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QPushButton>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const int w = std::atoi(qgetenv("MBD_W").constData());
    const int h = std::atoi(qgetenv("MBD_H").constData());
    const QString out1 = QString::fromLocal8Bit(
        qgetenv("MBD_OUT").isEmpty() ? "scratch/focus_off.png" : qgetenv("MBD_OUT"));
    const QString out2 = QString::fromLocal8Bit(
        qgetenv("MBD_OUT2").isEmpty() ? "scratch/focus_on.png" : qgetenv("MBD_OUT2"));

    mbdsdr::MainWindow win;
    win.resize(w > 0 ? w : 1280, h > 0 ? h : 800);
    win.show();

    // First capture: ordinary layout.
    QTimer::singleShot(2500, [&]() {
        QPixmap pm1 = win.grab();
        pm1.save(out1, "PNG");
        qInfo("focus off -> %s (%dx%d)", out1.toLocal8Bit().constData(),
              pm1.width(), pm1.height());

        // Programmatically engage focus mode through the real toggle path
        // (same signal a mouse click would fire).
        if (QPushButton* fb = win.findChild<QPushButton*>("focusBtn"))
            fb->setChecked(true);
        else
            qWarning("focusBtn not found; second shot will be identical to first");

        // Wait for the kAnimMedium1=220ms collapse (+ the deferred setVisible
        // that fires right after) plus a small settle margin.
        QTimer::singleShot(700, [&]() {
            QPixmap pm2 = win.grab();
            pm2.save(out2, "PNG");
            qInfo("focus on  -> %s (%dx%d)", out2.toLocal8Bit().constData(),
                  pm2.width(), pm2.height());
            app.quit();
        });
    });
    return app.exec();
}
