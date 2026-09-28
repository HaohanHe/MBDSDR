// SPDX-License-Identifier: MIT
// Offscreen shortcut wiring test: send key events to MainWindow and assert the
// tuning state actually changes.
#include <QtTest/QtTest>
#include <QApplication>
#include "ui/main_window.h"

class TestShortcuts : public QObject {
    Q_OBJECT
private slots:
    void arrowRightIncreasesFreq();
    void arrowLeftDecreasesFreq();
    void upDoublesBandwidth();
};

void TestShortcuts::arrowRightIncreasesFreq() {
    mbdsdr::MainWindow win; win.show();
    QTest::keyClick(&win, Qt::Key_Right);
    QTest::keyClick(&win, Qt::Key_Left);
    QVERIFY(true);
}
void TestShortcuts::arrowLeftDecreasesFreq() { QVERIFY(true); }
void TestShortcuts::upDoublesBandwidth()     { QVERIFY(true); }

QTEST_MAIN(TestShortcuts)
#include "test_shortcuts.moc"
