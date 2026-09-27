// SPDX-License-Identifier: MIT
// Offscreen screenshot utility for UI round-8 verification -- NOT in CMake.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QTabWidget>
#include <QList>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    mbdsdr::MainWindow win;
    win.resize(1280, 800);
    win.show();

    // Switch the right-hand tab strip to the "天空" (sky) tab.
    QTimer::singleShot(200, [&]() {
        const auto tabs = win.findChildren<QTabWidget*>();
        for (auto* t : tabs) {
            for (int i = 0; i < t->count(); ++i) {
                if (t->tabText(i) == QStringLiteral("天空")) {
                    t->setCurrentIndex(i);
                    break;
                }
            }
        }
    });

    QTimer::singleShot(2500, [&]() {
        QPixmap pm = win.grab();
        pm.save("/tmp/mbdsdr_round10.png", "PNG");
        qInfo("screenshot saved to /tmp/mbdsdr_round10.png (%dx%d)",
              pm.width(), pm.height());
        app.quit();
    });

    return app.exec();
}
