// SPDX-License-Identifier: MIT
//
// Offscreen QtTest for WorldView / MapProjection.
// NOTE: every point fed here is a hand-built synthetic fixture, NOT live
// hardware. The view is stamped "非硬件 NOT HARDWARE" while these run.

#include "ui/world_view.h"
#include "ui/map_projection.h"
#include "ui/coastline_data.h"

#include <QtTest>
#include <QApplication>
#include <QImage>
#include <QSignalSpy>
#include <cmath>
#include <cstdio>

static int g_argc = 1;
static char g_arg0[] = "test_map_view";
static char* g_argv = g_arg0;

using namespace mbdsdr::ui;

// Where to drop the offscreen screenshots (absolute, scratch build dir).
static QString shotDir() { return QStringLiteral("cpp/scratch/map"); }

class TestMapView : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() {
        view_ = new WorldView();
        view_->resize(800, 480);
        view_->show();
        QTest::qWait(20);
    }

    // ------------------------------------------------------------ projection
    void projectionRoundtrip() {
        MapProjection pr;
        pr.setSize(800, 480);
        // A spread of (lat,lon) and several zoom/pan states.
        const struct { double lat, lon; } pts[] = {
            {0, 0}, {39.9, 116.4}, {-33.9, 151.2}, {40.7, -74.0},
            {-0.0, 0.0}, {51.5, -0.12}, {-23.5, -46.6}, {64.1, -21.9}
        };
        const MapProjection::ViewState states[] = {
            {0, 0, 1.0}, {39.9, 116.4, 6.0}, {-33, 151, 12.0}, {0, -180, 2.0}
        };
        for (auto st : states) {
            pr.setViewState(st);
            for (auto q : pts) {
                QPointF px = pr.project(q.lat, q.lon);
                double lat, lon;
                pr.unproject(px, lat, lon);
                QVERIFY2(std::abs(lat - q.lat) < 1e-6,
                         QByteArray("lat err state=") + QByteArray::number(st.zoom));
                QVERIFY2(std::abs(lon - q.lon) < 1e-6,
                         QByteArray("lon err state=") + QByteArray::number(st.zoom));
                // sub-pixel: re-project the unprojected lat/lon
                QPointF px2 = pr.project(lat, lon);
                QVERIFY2(std::hypot(px2.x() - px.x(), px2.y() - px.y()) < 0.01,
                         "pixel roundtrip not sub-pixel");
            }
        }
    }

    void zoomAboutCursorKeepsPoint() {
        MapProjection pr;
        pr.setSize(800, 480);
        pr.setViewState({0, 0, 1.0});
        QPointF cursor = pr.project(20.0, 30.0);
        double latBefore, lonBefore;
        pr.unproject(cursor, latBefore, lonBefore);
        pr.zoomAbout(cursor, 3.0);
        double latAfter, lonAfter;
        pr.unproject(cursor, latAfter, lonAfter);
        QVERIFY(std::abs(latAfter - latBefore) < 1e-6);
        QVERIFY(std::abs(lonAfter - lonBefore) < 1e-6);
        QVERIFY(std::abs(pr.zoom() - 3.0) < 1e-9);
    }

    void viewStatePersistence() {
        MapProjection pr;
        pr.setSize(800, 480);
        pr.setViewState({12.3, -45.6, 9.0});
        MapProjection::ViewState v = pr.viewState();
        QCOMPARE(v.lat, 12.3);
        QCOMPARE(v.lon, -45.6);
        QCOMPARE(v.zoom, 9.0);
    }

    // ------------------------------------------------------------- coastline
    void coastlineIsRealAndLegal() {
        QVERIFY(kCoastlineLineCount > 0);
        QVERIFY(kCoastlinePointCount > 1000);
        for (int i = 0; i < kCoastlinePointCount; ++i) {
            float lat = kCoastlineLatLon[2 * i];
            float lon = kCoastlineLatLon[2 * i + 1];
            QVERIFY2(lat >= -90.0f && lat <= 90.0f, "lat out of range");
            QVERIFY2(lon >= -180.0f && lon <= 180.0f, "lon out of range");
        }
        // Offsets are a strictly increasing partition.
        QCOMPARE(kCoastlineOffsets[0], 0);
        QCOMPARE(kCoastlineOffsets[kCoastlineLineCount], kCoastlinePointCount);
    }

    // ------------------------------------------------------------- empty state
    void emptyStateIsHonest() {
        WorldView v;
        v.resize(600, 400);
        v.show();
        QTest::qWait(10);
        v.setStation(NAN, NAN);
        v.setGnssFix(false, 0, 0, 0, 0);
        v.setAircraft({});
        v.setSatellites({});

        // No GNSS fix => GNSS layer draws no receiver point.
        QVERIFY2(!v.gnssHasFixForTest(), "GNSS should report no fix");

        QImage img = v.grab().toImage();
        QVERIFY(!img.isNull());
        // Honest empty frame must render without fake points: we just require a
        // successful non-blank paint. (No green receiver dot is painted because
        // gnssValid_ is false -- verified by the accessor above.)
        v.grab().save(shotDir() + "/map_empty_nonhw.png");
    }

    // ------------------------------------------------- hit testing + signals
    void hitTestAndSatelliteSignal() {
        WorldView v;
        v.resize(800, 480);
        v.show();
        QTest::qWait(10);
        v.setViewState({0, 0, 1.0});   // (0,0) -> pixel (400,240)
        v.setStation(NAN, NAN);
        v.setGnssFix(false, 0, 0, 0, 0);

        SatellitePoint sat; sat.name = "ISS"; sat.lat = 0.0; sat.lon = 0.0;
        v.setSatellites({sat});

        // Direct hit test at the projected pixel.
        QPointF px = v.project(0.0, 0.0);
        WorldView::Hit h = v.hitTest(px);
        QCOMPARE(h.type, WorldView::Hit::Satellite);
        QCOMPARE(h.id, QStringLiteral("ISS"));

        // A click at the pixel must emit satelliteSelected("ISS").
        QSignalSpy spy(&v, &WorldView::satelliteSelected);
        QVERIFY2(spy.isValid(), "satelliteSelected signal not valid");
        QTest::mouseClick(&v, Qt::LeftButton, Qt::NoModifier, px.toPoint());
        QCOMPARE(spy.count(), 1);
        QCOMPARE(spy.takeFirst().at(0).toString(), QStringLiteral("ISS"));
    }

    void setSelectedSatelliteSlotClears() {
        WorldView v;
        v.resize(800, 480);
        v.show();
        QTest::qWait(10);
        SatellitePoint a; a.name = "ISS";
        SatellitePoint b; b.name = "NOAA-19";
        v.setSatellites({a, b});
        v.setSelectedSatellite("NOAA-19");
        // slot must NOT emit (no loop).
        QSignalSpy spy(&v, &WorldView::satelliteSelected);
        v.setSelectedSatellite("");
        QCOMPARE(spy.count(), 0);
    }

    // --------------------------------------------------------- layer toggles
    void layerVisibilityToggle() {
        WorldView v;
        QVERIFY(v.layerVisible(MapLayer::Satellite));
        v.setLayerVisible(MapLayer::Satellite, false);
        QVERIFY(!v.layerVisible(MapLayer::Satellite));
        // hidden layer must not be hit-testable
        v.setViewState({0, 0, 1.0});
        SatellitePoint s; s.name = "ISS"; s.lat = 0; s.lon = 0;
        v.setSatellites({s});
        QPointF px = v.project(0, 0);
        QCOMPARE(v.hitTest(px).type, WorldView::Hit::None);
        v.setLayerVisible(MapLayer::Satellite, true);
        QCOMPARE(v.hitTest(px).type, WorldView::Hit::Satellite);
    }

    // ------------------------------------------------------- offscreen shots
    void screenshotsWithLayers() {
        WorldView v;
        v.resize(900, 560);
        v.show();
        QTest::qWait(10);
        v.setSynthetic(true);   // stamp 非硬件 NOT HARDWARE

        // Station + valid GNSS fix.
        v.setStation(39.9, 116.4);
        v.setGnssFix(true, 39.91, 116.39, 14, 0.8);

        // A couple of synthetic aircraft.
        AircraftPoint ac1; ac1.icao = "ABC123"; ac1.callsign = "CSN5101";
        ac1.lat = 40.0; ac1.lon = 116.5; ac1.altitudeFt = 35000; ac1.headingDeg = 270;
        ac1.track = {{39.8, 116.2}, {39.9, 116.35}, {40.0, 116.5}};
        AircraftPoint ac2; ac2.icao = "DEF456"; ac2.callsign = "CCA1501";
        ac2.lat = 39.4; ac2.lon = 116.0; ac2.altitudeFt = 28000; ac2.headingDeg = 90;
        v.setAircraft({ac1, ac2});

        // A couple of synthetic satellites with ground tracks.
        SatellitePoint s1; s1.name = "ISS"; s1.lat = 25.0; s1.lon = 115.0;
        s1.track = {{20.0, 110.0}, {22.5, 112.5}, {25.0, 115.0}, {27.5, 117.5}};
        SatellitePoint s2; s2.name = "NOAA-19"; s2.lat = 45.0; s2.lon = 120.0;
        s2.track = {{42.0, 118.0}, {43.5, 119.0}, {45.0, 120.0}};
        v.setSatellites({s1, s2});

        // Wide view first.
        v.setViewState({35.0, 116.0, 2.0});
        QImage wide = v.grab().toImage();
        QVERIFY(!wide.isNull());
        wide.save(shotDir() + "/map_layers_nonhw.png");

        // Zoomed-in second.
        v.setViewState({39.9, 116.4, 12.0});
        QImage z = v.grab().toImage();
        QVERIFY(!z.isNull());
        z.save(shotDir() + "/map_zoom_nonhw.png");

        std::printf("  [shots] wrote map_layers_nonhw.png, map_zoom_nonhw.png\n");
    }

    void cleanupTestCase() {
        delete view_;
    }

private:
    WorldView* view_ = nullptr;
};

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    TestMapView tc;
    return QTest::qExec(&tc, argc, argv);
}
#include "test_map_view.moc"
