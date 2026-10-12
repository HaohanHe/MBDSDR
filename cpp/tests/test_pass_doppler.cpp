// SPDX-License-Identifier: MIT
//
// Deterministic (wall-clock free) validation of the pass finder + line-of-sight
// range-rate / Doppler against the published oracle for 28057 (CBERS 2).
//
// Scenario (fixed):
//   * TLE      = 28057 (CBERS 2), copied verbatim from the AIAA-2006-6753
//                verification ephemerides (see tests/test_sgp4.cpp).
//   * Station  = lat 40.0 N, lon 100.0 W (a test coordinate, NOT a user fix).
//   * Sweep    = epoch + 240 min, 6 h window.
//
// Oracle gold reference (1 s steps, elevation up/down-crossing 5 deg):
//   AOS = start + 16120.0 s (az ~128.9 deg), LOS = start + 16771.9 s (az ~4.5)
//   peak elevation 26.661 deg @ start + 16445.3 s
//   f0 = 437.8 MHz:  AOS  fd = +7832 Hz (v_r = -5.363 km/s)
//                    peak fd ~  -881 Hz (v_r = +0.604 km/s)
//                    LOS  fd = -8763 Hz (v_r = +6.000 km/s)
//
// Our finder sweeps at 30 s steps and triggers on elevation crossing 5 deg
// (the practical reception gate, matching the oracle), so AOS/LOS land within
// ~50 s of the gold 5-deg crossing; we assert the loose, physically-meaningful
// bands rather than the exact oracle.

#include "dsp/tle_client.h"
#include "ai/sat_task_planner.h"

#include <cmath>
#include <cstdio>
#include <limits>

#include <QDateTime>
#include <QTime>
#include <QDate>

using namespace mbdsdr::dsp;
using namespace mbdsdr::ai;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

