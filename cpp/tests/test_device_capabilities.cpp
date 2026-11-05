// SPDX-License-Identifier: MIT
// Device capability readback unit tests. Pure logic (no radio, no UI):
//   * rtl_tcp RTL0 handshake -> real tuner identity + driver-known ranges
//   * unknown tuner -> honest 0/未知 (never a guessed range)
//   * sample-rate options derived from the device's real range (filtered, dead
//     band excluded)
//   * no-device state -> empty option list (honest empty state)
#include <QtTest>
#include "dsp/device_capabilities.h"
// wingdi.h #define's DeviceCapabilities to DeviceCapabilitiesW; undef so the
// project's DeviceCapabilities type resolves (harmless when the macro is absent).
#undef DeviceCapabilities

using namespace mbdsdr::dsp;

class TestDeviceCapabilities : public QObject {
    Q_OBJECT
private slots:
    void tunerNameMapping();
    void handshakeR820T();
    void handshakeE4000();
    void handshakeFcinian();
    void unknownTunerIsHonest();
    void localR820T();
    void localProvenanceNamesDriverQuery();
    void noDeviceIsEmpty();
    void optionsFromRange();
    void optionsFilteredByNarrowRange();
    void deadBandExcluded();
    void optionsStatusNeverFakesZeroTiers();
};

void TestDeviceCapabilities::tunerNameMapping() {
    QCOMPARE(rtlTunerName(RtlTuner::R820T), QStringLiteral("R820T"));
    QCOMPARE(rtlTunerName(RtlTuner::R828D), QStringLiteral("R828D"));
    QCOMPARE(rtlTunerName(RtlTuner::E4000), QStringLiteral("E4000"));
    QCOMPARE(rtlTunerName(RtlTuner::Unknown), QStringLiteral("未知调谐器"));
}

void TestDeviceCapabilities::handshakeR820T() {
    // tuner_type=5 is R820T on the wire (enum rtlsdr_tuner).
    DeviceCapabilities c = rtlCapabilitiesFromHandshake(5, 29, "127.0.0.1:1234");
    QVERIFY(c.connected);
    QVERIFY(c.deviceName.contains("R820T"));
    // Real driver-known range (tuner_r82xx.c:1168).
    QCOMPARE(c.tunableMinHz, 24.0e6);
    QCOMPARE(c.tunableMaxHz, 1766.0e6);
    // Real RTL2832U bounds.
    QCOMPARE(c.sampleRateMinHz, 250000.0);
    QCOMPARE(c.sampleRateMaxHz, 3200000.0);
    QVERIFY(!c.provenance.isEmpty());
}

void TestDeviceCapabilities::handshakeE4000() {
    DeviceCapabilities c = rtlCapabilitiesFromHandshake(1, 14, "");
    QVERIFY(c.deviceName.contains("E4000"));
    QCOMPARE(c.tunableMinHz, 64.0e6);
    QCOMPARE(c.tunableMaxHz, 1700.0e6);
}

void TestDeviceCapabilities::handshakeFcinian() {
    // FC0012 / FC0013 share the 22 – 948.6 MHz Fitipower range.
    DeviceCapabilities c12 = rtlCapabilitiesFromHandshake(2, 5, "");
    QCOMPARE(c12.tunableMinHz, 22.0e6);
    QCOMPARE(c12.tunableMaxHz, 948.6e6);
}

void TestDeviceCapabilities::unknownTunerIsHonest() {
    // A wire value with no driver table entry -> range 0 = 未知, never a guess.
    DeviceCapabilities c = rtlCapabilitiesFromHandshake(999, 0, "ep");
    QVERIFY(c.connected);
    QVERIFY(c.tunableMinHz == 0.0);
    QVERIFY(c.tunableMaxHz == 0.0);
    QVERIFY(c.provenance.contains(QStringLiteral("未知")));
}

void TestDeviceCapabilities::localR820T() {
    // Locally opened device (rtlsdr_get_tuner_type): same real ranges as the
    // handshake path, tuner identity in the name, gain steps in the note.
    DeviceCapabilities c = rtlCapabilitiesLocal(5, 29);
    QVERIFY(c.connected);
    QVERIFY(c.deviceName.contains("R820T"));
    QCOMPARE(c.tunableMinHz, 24.0e6);
    QCOMPARE(c.tunableMaxHz, 1766.0e6);
    QCOMPARE(c.sampleRateMinHz, 250000.0);
    QCOMPARE(c.sampleRateMaxHz, 3200000.0);
    QVERIFY(c.provenance.contains(QStringLiteral("增益档位 29")));
}

void TestDeviceCapabilities::localProvenanceNamesDriverQuery() {
    // The provenance must name the LOCAL driver query -- not the rtl_tcp
    // handshake -- as the identity source. Same numbers, honest attribution.
    DeviceCapabilities c = rtlCapabilitiesLocal(2, 0);
    QVERIFY(c.provenance.contains(QStringLiteral("本机 librtlsdr 驱动查询")));
    QVERIFY(!c.provenance.contains(QStringLiteral("RTL0")));
    // FC0012 range table entry carries through the local path too.
    QCOMPARE(c.tunableMinHz, 22.0e6);
    QCOMPARE(c.tunableMaxHz, 948.6e6);
}

