// SPDX-License-Identifier: MIT
// Offscreen screenshot: the upgraded multi-VFO management panel.
// Not in ctest. Env: MBD_OUT (png path). Drives the REAL engine: adds a few VFOs
// at distinct frequencies/modes/bandwidths, gives one a user display name via the
// inline-edit path, then grabs the window so the left-rail "多 VFO" panel shows
// every column (active dot, name(id), freq MHz, mode, bandwidth) plus the
// add/copy/delete button row.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QListWidget>
#include <QListWidgetItem>
#include <QSettings>
#include <QDir>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"
#include "dsp/spectrum_engine.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    // Use a throwaway QSettings dir so persisted focus-mode / collapsed-rail state
    // from a real install never hides the VFO panel in the screenshot.
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_vfo_" +
                           QString::number(QCoreApplication::applicationPid()));
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    mbdsdr::MainWindow win;
    win.resize(1280, 800);
    win.show();

    auto* eng = win.engine();
    auto* list = win.findChild<QListWidget*>("vfoList");

    // Build three VFOs with distinct, real params (engine API only).
    eng->vfoAdd();                       // VFO #2, selected
    eng->vfoSetFreq(eng->selectedVfoId(), 100.7e6);
    eng->vfoSetMode(eng->selectedVfoId(), "AM");
    eng->vfoSetBandwidth(eng->selectedVfoId(), 8000.0);
    eng->vfoAdd();                       // VFO #3, selected
    eng->vfoSetFreq(eng->selectedVfoId(), 98.5e6);
    eng->vfoSetMode(eng->selectedVfoId(), "NFM");
    eng->vfoSetBandwidth(eng->selectedVfoId(), 12500.0);

    QTimer::singleShot(600, [&]() {
        // Give the first VFO a user display name through the real edit path.
        if (list && list->count() > 0) {
            list->item(0)->setData(Qt::EditRole, QString::fromUtf8("气象预警"));
        }
        QApplication::processEvents();
    });

    QTimer::singleShot(1200, [&]() {
        QWidget* grp = win.findChild<QWidget*>("vfoGroup");
        QPixmap pm;
        if (grp) pm = grp->grab();
        else     pm = win.grab();
        pm.save(out, "PNG");
        qInfo("vfo panel screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