int main() {
    // 28057 (CBERS 2) -- same TLE as tests/test_sgp4.cpp / the oracle driver.
    TleEntry tle;
    tle.name  = "CBERS 2";
    tle.line1 = "1 28057U 03049A   06177.78615833  .00000060  00000-0  35940-4 0  1836";
    tle.line2 = "2 28057  98.4283 247.6961 0000884  88.1964 271.9322 14.35478080140550";

    // Epoch = 2006, day-of-year 177.78615833 UTC (same parse as buildOrbit).
    QDate d0(2006, 1, 1);
    double dayFrac = 177.78615833 - 1.0;
    qint64 dayInt = static_cast<qint64>(std::floor(dayFrac));
    double secs = (dayFrac - dayInt) * 86400.0;
    QDateTime midnight(d0.addDays(dayInt), QTime(0, 0, 0), Qt::UTC);
    QDateTime epoch = QDateTime::fromMSecsSinceEpoch(
        midnight.toMSecsSinceEpoch() + static_cast<qint64>(secs * 1000.0), Qt::UTC);
    QDateTime start = epoch.addSecs(240 * 60);   // sweep start = epoch + 240 min

    const double staLat = 40.0, staLon = -100.0;
    const double f0 = 437.8e6;   // Hz, as in the oracle

    TleClient client;
    QList<SatPass> passes = client.computePasses({tle}, staLat, staLon, start, 6);

    std::printf("passes found in 6h window: %d\n", static_cast<int>(passes.size()));
    if (passes.isEmpty()) {
        std::printf("FAIL: no pass found for 28057\n");
        return 1;
    }
    const SatPass& p = passes.first();

    double aosSec = static_cast<double>(start.secsTo(p.aos));
    double losSec = static_cast<double>(start.secsTo(p.los));
    std::printf("first pass: %s\n", p.name.toUtf8().constData());
    std::printf("  AOS rel start = %.1f s  (gold 16120.0)\n", aosSec);
    std::printf("  LOS rel start = %.1f s  (gold 16771.9)\n", losSec);
    std::printf("  max elevation  = %.3f deg (gold 26.661)\n", p.maxEl);

    check(aosSec >= 16070.0 && aosSec <= 16170.0, "AOS within +-50s of gold");
    check(losSec >= 16720.0 && losSec <= 16820.0, "LOS within +-50s of gold");
    check(p.maxEl >= 24.0 && p.maxEl <= 29.0, "max elevation 24..29 deg");

    // Doppler at the AOS / LOS instants using the live range-rate interface.
    Topocentric tAos = client.propagateAt(p.aos, tle, staLat, staLon);
    Topocentric tLos = client.propagateAt(p.los, tle, staLat, staLon);
    double fdAos = dopplerHz(f0, tAos.rangeRateKmS);
    double fdLos = dopplerHz(f0, tLos.rangeRateKmS);
    std::printf("  AOS: v_r = %+.3f km/s, fd = %+.1f Hz (gold +7832)\n",
                tAos.rangeRateKmS, fdAos);
    std::printf("  LOS: v_r = %+.3f km/s, fd = %+.1f Hz (gold -8763)\n",
                tLos.rangeRateKmS, fdLos);
    check(fdAos > 0.0, "AOS closing => fd > 0");
    check(std::fabs(fdAos) >= 2000.0 && std::fabs(fdAos) <= 15000.0,
          "|fd(AOS)| is LEO@437.8MHz scale (2k..15k Hz)");
    check(fdLos < 0.0, "LOS receding => fd < 0");
    check(std::fabs(fdLos) >= 2000.0 && std::fabs(fdLos) <= 15000.0,
          "|fd(LOS)| is LEO@437.8MHz scale (2k..15k Hz)");

    // Physical symmetry: near peak elevation the satellite is roughly
    // tangential to the station, so |fd| should be small.  Re-sweep the pass
    // at 30 s steps via propagateAt to locate the peak elevation instant.
    double bestEl = -999.0, fdAtPeak = 0.0;
    for (qint64 s = 0; s <= p.aos.secsTo(p.los); s += 30) {
        QDateTime t = p.aos.addSecs(s);
        Topocentric tp = client.propagateAt(t, tle, staLat, staLon);
        if (tp.el > bestEl) {
            bestEl = tp.el;
            fdAtPeak = dopplerHz(f0, tp.rangeRateKmS);
        }
    }
    std::printf("  peak-el re-sweep el=%.3f deg, fd=%.1f Hz (gold ~ -881)\n",
                bestEl, fdAtPeak);
    check(std::fabs(fdAtPeak) < 2000.0, "near peak elevation |fd| < 2000 Hz");

    // f0=0 must yield zero Doppler (honest unknown carrier).
    check(dopplerHz(0.0, tAos.rangeRateKmS) == 0.0, "f0=0 => fd=0");

    // ----------------------------------------------------------------------
    // Freshness / demo-tagging contract for planSatelliteCapture.
    //
    // The test environment has no on-disk celestrak cache, so the public
    // planSatelliteCapture() must NOT silently return ok=true from the 2006
    // builtinTle() snapshot. It must instead return demo=true, ok=false, and a
    // non-empty human warning. The pure seam (planCaptureFromEntries) must never
    // tag demo=true itself -- that tag is the public wrapper's job.
    // ----------------------------------------------------------------------
    {
        // (b) The 2006 epoch on line 1 of 28057 is orders of magnitude older than
        // kMaxTleAgeDays (30 days) when evaluated against "now" in 2026.
        const QDateTime epoch2006 = TleClient::parseTleEpoch(tle.line1);
        check(epoch2006.isValid(), "2006 TLE line1 parses to a valid epoch");
        const double daysOld = TleClient::daysSinceEpoch(epoch2006, QDateTime::currentDateTimeUtc());
        check(daysOld > kMaxTleAgeDays,
              "2006 epoch TLE is far older than kMaxTleAgeDays => stale");
        std::printf("  2006 epoch is %.0f days old (threshold %.1f)\n",
                    daysOld, kMaxTleAgeDays);

        // (a) With no fresh cache in the test env, the public planner must tag
        // the builtin reference result as demo-only and refuse to call ok=true.
        const SatTaskResult sr = planSatelliteCapture(
            QStringLiteral("CBERS"), 40.0, -100.0,
            QDateTime::currentDateTimeUtc(), 12);
        check(sr.demo == true, "no fresh cache => demo==true");
        check(sr.ok == false,  "no fresh cache => ok==false (never a fake real plan)");
        check(!sr.warning.isEmpty(), "demo warning is non-empty");
        std::printf("  demo result: ok=%d demo=%d warning=\"%s\"\n",
                    (int)sr.ok, (int)sr.demo, sr.warning.toUtf8().constData());

        // (c) Pure seam: feed the oracle-fresh (epoch-relative) 28057 TLE at the
        // oracle sweep start. The seam trusts its input -- it must NOT tag demo,
        // and when a pass is found it must set ok=true. We are only pinning the
        // demo/ok tagging contract, not re-claiming physical accuracy.
        const SatTaskResult seam = planCaptureFromEntries(
            {tle}, QStringLiteral("CBERS"), 40.0, -100.0, start, 6);
        check(seam.demo == false, "pure seam never tags demo=true itself");
        check(seam.ok == true,    "pure seam on a known-pass TLE returns ok=true");

        // Freshness gate: a TLE whose epoch is within kMaxTleAgeDays must be
        // accepted; the 2006 one must be rejected. We hand-craft a line1 with a
        // 30-day-ago epoch and reuse the rest of the 28057 record shape.
        QList<TleEntry> freshList;
        TleEntry freshE = tle;
        // Build a line1 with a recent epoch (today - 5 days). YYDDD.DDDDD.
        const QDateTime now = QDateTime::currentDateTimeUtc();
        const QDateTime recent = now.addDays(-5);
        const int yy = recent.date().year() % 100;
        const int doy = recent.date().dayOfYear();
        const double frac =
            static_cast<double>(recent.time().msecsSinceStartOfDay()) / 86400000.0;
        // Reuse the trailing fields of the original line1; only cols 19..32 move.
        freshE.line1 = QString("1 28057U 03049A   %1%2 .00000060  00000-0  35940-4 0  1836")
                           .arg(yy, 2, 10, QLatin1Char('0'))
                           .arg(doy + frac, 0, 'f', 8);
        freshList.append(freshE);
        check(entriesAreFresh(freshList, now) == true,
              "entriesAreFresh accepts a 5-day-old epoch");
        QList<TleEntry> staleList;
        staleList.append(tle);
        check(entriesAreFresh(staleList, now) == false,
              "entriesAreFresh rejects the 2006 builtin snapshot");
    }

    if (failures == 0) std::printf("test_pass_doppler: ALL PASS\n");
    else std::printf("test_pass_doppler: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
