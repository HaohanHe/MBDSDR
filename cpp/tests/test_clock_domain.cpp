// SPDX-License-Identifier: MIT
//
// Offscreen unit test for the clock-domain offset: Δt = GNSS − system (ms).
// Pure, deterministic: we inject a known GNSS timestamp relative to a chosen
// "system" timestamp and assert the offset, incl. the honest empty state when
// either instant is invalid. No receiver, no wall clock dependency.
#include <QtTest>
#include <QDateTime>

#include "gnss/gnss_types.h"
#include "core/tokens.h"

using mbdsdr::gnss::clockOffsetMs;

class TestClockDomain : public QObject {
    Q_OBJECT
private slots:
    void offsetIsGnssMinusSystem();
    void negativeOffsetWhenGnssBehind();
    void invalidInstantsGiveZero();
    void tokenThrottleIsAtMost10Hz();
};

void TestClockDomain::offsetIsGnssMinusSystem() {
    // Inject a "system" clock and a GNSS fix 1500 ms AHEAD of it.
    QDateTime sys(QDate(2026, 9, 30), QTime(12, 0, 0, 0), Qt::UTC);
    QDateTime gnss = sys.addMSecs(1500);
    QCOMPARE(clockOffsetMs(gnss, sys), qint64(1500));
}

void TestClockDomain::negativeOffsetWhenGnssBehind() {
    QDateTime sys(QDate(2026, 9, 30), QTime(12, 0, 0, 0), Qt::UTC);
    QDateTime gnss = sys.addMSecs(-750);   // receiver behind host clock
    QCOMPARE(clockOffsetMs(gnss, sys), qint64(-750));
}

void TestClockDomain::invalidInstantsGiveZero() {
    QDateTime sys(QDate(2026, 9, 30), QTime(12, 0, 0), Qt::UTC);
    QDateTime invalid;
    // Missing GNSS time (no NMEA clock yet) => empty state, never a fake 0 bias.
    QCOMPARE(clockOffsetMs(invalid, sys), qint64(0));
    QCOMPARE(clockOffsetMs(sys, invalid), qint64(0));
    QCOMPARE(clockOffsetMs(invalid, invalid), qint64(0));
}

// The drag recompute coalesce must be <=10 Hz (>=100 ms), per the honesty
// contract: a fast slider drag never issues more than 10 SGP4 sweeps/sec.
void TestClockDomain::tokenThrottleIsAtMost10Hz() {
    QVERIFY2(mbdsdr::tokens::kSkyPreviewThrottleMs >= 100,
             "preview drag throttle must coalesce to <=10 Hz (>=100 ms)");
    QCOMPARE(mbdsdr::tokens::kSkyTrajectorySamples, 61);
    QCOMPARE(mbdsdr::tokens::kSkyTrajectoryWindowMin, 10);
}

QTEST_MAIN(TestClockDomain)
#include "test_clock_domain.moc"
