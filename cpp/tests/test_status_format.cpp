// SPDX-License-Identifier: MIT
// Pure-formatting test for the permanent status strip. The formatters are
// header-only: assert the exact strings (including the honest "--" empty
// states and the （非硬件） tag) the UI slot bodies used to inline.
#include <QtTest/QtTest>
#include <cmath>
#include <limits>
#include "ui/status_format.h"

class TestStatusFormat : public QObject {
    Q_OBJECT
private slots:
    void sampleRate();
    void vfoFreq();
    void gain();
    void sourceTag();
    void squelch();
    void rssiSnr();
};

void TestStatusFormat::sampleRate() {
    QCOMPARE(mbdsdr::ui::fmtStripSampleRate(2.4e6), QString("2.400 MS/s"));
    QCOMPARE(mbdsdr::ui::fmtStripSampleRate(1.0e6), QString("1.000 MS/s"));
    QCOMPARE(mbdsdr::ui::fmtStripSampleRate(0.0), QString("--"));
    QCOMPARE(mbdsdr::ui::fmtStripSampleRate(-5.0), QString("--"));
}

void TestStatusFormat::vfoFreq() {
    QCOMPARE(mbdsdr::ui::fmtStripVfoFreq(98.5e6), QString("98.500 MHz"));
    QCOMPARE(mbdsdr::ui::fmtStripVfoFreq(137.1e6), QString("137.100 MHz"));
    QCOMPARE(mbdsdr::ui::fmtStripVfoFreq(0.0), QString("--"));
}

void TestStatusFormat::gain() {
    QCOMPARE(mbdsdr::ui::fmtStripGain(25.0), QString("增益 25.0 dB"));
    QCOMPARE(mbdsdr::ui::fmtStripGain(0.0), QString("--"));      // AGC / unknown
    QCOMPARE(mbdsdr::ui::fmtStripGain(-3.0), QString("--"));
}

void TestStatusFormat::sourceTag() {
    QCOMPARE(mbdsdr::ui::fmtStripSource("RTL0", true), QString("RTL0"));
    QCOMPARE(mbdsdr::ui::fmtStripSource("Test Signal", false),
             QString("Test Signal（非硬件）"));
}

void TestStatusFormat::squelch() {
    QCOMPARE(mbdsdr::ui::fmtStripSquelch(false, true),  QString("静噪 OFF"));
    QCOMPARE(mbdsdr::ui::fmtStripSquelch(true, true),   QString("静噪 OPEN"));
    QCOMPARE(mbdsdr::ui::fmtStripSquelch(true, false),  QString("静噪 CLOSED"));
}

void TestStatusFormat::rssiSnr() {
    QCOMPARE(mbdsdr::ui::fmtStripRssi(-45.2f), QString("RSSI -45.2"));
    QCOMPARE(mbdsdr::ui::fmtStripRssi(std::numeric_limits<float>::quiet_NaN()),
             QString("--"));
    QCOMPARE(mbdsdr::ui::fmtStripSnr(12.3f), QString("SNR 12.3"));
    QCOMPARE(mbdsdr::ui::fmtStripSnr(std::numeric_limits<float>::quiet_NaN()),
             QString("--"));
}

QTEST_MAIN(TestStatusFormat)
#include "test_status_format.moc"
