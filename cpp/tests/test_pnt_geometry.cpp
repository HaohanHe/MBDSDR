// SPDX-License-Identifier: MIT
//
// Offscreen unit test for LEO PNT geometry availability (PREDICTION only).
// Injects known elevation sets, asserts the simplified DOP and the four-state
// gate. No propagation, no hardware -- pure functions.
#include <QtTest>
#include <QList>

#include "core/tokens.h"
#include "core/pnt_geometry.h"

using namespace mbdsdr;

class TestPntGeometry : public QObject {
    Q_OBJECT
private slots:
    void dopIsLowWhenSatsHigh();
    void dopIsBadWhenFewSats();
    void fourStateGates();
    void belowHorizonMasked();
};

void TestPntGeometry::dopIsLowWhenSatsHigh() {
    // 6 sats well spread, all high elevation -> good geometry.
    QList<double> els = {60, 55, 50, 65, 45, 70};
    const double dop = geo::simplifiedDop(els, tokens::kGeoMinElevationDeg);
    QVERIFY2(dop < tokens::kGeoDopGood, "high spread sats must give good DOP");
    int usable = 0; for (double el : els) if (el >= tokens::kGeoMinElevationDeg) ++usable;
    QCOMPARE(usable, 6);
    QCOMPARE(geo::classifyGeometry(usable, dop, tokens::kGeoGoodMinVisible,
                                  tokens::kGeoDopGood, tokens::kGeoDopFair,
                                  tokens::kGeoDopPoor),
             geo::GeoQuality::Good);
}

void TestPntGeometry::dopIsBadWhenFewSats() {
    QList<double> els = {30, 40};
    const double dop = geo::simplifiedDop(els, tokens::kGeoMinElevationDeg);
    // <4 visible -> insufficient regardless of DOP.
    QCOMPARE(geo::classifyGeometry(2, dop, tokens::kGeoGoodMinVisible,
                                  tokens::kGeoDopGood, tokens::kGeoDopFair,
                                  tokens::kGeoDopPoor),
             geo::GeoQuality::Insufficient);
}

void TestPntGeometry::fourStateGates() {
    // 4 visible, moderate DOP -> 中.
    QCOMPARE(geo::classifyGeometry(4, 5.0, 6, 4.0, 6.0, 8.0),
             geo::GeoQuality::Fair);
    // 4 visible, DOP 7 -> 差.
    QCOMPARE(geo::classifyGeometry(4, 7.0, 6, 4.0, 6.0, 8.0),
             geo::GeoQuality::Poor);
    // DOP > 8 -> 不足.
    QCOMPARE(geo::classifyGeometry(4, 9.5, 6, 4.0, 6.0, 8.0),
             geo::GeoQuality::Insufficient);
    QVERIFY(QString::fromUtf8(geo::geoQualityToString(geo::GeoQuality::Good)).contains("好"));
}

void TestPntGeometry::belowHorizonMasked() {
    // Two usable high sats + two below the mask -> only 2 count.
    QList<double> els = {60, 55, 1.0, 2.0};
    const double dop = geo::simplifiedDop(els, tokens::kGeoMinElevationDeg);
    int usable = 0; for (double el : els) if (el >= tokens::kGeoMinElevationDeg) ++usable;
    QCOMPARE(usable, 2);
    QVERIFY(std::isfinite(dop));
}

QTEST_MAIN(TestPntGeometry)
#include "test_pnt_geometry.moc"
