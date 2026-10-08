// SPDX-License-Identifier: MIT
// Pure-formatting test for the permanent status strip. The formatters are
// header-only: assert the exact strings (including the honest "--" empty
// states and the （非硬件） tag) the UI slot bodies used to inline.
#include <QtTest/QtTest>
#include <cmath>
#include <limits>
#include "ui/status_format.h"
#include "ui/tune_history.h"

class TestStatusFormat : public QObject {
    Q_OBJECT
private slots:
    void sampleRate();
    void vfoFreq();
    void gain();
    void sourceTag();
    void squelch();
    void rssiSnr();
    void freqAuto();
    void tuneHistoryDedup();
    void tuneHistoryCap();
    void tuneHistoryRoundtrip();
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

// Auto-unit frequency label: MHz/kHz/Hz choice + precision must match the
// bandwidth combo labels this formatter now serves.
void TestStatusFormat::freqAuto() {
    using mbdsdr::ui::formatFrequencyAutoHz;
    QCOMPARE(formatFrequencyAutoHz(2000000.0), QString("2 MHz"));    // bw preset
    QCOMPARE(formatFrequencyAutoHz(200000.0),  QString("200 kHz")); // bw preset
    QCOMPARE(formatFrequencyAutoHz(12500.0),  QString("12.5 kHz"));
    QCOMPARE(formatFrequencyAutoHz(9000.0),    QString("9 kHz"));
    QCOMPARE(formatFrequencyAutoHz(2400.0),    QString("2.4 kHz"));
    QCOMPARE(formatFrequencyAutoHz(500.0),     QString("500 Hz"));
    QCOMPARE(formatFrequencyAutoHz(98.5e6),   QString("98.5 MHz"));
    QCOMPARE(formatFrequencyAutoHz(0.0),       QString("--"));
    QCOMPARE(formatFrequencyAutoHz(-1.0),      QString("--"));
}

// Dedup + move-to-front: repeated read-backs collapse, an old frequency
// revisited jumps to the top without creating a duplicate.
void TestStatusFormat::tuneHistoryDedup() {
    mbdsdr::ui::TuneHistory h(12, 1.0);
    QVERIFY(h.isEmpty());
    QVERIFY(h.maybePush(98.5e6));                 // first entry
    QVERIFY(!h.maybePush(98.5e6 + 0.5));          // within epsilon of newest
    QCOMPARE(h.size(), 1);
    QVERIFY(h.maybePush(100.0e6));                // new frequency
    QCOMPARE(h.size(), 2);
    QCOMPARE(h.entries().first(), 100.0e6);
    QVERIFY(h.maybePush(98.5e6));                 // revisit old -> move to front
    QCOMPARE(h.size(), 2);                        // NOT a duplicate
    QCOMPARE(h.entries().first(), 98.5e6);
    QCOMPARE(h.labelAt(0), QString("98.5 MHz"));
    QVERIFY(!h.maybePush(0.0));                   // invalid ignored
    QCOMPARE(h.size(), 2);
}

// Cap: pushing beyond the limit drops the oldest entry.
void TestStatusFormat::tuneHistoryCap() {
    mbdsdr::ui::TuneHistory h(3, 1.0);
    h.maybePush(98.0e6);
    h.maybePush(99.0e6);
    h.maybePush(100.0e6);
    QCOMPARE(h.size(), 3);
    h.maybePush(101.0e6);                         // evicts the oldest (98 MHz)
    QCOMPARE(h.size(), 3);
    QCOMPARE(h.entries().first(), 101.0e6);
    QCOMPARE(h.entries().last(), 99.0e6);
}

// QSettings round-trip: to/from variant list preserves order and skips junk.
void TestStatusFormat::tuneHistoryRoundtrip() {
    mbdsdr::ui::TuneHistory a(12, 1.0);
    a.maybePush(100.0e6);
    a.maybePush(98.5e6);
    a.maybePush(137.0e6);
    const QVariantList stored = a.toVariantList();
    QCOMPARE(stored.size(), 3);

    mbdsdr::ui::TuneHistory b(12, 1.0);
    b.fromVariantList(stored);
    QCOMPARE(b.size(), 3);
    QCOMPARE(b.entries(), a.entries());
    QCOMPARE(b.labelAt(0), QString("137 MHz"));

    // Malformed / non-positive entries are skipped on restore.
    mbdsdr::ui::TuneHistory c(12, 1.0);
    c.fromVariantList(QVariantList{118.0e6, "junk", -5.0, 0.0});
    QCOMPARE(c.size(), 1);
    QCOMPARE(c.entries().first(), 118.0e6);
}

QTEST_MAIN(TestStatusFormat)
#include "test_status_format.moc"
