// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 ***
//
// Hot-plug UI-notice seam test. A scripted fake enumerator drives DeviceLister;
// DevicePresenceNotifier forwards the added/removed diff to a single UI-ready
// notice signal. We assert the EXACT prompt text the status bar / device banner
// will show, end to end: diff event -> notice string. No librtlsdr, no USB.
#include <QtTest/QtTest>
#include <QSignalSpy>

#include "dsp/device_lister.h"
#include "dsp/device_presence_notifier.h"

using namespace mbdsdr::dsp;

namespace {
// Scripted enumerator: returns the next canned list each call.
class FakeEnumerator : public IRtlDeviceEnumerator {
public:
    std::vector<std::vector<RtlDeviceInfo>> script;
    size_t next = 0;
    std::vector<RtlDeviceInfo> enumerate() override {
        if (next < script.size()) return script[next++];
        return script.back();
    }
};
RtlDeviceInfo dev(uint32_t i, const QString& name) {
    RtlDeviceInfo d; d.index = i; d.name = name; return d;
}
} // namespace

class TestDevicePresenceNotifier : public QObject {
    Q_OBJECT
private slots:
    void pureMappingAddRemove();
    void plugThenUnplugNoticeText();
    void baselinePollNoNotice();
};

void TestDevicePresenceNotifier::pureMappingAddRemove() {
    // Direct mapping contract: exact strings, never fabricated.
    QCOMPARE(deviceAddedNotice({}), QString());
    QCOMPARE(deviceRemovedNotice({}), QString());
    QCOMPARE(deviceAddedNotice({dev(0, "Fake Dongle")}),
             QStringLiteral("RTL-SDR 已连接：Fake Dongle"));
    QCOMPARE(deviceRemovedNotice({dev(0, "Fake Dongle")}),
             QStringLiteral("RTL-SDR 已移除：Fake Dongle"));
    // Multiple devices joined.
    QCOMPARE(deviceAddedNotice({dev(0, "A"), dev(1, "B")}),
             QStringLiteral("RTL-SDR 已连接：A、B"));
    // Empty name falls back to "RTL-SDR #idx".
    QCOMPARE(deviceAddedNotice({dev(2, "")}),
             QStringLiteral("RTL-SDR 已连接：RTL-SDR #2"));
}

void TestDevicePresenceNotifier::plugThenUnplugNoticeText() {
    FakeEnumerator fe;
    fe.script = {
        {dev(0, "RTL0")},                 // baseline: one device present
        {},                               // unplug it -> removed notice
        {dev(0, "RTL0"), dev(1, "RTL1")}, // plug back + a second -> added notice
    };
    DeviceLister lister(&fe);
    DevicePresenceNotifier notifier(&lister);
    QSignalSpy spy(&notifier, &DevicePresenceNotifier::presenceNotice);
    QVERIFY(spy.isValid());

    notifier.pollOnce();  // baseline, no event
    QCOMPARE(spy.count(), 0);

    notifier.pollOnce();  // unplug
    QCOMPARE(spy.count(), 1);
    QCOMPARE(spy[0][0].toString(), QStringLiteral("RTL-SDR 已移除：RTL0"));
    QCOMPARE(static_cast<DevicePresenceNotifier::Kind>(spy[0][1].toInt()),
             DevicePresenceNotifier::Kind::Removed);

    notifier.pollOnce();  // two devices now present
    QCOMPARE(spy.count(), 2);
    QCOMPARE(spy[1][0].toString(), QStringLiteral("RTL-SDR 已连接：RTL0、RTL1"));
    QCOMPARE(static_cast<DevicePresenceNotifier::Kind>(spy[1][1].toInt()),
             DevicePresenceNotifier::Kind::Connected);
}

void TestDevicePresenceNotifier::baselinePollNoNotice() {
    FakeEnumerator fe;
    fe.script = {{dev(0, "RTL0")}, {dev(0, "RTL0")}};  // unchanged
    DeviceLister lister(&fe);
    DevicePresenceNotifier notifier(&lister);
    QSignalSpy spy(&notifier, &DevicePresenceNotifier::presenceNotice);
    notifier.pollOnce();  // baseline
    notifier.pollOnce();  // unchanged set
    QCOMPARE(spy.count(), 0);   // no spurious notice on a steady state
}

QTEST_MAIN(TestDevicePresenceNotifier)
#include "test_device_presence_notifier.moc"
