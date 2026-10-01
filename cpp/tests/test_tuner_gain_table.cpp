// SPDX-License-Identifier: MIT
// TunerGainTable snapping unit tests. Pure logic -- no hardware, no librtlsdr,
// no network. The injected table mirrors the R82xx 29-step discrete table that
// rtlsdr_get_tuner_gains returns on real hardware (we use it as a fixed test
// fixture; the production path reads the table from the driver).
//
// Covers: nearest-step snapping, out-of-range clamp, half-step tie, empty-table
// honest passthrough, and availableGainsDb() shape.
#include <QtTest>
#include "dsp/tuner_gain_table.h"

using namespace mbdsdr::dsp;

// Real R82xx legal steps in tenths of dB (librtlsdr.c:966).
static const std::vector<int> kR82xxDb10 = {
    0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229,
    254, 280, 297, 328, 338, 364, 372, 386, 402, 421, 434, 439,
    445, 480, 496
};

class TestTunerGainTable : public QObject {
    Q_OBJECT
private slots:
    void emptyIsPassthrough();
    void snapsToNearest();
    void belowMinClamps();
    void aboveMaxClamps();
    void exactStepUnchanged();
    void halfStepTieGoesUp();
    void availableGainsShape();
    void actualGainReadback();
};

void TestTunerGainTable::emptyIsPassthrough() {
    TunerGainTable t;
    QVERIFY(t.empty());
    QCOMPARE(t.size(), 0);
    // No discrete info: request passes through unchanged (honest).
    QCOMPARE(t.snap(20.0), 20.0);
    QCOMPARE(t.snap(-5.0), -5.0);
    QVERIFY(t.availableGainsDb().empty());
}

void TestTunerGainTable::snapsToNearest() {
    TunerGainTable t;
    t.setTable(kR82xxDb10);
    QVERIFY(!t.empty());
    QCOMPARE(t.size(), 29);

    // 20.0 dB -> 200 tenths. Nearest R82xx step: 197 (19.7) vs 207 (20.7).
    // |200-197|=3, |207-200|=7 -> 197 = 19.7 dB.
    QCOMPARE(t.snap(20.0), 19.7);

    // 15.0 dB -> 150 tenths. Nearest: 144 (14.4) vs 157 (15.7).
    // |150-144|=6, |157-150|=7 -> 144 = 14.4 dB.
    QCOMPARE(t.snap(15.0), 14.4);

    // 40.0 dB -> 400 tenths. Nearest: 402 (40.2) vs 386 (38.6).
    QCOMPARE(t.snap(40.0), 40.2);
}

void TestTunerGainTable::belowMinClamps() {
    TunerGainTable t;
    t.setTable(kR82xxDb10);
    // -10 dB -> -100 tenths, below min 0 -> clamp to 0.
    QCOMPARE(t.snap(-10.0), 0.0);
    QCOMPARE(t.snap(-0.5), 0.0);
}

void TestTunerGainTable::aboveMaxClamps() {
    TunerGainTable t;
    t.setTable(kR82xxDb10);
    // 60 dB -> 600 tenths, above max 496 -> clamp to 49.6.
    QCOMPARE(t.snap(60.0), 49.6);
}

void TestTunerGainTable::exactStepUnchanged() {
    TunerGainTable t;
    t.setTable(kR82xxDb10);
    QCOMPARE(t.snap(0.0), 0.0);
    QCOMPARE(t.snap(14.4), 14.4);   // exact step 144 tenths
    QCOMPARE(t.snap(49.6), 49.6);   // max step
}

void TestTunerGainTable::halfStepTieGoesUp() {
    // Use a tiny table with a deliberate half-step gap to test the tie rule.
    // Steps at 0 and 10 dB (0 and 100 tenths). A request at 5.0 dB (50) is
    // exactly halfway -> must resolve to the HIGHER step (10.0), deterministic.
    TunerGainTable t;
    t.setTable({0, 100});
    QCOMPARE(t.snap(5.0), 10.0);
    // Slightly below the tie point (4.9) -> lower step.
    QCOMPARE(t.snap(4.9), 0.0);
    // Slightly above (5.1) -> higher step.
    QCOMPARE(t.snap(5.1), 10.0);
}

void TestTunerGainTable::availableGainsShape() {
    TunerGainTable t;
    t.setTable(kR82xxDb10);
    const std::vector<double> avail = t.availableGainsDb();
    QCOMPARE(avail.size(), static_cast<size_t>(29));
    // Ascending, in dB.
    for (size_t i = 1; i < avail.size(); ++i)
        QVERIFY(avail[i] > avail[i - 1]);
    QCOMPARE(avail.front(), 0.0);
    QCOMPARE(avail.back(), 49.6);
}

void TestTunerGainTable::actualGainReadback() {
    TunerGainTable t;
    t.setTable(kR82xxDb10);
    // Default actual is 0 until the source records what the driver accepted.
    QCOMPARE(t.actualGainDb(), 0.0);
    t.setActualGainDb(19.7);
    QCOMPARE(t.actualGainDb(), 19.7);
}

QTEST_MAIN(TestTunerGainTable)
#include "test_tuner_gain_table.moc"
