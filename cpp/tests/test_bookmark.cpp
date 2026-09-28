// SPDX-License-Identifier: MIT
#include <QtTest/QtTest>
#include <QSettings>
#include "ui/bookmark_manager.h"

using namespace mbdsdr::ui;

class TestBookmark : public QObject {
    Q_OBJECT
private slots:
    void addRemoveRoundtrip();
};

void TestBookmark::addRemoveRoundtrip() {
    // Clear any residual bookmark entries first so the test is independent of
    // the developer's real config (no fragile temp-dir isolation).
    QSettings("MBDSDR", "MBDSDR").remove("ui/bookmarks");
    BookmarkManager bm;
    bm.load();
    QVERIFY(bm.list().isEmpty());
    bm.add(98.5e6, "test");
    bm.add(145.0e6, "");
    QCOMPARE(bm.list().size(), 2);
    QCOMPARE(bm.list()[0].note, QString("test"));

    // Persist + reload.
    BookmarkManager bm2;
    bm2.load();
    QCOMPARE(bm2.list().size(), 2);

    bm2.remove(0);
    QCOMPARE(bm2.list().size(), 1);
    QVERIFY(bm2.list()[0].freqHz > 100e6);

    // Leave no trace in the real config.
    QSettings("MBDSDR", "MBDSDR").remove("ui/bookmarks");
}

QTEST_MAIN(TestBookmark)
#include "test_bookmark.moc"
