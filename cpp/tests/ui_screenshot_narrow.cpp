// SPDX-License-Identifier: MIT
// Offscreen screenshot: narrow-window adaptation check.
// Not in ctest. Env: MBD_OUT (png path). Shrinks the main window to a small
// usable width and grabs it so the left control rail (already a flick-scroll
// QScrollArea), the center spectrum/waterfall, and the right tab rail must not
// clip or overlap text. No demo data -- the offline test-signal engine feeds it.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QSettings>
#include <QDir>
#include <QSplitter>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    // Throwaway QSettings so real persisted focus-mode state never hides rails.
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_narrow_" +
                           QString::number(QCoreApplication::applicationPid()));
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    mbdsdr::MainWindow win;
    // Shrink to a narrow but usable width. The left rail is a QScrollArea so
    // its controls scroll rather than clip; the spectrum keeps its minimum.
    // Width/height are parameterized via env (MBD_W/MBD_H) so the same target
    // can reproduce a 640 narrow shot and a 1920 full-width shot for
    // flexibility checks; defaults preserve the original 820x640.
    const int w = qEnvironmentVariableIntValue("MBD_W");
    const int h = qEnvironmentVariableIntValue("MBD_H");
    win.resize(w > 0 ? w : 820, h > 0 ? h : 640);
    win.show();

    QTimer::singleShot(1200, [&]() {
        qInfo("scaleFactor=%.3f window=%dx%d", mbdsdr::tokens::scaleFactor(),
              win.width(), win.height());
        const auto splits = win.findChildren<QSplitter*>();
        for (auto* s : splits) {
            QStringList sz;
            for (int i = 0; i < s->count(); ++i) sz << QString::number(s->widget(i)->width());
            qInfo("splitter widths: %s", sz.join(' ').toLocal8Bit().constData());
        }
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("narrow screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
