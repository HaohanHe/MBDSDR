// SPDX-License-Identifier: MIT
// Offscreen interaction test for the SpectrumWidget container:
//   * drag retune on the trace;
//   * SDR++-aligned gestures:
//       - plain wheel  = step-tune the selected VFO (zoom moved to Ctrl+wheel);
//       - Ctrl+wheel   = zoom;
//       - click blank spectrum = retune the selected VFO;
//       - drag the frequency strip = pan the view (no Shift held).
// We inject mouse/wheel events into the embedded canvas and assert on the
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
    void ctrlWheelZooms();
    void plainWheelDoesNotZoomButStepTunes();
    void blankClickTunesSelectedVfo();
    void freqStripDragPansWithoutShift();
};

static mbdsdr::SpectrumFrame fakeFrame() {
    mbdsdr::SpectrumFrame fr;
    fr.sampleRateHz = 2.4e6; fr.centerFreqHz = 98.5e6; fr.fftSize = 1024;
    fr.dbfs.assign(1024, -80.0f); fr.sourceName="t"; fr.isTestSignal=true;
    return fr;
}

static void sendWheel(mbdsdr::ui::SpectrumDisplay* canvas, int dy, Qt::KeyboardModifiers mod) {
    const QPoint pc = canvas->rect().center();
    const QPointF pf(pc);
    QWheelEvent ev(pf, pf, QPoint(0, dy), QPoint(0, dy), Qt::NoButton, mod,
                   Qt::NoScrollPhase, false);
    canvas->wheelEvent(&ev);
}

void TestSpectrumInteraction::dragRetunes() {
    mbdsdr::ui::SpectrumWidget w; w.resize(800, 400); w.show();
    w.setSpectrum(fakeFrame());
    mbdsdr::ui::SpectrumDisplay* canvas = w.displayCanvas();
    QVERIFY(canvas);
    QSignalSpy spy(&w, &mbdsdr::ui::SpectrumWidget::frequencyChanged);
    // Press inside the TRACE area, off the narrow VFO band edges, then drag.
    const QPoint c = canvas->spectrumRect().center() + QPoint(120, 0);
    QTest::mousePress(canvas, Qt::LeftButton, Qt::NoModifier, c);
    QTest::mouseMove(canvas, c + QPoint(80, 0));
    QTest::mouseRelease(canvas, Qt::LeftButton, Qt::NoModifier, c + QPoint(80, 0));
    QVERIFY(spy.count() >= 1);
}

void TestSpectrumInteraction::ctrlWheelZooms() {
    mbdsdr::ui::SpectrumWidget w; w.resize(800, 400); w.show();
    w.setSpectrum(fakeFrame());
    mbdsdr::ui::SpectrumDisplay* canvas = w.displayCanvas();
    QVERIFY(canvas);
    const double before = w.zoomFactor();
    sendWheel(canvas, 120, Qt::ControlModifier);
    QVERIFY2(w.zoomFactor() > before, "Ctrl+wheel must zoom in");
}

void TestSpectrumInteraction::plainWheelDoesNotZoomButStepTunes() {
    mbdsdr::ui::SpectrumWidget w; w.resize(800, 400); w.show();
    w.setSpectrum(fakeFrame());
    w.setStepHz(10000.0);   // 10 kHz step
    mbdsdr::ui::SpectrumDisplay* canvas = w.displayCanvas();
    QVERIFY(canvas);
    QSignalSpy spy(&w, &mbdsdr::ui::SpectrumWidget::frequencyChanged);
    const double zoomBefore = w.zoomFactor();

    sendWheel(canvas, 120, Qt::NoModifier);

    // Zoom must NOT move on a plain wheel (it moved to Ctrl).
    QCOMPARE(w.zoomFactor(), zoomBefore);
    // But the selected VFO must step-tune by exactly one 10 kHz step.
    QVERIFY2(spy.count() >= 1, "plain wheel must emit a tuning signal");
    const double tuned = qvariant_cast<double>(spy.takeLast().at(0));
    QVERIFY2(std::abs(tuned - (98.5e6 + 10000.0)) < 1.0,
             "plain wheel must step the VFO by the configured step");
}

void TestSpectrumInteraction::blankClickTunesSelectedVfo() {
    mbdsdr::ui::SpectrumWidget w; w.resize(800, 400); w.show();
    w.setSpectrum(fakeFrame());
    w.setStepHz(10000.0);
    mbdsdr::ui::SpectrumDisplay* canvas = w.displayCanvas();
    QVERIFY(canvas);

    // One narrow selected VFO box at the current center.
    QVector<mbdsdr::dsp::VfoMarker> markers;
    mbdsdr::dsp::VfoMarker m;
    m.id = 1; m.freqHz = 98.5e6; m.bandwidthHz = 12500.0;
    m.mode = "NFM"; m.selected = true;
    markers.append(m);
    canvas->setVfoMarkers(markers);

    QSignalSpy spy(canvas, &mbdsdr::ui::SpectrumDisplay::vfoMarkerCenterTuned);
    // Click the trace well left of the tiny box (blank spectrum).
    const QPoint c = canvas->spectrumRect().center();
    const QPoint click = c + QPoint(-150, 0);
    QTest::mousePress(canvas, Qt::LeftButton, Qt::NoModifier, click);
    QTest::mouseRelease(canvas, Qt::LeftButton, Qt::NoModifier, click);

    QVERIFY2(spy.count() >= 1, "clicking blank spectrum must retune the selected VFO");
    const QVariantList last = spy.takeLast();
    const int id = qvariant_cast<int>(last.at(0));
    const double freq = qvariant_cast<double>(last.at(1));
    QCOMPARE(id, 1);
    QVERIFY2(std::abs(freq - 98.5e6) > 10000.0,
             "tuned frequency must move away from the box center");
}

void TestSpectrumInteraction::freqStripDragPansWithoutShift() {
    mbdsdr::ui::SpectrumWidget w; w.resize(800, 400); w.show();
    w.setSpectrum(fakeFrame());
    mbdsdr::ui::SpectrumDisplay* canvas = w.displayCanvas();
    QVERIFY(canvas);
    QVERIFY(canvas->freqStripRect().height() > 0);

    const double loBefore = canvas->visLoHz();
    const QPoint strip = canvas->freqStripRect().center();
    // No Shift held: dragging the strip must pan, not tune.
    QTest::mousePress(canvas, Qt::LeftButton, Qt::NoModifier, strip);
    QTest::mouseMove(canvas, strip + QPoint(80, 0));
    QTest::mouseRelease(canvas, Qt::LeftButton, Qt::NoModifier, strip + QPoint(80, 0));

    QVERIFY2(std::abs(canvas->visLoHz() - loBefore) > 1000.0,
             "dragging the frequency strip must pan the visible window");
}

QTEST_MAIN(TestSpectrumInteraction)
#include "test_spectrum_interaction.moc"
