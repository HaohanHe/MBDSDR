// SPDX-License-Identifier: MIT
// Offscreen shortcut-wiring test: send REAL key events to MainWindow and assert
// the tuned centre frequency / step combo / gain slider actually move. Replaces
// the previous stub that only called QVERIFY(true) -- the wiring contract is
// now proven, not merely compiled.
#include <QtTest/QtTest>
#include <QApplication>
#include <cmath>
#include "ui/main_window.h"
#include "dsp/spectrum_engine.h"

class TestShortcuts : public QObject {
    Q_OBJECT
private:
    // WindowShortcut only dispatches when the window is actually active / has
    // focus; offscreen show() does neither on its own. Also pump until the
    // engine's offline test source has been installed (a saved RTL setting may
    // briefly swap in a stub source whose centre is 0; tuning from 0 would make
    // the symmetric +/-step assertions land on the engine's >0 guard).
    void arm(mbdsdr::MainWindow& win) {        // Pump first: let the offline test source install (a saved RTL setting
        // may briefly swap in a stub source whose centre is 0; tuning from 0
        // would make the symmetric +/-step assertions land on the engine's >0
        // guard). Claim focus LAST so pumping cannot steal it back.
        for (int i = 0; i < 200 && win.engine()->centerFreq() < 1.0e6; ++i)
            QApplication::processEvents(QEventLoop::AllEvents, 10);
        // Fixed settling window: the saved-RTL fallback (stub start() fails ->
        // swap in TestSignalSource) can land AFTER the centre first looks sane,
        // wiping a nudge taken before the swap. Let that async swap finish
        // deterministically before we take readings / claim focus.
        for (int i = 0; i < 40; ++i)
            QApplication::processEvents(QEventLoop::AllEvents, 10);
        win.activateWindow();
        win.setFocus();
        QApplication::processEvents(QEventLoop::AllEvents, 10);
    }
private slots:
    // Phase21: the engine no longer auto-falls back to the offline test source.
    // arm() waits for the engine to come up on the synthetic 98.5 MHz source, so
    // opt in explicitly BEFORE any MainWindow (which builds the engine) exists.
    void initTestCase();
    void arrowRightMovesCenterFreq();
    void shiftArrowFineTunes();
    void pageUpAdvancesStepPreset();
    void pageDownWrapsStepPreset();
    void plusKeyStepsGainUp();
};

void TestShortcuts::initTestCase() {
    qputenv("MBDSDR_TEST_SOURCE", "1");   // explicit synthetic offline source
}

void TestShortcuts::arrowRightMovesCenterFreq() {
    mbdsdr::MainWindow win; win.show(); arm(win);
    const double before = win.engine()->centerFreq();
    QTest::keyClick(&win, Qt::Key_Right);
    const double nudged = win.engine()->centerFreq();
    QVERIFY2(nudged > before, "Right arrow must raise the tuned centre frequency");
    QTest::keyClick(&win, Qt::Key_Left);
    QVERIFY2(std::abs(win.engine()->centerFreq() - before) < 1.0,
             "Left arrow must undo the +step nudge");
}

void TestShortcuts::shiftArrowFineTunes() {
    mbdsdr::MainWindow win; win.show(); arm(win);
    const double before = win.engine()->centerFreq();
    QTest::keyClick(&win, Qt::Key_Right, Qt::ShiftModifier);
    const double fine = win.engine()->centerFreq();
    QVERIFY2(fine > before, "Shift+Right must fine-tune upward");
    QVERIFY2(fine - before < 200000.0,
             "fine nudge must be step/10 (100 Hz..100 kHz across presets)");
    QTest::keyClick(&win, Qt::Key_Left, Qt::ShiftModifier);
    QVERIFY2(std::abs(win.engine()->centerFreq() - before) < 1.0,
             "Shift+Left must undo the fine nudge");
}

void TestShortcuts::pageUpAdvancesStepPreset() {
    mbdsdr::MainWindow win; win.show(); arm(win);
    const int i0 = win.harnessStepIndex();
    QTest::keyClick(&win, Qt::Key_PageUp);
    QCOMPARE(win.harnessStepIndex(), (i0 + 1) % 7);   // 7 step presets
    QTest::keyClick(&win, Qt::Key_PageUp);
    QCOMPARE(win.harnessStepIndex(), (i0 + 2) % 7);
}

void TestShortcuts::pageDownWrapsStepPreset() {
    mbdsdr::MainWindow win; win.show(); arm(win);
    const int i0 = win.harnessStepIndex();
    QTest::keyClick(&win, Qt::Key_PageDown);
    QCOMPARE(win.harnessStepIndex(), (i0 - 1 + 7) % 7);
}

void TestShortcuts::plusKeyStepsGainUp() {
    mbdsdr::MainWindow win; win.show(); arm(win);
    // The slider value survives in QSettings across runs (saveSettings writes
    // "rx/gain"), so `before` is NOT necessarily 0. The continuous path clamps
    // to [0,50] -- assert the real clamped step, not an assumed low start.
    const int before = win.harnessGainDb();
    QTest::keyClick(&win, Qt::Key_Plus);
    const int after1 = win.harnessGainDb();
    QCOMPARE(after1, qMin(before + 2, 50));   // continuous path: +/-2 dB, clamped top
    QVERIFY2(after1 >= before, "gain up must not lower the gain");
    QTest::keyClick(&win, Qt::Key_Plus);
    QCOMPARE(win.harnessGainDb(), qMin(after1 + 2, 50));
}

QTEST_MAIN(TestShortcuts)
#include "test_shortcuts.moc"