void TestDeviceCapabilities::noDeviceIsEmpty() {
    DeviceCapabilities c = noDeviceCapabilities();
    QVERIFY(!c.connected);
    QVERIFY(buildSampleRateOptions(c).isEmpty());
}

void TestDeviceCapabilities::optionsFromRange() {
    DeviceCapabilities c;
    c.connected = true;
    c.sampleRateMinHz = 250000.0;
    c.sampleRateMaxHz = 3200000.0;
    const QList<double> opts = buildSampleRateOptions(c);
    QVERIFY(!opts.isEmpty());
    // The real standard rates the RTL2832U accepts survive.
    QVERIFY(opts.contains(2400000.0));
    QVERIFY(opts.contains(3200000.0));
    QVERIFY(opts.contains(250000.0));
    // 2.16M is a standard PLL config the desktop list already ships; it must not
    // be missing here (it used to be). 2.88M is what the HARDWARE reports:
    // requesting 2,800,000 comes back as 2,880,000 (+80,000 Hz, real-device
    // readback, E3 -- docs/limitations-register.md 附 M.4 L60), so the candidate
    // table lists only rates the device actually delivers. 2.8M is not a second
    // spelling of the same rate and must NOT be offered.
    QVERIFY(opts.contains(2160000.0));
    QVERIFY(opts.contains(2880000.0));
    QVERIFY(!opts.contains(2800000.0));
    for (int i = 1; i < opts.size(); ++i)
        QVERIFY(opts[i] > opts[i - 1]);   // ascending
}

void TestDeviceCapabilities::optionsFilteredByNarrowRange() {
    // A device reporting a smaller usable range: options must be filtered to
    // [min,max] -- this proves the menu is range-driven, not a fixed list.
    DeviceCapabilities c;
    c.connected = true;
    c.sampleRateMinHz = 250000.0;
    c.sampleRateMaxHz = 2000000.0;
    const QList<double> opts = buildSampleRateOptions(c);
    QVERIFY(!opts.isEmpty());
    for (double r : opts) {
        QVERIFY(r >= 250000.0 - 1.0);
        QVERIFY(r <= 2000000.0 + 1.0);
    }
    QVERIFY(!opts.contains(2400000.0));   // above the reported max
    QVERIFY(!opts.contains(3200000.0));
    QVERIFY(opts.contains(1024000.0));    // inside the range
}

void TestDeviceCapabilities::deadBandExcluded() {
    // The (300k, 900k] dead band must never appear as an option.
    DeviceCapabilities c;
    c.connected = true;
    c.sampleRateMinHz = 250000.0;
    c.sampleRateMaxHz = 3200000.0;
    const QList<double> opts = buildSampleRateOptions(c);
    for (double r : opts) {
        const bool inDeadBand = (r > 300000.0 && r <= 900000.0);
        QVERIFY2(!inDeadBand, qPrintable(QStringLiteral("rate %1 fell in dead band").arg(r)));
    }
}

void TestDeviceCapabilities::optionsStatusNeverFakesZeroTiers() {
    // An EMPTY options array must always carry a status that says WHY, so nobody
    // reads "zero rates" as "this device supports no sample rate".
    DeviceCapabilities none = noDeviceCapabilities();
    QCOMPARE(sampleRateOptionsStatus(none, buildSampleRateOptions(none)),
             QStringLiteral("no_device"));

    DeviceCapabilities unknown;
    unknown.connected = true;            // connected, driver reported no range
    QCOMPARE(sampleRateOptionsStatus(unknown, buildSampleRateOptions(unknown)),
             QStringLiteral("range_unknown"));

    DeviceCapabilities narrow;
    narrow.connected = true;
    narrow.sampleRateMinHz = 1200000.0;  // real range, but no candidate tier fits
    narrow.sampleRateMaxHz = 1300000.0;
    const QList<double> narrowOpts = buildSampleRateOptions(narrow);
    QVERIFY(narrowOpts.isEmpty());
    QCOMPARE(sampleRateOptionsStatus(narrow, narrowOpts),
             QStringLiteral("no_tier_in_range"));

    DeviceCapabilities full;
    full.connected = true;
    full.sampleRateMinHz = 250000.0;
    full.sampleRateMaxHz = 3200000.0;
    const QList<double> fullOpts = buildSampleRateOptions(full);
    QCOMPARE(sampleRateOptionsStatus(full, fullOpts), QStringLiteral("ok"));
    // Every rate offered for a real device must be one of the candidate tiers
    // (the widest-range list is the candidate table itself).
    DeviceCapabilities narrowReal;
    narrowReal.connected = true;
    narrowReal.sampleRateMinHz = 1000000.0;
    narrowReal.sampleRateMaxHz = 1500000.0;
    for (double r : buildSampleRateOptions(narrowReal))
        QVERIFY2(fullOpts.contains(r),
                 qPrintable(QStringLiteral("option %1 is not a candidate tier").arg(r)));
}

QTEST_MAIN(TestDeviceCapabilities)
#include "test_device_capabilities.moc"
