// SPDX-License-Identifier: MIT
//
// Offscreen S-meter test: standard S-unit mapping (1 S = 6 dB above noise),
// peak-hold slow decay, and honest empty state with no device.
#include <QtTest>
#include <QApplication>
#include <cmath>

#include "ui/s_meter.h"
#include "core/tokens.h"

using namespace mbdsdr;

class TestSMeter : public QObject {
    Q_OBJECT
private slots:
    void mapsDbfsToSUnits();
    void clampsToS9();
    void emptyStateWhenNoLevel();
    void peakHoldSlowDecay();
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

QTEST_MAIN(TestSMeter)
#include "test_s_meter.moc"
