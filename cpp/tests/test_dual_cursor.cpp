// SPDX-License-Identifier: MIT
// Dual measurement cursors offscreen checks: the pure helper measurementDeltaHz
// is exact for finite inputs and returns 0 when either cursor is unplaced (NaN),
// placement/clear round-trips, and the two cursors are independent.
#include <QtTest>
#include <QSettings>
#include "ui/spectrum_display.h"

using namespace mbdsdr;
using mbdsdr::ui::SpectrumDisplay;

class TestDualCursor : public QObject {
    Q_OBJECT
private slots:
    void deltaPureFunction();
    void placementRoundTrip();
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

QTEST_MAIN(TestDualCursor)
#include "test_dual_cursor.moc"
