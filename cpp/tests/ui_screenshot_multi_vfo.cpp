// SPDX-License-Identifier: MIT
// Offscreen screenshot: multi-VFO band boxes over the spectrum/waterfall.
// Not in ctest. Env: MBD_OUT (png path). Builds MainWindow, adds two extra
// VFOs via the add button, lets the test-signal engine feed a few frames, then
// grabs the window. The VFO boxes are drawn by SpectrumDisplay from the engine's
// VfoManager markers.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QPushButton>
#include <QDoubleSpinBox>
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

    // Space three VFOs across the capture:
    //   A stays at 98.5. Add B, retune the receiver to 98.7 (B follows, A is
    //   left at +200 kHz offset). Add C, retune to 98.3 (C follows, B at +400k,
    //   A at +200k relative to the 98.3 capture center).
    QPushButton* addBtn = win.findChild<QPushButton*>("vfoAddBtn");
    QDoubleSpinBox* spin = win.findChild<QDoubleSpinBox*>("freqSpin");
    auto setFreq = [&](double mhz) {
        if (spin) { spin->setValue(mhz); }
    };
    if (addBtn && spin) {
        addBtn->click();          // VFO B created at center, selected
        setFreq(98.7);            // retune source + B
        addBtn->click();          // VFO C created at new center, selected
        setFreq(98.3);            // retune source + C
    }

    QTimer::singleShot(2500, [&]() {
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("multi-vfo screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
