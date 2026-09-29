// SPDX-License-Identifier: MIT
// AircraftTracker unit tests: empty/honest state, field merge (no fake
// defaults), bounded track tail, simulated-clock TTL pruning, multi-aircraft
// bookkeeping, and haversine station distance.
#include <cmath>
#include <cstdio>
#include <QDateTime>
#include <QTimeZone>

#include "ui/aircraft_tracker.h"

using namespace mbdsdr::ui;
using mbdsdr::dsp::AircraftInfo;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

// Deterministic "simulated clock" so TTL behaviour is hermetic.
static QDateTime clock0() {
    return QDateTime::fromSecsSinceEpoch(1700000000, QTimeZone::UTC);
}

// A message that carries ONLY a callsign, no fix, no velocity.
static AircraftInfo callsignOnly(const QString& icao, const QString& cs) {
    AircraftInfo a;
    a.icao = icao;
    a.callsign = cs;
    return a;
}

// A message that carries ONLY a decoded position.
static AircraftInfo withPosition(const QString& icao, double lat, double lon) {
    AircraftInfo a;
    a.icao = icao;
    a.lat = lat;
    a.lon = lon;
    a.hasPosition = true;
    return a;
}

int main() {
    // 1. Initial empty; clear() empties again.
    {
        AircraftTracker t;
        check(t.isEmpty(), "fresh tracker isEmpty");
        check(t.count() == 0, "fresh tracker count==0");
        check(t.points().isEmpty(), "fresh tracker points empty");
        t.upsert(callsignOnly("ABCDEF", "TST1"), clock0());
        check(!t.isEmpty(), "after upsert not empty");
        t.clear();
        check(t.isEmpty() && t.count() == 0, "clear() -> empty again");
    }

    // 2. Callsign-only frame: count==1, but points() stays empty (no fake map
    //    dot), while aircraft() exposes the row for the table.
    {
        AircraftTracker t;
        QDateTime now = clock0();
        bool isNew = t.upsert(callsignOnly("4CA123", "DLH123"), now);
        check(isNew, "first upsert of new ICAO returns true");
        check(t.count() == 1, "callsign-only frame: count==1");
        check(t.points().isEmpty(), "callsign-only frame: points() empty (honest)");
        auto ac = t.aircraft();
        check(ac.size() == 1 && ac[0].callsign == "DLH123",
              "aircraft() lists the callsign-only row");
        check(!ac[0].hasPosition, "row flagged hasPosition==false");
    }

    // 3. Same ICAO later sends a fix: exactly one point, correct lat/lon,
    //    callsign preserved, track contains the fix.
    {
        AircraftTracker t;
        QDateTime now = clock0();
        t.upsert(callsignOnly("4CA123", "DLH123"), now);
        AircraftInfo pos = withPosition("4CA123", 39.9, 116.4);
        pos.altitudeFt = 30000;
        bool again = t.upsert(pos, now);
        check(!again, "second upsert of known ICAO returns false");
        check(t.count() == 1, "still one aircraft");
        auto pts = t.points();
        check(pts.size() == 1, "after fix: points() exactly 1");
        check(std::abs(pts[0].lat - 39.9) < 1e-9 &&
              std::abs(pts[0].lon - 116.4) < 1e-9, "point lat/lon correct");
        check(pts[0].callsign == "DLH123", "callsign kept across position merge");
        check(pts[0].altitudeFt == 30000, "altitude carried into point");
        check(pts[0].track.size() == 1, "track contains the single fix");
        check(std::abs(pts[0].track.last().first - 39.9) < 1e-9,
              "track tail equals latest fix");
    }

    // 4. More than 12 fixes: track is capped, oldest points dropped.
    {
        AircraftTracker t;
        QDateTime now = clock0();
        t.upsert(callsignOnly("7CDEF0", "BAW9"), now);
        for (int i = 0; i < 20; ++i) {
            t.upsert(withPosition("7CDEF0", 10.0 + i * 0.01, 20.0 + i * 0.01),
                     now.addSecs(i));
        }
        auto pts = t.points();
        check(pts.size() == 1, "one aircraft -> one point");
        check(pts[0].track.size() == 12, "track capped at 12 points");
        // Points i=0..19 appended; i=0..7 dropped, tail holds i=8..19.
        check(std::abs(pts[0].track.first().first - (10.0 + 8.0 * 0.01)) < 1e-9,
              "oldest points pruned from tail");
        check(std::abs(pts[0].track.last().first - (10.0 + 19.0 * 0.01)) < 1e-9,
              "newest point kept at tail");
    }

    // 5. TTL with a simulated clock: not-yet-expired kept, exactly-at-boundary
    //    kept (strict <), past-TTL removed and reported, then empty.
    {
        AircraftTracker t;
        t.setTtlSeconds(30);
        QDateTime t0 = clock0();
        t.upsert(callsignOnly("111111", "AAA1"), t0);

        check(t.prune(t0.addSecs(29)).isEmpty(), "prune at +29s keeps aircraft");
        check(t.count() == 1, "still present before TTL");
        check(t.prune(t0.addSecs(30)).isEmpty(), "prune exactly at +30s keeps (strict <)");
        QStringList dead = t.prune(t0.addSecs(31));
        check(dead.size() == 1 && dead.first() == "111111",
              "prune at +31s returns the removed ICAO");
        check(t.isEmpty(), "after pruning the only aircraft -> isEmpty");
    }

    // 6. Multiple aircraft; missing fields never overwritten by fake defaults,
    //    in both merge orders.
    {
        AircraftTracker t;
        QDateTime now = clock0();
        // order A: position first, then callsign -> position must survive.
        t.upsert(withPosition("222222", 31.2, 121.5), now);
        t.upsert(callsignOnly("222222", "CES5101"), now);
        // order B: callsign first, then position -> callsign must survive.
        t.upsert(callsignOnly("333333", "UAL900"), now);
        t.upsert(withPosition("333333", 40.0, -74.0), now);

        check(t.count() == 2, "two aircraft tracked");
        check(t.points().size() == 2, "both have fixes -> two map points");
        bool saw222 = false, saw333 = false;
        for (const auto& p : t.points()) {
            if (p.icao == "222222") {
                saw222 = true;
                check(std::abs(p.lat - 31.2) < 1e-9 && p.callsign == "CES5101",
                      "pos-then-callsign: position + callsign both kept");
            } else if (p.icao == "333333") {
                saw333 = true;
                check(std::abs(p.lat - 40.0) < 1e-9 && p.callsign == "UAL900",
                      "callsign-then-pos: callsign + position both kept");
            }
        }
        check(saw222 && saw333, "both ICAOs present in points()");

        // A frame that omits altitude must not flip a known altitude to 0/-1,
        // and a never-reported altitude must stay at its sentinel (-1), not 0.
        bool sawSentinelAlt = false;
        for (const auto& a : t.aircraft())
            if (a.altitudeFt == -1) sawSentinelAlt = true;
        check(sawSentinelAlt, "unreported altitude stays -1 (no fake 0)");
    }

    // 7. distanceKm: finite positive when station+aircraft known, NaN otherwise.
    {
        AircraftTracker t;
        QDateTime now = clock0();
        t.upsert(withPosition("444444", 39.9, 116.4), now);   // near Beijing

        check(std::isnan(t.distanceKm("444444")),
              "distanceKm NaN before station is set");

        t.setStation(39.9, 116.4);                            // co-located
        double d0 = t.distanceKm("444444");
        check(!std::isnan(d0) && d0 >= 0.0 && d0 < 1.0,
              "distanceKm finite (~0) when station == aircraft");

        t.setStation(39.9, 117.4);                           // ~1 deg east
        double d = t.distanceKm("444444");
        check(!std::isnan(d) && d > 50.0 && d < 120.0,
              "distanceKm ~85 km sanity at ~1 deg longitude");

        check(std::isnan(t.distanceKm("ZZZZZZ")),
              "distanceKm NaN for unknown ICAO");

        t.upsert(callsignOnly("555555", "NOFIX"), now);       // no position
        check(std::isnan(t.distanceKm("555555")),
              "distanceKm NaN for position-less aircraft");
    }

    if (failures == 0) std::printf("test_aircraft_tracker: ALL PASS\n");
    else std::printf("test_aircraft_tracker: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
