// SPDX-License-Identifier: MIT
// Offscreen mouse-interaction test for SpectrumWidget: drag retune + wheel zoom.
#include <QtTest/QtTest>
#include <QApplication>
#include <QWheelEvent>
#include "ui/spectrum_widget.h"
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
    QSignalSpy spy(&w, &mbdsdr::ui::SpectrumWidget::frequencyChanged);
    QTest::mousePress(&w, Qt::LeftButton, Qt::NoModifier, QPoint(400, 200));
    QTest::mouseMove(&w, QPoint(500, 200));
    QTest::mouseRelease(&w, Qt::LeftButton, Qt::NoModifier, QPoint(500, 200));
    QVERIFY(spy.count() >= 1);
}

void TestSpectrumInteraction::wheelZooms() {
    mbdsdr::ui::SpectrumWidget w; w.resize(800, 400); w.show();
    w.setSpectrum(fakeFrame());
    const double before = w.zoomFactor();
    QWheelEvent ev(QPointF(400,200), QPointF(400,200), QPoint(120,0),
                   QPoint(0,120), Qt::NoButton, Qt::NoModifier,
                   Qt::NoScrollPhase, false);
    w.wheelEvent(&ev);
    QVERIFY(w.zoomFactor() > before);
}

QTEST_MAIN(TestSpectrumInteraction)
#include "test_spectrum_interaction.moc"
