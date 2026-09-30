// SPDX-License-Identifier: MIT
// Offscreen screenshot: constellation panel with the NEW controls -- point
// density read-out, a zoom notch, and the real I-histogram overlay toggled on.
// Driven by the offline Test Signal (non-hardware), so the panel honestly
// shows the "非硬件 NOT HARDWARE" tag. Not in ctest. Env: MBD_OUT (png path).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QPushButton>
#include <QComboBox>
#include <QSpinBox>
#include <QListWidget>
#include <QTabWidget>
#include <QSettings>
#include <QDir>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    // Throwaway QSettings so persisted focus-mode (专注) never hides the right
    // rail that holds the constellation tab.
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_cst2_" +
                           QString::number(QCoreApplication::applicationPid()));
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());
    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    mbdsdr::MainWindow win;
    win.resize(1280, 800);
    win.show();

    // Add VFOs first (vfoAdd is queued/async); only then pick row 0 + BPSK,
    // after the VFO list has settled.
    QTimer::singleShot(500, [&]() {
        if (QPushButton* addBtn = win.findChild<QPushButton*>("vfoAddBtn")) {
            addBtn->click(); addBtn->click();
        }
    });
    QTimer::singleShot(2500, [&]() {
        if (QListWidget* vfoList = win.findChild<QListWidget*>("vfoList"))
            vfoList->setCurrentRow(0);
        if (QSpinBox* freqSpin = win.findChild<QSpinBox*>("freqSpin"))
            freqSpin->setValue(98301500);
        if (QComboBox* demod = win.findChild<QComboBox*>("demodCombo")) {
            const int idx = demod->findText("BPSK");
            if (idx >= 0) demod->setCurrentIndex(idx);
        }
        if (QTabWidget* tabs = win.findChild<QTabWidget*>("rightTabs")) {
            for (int i = 0; i < tabs->count(); ++i)
                if (tabs->tabText(i) == QStringLiteral("星座")) { tabs->setCurrentIndex(i); break; }
        }
        if (QPushButton* zoomIn = win.findChild<QPushButton*>("cstZoomInBtn"))
            zoomIn->click();
        if (QPushButton* hist = win.findChild<QPushButton*>("cstHistBtn"))
            hist->setChecked(true);
    });

    // Re-assert BPSK on the first VFO right before the shot (async signals can
    // otherwise move the selected VFO / mode).
    QTimer::singleShot(18000, [&]() {
        if (QListWidget* vfoList = win.findChild<QListWidget*>("vfoList"))
            vfoList->setCurrentRow(0);
        if (QComboBox* demod = win.findChild<QComboBox*>("demodCombo")) {
            const int idx = demod->findText("BPSK");
            if (idx >= 0 && demod->currentIndex() != idx) demod->setCurrentIndex(idx);
        }
    });

    QTimer::singleShot(20000, [&]() {
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("constellation(+density/zoom/hist) screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
