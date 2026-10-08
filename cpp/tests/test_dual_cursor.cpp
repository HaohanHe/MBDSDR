// SPDX-License-Identifier: MIT
// Dual measurement cursors offscreen checks: the pure helper measurementDeltaHz
// is exact for finite inputs and returns 0 when either cursor is unplaced (NaN),
// placement/clear round-trips, the two cursors are independent, and the
// center-symmetric mirror auxiliary line (f_mirror = 2*tunedFreq - f_cursor)
// lands exactly where the SDR image/sideband symmetry expects it, follows a
// cursor drag, recomputes on retune, and honestly vanishes when no cursor is
// placed.
#include <QtTest>
#include <QSettings>
#include <QMouseEvent>
#include "core/tokens.h"
#include "core/spectrum_frame.h"
#include "ui/spectrum_display.h"

using namespace mbdsdr;
using mbdsdr::ui::SpectrumDisplay;

class TestDualCursor : public QObject {
    Q_OBJECT
private slots:
    void deltaPureFunction();
    void placementRoundTrip();
    void mirrorPositionAboutTunedFreq();
    void mirrorFollowsDrag();
    void mirrorRecomputesOnRetune();
    void mirrorHonestEmptyState();
};

void TestDualCursor::deltaPureFunction() {
    QCOMPARE(SpectrumDisplay::measurementDeltaHz(100e6, 100.5e6), 500e3);
    QCOMPARE(SpectrumDisplay::measurementDeltaHz(100.5e6, 100e6), 500e3); // symmetric
    QCOMPARE(SpectrumDisplay::measurementDeltaHz(std::nan(""), 100e6), 0.0);
    QCOMPARE(SpectrumDisplay::measurementDeltaHz(100e6, std::nan("")), 0.0);
}

void TestDualCursor::placementRoundTrip() {
    SpectrumDisplay w;
    w.clearCursors();
    QVERIFY(!std::isfinite(w.cursorAHz()));
    QVERIFY(!std::isfinite(w.cursorBHz()));
    QCOMPARE(w.cursorDeltaHz(), 0.0);
    w.placeCursorA(100.0e6);
    w.placeCursorB(100.25e6);
    QCOMPARE(w.cursorAHz(), 100.0e6);
    QCOMPARE(w.cursorBHz(), 100.25e6);
    QCOMPARE(w.cursorDeltaHz(), 250e3);
    w.clearCursors();
    QCOMPARE(w.cursorDeltaHz(), 0.0);
}

// --- shared harness: bring the canvas up on a real 2.4 MHz frame ------------
static void pumpAndFeed(SpectrumDisplay& cv, double centerHz = 98.5e6,
                        double sr = 2.4e6) {
    cv.resize(960, 600);
    cv.show();
    QTest::qWait(20);
    mbdsdr::SpectrumFrame f;
    f.centerFreqHz = centerHz;
    f.sampleRateHz = sr;
    f.fftSize = 1024;
    f.dbfs.assign(1024, -80.0f);
    cv.setSpectrum(f);
    QTest::qWait(10);
}

static void sendMouse(SpectrumDisplay* cv, QEvent::Type type, const QPoint& p) {
    QMouseEvent me(type, p, Qt::LeftButton, Qt::LeftButton, Qt::NoModifier);
    QApplication::sendEvent(cv, &me);
    QApplication::processEvents();
}

