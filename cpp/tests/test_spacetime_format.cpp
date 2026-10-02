// SPDX-License-Identifier: MIT
// Pure state->text/role mapping test for the 时空视图 (Spacetime) tab. The
// formatters are header-only (no QWidget): assert the exact strings AND the
// honest empty-state roles (no hardware -> system time / no GNSS fix /
// uncompensated), and that a "system" time source is NEVER upgraded to "gnss".
#include <QtTest/QtTest>
#include <cmath>
#include <limits>
#include "ui/spacetime_format.h"

using mbdsdr::ui::SpRole;
using mbdsdr::ui::spLineDoppler;
using mbdsdr::ui::spLineTarget;
using mbdsdr::ui::spLineTimeSource;
using mbdsdr::ui::spRoleKey;
using mbdsdr::ui::spTileDecode;
using mbdsdr::ui::spTileDevice;
using mbdsdr::ui::spTileGnss;
using mbdsdr::ui::spTileSignal;

static float nanf() { return std::numeric_limits<float>::quiet_NaN(); }

class TestSpacetimeFormat : public QObject {
    Q_OBJECT
private slots:
    void roleKeys();
    void deviceTile();
    void signalTile();
    void decodeTile();
    void gnssTile();
    void timeSourceLine();
    void targetLine();
    void dopplerLine();
};

void TestSpacetimeFormat::roleKeys() {
    QCOMPARE(spRoleKey(SpRole::Ok),      "ok");
    QCOMPARE(spRoleKey(SpRole::Warn),    "warn");
    QCOMPARE(spRoleKey(SpRole::Danger),  "danger");
    QCOMPARE(spRoleKey(SpRole::Info),    "info");
    QCOMPARE(spRoleKey(SpRole::Neutral), "neutral");
}

void TestSpacetimeFormat::deviceTile() {
    auto live = spTileDevice(true, "RTL0");
    QCOMPARE(live.text, QString("RTL0"));
    QCOMPARE(spRoleKey(live.role), "ok");
    // Honest empty state: offline is Neutral, NOT an alarm red.
    auto off = spTileDevice(false, "");
    QCOMPARE(off.text, QString("未连接（非硬件）"));
    QCOMPARE(spRoleKey(off.role), "neutral");
}

void TestSpacetimeFormat::signalTile() {
    auto empty = spTileSignal(nanf(), nanf());
    QCOMPARE(empty.text, QString("--"));
    QCOMPARE(spRoleKey(empty.role), "neutral");
    auto one = spTileSignal(-45.2f, nanf());
    QCOMPARE(one.text, QString("-45.2 dBFS · --"));
    QCOMPARE(spRoleKey(one.role), "info");
    auto both = spTileSignal(-45.2f, 12.3f);
    QCOMPARE(both.text, QString("-45.2 dBFS · SNR 12.3 dB"));
}

void TestSpacetimeFormat::decodeTile() {
    auto none = spTileDecode("", 0);
    QCOMPARE(none.text, QString("无解码"));
    QCOMPARE(spRoleKey(none.role), "neutral");
    // A mode with zero frames is still honest "no decode yet".
    auto modeOnly = spTileDecode("WFM", 0);
    QCOMPARE(modeOnly.text, QString("无解码"));
    auto live = spTileDecode("CW", 7);
    QCOMPARE(live.text, QString("CW · 7 帧"));
    QCOMPARE(spRoleKey(live.role), "ok");
}

void TestSpacetimeFormat::gnssTile() {
    auto nofix = spTileGnss(false, 0, 0, 0, 0);
    QCOMPARE(nofix.text, QString("无 fix"));
    QCOMPARE(spRoleKey(nofix.role), "neutral");
    auto fix = spTileGnss(true, 39.9042, 116.4074, 9, 1.2);
    QCOMPARE(fix.text, QString("39.90420,116.40740 · 星9 · HDOP 1.2"));
    QCOMPARE(spRoleKey(fix.role), "ok");
}

void TestSpacetimeFormat::timeSourceLine() {
    // GNSS time: Ok, carries the UTC instant.
    auto g = spLineTimeSource("gnss", "2026-10-02T07:25:45Z");
    QCOMPARE(g.text, QString("时间源 GNSS · 2026-10-02T07:25:45Z UTC"));
    QCOMPARE(spRoleKey(g.role), "ok");
    // System time: Warn with an explicit honest caveat -- never dressed up as GNSS.
    auto s = spLineTimeSource("system", "2026-10-02T07:25:46Z");
    QVERIFY(s.text.contains("本机时钟，非 GNSS 授时"));
    QVERIFY(!s.text.contains("GNSS ·"));   // must not look like a GNSS lock
    QCOMPARE(spRoleKey(s.role), "warn");
    // Unknown source (anything that isn't "gnss") is treated as system, honestly.
    auto unknown = spLineTimeSource("", "");
    QVERIFY(unknown.text.contains("system"));
    QVERIFY(unknown.text.contains("--"));
    QCOMPARE(spRoleKey(unknown.role), "warn");
}

void TestSpacetimeFormat::targetLine() {
    auto none = spLineTarget("", 0.0);
    QCOMPARE(none.text, QString("当前接收目标：无"));
    QCOMPARE(spRoleKey(none.role), "neutral");
    auto t = spLineTarget("NOAA-19", 137.1e6);
    QCOMPARE(t.text, QString("当前接收目标：NOAA-19 · 137.1000 MHz"));
    QCOMPARE(spRoleKey(t.role), "info");
}

void TestSpacetimeFormat::dopplerLine() {
    // Not armed, no target: honest empty state.
    auto idle = spLineDoppler(false, false, std::numeric_limits<double>::quiet_NaN());
    QCOMPARE(idle.text, QString("多普勒补偿：未补偿（无目标）"));
    QCOMPARE(spRoleKey(idle.role), "neutral");
    // Armed + target + real applied compensation value: compensating, Ok, shows Hz.
    auto on = spLineDoppler(true, true, -12.5);
    QVERIFY(on.text.contains("补偿中"));
    QVERIFY(on.text.contains("补偿值 -12.5 Hz"));
    QCOMPARE(spRoleKey(on.role), "ok");
    // Armed + target but no real range-rate reading yet (NaN): compensating, Ok,
    // but NO fabricated number -- honest omission.
    auto onNoVal = spLineDoppler(true, true,
                                 std::numeric_limits<double>::quiet_NaN());
    QVERIFY(onNoVal.text.contains("补偿中"));
    QVERIFY(!onNoVal.text.contains("补偿值"));
    QCOMPARE(spRoleKey(onNoVal.role), "ok");
    // Armed but no target: inconsistent, Warn.
    auto weird = spLineDoppler(true, false, 0.0);
    QVERIFY(weird.text.contains("状态不一致"));
    QCOMPARE(spRoleKey(weird.role), "warn");
}

QTEST_MAIN(TestSpacetimeFormat)
#include "test_spacetime_format.moc"
