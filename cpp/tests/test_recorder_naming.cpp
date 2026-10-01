// SPDX-License-Identifier: MIT
// Recorder naming: two captures in the same second at the same frequency must
// NOT overwrite each other -- the second gets a disambiguated _2 suffix.
#include <QtTest/QtTest>
#include <QTemporaryDir>
#include <QFile>
#include <QDateTime>

#include "dsp/recorder.h"

using namespace mbdsdr;

class TestRecorderNaming : public QObject {
    Q_OBJECT
private slots:
    void sameSecondNoOverwrite();
};

void TestRecorderNaming::sameSecondNoOverwrite() {
    QTemporaryDir dir;
    dsp::Recorder r1, r2;
    // Force the same second stamp by calling back-to-back (both inside <1s).
    QVERIFY(r1.start(dir.path(), 2.4e6, 100.0e6, 0.0, "test"));
    const QString p1 = r1.currentFilePath();
    r1.stop();

    QVERIFY(r2.start(dir.path(), 2.4e6, 100.0e6, 0.0, "test"));
    const QString p2 = r2.currentFilePath();
    r2.stop();

    QVERIFY2(QFile::exists(p1), "first file must still exist");
    QVERIFY2(QFile::exists(p2), "second file must exist");
    QVERIFY2(p1 != p2,
             qPrintable(QString("same-second captures must be disambiguated: %1 vs %2")
                            .arg(p1, p2)));
}

QTEST_MAIN(TestRecorderNaming)
#include "test_recorder_naming.moc"
