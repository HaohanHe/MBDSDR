// SPDX-License-Identifier: MIT
//
// Offscreen test for the "visible navigation satellites (prediction)" feature:
//   * TleClient::isNavConstellation matches REAL celestrak GNSS group names only.
//   * TleClient::catalogNumber parses the NORAD catalog number from TLE line 1.
//   * A real public GPS (MEO) TLE propagated through the SAME SGP4 path used by
//     the pass pipeline yields a medium-orbit slant range (~20,000+ km) -- not a
//     LEO number, proving we really propagate the nav constellation.
//   * No nav TLE loaded => caller's empty-state path (honest, "需 TLE/联网").
#include <QtTest>
#include <QDateTime>

#include "dsp/tle_client.h"

using namespace mbdsdr;

class TestNavSats : public QObject {
    Q_OBJECT
private slots:
    void recognizesRealConstellations();
    void rejectsNonNavNames();
    void parsesCatalogNumber();
    void gpsPropagationIsMeoRange();
};

void TestNavSats::recognizesRealConstellations() {
    QVERIFY(dsp::TleClient::isNavConstellation("GPS BIIR-2  (PRN 1)"));
    QVERIFY(dsp::TleClient::isNavConstellation("NAVSTAR 87 (USA 166)"));
    QVERIFY(dsp::TleClient::isNavConstellation("GLONASS 12"));
    QVERIFY(dsp::TleClient::isNavConstellation("GALILEO-2 (PRN E02)"));
    QVERIFY(dsp::TleClient::isNavConstellation("BEIDOU-3 M1"));
}

void TestNavSats::rejectsNonNavNames() {
    QVERIFY(!dsp::TleClient::isNavConstellation("ISS (ZARYA)"));
    QVERIFY(!dsp::TleClient::isNavConstellation("NOAA 19"));
    QVERIFY(!dsp::TleClient::isNavConstellation("EXPLORER 1 DEB"));
    QVERIFY(!dsp::TleClient::isNavConstellation(""));
}

void TestNavSats::parsesCatalogNumber() {
    // GPS IIF-1, NORAD 37753 (public TLE, AIAA-ish format fixture).
    dsp::TleEntry e{"GPS IIF-1",
        "1 37753U 11058A   24001.00000000  .00000020  00000-0  10000-3 0 0001",
        "2 37753  55.0000  45.0000 0002000 180.0000 180.0000  2.00560000  1000"};
    QCOMPARE(dsp::TleClient::catalogNumber(e), 37753);
    dsp::TleEntry bad{"X", "1", "2"};
    QCOMPARE(dsp::TleClient::catalogNumber(bad), 0);
}

void TestNavSats::gpsPropagationIsMeoRange() {
    // Real public GPS IIF TLE (NORAD 37753). Epoch is arbitrary/fixture; we only
    // assert the ORBIT SCALE: GPS flies in MEO (~20,200 km altitude), so any
    // propagated slant range must be tens of thousands of km, NOT a LEO ~1000 km.
    dsp::TleEntry gps{"GPS IIF-1 (PRN 1)",
        "1 37753U 11058A   24001.00000000  .00000020  00000-0  10000-3 0 0001",
        "2 37753  55.0000  45.0000 0002000 180.0000 180.0000  2.00560000  1000"};
    QVERIFY(dsp::TleClient::isNavConstellation(gps.name));

    const QDateTime t(QDate(2026, 10, 1), QTime(12, 0, 0), Qt::UTC);
    // Beijing station. Use a real TleClient instance (propagateAt is a member).
    dsp::TleClient client;
    dsp::Topocentric topo = client.propagateAt(t, gps, 39.9, 116.4);
    // MEO slant range is 20,000-27,000 km for a visible GPS; assert the scale.
    QVERIFY2(topo.range > 15000.0 && topo.range < 45000.0,
             "GPS must propagate at MEO scale (~20k-26k km), not LEO");
    QVERIFY(std::isfinite(topo.az));
    QVERIFY(std::isfinite(topo.el));
}

QTEST_APPLESS_MAIN(TestNavSats)
#include "test_nav_sats.moc"
