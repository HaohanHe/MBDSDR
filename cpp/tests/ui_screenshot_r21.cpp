// SPDX-License-Identifier: MIT
// Offscreen screenshot for round-21 right-column tabs (CW / ADS-B / AI).
// Env: MBD_W, MBD_H, MBD_OUT, MBD_RTAB (right-tab index to activate).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QTabWidget>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const int w = std::atoi(qgetenv("MBD_W").constData());
    const int h = std::atoi(qgetenv("MBD_H").constData());
    const int tab = std::atoi(qgetenv("MBD_RTAB").constData());
    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    mbdsdr::MainWindow win;
    win.resize(w > 0 ? w : 1280, h > 0 ? h : 800);
    win.show();

    QTimer::singleShot(500, [&]() {
        // The right-column QTabWidget is the larger-of-two tab widgets.
        const auto tabs = win.findChildren<QTabWidget*>();
        QTabWidget* right = nullptr;
        for (auto* t : tabs) if (t->count() >= 4) right = t;
        if (right && tab >= 0 && tab < right->count()) right->setCurrentIndex(tab);
    });

    QTimer::singleShot(2500, [&]() {
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("saved %s", out.toLocal8Bit().constData());
        app.quit();
    });
    return app.exec();
}
