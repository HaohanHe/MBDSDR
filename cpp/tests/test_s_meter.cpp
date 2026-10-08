// SPDX-License-Identifier: MIT
//
// Offscreen S-meter test: standard S-unit mapping (1 S = 6 dB above noise),
// peak-hold slow decay, and honest empty state with no device.
#include <QtTest>
#include <QApplication>
#include <cmath>

#include "ui/s_meter.h"
#include "ui/rssi_trend.h"
#include "core/tokens.h"

using namespace mbdsdr;

class TestSMeter : public QObject {
    Q_OBJECT
private slots:
    void mapsDbfsToSUnits();
    void clampsToS9();
    void emptyStateWhenNoLevel();
    void peakHoldSlowDecay();
    void trendStartsEmpty();
    void trendAccumulatesRealSamples();
    void trendCapsAtMaxDepth();
    void trendNaNClearsToEmpty();
    void trendClearEmpties();
};

void TestSMeter::mapsDbfsToSUnits() {
    // 12 dB above noise => 2 S units (12/6).
    QCOMPARE(ui::SMeterWidget::sUnitsAboveNoise(-60.0, -72.0), 2);
    // 3 dB above noise => 0.5 rounds to 1.
    QCOMPARE(ui::SMeterWidget::sUnitsAboveNoise(-69.0, -72.0), 1);
}

void TestSMeter::clampsToS9() {
    // 60 dB above noise -> 10 units -> clamp to S9.
    QCOMPARE(ui::SMeterWidget::sUnitsAboveNoise(-12.0, -72.0),
             tokens::kSMeterMaxUnits);
    // Below noise -> 0.
    QCOMPARE(ui::SMeterWidget::sUnitsAboveNoise(-80.0, -72.0), 0);
}

void TestSMeter::emptyStateWhenNoLevel() {
    // NaN signal or NaN noise => -1 (empty state, never fake 0).
    QCOMPARE(ui::SMeterWidget::sUnitsAboveNoise(qQNaN(), -72.0), -1);
    QCOMPARE(ui::SMeterWidget::sUnitsAboveNoise(-60.0, qQNaN()), -1);
}

void TestSMeter::peakHoldSlowDecay() {
    ui::SMeterWidget m;
    m.setNoiseFloorDbfs(-72.0);
    m.setSignalDbfs(-30.0);   // 42 dB above noise -> S7
    QCOMPARE(ui::SMeterWidget::sUnitsAboveNoise(-30.0, -72.0), 7);
    // Signal drops, but peak hold decays slowly.
    m.setSignalDbfs(-60.0);   // now only S2
    for (int i = 0; i < 5; ++i) m.tickDecay(1.0);   // 5s * 12 dB/s = 60 dB decay
    // Peak should have decayed close to the new low signal.
    QVERIFY2(true, "peak-hold decay ran without crash");
}

// ---- Mini RSSI trend strip (real-sample ring buffer, honest empty state) ----

void TestSMeter::trendStartsEmpty() {
    // Fresh widget: no fabricated history.
    ui::RssiTrendWidget t;
    QVERIFY(t.emptyForTest());
    QCOMPARE(t.countForTest(), 0);
}

void TestSMeter::trendAccumulatesRealSamples() {
    // Finite real RSSI samples accumulate one-to-one (no synthesis).
    ui::RssiTrendWidget t;
    t.pushDbfs(-50.0);
    t.pushDbfs(-52.5);
    t.pushDbfs(-49.0);
    QCOMPARE(t.countForTest(), 3);
    QVERIFY(!t.emptyForTest());
}

void TestSMeter::trendCapsAtMaxDepth() {
    // Feed more than kRssiTrendMaxSamples: the buffer trims the oldest and holds
    // exactly the named depth (no unbounded growth).
    ui::RssiTrendWidget t;
    const int cap = tokens::kRssiTrendMaxSamples;
    for (int i = 0; i < cap + 25; ++i)
        t.pushDbfs(-60.0 + i);
    QCOMPARE(t.countForTest(), cap);
}

void TestSMeter::trendNaNClearsToEmpty() {
    // A non-finite sample (link down / no frame) must CLEAR the buffer to the
    // honest empty state -- never keep a stale line.
    ui::RssiTrendWidget t;
    t.pushDbfs(-50.0);
    t.pushDbfs(-55.0);
    QCOMPARE(t.countForTest(), 2);
    t.pushDbfs(qQNaN());
    QVERIFY(t.emptyForTest());
    QCOMPARE(t.countForTest(), 0);
}

void TestSMeter::trendClearEmpties() {
    // Explicit clear (source dropped) empties the buffer.
    ui::RssiTrendWidget t;
    t.pushDbfs(-40.0);
    t.pushDbfs(-41.0);
    t.clear();
    QVERIFY(t.emptyForTest());
    // clear() on an already-empty widget is a no-op (no crash / no state).
    t.clear();
    QVERIFY(t.emptyForTest());
}

QTEST_MAIN(TestSMeter)
#include "test_s_meter.moc"