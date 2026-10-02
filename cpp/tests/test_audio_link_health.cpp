// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 ***
//
// Sound-card link health state-machine test. We feed AudioLinkHealth the raw
// outcomes the worker thread observes from QAudioSink (good/bad writes,
// buffer-drained underruns, fatal device errors) and verify:
//   * underruns accumulate into a warning (not a disconnect),
//   * a fatal device error latches Dead,
//   * reconnect attempts are throttled and capped (bounded retry -> GivenUp),
//   * a successful rebuild resets to Healthy.
// No audio device is opened; transitions are fully deterministic.
#include <QtTest/QtTest>

#include "dsp/audio_link_health.h"

using namespace mbdsdr::dsp;

class TestAudioLinkHealth : public QObject {
    Q_OBJECT
private slots:
    void healthyUntilUnderrunThreshold();
    void fatalErrorLatchesDead();
    void reconnectIsThrottledAndCapped();
    void resetAfterSuccessfulRebuild();
};

void TestAudioLinkHealth::healthyUntilUnderrunThreshold() {
    AudioLinkHealth h;
    QCOMPARE(h.state(), AudioLinkHealth::State::Healthy);
    for (int i = 0; i < 19; ++i) h.onUnderrun();   // below the 20 threshold
    QCOMPARE(h.state(), AudioLinkHealth::State::Healthy);
    h.onUnderrun();                                 // reaches 20
    QCOMPARE(h.state(), AudioLinkHealth::State::Underrun);
    QCOMPARE(h.underrunCount(), 20);
    QVERIFY(h.statusText().contains(QStringLiteral("欠载")));
}

void TestAudioLinkHealth::fatalErrorLatchesDead() {
    AudioLinkHealth h;
    h.onDeviceError();
    QCOMPARE(h.state(), AudioLinkHealth::State::Dead);
    QVERIFY(h.statusText().contains(QStringLiteral("重连")));
    // Once Dead, further good writes cannot revive it (must go through rebuild).
    for (int i = 0; i < 10; ++i) h.onWrite(true);
    QCOMPARE(h.state(), AudioLinkHealth::State::Dead);
}

void TestAudioLinkHealth::reconnectIsThrottledAndCapped() {
    AudioLinkHealth h;
    h.onDeviceError();                       // -> Dead
    QCOMPARE(h.state(), AudioLinkHealth::State::Dead);

    // Throttled: the first ticks must NOT trigger a reconnect (cooldown of 10).
    for (int i = 0; i < 9; ++i) QVERIFY(!h.tickReconnect());
    QCOMPARE(h.reconnectAttempts(), 0);
    QVERIFY(h.tickReconnect());             // tick 10 -> attempt #1
    QCOMPARE(h.reconnectAttempts(), 1);
    QVERIFY(!h.tickReconnect());            // immediately back into cooldown

    // Cap: burn through the remaining attempts. kMaxRetries = 20.
    int attempts = 1;
    while (h.state() == AudioLinkHealth::State::Dead) {
        for (int i = 0; i < 10 && h.state() == AudioLinkHealth::State::Dead; ++i)
            h.tickReconnect();
        if (h.state() != AudioLinkHealth::State::Dead) break;
        ++attempts;
        if (attempts > 25) QFAIL("reconnect never gave up");
    }
    QCOMPARE(h.state(), AudioLinkHealth::State::GivenUp);
    QVERIFY(h.statusText().contains(QStringLiteral("不可用")));
    // After GivenUp, further ticks never retry again.
    for (int i = 0; i < 50; ++i) QVERIFY(!h.tickReconnect());
}

void TestAudioLinkHealth::resetAfterSuccessfulRebuild() {
    AudioLinkHealth h;
    h.onDeviceError();
    QCOMPARE(h.state(), AudioLinkHealth::State::Dead);
    h.reset();                              // a successful buildSink
    QCOMPARE(h.state(), AudioLinkHealth::State::Healthy);
    QCOMPARE(h.underrunCount(), 0);
    QCOMPARE(h.reconnectAttempts(), 0);
}

QTEST_MAIN(TestAudioLinkHealth)
#include "test_audio_link_health.moc"
