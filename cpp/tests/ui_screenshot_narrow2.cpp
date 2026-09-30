// SPDX-License-Identifier: MIT
// Offscreen screenshot: narrow-window (820px) re-check of the NEW controls.
// Switches the right rail to the constellation tab so the newly added toolbar
// (zoom −/1:1/+ + histogram toggle) is visible at a phone-narrow width and must
// not clip or overlap text. Not in ctest. Env: MBD_OUT (png path).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QSettings>
#include <QDir>
#include <QTabWidget>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_narrow2_" +
                           QString::number(QCoreApplication::applicationPid()));
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    mbdsdr::MainWindow win;
    win.resize(820, 640);   // phone-narrow width
    win.show();

    QTimer::singleShot(300, [&]() {
        if (QTabWidget* tabs = win.findChild<QTabWidget*>("rightTabs")) {
            for (int i = 0; i < tabs->count(); ++i) {
                if (tabs->tabText(i) == QStringLiteral("星座")) { tabs->setCurrentIndex(i); break; }
            }
        }
    });

    QTimer::singleShot(1200, [&]() {
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("narrow recheck screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
