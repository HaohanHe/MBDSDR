// SPDX-License-Identifier: MIT
// Offline tests for the SDR++-style BookmarkManager.
//
// To stay isolated from the developer's real config, the "ui/bookmarks" key
// is removed from QSettings("MBDSDR","MBDSDR") both before and after the run.
#include <QtTest/QtTest>
#include <QSettings>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonDocument>

#include "ui/bookmark_manager.h"

using namespace mbdsdr::ui;

static const char* kKey = "ui/bookmarks";

class TestBookmark : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void lifecycle();
    void cleanupTestCase();
};

void TestBookmark::initTestCase() {
    QSettings("MBDSDR", "MBDSDR").remove(kKey);
}

void TestBookmark::cleanupTestCase() {
    QSettings("MBDSDR", "MBDSDR").remove(kKey);
}

void TestBookmark::lifecycle() {
    // 1. Default empty: no storage -> empty list, never built-in stations.
    {
        BookmarkManager bm;
        bm.load();
        QCOMPARE(bm.count(), 0);
        QVERIFY(bm.list().isEmpty());
        QVERIFY(bm.groups().isEmpty());
    }

    // 2. Add four bookmarks (auto-sorted on insert).
    BookmarkManager bm;
    bm.load();
    QCOMPARE(bm.add(Bookmark{"WFM台",   98.5e6,  "WFM", 120000, ""    }), 0);
    QCOMPARE(bm.add(Bookmark{"航空",    127.6e6,  "AM",  8000,   "AIR" }), 1);
    QCOMPARE(bm.add(Bookmark{"Simplex", 144.8e6,  "NFM", 12500,  "VHF" }), 2);
    QCOMPARE(bm.add(Bookmark{"中继",    145.05e6, "NFM", 12500,  "VHF" }), 3);
    QCOMPARE(bm.count(), 4);

    // 3. Order must be by (group, frequency) ascending.
    QCOMPARE(bm.list()[0].frequencyHz, 98.5e6);
    QCOMPARE(bm.list()[0].group,       QString(""));
    QCOMPARE(bm.list()[1].frequencyHz, 127.6e6);
    QCOMPARE(bm.list()[1].group,       QString("AIR"));
    QCOMPARE(bm.list()[2].frequencyHz, 144.8e6);
    QCOMPARE(bm.list()[2].group,       QString("VHF"));
    QCOMPARE(bm.list()[3].frequencyHz, 145.05e6);
    QCOMPARE(bm.list()[3].group,       QString("VHF"));
    const QList<double> fq = bm.frequencies();
    QCOMPARE(fq.size(), 4);
    QCOMPARE(fq[0], 98.5e6);
    QCOMPARE(fq[1], 127.6e6);
    QCOMPARE(fq[2], 144.8e6);
    QCOMPARE(fq[3], 145.05e6);

    // 4. groups() distinct & ascending (incl. empty default group); byGroup.
    const QStringList gs = bm.groups();
    QCOMPARE(gs.size(), 3);
    QCOMPARE(gs[0], QString(""));
    QCOMPARE(gs[1], QString("AIR"));
    QCOMPARE(gs[2], QString("VHF"));
    const QList<Bookmark> vhf = bm.byGroup("VHF");
    QCOMPARE(vhf.size(), 2);
    QCOMPARE(vhf[0].frequencyHz, 144.8e6);
    QCOMPARE(vhf[1].frequencyHz, 145.05e6);

    // 5. Update index 0 -> moves it into VHF and re-sorts by frequency.
    bm.update(0, Bookmark{"改名", 100.0e6, "NFM", 12500, "VHF"});
    QCOMPARE(bm.count(), 4);
    // Expected order: AIR(127.6), VHF(100.0), VHF(144.8), VHF(145.05)
    QCOMPARE(bm.list()[0].group,       QString("AIR"));
    QCOMPARE(bm.list()[0].frequencyHz, 127.6e6);
    QCOMPARE(bm.list()[1].name,        QString("改名"));
    QCOMPARE(bm.list()[1].frequencyHz, 100.0e6);
    QCOMPARE(bm.list()[1].mode,        QString("NFM"));
    QCOMPARE(bm.list()[1].bandwidthHz, 12500.0);
    QCOMPARE(bm.list()[1].group,       QString("VHF"));
    QCOMPARE(bm.list()[2].frequencyHz, 144.8e6);
    QCOMPARE(bm.list()[3].frequencyHz, 145.05e6);

    // 6. Remove one; out-of-bounds remove/update must not crash or change size.
    bm.removeAt(2);  // drops Simplex @144.8e6
    QCOMPARE(bm.count(), 3);
    bm.removeAt(999);
    bm.update(-1,   Bookmark{"x", 1e6, "", 0, ""});
    bm.update(999,  Bookmark{"x", 1e6, "", 0, ""});
    QCOMPARE(bm.count(), 3);

    // 7. Persistence roundtrip: a fresh manager reads back identical data.
    {
        BookmarkManager bm2;
        bm2.load();
        QCOMPARE(bm2.count(), 3);
        // AIR(航空,127.6,AM,8000), VHF(改名,100.0,NFM,12500), VHF(中继,145.05,NFM,12500)
        QCOMPARE(bm2.list()[0].name,        QString("航空"));
        QCOMPARE(bm2.list()[0].frequencyHz, 127.6e6);
        QCOMPARE(bm2.list()[0].mode,        QString("AM"));
        QCOMPARE(bm2.list()[0].bandwidthHz, 8000.0);
        QCOMPARE(bm2.list()[0].group,       QString("AIR"));
        QCOMPARE(bm2.list()[1].name,        QString("改名"));
        QCOMPARE(bm2.list()[1].frequencyHz, 100.0e6);
        QCOMPARE(bm2.list()[1].mode,        QString("NFM"));
        QCOMPARE(bm2.list()[1].bandwidthHz, 12500.0);
        QCOMPARE(bm2.list()[1].group,       QString("VHF"));
        QCOMPARE(bm2.list()[2].name,        QString("中继"));
        QCOMPARE(bm2.list()[2].frequencyHz, 145.05e6);
        QCOMPARE(bm2.list()[2].mode,        QString("NFM"));
        QCOMPARE(bm2.list()[2].bandwidthHz, 12500.0);
        QCOMPARE(bm2.list()[2].group,       QString("VHF"));
    }

    // Reset state, then exercise the legacy migration path.
    bm.clear();
    QCOMPARE(bm.count(), 0);

    // 8. Legacy {freq, note} -> name, with mode="" / bw=0 / group="".
    {
        QJsonObject o;
        o["freq"] = 145.0e6;
        o["note"] = "旧备注";
        QJsonArray arr;
        arr.append(o);
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(kKey, QJsonDocument(arr).toJson(QJsonDocument::Compact));
    }
    {
        BookmarkManager bm3;
        bm3.load();
        QCOMPARE(bm3.count(), 1);
        QCOMPARE(bm3.list()[0].name,        QString("旧备注"));
        QCOMPARE(bm3.list()[0].frequencyHz, 145.0e6);
        QCOMPARE(bm3.list()[0].mode,        QString(""));
        QCOMPARE(bm3.list()[0].bandwidthHz, 0.0);
        QCOMPARE(bm3.list()[0].group,       QString(""));
    }

    // 9. add() rejects frequency <= 0 (returns -1, no insertion).
    {
        BookmarkManager bm4;
        bm4.load();  // inherits the 1 legacy entry
        QCOMPARE(bm4.count(), 1);
        QCOMPARE(bm4.add(Bookmark{"zero", 0.0,   "", 0, ""}), -1);
        QCOMPARE(bm4.add(Bookmark{"neg",  -1e6,  "", 0, ""}), -1);
        QCOMPARE(bm4.count(), 1);
    }
}

QTEST_MAIN(TestBookmark)
#include "test_bookmark.moc"
