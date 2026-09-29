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

#include <cmath>
#include <cstdio>
#include <limits>

#include <QDateTime>
#include <QTime>
#include <QDate>

using namespace mbdsdr::dsp;

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

    if (failures == 0) std::printf("test_pass_doppler: ALL PASS\n");
    else std::printf("test_pass_doppler: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
