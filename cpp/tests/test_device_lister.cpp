// SPDX-License-Identifier: MIT
// DeviceLister enumeration-diff unit tests. Pure logic -- no librtlsdr, no
// libusb, no network. A scripted fake enumerator drives the pull/diff state
// machine: baseline, device added, device removed, unchanged set, rename
// (same index, new name -> removed+added).
#include <QtTest>
#include "dsp/device_lister.h"

using namespace mbdsdr::dsp;

// Scripted enumerator: each enumerate() returns the next item in a fixed queue.
class FakeEnumerator : public IRtlDeviceEnumerator {
public:
    void script(std::vector<std::vector<RtlDeviceInfo>> calls) {
        calls_ = std::move(calls);
        idx_ = 0;
    }
    std::vector<RtlDeviceInfo> enumerate() override {
        if (idx_ < static_cast<int>(calls_.size()))
            return calls_[idx_++];
        return calls_.empty() ? std::vector<RtlDeviceInfo>{}
                              : calls_.back();   // stable after script exhausted
    }
    int callsMade() const { return idx_; }
private:
    std::vector<std::vector<RtlDeviceInfo>> calls_;
    int idx_ = 0;
};

static RtlDeviceInfo mk(uint32_t idx, const QString& name) {
    RtlDeviceInfo d; d.index = idx; d.name = name; return d;
}

class TestDeviceLister : public QObject {
    Q_OBJECT
private slots:
    void firstPollIsBaseline();
    void deviceAdded();
    void deviceRemoved();
    void unchangedEmitsOnlyListChanged();
    void renameIsRemovePlusAdd();
    void emptyToEmpty();
};

void TestDeviceLister::firstPollIsBaseline() {
    FakeEnumerator en;
    en.script({{mk(0, "RTL-SDR #0")}});
    DeviceLister lister(&en);
    QSignalSpy added(&lister, &DeviceLister::devicesAdded);
    QSignalSpy removed(&lister, &DeviceLister::devicesRemoved);
    QSignalSpy changed(&lister, &DeviceLister::deviceListChanged);

    auto list = lister.poll();
    QCOMPARE(list.size(), static_cast<size_t>(1));
    QVERIFY(lister.hasBaseline());
    // First poll: baseline only -> NO added/removed events.
    QCOMPARE(added.count(), 0);
    QCOMPARE(removed.count(), 0);
    QCOMPARE(changed.count(), 1);
}

void TestDeviceLister::deviceAdded() {
    FakeEnumerator en;
    en.script({
        {mk(0, "RTL-SDR #0")},
        {mk(0, "RTL-SDR #0"), mk(1, "RTL-SDR #1")},   // second device plugged
    });
    DeviceLister lister(&en);
    QSignalSpy added(&lister, &DeviceLister::devicesAdded);
    QSignalSpy removed(&lister, &DeviceLister::devicesRemoved);

    lister.poll();   // baseline
    lister.poll();  // poll 2 -> device 1 added

    QCOMPARE(added.count(), 1);
    QCOMPARE(removed.count(), 0);
    const auto addedList = added.first().at(0).value<std::vector<RtlDeviceInfo>>();
    QCOMPARE(addedList.size(), static_cast<size_t>(1));
    QCOMPARE(addedList.front().index, 1u);
    QCOMPARE(lister.current().size(), static_cast<size_t>(2));
}

void TestDeviceLister::deviceRemoved() {
    FakeEnumerator en;
    en.script({
        {mk(0, "RTL-SDR #0"), mk(1, "RTL-SDR #1")},
        {mk(0, "RTL-SDR #0")},                          // device 1 pulled
    });
    DeviceLister lister(&en);
    QSignalSpy added(&lister, &DeviceLister::devicesAdded);
    QSignalSpy removed(&lister, &DeviceLister::devicesRemoved);

    lister.poll();   // baseline (2 devices)
    lister.poll();  // poll 2 -> device 1 removed

    QCOMPARE(added.count(), 0);
    QCOMPARE(removed.count(), 1);
    const auto removedList = removed.first().at(0).value<std::vector<RtlDeviceInfo>>();
    QCOMPARE(removedList.size(), static_cast<size_t>(1));
    QCOMPARE(removedList.front().index, 1u);
    QCOMPARE(lister.current().size(), static_cast<size_t>(1));
}

void TestDeviceLister::unchangedEmitsOnlyListChanged() {
    FakeEnumerator en;
    en.script({
        {mk(0, "RTL-SDR #0")},
        {mk(0, "RTL-SDR #0")},   // identical set
    });
    DeviceLister lister(&en);
    QSignalSpy added(&lister, &DeviceLister::devicesAdded);
    QSignalSpy removed(&lister, &DeviceLister::devicesRemoved);
    QSignalSpy changed(&lister, &DeviceLister::deviceListChanged);

    lister.poll();
    lister.poll();

    QCOMPARE(added.count(), 0);
    QCOMPARE(removed.count(), 0);
    // Every poll emits deviceListChanged (UI refresh).
    QCOMPARE(changed.count(), 2);
}

void TestDeviceLister::renameIsRemovePlusAdd() {
    // Same index, different name -> identity changed -> removed(old)+added(new).
    FakeEnumerator en;
    en.script({
        {mk(0, "Old Name")},
        {mk(0, "New Name")},
    });
    DeviceLister lister(&en);
    QSignalSpy added(&lister, &DeviceLister::devicesAdded);
    QSignalSpy removed(&lister, &DeviceLister::devicesRemoved);

    lister.poll();
    lister.poll();

    QCOMPARE(added.count(), 1);
    QCOMPARE(removed.count(), 1);
    QCOMPARE(removed.first().at(0).value<std::vector<RtlDeviceInfo>>().front().name,
             QStringLiteral("Old Name"));
    QCOMPARE(added.first().at(0).value<std::vector<RtlDeviceInfo>>().front().name,
             QStringLiteral("New Name"));
}

void TestDeviceLister::emptyToEmpty() {
    FakeEnumerator en;
    en.script({{}, {}});
    DeviceLister lister(&en);
    QSignalSpy added(&lister, &DeviceLister::devicesAdded);
    QSignalSpy removed(&lister, &DeviceLister::devicesRemoved);

    lister.poll();   // baseline empty
    QVERIFY(lister.current().empty());
    lister.poll();  // still empty

    QCOMPARE(added.count(), 0);
    QCOMPARE(removed.count(), 0);
}

QTEST_MAIN(TestDeviceLister)
#include "test_device_lister.moc"
