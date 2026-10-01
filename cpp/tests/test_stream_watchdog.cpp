// SPDX-License-Identifier: MIT
// StreamWatchdog unit tests. Pure state machine -- no hardware, no timers.
// Covers: failure counting, threshold latch, successful-read reset, latched-dead
// behaviour (stays dead until reset), and the reconnect path (reset -> alive).
#include <QtTest>
#include "dsp/stream_watchdog.h"

using namespace mbdsdr::dsp;

class TestStreamWatchdog : public QObject {
    Q_OBJECT
private slots:
    void successKeepsAlive();
    void failureCountsUp();
    void latchesAtThreshold();
    void latchedDeadStaysDead();
    void resetRevives();
    void recoverAfterReconnect();
    void thresholdFloor();
};

void TestStreamWatchdog::successKeepsAlive() {
    StreamWatchdog w(3);
    for (int i = 0; i < 10; ++i)
        QVERIFY(!w.onRead(true));
    QVERIFY(!w.isDead());
    QCOMPARE(w.failureCount(), 0);
}

void TestStreamWatchdog::failureCountsUp() {
    StreamWatchdog w(5);
    QVERIFY(!w.onRead(false));   // 1
    QCOMPARE(w.failureCount(), 1);
    QVERIFY(!w.onRead(false));   // 2
    QVERIFY(!w.onRead(false));   // 3
    QCOMPARE(w.failureCount(), 3);
    QVERIFY(!w.isDead());
}

void TestStreamWatchdog::latchesAtThreshold() {
    StreamWatchdog w(3);
    QVERIFY(!w.onRead(false));   // 1
    QVERIFY(!w.onRead(false));   // 2
    QVERIFY(w.onRead(false));    // 3 -> latched dead
    QVERIFY(w.isDead());
    QCOMPARE(w.failureCount(), 3);
}

void TestStreamWatchdog::latchedDeadStaysDead() {
    StreamWatchdog w(2);
    QVERIFY(!w.onRead(false));   // 1
    QVERIFY(w.onRead(false));    // 2 -> dead
    // Once latched, further reads (even "successful") do NOT revive; only
    // reset() does. This is the honest "device gone" latch.
    QVERIFY(w.onRead(true));
    QVERIFY(w.onRead(false));
    QVERIFY(w.isDead());
}

void TestStreamWatchdog::resetRevives() {
    StreamWatchdog w(2);
    QVERIFY(!w.onRead(false));   // 1 failure, not yet dead
    QVERIFY(w.onRead(false));     // 2nd failure -> latched dead
    QVERIFY(w.isDead());
    w.reset();
    QVERIFY(!w.isDead());
    QCOMPARE(w.failureCount(), 0);
}

void TestStreamWatchdog::recoverAfterReconnect() {
    // Simulates: stream dies (unplug) -> reconnect path calls reset() ->
    // subsequent reads succeed and the watchdog is healthy again.
    StreamWatchdog w(3);
    QVERIFY(!w.onRead(false));
    QVERIFY(!w.onRead(false));
    QVERIFY(w.onRead(false));   // dead
    QVERIFY(w.isDead());

    w.reset();                   // reconnect reopened the device
    QVERIFY(!w.isDead());
    for (int i = 0; i < 5; ++i)
        QVERIFY(!w.onRead(true));
    QVERIFY(!w.isDead());
}

void TestStreamWatchdog::thresholdFloor() {
    // A threshold below 1 must be clamped to 1 (never divide-by-zero / never
    // "dead on first call" with a 0 threshold).
    StreamWatchdog w(0);
    QVERIFY(w.onRead(false));    // threshold clamped to 1 -> dead on first fail
    QVERIFY(w.isDead());
}

QTEST_MAIN(TestStreamWatchdog)
#include "test_stream_watchdog.moc"
