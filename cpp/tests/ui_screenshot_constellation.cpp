// SPDX-License-Identifier: MIT
// Offscreen screenshot: multi-VFO band boxes (center) + a lit BPSK constellation
// (right tab) driven by the offline Test Signal (non-hardware). Not in ctest.
// Env: MBD_OUT (png path).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QPushButton>
#include <QComboBox>
#include <QSpinBox>
#include <QListWidget>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());
    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    mbdsdr::MainWindow win;
    win.resize(1280, 800);
    win.show();

    QPushButton* addBtn = win.findChild<QPushButton*>("vfoAddBtn");
    QComboBox* demod = win.findChild<QComboBox*>("demodCombo");
    QSpinBox* freqSpin = win.findChild<QSpinBox*>("freqSpin");
    QListWidget* vfoList = win.findChild<QListWidget*>("vfoList");

    // Add two VFOs so the panadapter shows multiple band boxes.
    if (addBtn) { addBtn->click(); addBtn->click(); }

    // Select the FIRST VFO row, tune it to the synthetic carrier at +1.5 kHz,
    // and set it to BPSK.
    if (vfoList) vfoList->setCurrentRow(0);
    if (freqSpin) freqSpin->setValue(98301500);
    if (demod) {
        const int idx = demod->findText("BPSK");
        if (idx >= 0) demod->setCurrentIndex(idx);
    }
    // Re-assert selection right before the shot (restoreUiState / async signals
    // can otherwise move the active row).
    QTimer::singleShot(18000, [&]() {
        if (vfoList) vfoList->setCurrentRow(0);
    });

    QTimer::singleShot(20000, [&]() {
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("constellation screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
