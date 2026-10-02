// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 ***
//
// Discrete gain-control decision test. GainControlModel.decide() picks the UI
// gain widget mode from the ACTUAL driver gain table:
//   * non-empty table + hardware  -> discrete step combo, enabled, no reason
//   * empty table                 -> honest continuous slider (rtl_tcp/test)
//   * no hardware                 -> control disabled with an explicit reason
// No radio opens; the step-snapping boundary itself is covered separately by
// test_tuner_gain_table.cpp.
#include <QtTest/QtTest>

#include "ui/gain_control_model.h"

using namespace mbdsdr::ui;

class TestGainControlModel : public QObject {
    Q_OBJECT
private slots:
    void tablePresentWithHardware();
    void emptyTableStaysSlider();
    void noHardwareDisables();
};

void TestGainControlModel::tablePresentWithHardware() {
    const std::vector<double> table{0.0, 0.9, 1.4, 19.7, 32.0};
    const GainControlModel m = GainControlModel::decide(table, true);
    QCOMPARE(m.mode, GainControlModel::Mode::DiscreteCombo);
    QVERIFY(m.enabled);
    QVERIFY(m.reason.isEmpty());   // nothing to apologize for
}

void TestGainControlModel::emptyTableStaysSlider() {
    const GainControlModel m = GainControlModel::decide({}, true);
    QCOMPARE(m.mode, GainControlModel::Mode::ContinuousSlider);
    QVERIFY(m.enabled);                       // hardware present
    QVERIFY(m.reason.contains(QStringLiteral("连续增益")));  // honest fallback reason
}

void TestGainControlModel::noHardwareDisables() {
    // Real table but no device connected: control must be disabled, not look usable.
    GainControlModel m = GainControlModel::decide({0.0, 19.7}, false);
    QCOMPARE(m.mode, GainControlModel::Mode::DiscreteCombo);
    QVERIFY(!m.enabled);
    QVERIFY(m.reason.contains(QStringLiteral("无设备")));

    // Empty table AND no device: still disabled with the same honest reason.
    m = GainControlModel::decide({}, false);
    QCOMPARE(m.mode, GainControlModel::Mode::ContinuousSlider);
    QVERIFY(!m.enabled);
    QVERIFY(m.reason.contains(QStringLiteral("无设备")));
}

QTEST_MAIN(TestGainControlModel)
#include "test_gain_control_model.moc"
