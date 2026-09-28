// SPDX-License-Identifier: MIT
// Offscreen interaction test for the SpectrumWidget container: drag retune +
// wheel zoom are injected into the embedded canvas, while we assert on the
// container's forwarded signals (proving the wiring survives the refactor).
#include <QtTest/QtTest>
#include <QApplication>
#include <QWheelEvent>
#include "ui/spectrum_widget.h"
#include "ui/spectrum_display.h"
#include "core/spectrum_frame.h"

class TestSpectrumInteraction : public QObject {
    Q_OBJECT
private slots:
    void dragRetunes();
    void wheelZooms();
};

static mbdsdr::SpectrumFrame fakeFrame() {
    mbdsdr::SpectrumFrame fr;
    fr.sampleRateHz = 2.4e6; fr.centerFreqHz = 98.5e6; fr.fftSize = 1024;
    fr.dbfs.assign(1024, -80.0f); fr.sourceName="t"; fr.isTestSignal=true;
    return fr;
}

void TestSpectrumInteraction::dragRetunes() {
    mbdsdr::ui::SpectrumWidget w; w.resize(800, 400); w.show();
    w.setSpectrum(fakeFrame());
    mbdsdr::ui::SpectrumDisplay* canvas = w.displayCanvas();
    QVERIFY(canvas);
    QSignalSpy spy(&w, &mbdsdr::ui::SpectrumWidget::frequencyChanged);
    const QPoint c = canvas->rect().center();
    QTest::mousePress(canvas, Qt::LeftButton, Qt::NoModifier, c);
    QTest::mouseMove(canvas, c + QPoint(80, 0));
    QTest::mouseRelease(canvas, Qt::LeftButton, Qt::NoModifier, c + QPoint(80, 0));
    QVERIFY(spy.count() >= 1);
}

void TestSpectrumInteraction::wheelZooms() {
    mbdsdr::ui::SpectrumWidget w; w.resize(800, 400); w.show();
    w.setSpectrum(fakeFrame());
    mbdsdr::ui::SpectrumDisplay* canvas = w.displayCanvas();
    QVERIFY(canvas);
    const double before = w.zoomFactor();
    const QPoint pc = canvas->rect().center();
    const QPointF pf(pc);
    QWheelEvent ev(pf, pf, QPoint(120,0),
                   QPoint(0,120), Qt::NoButton, Qt::NoModifier,
                   Qt::NoScrollPhase, false);
    canvas->wheelEvent(&ev);
    QVERIFY(w.zoomFactor() > before);
}

QTEST_MAIN(TestSpectrumInteraction)
#include "test_spectrum_interaction.moc"
