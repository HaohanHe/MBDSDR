// SPDX-License-Identifier: MIT
// Activity log tests: a real scan result becomes a fully-fields log entry;
// export / clear / persist work; the log is a SEPARATE structure from bookmarks.
#include <QtTest/QtTest>
#include <QSettings>
#include <QTemporaryDir>
#include <QJsonDocument>
#include <QJsonArray>
#include <QFile>

#include "ui/activity_log.h"
#include "ui/bookmark_manager.h"

using namespace mbdsdr;

class TestActivityLog : public QObject {
    Q_OBJECT
private:
    QTemporaryDir* tmp = nullptr;
private slots:
    void init() {
        tmp = new QTemporaryDir;
        // Windows NativeFormat is the registry, where setPath() is ignored and an
        // unset org/app name makes default QSettings unwritable. Use IniFormat,
        // which honors setPath() on every platform (on Linux native == ini).
        QCoreApplication::setOrganizationName("MBDSDR");
        QCoreApplication::setApplicationName("MBDSDR");
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, tmp->path());
    }
    void cleanup() { delete tmp; tmp = nullptr; }
    void appendFields();
    void exportAndClear();
    void persistsAcrossReload();
    void separateFromBookmarks();
};

void TestActivityLog::appendFields() {
    ui::ActivityLog log;
    log.load();
    ui::SignalActivity a;
    a.timeUtc = QDateTime::fromString("2026-10-01T12:00:00Z", Qt::ISODate);
    a.frequencyHz = 100100000.0;
    a.levelDbfs = -23.4;
    a.mode = "NFM";
    a.source = "scan_band";
    QCOMPARE(log.append(a), 0);
    QCOMPARE(log.count(), 1);
    const ui::SignalActivity r = log.list().first();
    QCOMPARE(r.frequencyHz, 100100000.0);
    QCOMPARE(r.levelDbfs, -23.4);
    QCOMPARE(r.source, QString::fromLatin1("scan_band"));
    // zero/negative frequency rejected honestly
    ui::SignalActivity bad; bad.frequencyHz = 0.0;
    QCOMPARE(log.append(bad), -1);
}

void TestActivityLog::exportAndClear() {
    ui::ActivityLog log; log.load();
    ui::SignalActivity a; a.frequencyHz = 145.0e6; a.levelDbfs = -10.0;
    log.append(a);
    const QString path = tmp->filePath("activity.json");
    QVERIFY2(log.exportToFile(path), "export must write a real file");
    QVERIFY(QFile::exists(path));
    log.clear();
    QCOMPARE(log.count(), 0);
}

void TestActivityLog::persistsAcrossReload() {
    {
        ui::ActivityLog log; log.load();
        ui::SignalActivity a; a.frequencyHz = 433.0e6; a.levelDbfs = -5.0;
        a.source = "scan_band";
        log.append(a);
    }
    ui::ActivityLog log2; log2.load();
    QCOMPARE(log2.count(), 1);
    QVERIFY2(qAbs(log2.list().first().frequencyHz - 433.0e6) < 1.0,
             "entry must survive a reload");
    log2.clear();
}

void TestActivityLog::separateFromBookmarks() {
    ui::ActivityLog log; log.load(); log.clear();
    ui::BookmarkManager bm; bm.load(); bm.clear();
    ui::SignalActivity a; a.frequencyHz = 100.5e6;
    log.append(a);
    QCOMPARE(log.count(), 1);
    QCOMPARE(bm.count(), 0);   // appending an activity entry must NOT add a bookmark
}

QTEST_MAIN(TestActivityLog)
#include "test_activity_log.moc"
