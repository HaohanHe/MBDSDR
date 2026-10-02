// SPDX-License-Identifier: MIT
// Pure-logic test for the shortcut catalog + tuning arithmetic helpers.
// No QApplication / no radio: the catalog strings and the nudge/zoom/gain
// arithmetic are header-only and deterministic.
#include <QtTest/QtTest>
#include <QSet>
#include "ui/shortcuts_catalog.h"

class TestShortcutCatalog : public QObject {
    Q_OBJECT
private slots:
    void catalogRowsDocumented();
    void catalogKeysUnique();
    void nudgeFreq();
    void nudgeBandwidthClamps();
    void cycleStepWraps();
    void stepGainContinuous();
    void stepGainDiscreteTable();
};

void TestShortcutCatalog::catalogRowsDocumented() {
    const auto& rows = mbdsdr::ui::shortcutCatalog();
    QVERIFY2(rows.size() >= 8, "catalog must cover the full wired surface");
    for (const auto& r : rows) {
        QVERIFY(r.sequence && *r.sequence);
        QVERIFY(r.description && *r.description);
    }
    // The two P3 additions must be advertised in the dialog now.
    bool gainRow = false, stepRow = false;
    for (const auto& r : rows) {
        const QString s = QString::fromUtf8(r.sequence);
        if (s.contains("+") && s.contains("-")) gainRow = true;
        if (s.contains("Pg")) stepRow = true;
    }
    QVERIFY(gainRow);
    QVERIFY(stepRow);
}

void TestShortcutCatalog::catalogKeysUnique() {
    const auto& rows = mbdsdr::ui::shortcutCatalog();
    QSet<QString> seen;
    for (const auto& r : rows) {
        const QString s = QString::fromUtf8(r.sequence);
        QVERIFY2(!seen.contains(s), "duplicate shortcut row");
        seen.insert(s);
    }
}

void TestShortcutCatalog::nudgeFreq() {
    using mbdsdr::ui::nudgeFreqHz;
    QCOMPARE(nudgeFreqHz(100.0e6, 1000.0, +1, false), 100.0e6 + 1000.0);
    QCOMPARE(nudgeFreqHz(100.0e6, 1000.0, -1, false), 100.0e6 - 1000.0);
    QCOMPARE(nudgeFreqHz(100.0e6, 1000.0, +1, true), 100.0e6 + 100.0);
    QCOMPARE(nudgeFreqHz(100.0e6, 1000.0, -1, true), 100.0e6 - 100.0);
}

void TestShortcutCatalog::nudgeBandwidthClamps() {
    using mbdsdr::ui::nudgeBandwidthHz;
    QCOMPARE(nudgeBandwidthHz(12500.0, 2.0, 100.0, 2.0e6), 25000.0);
    QCOMPARE(nudgeBandwidthHz(12500.0, 0.5, 100.0, 2.0e6), 6250.0);
    QCOMPARE(nudgeBandwidthHz(1.0e6, 2.0, 100.0, 2.0e6), 2.0e6);   // clamped up
    QCOMPARE(nudgeBandwidthHz(50.0, 0.5, 100.0, 2.0e6), 100.0);    // clamped down
}

void TestShortcutCatalog::cycleStepWraps() {
    using mbdsdr::ui::cycleStepIndex;
    QCOMPARE(cycleStepIndex(4, 7, +1), 5);
    QCOMPARE(cycleStepIndex(6, 7, +1), 0);   // wrap forward
    QCOMPARE(cycleStepIndex(0, 7, -1), 6);  // wrap backward
    QCOMPARE(cycleStepIndex(-3, 7, +1), 1);  // stale index clamps to 0 then advances
    QCOMPARE(cycleStepIndex(2, 0, +1), 0);   // empty table is safe
}

void TestShortcutCatalog::stepGainContinuous() {
    using mbdsdr::ui::stepGainDb;
    const std::vector<double> empty;
    QCOMPARE(stepGainDb(empty, 20.0, +1, 0.0, 50.0, 2.0), 22.0);
    QCOMPARE(stepGainDb(empty, 20.0, -1, 0.0, 50.0, 2.0), 18.0);
    QCOMPARE(stepGainDb(empty, 49.0, +1, 0.0, 50.0, 2.0), 50.0);  // clamped top
    QCOMPARE(stepGainDb(empty, 1.0, -1, 0.0, 50.0, 2.0), 0.0);     // clamped bottom
}

void TestShortcutCatalog::stepGainDiscreteTable() {
    using mbdsdr::ui::stepGainDb;
    // Real RTL-style table, deliberately shuffled on input.
    const std::vector<double> table{36.7, 0.0, 14.7, 29.7, 7.7};
    QCOMPARE(stepGainDb(table, 14.7, +1, 0.0, 50.0, 2.0), 29.7);
    QCOMPARE(stepGainDb(table, 14.7, -1, 0.0, 50.0, 2.0), 7.7);
    QCOMPARE(stepGainDb(table, 36.7, +1, 0.0, 50.0, 2.0), 36.7);   // pinned at top
    QCOMPARE(stepGainDb(table, 0.0, -1, 0.0, 50.0, 2.0), 0.0);    // pinned at bottom
    // Readback snapped onto a level: stepping up moves to the NEXT legal level.
    QCOMPARE(stepGainDb(table, 29.74, +1, 0.0, 50.0, 2.0), 36.7);
}

QTEST_MAIN(TestShortcutCatalog)
#include "test_shortcut_catalog.moc"
