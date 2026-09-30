// SPDX-License-Identifier: MIT
// Offscreen screenshot: the SpyServer remote-IQ control in its ENABLED state.
// Shows the "SpyServer 远程" groupbox (开/关 checkbox + port spinbox + the
// "监听 5555 · 0 客户端" status line) switched ON, so the layout must not clip
// or overlap the labels. Not in ctest. Env: MBD_OUT (png path).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QSettings>
#include <QDir>
#include <QCheckBox>
#include <QSpinBox>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_spyserver_" +
                           QString::number(QCoreApplication::applicationPid()));
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    mbdsdr::MainWindow win;
    win.resize(1100, 800);   // wide enough that the left rail (with the
                             // SpyServer group) is fully visible, no edge clip
    win.show();

    QTimer::singleShot(350, [&]() {
        if (auto* chk = win.findChild<QCheckBox*>("spyserverChk"))
            chk->setChecked(true);   // starts the listener on its persisted port
    });

    QTimer::singleShot(1000, [&]() {
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("spyserver screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
