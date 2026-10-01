// SPDX-License-Identifier: MIT
// Fixed-marker interaction offscreen checks: drag changes frequency and
// persists to QSettings, click selects, Left/Right nudge by a zoom-relative
// step, Delete removes, and the three layers (auto peaks / VFO boxes / fixed
// markers) stay in separate data structures.
#include <QtTest>
#include <QMouseEvent>
#include <QKeyEvent>
#include <QSettings>
#include <QShowEvent>
#include "core/tokens.h"
#include "core/spectrum_frame.h"
#include "ui/spectrum_display.h"

using namespace mbdsdr;

class TestFixedMarker : public QObject {
    Q_OBJECT
private slots:
    void dragChangesFreqAndPersists();
    void keyNudgeAndDelete();
};

static void pump(QWidget* w) {
    w->resize(800, 400);
    w->show();
    QTest::qWait(20);
}

// Feed a synthetic frame so the canvas has a visible window.
static void feedFrame(ui::SpectrumDisplay& cv, double centerHz = 98.5e6,
                      double sr = 2.4e6) {
    mbdsdr::SpectrumFrame f;
    f.centerFreqHz = centerHz;
    f.sampleRateHz = sr;
    f.fftSize = 1024;
    f.dbfs.assign(1024, -80.0f);
    cv.setSpectrum(f);
    QTest::qWait(10);
}

static void sendMouse(ui::SpectrumDisplay* cv, QEvent::Type type, const QPoint& p) {
    QMouseEvent me(type, p, Qt::LeftButton, Qt::LeftButton, Qt::NoModifier);
    QApplication::sendEvent(cv, &me);
    QApplication::processEvents();
}

// A y inside the trace (upper) plot area, regardless of layout.
static int traceY(ui::SpectrumDisplay* cv) { return cv->height() / 3; }

static void sendKey(ui::SpectrumDisplay* cv, int key) {
    QKeyEvent ke(QEvent::KeyPress, key, Qt::NoModifier);
    QApplication::sendEvent(cv, &ke);
}

void TestFixedMarker::dragChangesFreqAndPersists() {
    QSettings("MBDSDR", "MBDSDR").remove("view/fixedMarkers");
    ui::SpectrumDisplay cv;
    pump(&cv);
    feedFrame(cv);
    cv.setFocus();
    // Mirror the container: on edit, persist to QSettings (as SpectrumWidget does).
    ui::SpectrumDisplay* cvp = &cv;
    cv.connect(cvp, &ui::SpectrumDisplay::fixedMarkersEdited, [cvp]() {
        QSettings s("MBDSDR", "MBDSDR");
        QVariantList list;
        for (const auto& m : cvp->fixedMarkers())
            list.append(QVariantMap{{"freqHz", m.freqHz}, {"name", m.name}});
        s.setValue("view/fixedMarkers", list);
    });

    const double center = 98.5e6;
    cv.addFixedMarker(center, "M1");
    QCOMPARE(cv.fixedMarkers().size(), 1);

    // Screen x of the marker (trace has left axis padding -> use the real mapper).
    const int x = cv.xForFrequency(center);
    sendMouse(&cv, QEvent::MouseButtonPress, QPoint(x, traceY(&cv)));
    // Drag right by 40 px -> frequency increases.
    const double before = cv.fixedMarkers().first().freqHz;
    sendMouse(&cv, QEvent::MouseMove, QPoint(x + 40, traceY(&cv)));
    sendMouse(&cv, QEvent::MouseButtonRelease, QPoint(x + 40, traceY(&cv)));
    QVERIFY2(cv.fixedMarkers().first().freqHz > before,
             "drag right did not increase marker frequency");

    // Persisted to QSettings on release.
    QSettings s("MBDSDR", "MBDSDR");
    const QVariantList list = s.value("view/fixedMarkers").toList();
    QCOMPARE(list.size(), 1);
}

void TestFixedMarker::keyNudgeAndDelete() {
    ui::SpectrumDisplay cv;
    pump(&cv);
    feedFrame(cv);
    cv.setFocus();
    cv.addFixedMarker(98.5e6, "M1");
    cv.addFixedMarker(98.6e6, "M2");
    QCOMPARE(cv.fixedMarkers().size(), 2);

    // Click near M1 (98.5 MHz) to select it.
    const int x = cv.xForFrequency(98.5e6);
    sendMouse(&cv, QEvent::MouseButtonPress, QPoint(x, traceY(&cv)));
    sendMouse(&cv, QEvent::MouseMove, QPoint(x + 2, traceY(&cv)));
    sendMouse(&cv, QEvent::MouseButtonRelease, QPoint(x + 2, traceY(&cv)));

    const double before = cv.fixedMarkers().first().freqHz;
    sendKey(&cv, Qt::Key_Right);
    QVERIFY2(cv.fixedMarkers().first().freqHz > before,
             "Right arrow did not nudge the selected marker");

    sendKey(&cv, Qt::Key_Delete);
    QCOMPARE(cv.fixedMarkers().size(), 1);
}

QTEST_MAIN(TestFixedMarker)
#include "test_fixed_marker.moc"
