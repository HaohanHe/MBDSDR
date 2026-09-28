// SPDX-License-Identifier: MIT
//
// QtTest (offscreen) for the rewritten SkyView + new ElevationPlot.
// SELF-CONTAINED: no tle_client / gnss headers. The orbit geometry below is a
// hand-built circular pass (parabolic elevation, linear azimuth) so the
// AOS/LOS / peak values are known by construction. All data here is synthetic
// "非硬件/NOT HARDWARE" sample data.

#include "ui/sky_view.h"
#include "ui/elevation_plot.h"

#include <QtTest>
#include <QApplication>
#include <QSignalSpy>
#include <QImage>
#include <QHBoxLayout>
#include <QDateTime>

#include <cmath>

using mbdsdr::ui::SkyView;
using mbdsdr::ui::PassArc;
using mbdsdr::ui::LiveSat;
using mbdsdr::ui::GnssSkySat;
using mbdsdr::ui::ElevationPlot;

namespace {

// Build a known parabolic pass: el(t) = maxEl*sin(pi*t/T), az ramps linearly.
// AOS at t=0 (el=0), LOS at t=T (el=0), peak at t=T/2.
struct SyntheticPass {
    QList<QPair<double,double>> track;          // (az, el)
    QList<QPair<QDateTime,double>> timeSeries;  // (UTC, el) for ElevationPlot
    QDateTime aosUtc, losUtc;
    double maxEl = 0.0;
};

SyntheticPass makePass(const QDateTime& baseUtc, int durationSec,
                       double azAos, double azLos, double peakEl) {
    SyntheticPass sp;
    sp.aosUtc = baseUtc;
    sp.losUtc = baseUtc.addSecs(durationSec);
    sp.maxEl = peakEl;
    const int steps = 60;
    for (int i = 0; i <= steps; ++i) {
        double u = double(i) / double(steps);           // 0..1
        double el = peakEl * std::sin(M_PI * u);       // 0 -> peak -> 0
        double az  = azAos + (azLos - azAos) * u;
        sp.track.append({az, el});
        sp.timeSeries.append({baseUtc.addSecs(int(u * durationSec)), el});
    }
    return sp;
}

constexpr double kEps = 1.0; // px tolerance for hit/paint assertions

} // namespace