// f0 = 98.5 MHz. Place A at +300 kHz, B at -200 kHz; the mirror lines must sit
// at the symmetric offsets the other side of the dial.
void TestDualCursor::mirrorPositionAboutTunedFreq() {
    SpectrumDisplay cv;
    pumpAndFeed(cv);
    const double f0 = cv.tunedFrequencyHz();
    QCOMPARE(f0, 98.5e6);

    cv.placeCursorA(98.8e6);   // +300 kHz off the dial
    cv.placeCursorB(98.3e6);   // -200 kHz off the dial
    // mirror = 2*f0 - cursor
    QCOMPARE(cv.mirrorOfCursorHz(1), 2.0 * f0 - 98.8e6);   // == 98.2e6
    QCOMPARE(cv.mirrorOfCursorHz(2), 2.0 * f0 - 98.3e6);   // == 98.7e6
    // Sanity: the mirror is the exact symmetric offset, both inside the window.
    QCOMPARE(cv.mirrorOfCursorHz(1), 98.2e6);
    QCOMPARE(cv.mirrorOfCursorHz(2), 98.7e6);
    // The mirror x lands where the trace frequency mapper puts 98.2 / 98.7 MHz.
    QCOMPARE(cv.xForFrequency(cv.mirrorOfCursorHz(1)), cv.xForFrequency(98.2e6));
    QCOMPARE(cv.xForFrequency(cv.mirrorOfCursorHz(2)), cv.xForFrequency(98.7e6));
    cv.clearCursors();
}

// Drag cursor A by a real mouse gesture; the mirror must track the new position.
void TestDualCursor::mirrorFollowsDrag() {
    SpectrumDisplay cv;
    pumpAndFeed(cv);
    const double f0 = cv.tunedFrequencyHz();

    cv.placeCursorA(98.8e6);
    QCOMPARE(cv.mirrorOfCursorHz(1), 98.2e6);

    // Grab the cursor at its own x and drag right by 60 px -> frequency rises,
    // so the mirror (2*f0 - cursor) must move left by the same offset.
    const int xA = cv.xForFrequency(cv.cursorAHz());
    const int y = cv.height() / 3;
    sendMouse(&cv, QEvent::MouseButtonPress, QPoint(xA, y));
    sendMouse(&cv, QEvent::MouseMove, QPoint(xA + 60, y));
    sendMouse(&cv, QEvent::MouseButtonRelease, QPoint(xA + 60, y));

    QVERIFY2(cv.cursorAHz() > 98.8e6, "drag right did not move cursor A up");
    QCOMPARE(cv.mirrorOfCursorHz(1), 2.0 * f0 - cv.cursorAHz());
    // The mirror moved the OPPOSITE way (lower frequency) after dragging up.
    QVERIFY2(cv.mirrorOfCursorHz(1) < 98.2e6,
             "mirror did not follow the drag to the opposite side of f0");
    cv.clearCursors();
}

// Retune (f0 moves); the cursor position is held, but the mirror must recompute
// about the NEW centre.
void TestDualCursor::mirrorRecomputesOnRetune() {
    SpectrumDisplay cv;
    pumpAndFeed(cv);

    cv.placeCursorA(98.8e6);
    QCOMPARE(cv.mirrorOfCursorHz(1), 98.2e6);

    const double newF0 = 99.0e6;
    cv.tuneAndCenter(newF0);
    QCOMPARE(cv.tunedFrequencyHz(), newF0);
    // Cursor itself is unchanged by a retune; its mirror is about the new dial.
    QCOMPARE(cv.cursorAHz(), 98.8e6);
    QCOMPARE(cv.mirrorOfCursorHz(1), 2.0 * newF0 - 98.8e6);   // == 99.2e6
    QCOMPARE(cv.mirrorOfCursorHz(1), 99.2e6);
    cv.clearCursors();
}

// Honest empty state: no cursor placed -> no mirror frequency (NaN), so the
// paint loop draws nothing.
void TestDualCursor::mirrorHonestEmptyState() {
    SpectrumDisplay cv;
    pumpAndFeed(cv);
    // Fresh cursors are NaN -> mirror NaN (line not painted).
    QVERIFY(!std::isfinite(cv.mirrorOfCursorHz(1)));
    QVERIFY(!std::isfinite(cv.mirrorOfCursorHz(2)));
    // Place A, then clear: the mirror must go back to NaN.
    cv.placeCursorA(98.8e6);
    QVERIFY(std::isfinite(cv.mirrorOfCursorHz(1)));
    cv.clearCursors();
    QVERIFY(!std::isfinite(cv.mirrorOfCursorHz(1)));
    QVERIFY(!std::isfinite(cv.mirrorOfCursorHz(2)));
}

QTEST_MAIN(TestDualCursor)
#include "test_dual_cursor.moc"
