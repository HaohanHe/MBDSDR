// SPDX-License-Identifier: MIT
// Satellite auto-tune task planner: reuse the real offline SGP4 (same sky-tab
// propagator) to find CBERS-2's next pass, then assert the planner derives the
// capture frequency = downlink carrier + peak-elevation Doppler and emits a real
// capture TaskPlan. Unknown satellite / no downlink => honest ok=false.
#include <QtTest/QtTest>
#include <QDateTime>
#include <QDate>
#include <QTime>
#include <cmath>
#include <ctime>

#include "ai/sat_task_planner.h"

using namespace mbdsdr;

class TestSatTaskPlanner : public QObject {
    Q_OBJECT
private:
    static QDateTime cbersEpochStart() {
        // 28057 (CBERS 2) verification epoch from test_sat_capture.
        QDate d0(2006, 1, 1);
        double dayFrac = 177.78615833 - 1.0;
        qint64 dayInt = static_cast<qint64>(std::floor(dayFrac));
        double secs = (dayFrac - dayInt) * 86400.0;
        QDateTime midnight(d0.addDays(dayInt), QTime(0, 0, 0), Qt::UTC);
        return QDateTime::fromMSecsSinceEpoch(
            midnight.toMSecsSinceEpoch() + static_cast<qint64>(secs * 1000.0),
            Qt::UTC).addSecs(240 * 60);
    }
private slots:
    void findsPassAndAutoTunes();
    void unknownSatHonest();
};

void TestSatTaskPlanner::findsPassAndAutoTunes() {
    const QDateTime start = cbersEpochStart();
    ai::SatTaskResult r = ai::planSatelliteCapture(
        "CBERS", 40.0, -100.0, start, 6);
    QVERIFY2(r.ok, qPrintable(r.error));
    QCOMPARE(r.satName, QString::fromLatin1("CBERS 2"));
    QVERIFY(r.aosUtc.isValid());
    QVERIFY(r.maxElDeg > 0.0);

    // Real downlink carrier is 437.8 MHz; capture = f0 + peak Doppler.
    const double f0 = 437.8e6;
    QVERIFY2(std::fabs(r.captureFreqHz - (f0 + r.dopplerAtPeakHz)) < 1.0,
             qPrintable(QString("capture %1 vs f0+fd %2")
                            .arg(r.captureFreqHz).arg(f0 + r.dopplerAtPeakHz)));
    QVERIFY(r.captureFreqHz > 400e6 && r.captureFreqHz < 480e6);
    QVERIFY(!r.plan.steps.isEmpty());
    // The first step must retune to the derived capture frequency.
    QCOMPARE(r.plan.steps[0].tool, QString::fromLatin1("tune_frequency"));
    QVERIFY2(std::fabs(r.plan.steps[0].args.value("freq_hz").toDouble() - r.captureFreqHz) < 1.0,
             "plan must retune to the SGP4-derived capture frequency");
}

void TestSatTaskPlanner::unknownSatHonest() {
    ai::SatTaskResult r = ai::planSatelliteCapture(
        "NOT_A_REAL_SAT_XYZ", 40.0, -100.0, cbersEpochStart(), 6);
    QVERIFY2(!r.ok, "unknown satellite must report honestly, not invent");
    QVERIFY(!r.error.isEmpty());
}

QTEST_MAIN(TestSatTaskPlanner)
#include "test_sat_task_planner.moc"
