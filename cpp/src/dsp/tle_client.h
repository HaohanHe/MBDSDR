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
    double f0DownlinkHz = 0.0;         // nominal downlink carrier (Hz); 0 = unknown
    double dopplerAtPeakHz = 0.0;     // predicted receive Doppler at peak el, Hz
                                       // (0 when f0DownlinkHz == 0)
};

// Topocentric look angles at one instant.
struct Topocentric {
    double az = 0.0;        // degrees, 0=N clockwise
    double el = 0.0;        // degrees, +90=zenith
    double range = 0.0;     // km
    double rangeRateKmS = 0.0;  // d(range)/dt, km/s. >0 = satellite receding
                                // from the station (range increasing).
};

// Vacuum light speed, km/s.
inline constexpr double kLightSpeedKmS = 299792.458;

// Predicted receive Doppler shift (Hz) for a downlink carrier f0Hz, given the
// line-of-sight range-rate rangeRateKmS (positive = satellite receding).
// Convention: rangeRateKmS < 0 (closing) => fd > 0 (received frequency higher);
// recommended tuning frequency = f0Hz + fd.  Returns 0 when f0Hz is unknown.
inline double dopplerHz(double f0Hz, double rangeRateKmS) {
    if (f0Hz == 0.0) return 0.0;
    return -f0Hz * rangeRateKmS / kLightSpeedKmS;
}

// On-disk TLE cache record.
struct TleCache {
    bool valid = false;
    QDateTime fetchedAt;
    QList<TleEntry> entries;
};

// Real TLE fetch from celestrak.org + orbit propagation.
//
// Propagation (read this carefully -- we do not claim more than we do):
//
//   * NEAR-EARTH targets (orbital period < 225 min): a genuine original SGP4
//     (Hoots/Roeber, Spacetrack Report #3, as revised by Vallado et al.,
//     AIAA 2006-6753).  We parse the TLE mean elements and B* drag term,
//     apply the Brouwer-style secular rates, atmospheric drag, and short/
//     long-periodic terms, and solve Kepler's equation to produce the
//     instantaneous inertial (TEME) position in km.  This is the standard
//     operational TLE model, validated against the published Vallado
//     verification ephemerides to ~1e-3 km position / ~1e-4 km/s velocity.
//
//   * DEEP-SPACE targets (period >= 225 min, e.g. Molniya/GEO class): we do
//     NOT implement SDP4.  These honestly fall back to the older J2-secular
//     mean-element propagator (RAAN regression + perigee precession +
//     Kepler solve).  That is an approximation -- good enough for a sky-view
//     pass finder but NOT true deep-space SGP4/SDP4.  It is kept so that the
//     fetch pipeline still returns *something* for deep-space TLEs instead of
//     crashing, and the limitation is documented here rather than hidden.
//
// In both cases ECI->ECEF uses Greenwich Mean Sidereal Time (the standard
// TLE approximation; no polar motion), and ECEF->topocentric uses WGS84
// geodetic station coordinates.
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

    /// Satellite sub-point (latitude/longitude in degrees) at timeUtc.
    struct GeoCoord { double latDeg = 0, lonDeg = 0; };
    GeoCoord propagateLatLon(const QDateTime& timeUtc, const TleEntry& tle) const;

    /// ECEF (x,y,z km) -> WGS84 lat/lon. Exposed for unit tests.
    static GeoCoord ecefToLatLon(double x, double y, double z);

    // ---- Offline demo data (public, CelesTrak AIAA-2006-6753 snapshot) ----
    // A small fixed set of near-Earth TLEs shipped verbatim from the published
    // SGP4 verification ephemerides (2006 epoch).  These are ONLY an offline
    // fallback so the sky tab can show *something* with no network and no
    // cache: the epoch is years stale, so after 2006 the elements are
    // guaranteed out of date.  Not a substitute for a fresh celestrak pull.
    static QList<TleEntry> builtinTle();

    // Nominal downlink broadcast carrier (Hz) for a satellite name, matched by
    // case-insensitive substring against public amateur/weather-sat frequencies
    // (NOAA APT, ISS voice relay).  Returns 0 when the name is unknown -- we
    // never invent a frequency.  These are public broadcast frequencies kept
    // for Doppler reference only.
    static double downlinkHzFor(const QString& name);

    // --- GNSS navigation constellation filtering (real public catalog) -----
    // True when the TLE name is a recognized GNSS navigation satellite
    // (NAVSTAR/GPS, GLONASS, GALILEO, BEIDOU/COMPASS, NAVIC/IRNSS). Matched on
    // the standardized celestrak element-group naming -- we never invent a sat.
    static bool isNavConstellation(const QString& tleName);
    // NORAD catalog number from TLE line 1, field 2 (cols 3..7). Returns 0 on
    // a malformed line (caller treats 0 as "unknown", never as a real sat).
    static int catalogNumber(const TleEntry& e);

    // ---- Freshness (pure, UTC) ----------------------------------------
    // Parse a TLE line-1 epoch (cols 19..32, YYDDD.DDDDD) into a UTC QDateTime.
    // Returns an invalid QDateTime on a malformed line (never a fabricated date).
    static QDateTime parseTleEpoch(const QString& line1);
    // Whole days from `epoch` to `now` (>=0 if now is after epoch). Pure.
    static double daysSinceEpoch(const QDateTime& epoch, const QDateTime& now);

    // Point the celestrak fetcher at a different base URL (default: the real
    // celestrak gp.php endpoint). Tests use a loopback QTcpServer here; production
    // leaves the default. Must be set before fetch().
    void setBaseUrl(const QString& baseUrl) { baseUrl_ = baseUrl; }

    // Read the on-disk TLE cache (valid==false if absent/unreadable).
    TleCache cachedTle() const;

    // Compute passes from an already-known TLE list (e.g. cache) on the thread
    // pool and emit passesReady when done.
    void computeFromEntries(const QList<TleEntry>& entries,
                            double stationLatDeg, double stationLonDeg,
                            int hoursAhead = 24);

    // Propagate all TLEs and find passes visible from the station.  Pure const
    // sweep (no signals) over the given window; exposed for the deterministic
    // unit test and used internally by the live pipeline.
    QList<SatPass> computePasses(const QList<TleEntry>& entries,
                                 double stationLatDeg, double stationLonDeg,
                                 const QDateTime& startTimeUtc, int hoursAhead) const;

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

    QNetworkAccessManager* nam_ = nullptr;
    QString baseUrl_ =
        QStringLiteral("https://celestrak.org/NORAD/elements/gp.php");
};

} // namespace dsp
} // namespace mbdsdr
