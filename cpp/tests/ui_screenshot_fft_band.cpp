// SPDX-License-Identifier: MIT
// Offscreen screenshot: FFT control strip (FFT size / window / average) + the
// demod-mode / bandwidth-preset row. Not in ctest.
// Env: MBD_OUT (png path).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QComboBox>
#include <cstdlib>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "ui/spectrum_widget.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const QByteArray outBa = qgetenv("MBD_OUT");
    const QString out = QString::fromLocal8Bit(outBa);

    mbdsdr::MainWindow win;
    win.resize(1280, 800);
    win.show();

    QTimer::singleShot(1500, [&]() {
        // Show a concrete preset state: WFM mode (200 kHz bandwidth preset) so
        // the 解调 / 带宽 row reflects the B4 auto-preset.
        if (auto* d = win.findChild<QComboBox*>("demodCombo"))
            d->setCurrentText("WFM");
        if (auto* b = win.findChild<QComboBox*>("bwCombo"))
            b->setCurrentIndex(5);   // 200 kHz (WFM preset)
        QTimer::singleShot(300, [&]() {
            QPixmap pm = win.grab();
            pm.save(out, "PNG");
            qInfo("fft_band screenshot -> %s (%dx%d)",
                  out.toLocal8Bit().constData(), pm.width(), pm.height());
            app.quit();
        });
    });
    return app.exec();
}
