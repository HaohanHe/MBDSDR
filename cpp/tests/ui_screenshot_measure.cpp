// SPDX-License-Identifier: MIT
// Offscreen screenshot harness for the spectrum MEASUREMENT features:
// matured peak marker (small triangle + drop line), the real noise-floor
// dashed baseline with its "NF" caption, and the hover measurement cursor
// readout box (freq MHz / dBFS + SNR). Fully deterministic: the frames are
// pushed by the harness itself, so the capture never depends on engine timing.
//
// Registered as `ui_shot_measure` by CMake (offscreen; not in ctest).
// Env: MBD_OUT (png path, default scratch/shot_measure_canvas.png).
#include <QApplication>
#include <QMouseEvent>
#include <QTimer>
#include <QPixmap>
#include <cstdlib>

#include "core/spectrum_frame.h"
#include "core/tokens.h"
#include "ui/spectrum_display.h"
#include "ui/spectrum_widget.h"

using namespace mbdsdr;

int main(int argc, char** argv) {
    QApplication app(argc, argv);

    ui::SpectrumWidget w;
    w.resize(980, 560);
    w.show();
    app.processEvents();

    // Push a known frame: 2048 bins, a strong carrier at bin 700, floor -100.
    SpectrumFrame fr;
    fr.sampleRateHz = 2.4e6;
    fr.centerFreqHz = 98.5e6;
    fr.fftSize = 2048;
    fr.dbfs.assign(2048, -100.0f);
    fr.dbfs[700] = 0.0f;                      // carrier at 98.5 - (1.2-700/2048*2.4) MHz
    fr.sourceName = "test";
    fr.isTestSignal = true;

    // Enough frames to mature the peak (kPeakMinSeenFrames = 3).
    for (int i = 0; i < 8; ++i) {
        w.setSpectrum(fr);
        app.processEvents();
    }
    // Real noise floor (per-bin domain) injected by the integration layer.
    w.setNoiseFloorDb(-90.0f);
    app.processEvents();

    // Hover the measurement cursor over the carrier so the readout box shows.
    ui::SpectrumDisplay* canvas = w.displayCanvas();
    if (canvas) {
        const int cx = canvas->width() / 2;
        const int cy = canvas->height() / 3;
        QMouseEvent move(QEvent::MouseMove, QPoint(cx, cy),
                         QPoint(cx, cy), Qt::NoButton, Qt::NoButton, Qt::NoModifier);
        QApplication::sendEvent(canvas, &move);
    }
    app.processEvents();

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT").isEmpty()
        ? "scratch/shot_measure_canvas.png" : qgetenv("MBD_OUT"));
    QTimer::singleShot(120, [&]() {
        QPixmap pm = w.grab();
        pm.save(out, "PNG");
        qInfo("measurement canvas -> %s (%dx%d)", out.toLocal8Bit().constData(),
              pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
