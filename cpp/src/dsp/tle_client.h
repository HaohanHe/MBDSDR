// SPDX-License-Identifier: MIT
#pragma once

#include <QObject>
#include <QString>
#include <QList>
#include <QDateTime>

class QNetworkAccessManager;

namespace mbdsdr {
namespace dsp {

// One raw TLE record (3 lines as returned by celestrak).
struct TleEntry {
    QString name;
    QString line1;
    QString line2;
};

// One predicted satellite pass over the user's station.
struct SatPass {
    QString name;
    QDateTime aos;                     // Acquisition of signal (UTC)
    QDateTime los;                     // Loss of signal (UTC)
    double maxEl = 0.0;                // max elevation, degrees
    double azAos = 0.0;                // azimuth at AOS, degrees
    double azLos = 0.0;                // azimuth at LOS, degrees
    QList<QPair<double,double>> track; // sampled (az, el) over the pass, degrees
    TleEntry tle;                      // raw TLE used, for live re-propagation
};

// Topocentric look angles at one instant.
struct Topocentric {
    double az = 0.0;     // degrees, 0=N clockwise
    double el = 0.0;     // degrees, +90=zenith
    double range = 0.0;  // km
};

// On-disk TLE cache record.
struct TleCache {
    bool valid = false;
    QDateTime fetchedAt;
    QList<TleEntry> entries;
};

// Real TLE fetch from celestrak.org + LEO orbit propagation.
//
// Propagation note: this is a J2-perturbed *mean-element* propagator, not the
// full Hoots/Schloop SGP4 with all drag/long-period terms.  We propagate the
// TLE mean elements (inclination, RAAN, eccentricity, argument of perigee,
// mean anomaly) forward in time with the analytic J2 secular rates for RAAN
// regression and perigee precession, then solve Kepler's equation to get the
// instantaneous position.  This is genuine orbital mechanics (not a uniform
// circular orbit); for LEO weather/space-station satellites it predicts
// AOS/LOS times to well under a minute, which is more than enough for a sky
// view.  ECI->ECEF uses GMST; ECEF->topocentric uses WGS84 geodetic station
// coordinates.
class TleClient : public QObject {
    Q_OBJECT
public:
    explicit TleClient(QObject* parent = nullptr);

    // Async: fetch TLE groups from celestrak.org, then compute passes over the
    // given station for the next hoursAhead hours (computation runs on the
    // thread pool, UI stays responsive).  Does nothing if lat/lon are not
    // finite.
    void fetch(double stationLatDeg, double stationLonDeg, int hoursAhead = 24);

    // Propagate one TLE to an arbitrary instant and return station-relative
    // az/el/range. Pure, thread-safe w.r.t. UI (only reads its arguments).
    Topocentric propagateAt(const QDateTime& timeUtc, const TleEntry& tle,
                             double stationLatDeg, double stationLonDeg) const;

    // Read the on-disk TLE cache (valid==false if absent/unreadable).
    TleCache cachedTle() const;

    // Compute passes from an already-known TLE list (e.g. cache) on the thread
    // pool and emit passesReady when done.
    void computeFromEntries(const QList<TleEntry>& entries,
                            double stationLatDeg, double stationLonDeg,
                            int hoursAhead = 24);

signals:
    // Passes computed successfully. May be empty (24h window has no passes).
    void passesReady(QList<SatPass> passes);
    // Network / parse failure. UI must show an honest empty state.
    void fetchFailed(const QString& reason);

private:
    // Parse a celestrak TLE text blob into 3-line records.
    static QList<TleEntry> parseTle(const QByteArray& blob);

    // Persist the freshly-fetched TLE list to the on-disk cache.
    void writeCache(const QList<TleEntry>& entries);

    // Offload computePasses to the thread pool, emit passesReady on finish.
    void offloadCompute(QList<TleEntry> entries, double latDeg, double lonDeg,
                        int hoursAhead);

    // Propagate all TLEs and find passes visible from the station. Runs on a
    // worker thread via QtConcurrent.
    QList<SatPass> computePasses(const QList<TleEntry>& entries,
                                 double stationLatDeg, double stationLonDeg,
                                 const QDateTime& startTimeUtc, int hoursAhead) const;

    QNetworkAccessManager* nam_ = nullptr;
};

} // namespace dsp
} // namespace mbdsdr