class TestSkyView : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() {
        view_ = new SkyView();
        view_->resize(420, 420);
        view_->show();
        ep_ = new ElevationPlot();
        ep_->resize(420, 220);
        ep_->show();
        QTest::qWait(20);
        base_ = QDateTime(QDate(2026, 9, 28), QTime(14, 0, 0), Qt::UTC);
    }

    // --- 1) polar projection: el=0 outer ring, el=90 center, az=0 north ----
    void polarProjectionKnownPixels() {
        const QPointF c(100.0, 100.0);
        const double R = 100.0;
        QPointF n   = SkyView::polarToXY(0,   0, R, c); // N on horizon
        QPointF zen = SkyView::polarToXY(0,  90, R, c); // zenith
        QPointF e   = SkyView::polarToXY(90,  0, R, c); // E on horizon
        QPointF s   = SkyView::polarToXY(180, 0, R, c); // S on horizon
        QPointF w   = SkyView::polarToXY(270, 0, R, c); // W on horizon
        QPointF el30= SkyView::polarToXY(0,  30, R, c); // 30 deg up on N ray

        QVERIFY2(std::abs(n.x() - 100.0) < 1e-6 && std::abs(n.y() - 0.0) < 1e-6,
                 "az=0,el=0 must be north outer ring (top)");
        QVERIFY2(std::abs(zen.x() - 100.0) < 1e-6 && std::abs(zen.y() - 100.0) < 1e-6,
                 "el=90 must map to center");
        QVERIFY2(std::abs(e.x() - 200.0) < 1e-6 && std::abs(e.y() - 100.0) < 1e-6,
                 "az=90 must be east (right)");
        QVERIFY2(std::abs(s.y() - 200.0) < 1e-6, "az=180 must be south (bottom)");
        QVERIFY2(std::abs(w.x() - 0.0) < 1e-6, "az=270 must be west (left)");
        // el=30 -> r = R*(1-30/90) = 2R/3
        QVERIFY2(std::abs(el30.y() - (100.0 - 2.0*R/3.0)) < 1e-6,
                 "el=30 ring radius must be 2R/3");
    }

    // --- empty state is honest, renders without crashing -------------------
    void emptyStateHonest() {
        SkyView v;
        v.resize(300, 300);
        QVERIFY(v.isEmpty());
        QImage img = v.grab().toImage();
        QVERIFY(!img.isNull());
    }

    // --- 2) AOS/LOS round-trip and tooltip carries the expected times ------
    void passTooltipsCarryAosLos() {
        SyntheticPass sp = makePass(base_, 600, 40.0, 140.0, 60.0);
        PassArc arc;
        arc.name = QStringLiteral("ORB_A");
        arc.track = sp.track;
        arc.aosUtc = sp.aosUtc;
        arc.losUtc = sp.losUtc;
        arc.maxEl = sp.maxEl;
        view_->setPasses({arc});

        QCOMPARE(view_->passes().size(), 1);
        QCOMPARE(view_->passes()[0].aosUtc, sp.aosUtc);
        QCOMPARE(view_->passes()[0].losUtc, sp.losUtc);
        QVERIFY(std::abs(view_->passes()[0].maxEl - 60.0) < 1e-9);

        // Probe a point on the arc at the peak (u=0.5): az=90, el=60.
        QPointF c = view_->compassCenter();
        double R = view_->compassRadius();
        QPointF onArc = SkyView::polarToXY(90.0, 60.0, R, c);
        QString tip = view_->tooltipAt(onArc);
        QVERIFY2(tip.contains("ORB_A"), "pass tooltip must name the sat");
        QVERIFY2(tip.contains("AOS"), "tooltip must label AOS");
        QVERIFY2(tip.contains("LOS"), "tooltip must label LOS");
        QVERIFY2(tip.contains("14:00:00"), "tooltip must show AOS UTC time");
        QVERIFY2(tip.contains("14:10:00"), "tooltip must show LOS UTC time");
        QVERIFY2(tip.contains("60.0"), "tooltip must show max elevation");
    }

    // --- 3) ElevationPlot: x axis exactly AOS..LOS, peak = maxEl ----------
    void elevationPlotSpansAosToLos() {
        SyntheticPass sp = makePass(base_, 600, 40.0, 140.0, 60.0);
        ep_->setPass(QStringLiteral("ORB_A"), sp.timeSeries);
        QVERIFY(!ep_->isEmpty());
        QCOMPARE(ep_->aos(), sp.aosUtc);
        QCOMPARE(ep_->los(), sp.losUtc);
        QVERIFY(std::abs(ep_->maxEl() - 60.0) < 1e-9);

        int peak = ep_->peakIndex();
        QVERIFY(peak > 0 && peak < sp.timeSeries.size() - 1);
        QVERIFY(std::abs(sp.timeSeries[peak].second - 60.0) < 1e-9);

        QRectF pr = ep_->plotRect();
        QPointF p0 = ep_->pointAt(0);
        QPointF pn = ep_->pointAt(sp.timeSeries.size() - 1);
        QPointF pp = ep_->pointAt(peak);
        // x-axis covers exactly AOS..LOS
        QVERIFY2(std::abs(p0.x() - pr.left()) < kEps, "first sample at left edge (AOS)");
        QVERIFY2(std::abs(pn.x() - pr.right()) < kEps, "last sample at right edge (LOS)");
        // peak is the highest (smallest y) point on the curve
        QVERIFY2(pp.y() <= p0.y(), "peak must sit above the horizon edges");
        QImage img = ep_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    // --- 4) GNSS layer lands at known az/el and looks distinct -----------
    void gnssSatsDistinctFromOrbit() {
        view_->clearLiveSatellites();
        view_->clearGnssSatellites();

        // Orbit sat, selected -> accent BLUE dot.
        LiveSat orb; orb.name = "ORB_B"; orb.az = 225.0; orb.el = 45.0;
        view_->setLiveSatellites({orb});
        view_->setSelectedSatellite("ORB_B");

        // GNSS used sat -> amber DIAMOND.
        GnssSkySat g; g.prn = "G15"; g.az = 45.0; g.el = 45.0; g.snr = 48.0; g.used = true;
        view_->setGnssSatellites({g});
        QTest::qWait(20);

        QCOMPARE(view_->gnssSatellites().size(), 1);
        QCOMPARE(view_->gnssSatellites()[0].prn, QString("G15"));

        QPointF c = view_->compassCenter();
        double R = view_->compassRadius();
        QPointF pG = SkyView::polarToXY(45.0, 45.0, R, c);  // GNSS diamond
        QPointF pO = SkyView::polarToXY(225.0, 45.0, R, c); // orbit dot

        QImage img = view_->grab().toImage();
        QVERIFY(!img.isNull());

        QRgb cg = img.pixel(pG.toPoint());
        QRgb co = img.pixel(pO.toPoint());
        // Amber diamond: R should clearly exceed B.
        QVERIFY2(qRed(cg) > qBlue(cg) + 30,
                 "GNSS marker must be amber-toned (distinct from blue orbit)");
        // Blue orbit dot: B should clearly exceed R.
        QVERIFY2(qBlue(co) > qRed(co) + 30,
                 "selected orbit sat must be accent-blue");

        // GNSS tooltip carries PRN / el / az / SNR, NOT a selection.
        QString tip = view_->tooltipAt(pG);
        QVERIFY2(tip.contains("G15"), "GNSS tooltip shows PRN");
        QVERIFY2(tip.contains("SNR"), "GNSS tooltip shows SNR");

        view_->setSelectedSatellite("");
    }

    // --- 5) click orbit sat -> satelliteSelected(name); slot round trip ---
    void clickEmitsSelectionAndSlotHighlights() {
        view_->clearLiveSatellites();
        view_->clearGnssSatellites();
        LiveSat a; a.name = "ORB_C"; a.az = 0.0; a.el = 45.0;
        LiveSat b; b.name = "ORB_D"; b.az = 180.0; b.el = 30.0;
        view_->setLiveSatellites({a, b});

        QSignalSpy spy(view_, &SkyView::satelliteSelected);
        QVERIFY(spy.isValid());

        QPointF c = view_->compassCenter();
        double R = view_->compassRadius();
        QPointF pa = SkyView::polarToXY(0.0, 45.0, R, c);
        QTest::mouseClick(view_, Qt::LeftButton, Qt::NoModifier, pa.toPoint());

        QVERIFY2(spy.count() == 1, "clicking an orbit sat must emit satelliteSelected once");
        QCOMPARE(spy.takeFirst().at(0).toString(), QString("ORB_C"));
        QCOMPARE(view_->selectedSatellite(), QString("ORB_C"));

        // Programmatic slot: set + clear, must NOT re-emit.
        view_->setSelectedSatellite("ORB_D");
        QCOMPARE(view_->selectedSatellite(), QString("ORB_D"));
        view_->setSelectedSatellite(""); // empty clears
        QVERIFY(view_->selectedSatellite().isEmpty());
        QCOMPARE(spy.count(), 0);
    }

    // --- 6) current time is stored, view paints a non-null shot ----------
    void currentTimeStoredAndShotSaves() {
        view_->setCurrentTime(base_);
        QCOMPARE(view_->currentTimeUtc(), base_);
        view_->setNonHardware(true);
        QImage img = view_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    void cleanupTestCase() {
        delete view_;
        delete ep_;
    }

private:
    SkyView* view_ = nullptr;
    ElevationPlot* ep_ = nullptr;
    QDateTime base_;
};

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    TestSkyView tc;
    return QTest::qExec(&tc, argc, argv);
}
#include "test_sky_view.moc"
