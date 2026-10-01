// SPDX-License-Identifier: MIT
#include "tle_client.h"
#include "sgp4.h"

#include <QNetworkAccessManager>
#include <QNetworkRequest>
#include <QNetworkReply>
#include <QUrl>
#include <QRegularExpression>
#include <QtConcurrent>
#include <QFutureWatcher>
#include <QStandardPaths>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QFile>
#include <QDir>

#include <cmath>
#include <vector>

namespace mbdsdr {
namespace dsp {

namespace {

// ---- Physical constants (WGS84 / standard astrodynamics) -------------
constexpr double kEarthRadiusKm = 6378.137;
constexpr double kEarthMuKm3S2  = 398600.4418;   // GM, km^3/s^2
constexpr double kJ2             = 1.08263e-3;    // zonal harmonic
constexpr double kEartFlatE2    = 0.00669437999014; // WGS84 e^2
constexpr double kDeg2Rad        = M_PI / 180.0;
constexpr double kRad2Deg        = 180.0 / M_PI;
constexpr double kOmegaEarth     = 7.2921151467e-5;  // rad/s, Earth rotation

// Propagated per-satellite orbital elements (mean, J2-secular).
struct Orbit {
    double incl = 0.0;       // rad
    double raan0 = 0.0;      // rad at epoch
    double ecc = 0.0;
    double argp0 = 0.0;      // rad at epoch
    double M0 = 0.0;         // rad at epoch
    double n = 0.0;          // mean motion, rad/s
    double nodeDot = 0.0;    // RAAN regression, rad/s
    double argpDot = 0.0;    // perigee precession, rad/s
    double a = 0.0;          // semi-major axis, km
    QDateTime epoch;         // TLE epoch (UTC)

    // Real SGP4 near-earth propagator (period < 225 min).  When useSgp4 is
    // true the ECI position below comes from SGP4; otherwise we fall back to
    // the J2-secular mean-element path above (deep-space fallback, see header).
    Sgp4   sgp4;
    bool   useSgp4 = false;
};

double julianDay(const QDateTime& dt) {
    // QDateTime in UTC -> Julian Date.
    // toMSecsSinceEpoch() counts from the Unix epoch 1970-01-01 00:00 UTC,
    // whose Julian Date is 2440587.5.  (Using 2451545.0 -- the J2000 epoch at
    // 2000-01-01 -- here would double-count the 30 years 1970->2000 and leave
    // GMST off by ~180 deg, mirroring every predicted pass.)
    qint64 msecs = dt.toUTC().toMSecsSinceEpoch();
    return 2440587.5 + static_cast<double>(msecs) / 86400000.0;
}

// Greenwich Mean Sidereal Time (rad) for the given UTC instant.
double gmstRad(const QDateTime& dt) {
    double jd = julianDay(dt);
    double d = jd - 2451545.0;
    double T = d / 36525.0;
    // IAU 1982 expression, seconds of time.
    double gmstSec = 67310.54841
                   + (876600.0 * 3600.0 + 8640184.812866) * T
                   + 0.093104 * T * T
                   - 6.2e-6 * T * T * T;
    double turns = gmstSec / 86400.0;
    turns -= std::floor(turns);
    return turns * 2.0 * M_PI;
}

// Build a propagated orbit from parsed TLE line-2 fields.
bool buildOrbit(const TleEntry& e, Orbit& out) {
    const QString& l2 = e.line2;
    if (l2.length() < 63) return false;

    bool ok = false;
    double incl = l2.mid(8, 8).trimmed().toDouble(&ok);
    if (!ok) return false;
    double raan = l2.mid(17, 8).trimmed().toDouble(&ok);
    if (!ok) return false;
    double ecc = ("0." + l2.mid(26, 7).trimmed()).toDouble(&ok);
    if (!ok) return false;
    double argp = l2.mid(34, 8).trimmed().toDouble(&ok);
    if (!ok) return false;
    double M = l2.mid(43, 8).trimmed().toDouble(&ok);
    if (!ok) return false;
    double nRevDay = l2.mid(52, 11).trimmed().toDouble(&ok);
    if (!ok || nRevDay <= 0.0) return false;

    out.incl = incl * kDeg2Rad;
    out.raan0 = raan * kDeg2Rad;
    out.ecc = ecc;
    out.argp0 = argp * kDeg2Rad;
    out.M0 = M * kDeg2Rad;
    out.n = nRevDay * 2.0 * M_PI / 86400.0;   // rev/day -> rad/s

    // Semi-major axis from mean motion: n^2 a^3 = mu.
    out.a = std::cbrt(kEarthMuKm3S2 / (out.n * out.n));
    double p = out.a * (1.0 - out.ecc * out.ecc);

    // J2 secular rates (rad/s).
    double c = std::cos(out.incl);
    double fac = 1.5 * kJ2 * std::pow(kEarthRadiusKm / p, 2.0) * out.n;
    out.nodeDot = -fac * c;
    out.argpDot = fac * (5.0 * c * c - 1.0) / 2.0;

    // Epoch from line 1.
    const QString& l1 = e.line1;
    int yr = l1.mid(18, 2).trimmed().toInt(&ok);
    if (!ok) return false;
    double day = l1.mid(20, 12).trimmed().toDouble(&ok);
    if (!ok) return false;
    int year = (yr < 57) ? 2000 + yr : 1900 + yr;
    QDate d0(year, 1, 1);
    double dayFrac = day - 1.0;   // day-of-year is 1-based
    qint64 dayInt = static_cast<qint64>(std::floor(dayFrac));
    double secs = (dayFrac - dayInt) * 86400.0;
    // The TLE epoch is a UTC instant.  Anchor the midnight reference in UTC
    // explicitly: the QDateTime(date,time) overload defaults to LOCAL time, which
    // would shift the epoch by the machine's UTC offset (and hence the GMST,
    // and every predicted AOS/LOS) on non-UTC hosts.
    QDateTime midnight(d0.addDays(dayInt), QTime(0, 0, 0), Qt::UTC);
    out.epoch = QDateTime::fromMSecsSinceEpoch(
        midnight.toMSecsSinceEpoch() + static_cast<qint64>(secs * 1000.0), Qt::UTC);

    // Try to attach the real near-earth SGP4 propagator.  Deep-space targets
    // (period >= 225 min) self-flag and are left on the J2 fallback path.
    if (out.sgp4.loadTle(e.line1.toStdString(), e.line2.toStdString()))
        out.useSgp4 = !out.sgp4.isDeepSpace();
    return true;
}

// ECI position (km) and velocity (km/s) at t seconds after the TLE epoch,
// using the J2-secular mean-element path (deep-space fallback).  Position
// matches the legacy propagator; velocity is the analytic Keplerian velocity
// rotated by the same (argp, inclination, RAAN) frame.
void propagateEci(const Orbit& o, double tSec, double pos[3], double vel[3]) {
    // Advance mean elements with J2 secular rates.
    double M    = o.M0    + o.n * tSec;
    double raan = o.raan0 + o.nodeDot * tSec;
    double argp = o.argp0 + o.argpDot * tSec;

    // Solve Kepler M = E - e sin(E), Newton iteration.
    double E = M;
    for (int i = 0; i < 12; ++i) {
        double dE = (E - o.ecc * std::sin(E) - M) / (1.0 - o.ecc * std::cos(E));
        E -= dE;
        if (std::fabs(dE) < 1e-10) break;
    }

    double cosE = std::cos(E);
    double sinE = std::sin(E);
    // Position in the orbital plane (perifocal frame, size a).
    double xPer = o.a * (cosE - o.ecc);
    double yPer = o.a * std::sqrt(1.0 - o.ecc * o.ecc) * sinE;

    // Argument of latitude u = argp + true anomaly.
    double cosNu = (cosE - o.ecc) / (1.0 - o.ecc * cosE);
    double sinNu = (std::sqrt(1.0 - o.ecc * o.ecc) * sinE) / (1.0 - o.ecc * cosE);
    double nu = std::atan2(sinNu, cosNu);
    double u = argp + nu;

    // Keplerian rate of change of the eccentric anomaly.
    double dEdt = o.n / (1.0 - o.ecc * cosE);
    // Perifocal velocity.
    double vpx = -o.a * sinE * dEdt;
    double vpy =  o.a * std::sqrt(1.0 - o.ecc * o.ecc) * cosE * dEdt;
    // Decompose into radial / transverse (in-plane) components.
    double vRad = vpx * cosNu + vpy * sinNu;
    double vTra = -vpx * sinNu + vpy * cosNu;

    double cu = std::cos(u), su = std::sin(u);
    double cO = std::cos(raan), sO = std::sin(raan);
    double ci = std::cos(o.incl), si = std::sin(o.incl);
    double r = std::sqrt(xPer * xPer + yPer * yPer);

    pos[0] = r * (cu * cO - su * ci * sO);
    pos[1] = r * (cu * sO + su * ci * cO);
    pos[2] = r * (su * si);

    // In-plane velocity expressed in the (u) frame, then rotated by incl/raan.
    double ux = vRad * cu - vTra * su;
    double uy = vRad * su + vTra * cu;
    vel[0] = ux * cO - uy * ci * sO;
    vel[1] = ux * sO + uy * ci * cO;
    vel[2] = uy * si;
}

// ECI position (km) AND velocity (km/s) at t seconds after epoch, dispatching
// on propagator.
//  - near-earth (period < 225 min): real SGP4 (TEME).
//  - deep-space / SGP4 failure:      honest J2-secular fallback.
void computeEciState(const Orbit& o, double tSec, double pos[3], double vel[3]) {
    if (o.useSgp4) {
        if (o.sgp4.propagate(tSec / 60.0, pos, vel) == 0)
            return;   // SGP4 succeeded.
        // Decay / bad-state: fall through to the J2 path for this sample.
    }
    propagateEci(o, tSec, pos, vel);
}

// ECI -> ECEF (km) position AND velocity by rotating around z by -GMST and
// adding the ECEF transport term: v_ecef = Rz(-GMST) v_teme + omega x r_ecef.
// The station is stationary in the ECEF frame.
void eciToEcefState(const double eci[3], const double veci[3], const QDateTime& t,
                    double ecef[3], double vecef[3]) {
    double g = gmstRad(t);
    double cg = std::cos(g), sg = std::sin(g);
    ecef[0] = eci[0] * cg + eci[1] * sg;
    ecef[1] = -eci[0] * sg + eci[1] * cg;
    ecef[2] = eci[2];
    vecef[0] = veci[0] * cg + veci[1] * sg - kOmegaEarth * ecef[1];
    vecef[1] = -veci[0] * sg + veci[1] * cg + kOmegaEarth * ecef[0];
    vecef[2] = veci[2];
}

// Station ECEF (km) from WGS84 geodetic lat/lon, height 0.
void stationEcef(double latDeg, double lonDeg, double sta[3]) {
    double phi = latDeg * kDeg2Rad;
    double lam = lonDeg * kDeg2Rad;
    double sinPhi = std::sin(phi);
    double N = kEarthRadiusKm / std::sqrt(1.0 - kEartFlatE2 * sinPhi * sinPhi);
    sta[0] = N * std::cos(phi) * std::cos(lam);
    sta[1] = N * std::cos(phi) * std::sin(lam);
    sta[2] = N * (1.0 - kEartFlatE2) * sinPhi;
}

} // namespace

TleClient::TleClient(QObject* parent) : QObject(parent) {
    nam_ = new QNetworkAccessManager(this);
}

QList<TleEntry> TleClient::parseTle(const QByteArray& blob) {
    QList<TleEntry> out;
    QString text = QString::fromUtf8(blob);
    const auto lines = text.split(QRegularExpression(QStringLiteral("\\r?\\n")),
                                  Qt::SkipEmptyParts);
    for (int i = 0; i + 2 < lines.size(); ++i) {
        const QString& name = lines[i].trimmed();
        const QString& l1 = lines[i + 1];
        const QString& l2 = lines[i + 2];
        if (name.isEmpty()) continue;
        if (!l1.startsWith(QLatin1Char('1'))) continue;
        if (!l2.startsWith(QLatin1Char('2'))) continue;
        out.append({name, l1, l2});
        i += 2;
    }
    return out;
}

QDateTime TleClient::parseTleEpoch(const QString& line1) {
    int yr = line1.mid(18, 2).trimmed().toInt();
    bool ok = yr >= 0;
    double day = line1.mid(20, 12).trimmed().toDouble(&ok);
    if (!ok || yr < 0 || day <= 0.0) return QDateTime();
    int year = (yr < 57) ? 2000 + yr : 1900 + yr;
    QDate d0(year, 1, 1);
    if (!d0.isValid()) return QDateTime();
    double dayFrac = day - 1.0;
    qint64 dayInt = static_cast<qint64>(std::floor(dayFrac));
    double secs = (dayFrac - dayInt) * 86400.0;
    QDateTime midnight(d0.addDays(dayInt), QTime(0, 0, 0), Qt::UTC);
    return QDateTime::fromMSecsSinceEpoch(
        midnight.toMSecsSinceEpoch() + static_cast<qint64>(secs * 1000.0), Qt::UTC);
}

double TleClient::daysSinceEpoch(const QDateTime& epoch, const QDateTime& now) {
    if (!epoch.isValid() || !now.isValid()) return -1.0;
    return epoch.secsTo(now) / 86400.0;
}

void TleClient::fetch(double stationLatDeg, double stationLonDeg, int hoursAhead) {
    if (!std::isfinite(stationLatDeg) || !std::isfinite(stationLonDeg)) {
        emit fetchFailed(QStringLiteral("no-station"));
        return;
    }

    // Pull two real celestrak groups: crewed stations (ISS/Tiangong/...) and
    // LEO weather satellites (NOAA/Meteor). Merge into one TLE list.
    struct Shared {
        int pending = 0;
        QList<TleEntry> entries;
        QString firstError;
    };
    auto* sh = new Shared;
    // Pull crewed stations, weather sats, AND the GNSS navigation constellations
    // (GPS/GLONASS/Galileo/BeiDou) so the "visible nav satellites (predicted)"
    // panel has real orbit elements online. Offline it degrades honestly to the
    // builtin snapshot / cache (no nav sats -> empty state).
    const QStringList groups = {
        QStringLiteral("stations"), QStringLiteral("weather"),
        QStringLiteral("gnss"),     QStringLiteral("amateur")};
    sh->pending = groups.size();
    for (const QString& g : groups) {
        QUrl url(baseUrl_ + QStringLiteral("?GROUP=%1&FORMAT=tle").arg(g));
        QNetworkRequest req(url);
        req.setHeader(QNetworkRequest::UserAgentHeader,
                      QStringLiteral("MBDSDR/1.0 (+satellite pass viewer)"));
        req.setTransferTimeout(15000);
        QNetworkReply* reply = nam_->get(req);
        connect(reply, &QNetworkReply::finished, this, [this, reply, sh,
                stationLatDeg, stationLonDeg, hoursAhead]() {
            reply->deleteLater();
            if (reply->error() == QNetworkReply::NoError) {
                sh->entries += parseTle(reply->readAll());
            } else if (sh->firstError.isEmpty()) {
                sh->firstError = reply->errorString();
            }
            if (--sh->pending > 0) return;   // wait for all groups to finish

            if (sh->entries.isEmpty()) {
                emit fetchFailed(sh->firstError.isEmpty()
                                     ? QStringLiteral("TLE parse: no satellites")
                                     : sh->firstError);
                delete sh;
                return;
            }
            // Persist to disk, then offload the propagation sweep.
            QList<TleEntry> entries = sh->entries;
            double lat = stationLatDeg, lon = stationLonDeg;
            int hrs = hoursAhead;
            delete sh;
            writeCache(entries);
            offloadCompute(entries, lat, lon, hrs);
        });
    }
}

void TleClient::offloadCompute(QList<TleEntry> entries, double latDeg,
                               double lonDeg, int hoursAhead) {
    TleClient* self = this;
    auto* watcher = new QFutureWatcher<QList<SatPass>>(this);
    connect(watcher, &QFutureWatcher<QList<SatPass>>::finished,
            this, [this, watcher]() {
        QList<SatPass> passes = watcher->result();
        watcher->deleteLater();
        emit passesReady(passes);
    });
    watcher->setFuture(QtConcurrent::run([self, entries, latDeg, lonDeg, hoursAhead]() {
        return self->computePasses(entries, latDeg, lonDeg,
                                    QDateTime::currentDateTimeUtc(), hoursAhead);
    }));
}

void TleClient::computeFromEntries(const QList<TleEntry>& entries,
                                   double latDeg, double lonDeg, int hoursAhead) {
    if (entries.isEmpty()) return;
    offloadCompute(entries, latDeg, lonDeg, hoursAhead);
}

static QString tleCachePath() {
    QString dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(dir);
    return dir + QStringLiteral("/tle_cache.json");
}

void TleClient::writeCache(const QList<TleEntry>& entries) {
    QJsonArray arr;
    for (const TleEntry& e : entries) {
        QJsonObject o;
        o[QStringLiteral("name")] = e.name;
        o[QStringLiteral("line1")] = e.line1;
        o[QStringLiteral("line2")] = e.line2;
        arr.append(o);
    }
    QJsonObject root;
    root[QStringLiteral("fetchedAt")] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    root[QStringLiteral("satellites")] = arr;
    QFile f(tleCachePath());
    if (f.open(QIODevice::WriteOnly)) {
        f.write(QJsonDocument(root).toJson(QJsonDocument::Compact));
    }
}

TleCache TleClient::cachedTle() const {
    TleCache out;
    QFile f(tleCachePath());
    if (!f.open(QIODevice::ReadOnly)) return out;
    QJsonParseError pe;
    QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError || !doc.isObject()) return out;
    QJsonObject root = doc.object();
    out.fetchedAt = QDateTime::fromString(root.value(QStringLiteral("fetchedAt")).toString(),
                                          Qt::ISODate);
    out.fetchedAt.setTimeSpec(Qt::UTC);
    const QJsonArray arr = root.value(QStringLiteral("satellites")).toArray();
    for (const auto& v : arr) {
        QJsonObject o = v.toObject();
        TleEntry e;
        e.name = o.value(QStringLiteral("name")).toString();
        e.line1 = o.value(QStringLiteral("line1")).toString();
        e.line2 = o.value(QStringLiteral("line2")).toString();
        if (!e.name.isEmpty()) out.entries.append(e);
    }
    out.valid = out.fetchedAt.isValid() && !out.entries.isEmpty();
    return out;
}

QList<SatPass> TleClient::computePasses(const QList<TleEntry>& entries,
                                         double stationLatDeg, double stationLonDeg,
                                         const QDateTime& startUtc, int hoursAhead) const {
    QList<SatPass> result;
    double sta[3];
    stationEcef(stationLatDeg, stationLonDeg, sta);
    double phi = stationLatDeg * kDeg2Rad;
    double lam = stationLonDeg * kDeg2Rad;
    double sinPhi = std::sin(phi), cosPhi = std::cos(phi);
    double sinLam = std::sin(lam), cosLam = std::cos(lam);

    const double stepSec = 30.0;
    const double totalSec = hoursAhead * 3600.0;

    for (const TleEntry& e : entries) {
        Orbit o;
        if (!buildOrbit(e, o)) continue;

        // Walk forward from max(start, epoch) at 30s steps.
        QDateTime t0 = startUtc;
        if (o.epoch > t0) t0 = o.epoch;

        const double f0 = downlinkHzFor(e.name);   // 0 when unknown (honest)

        bool inPass = false;
        SatPass cur;
        double maxEl = -90.0;
        double peakRangeRate = 0.0;   // range-rate at the max-elevation sample

        for (double s = 0; s <= totalSec; s += stepSec) {
            QDateTime t = t0.addMSecs(static_cast<qint64>(s * 1000.0));
            double dt = o.epoch.secsTo(t);   // seconds since epoch
            double eci[3] = {0,0,0}, veci[3] = {0,0,0};
            double ecef[3] = {0,0,0}, vecef[3] = {0,0,0};
            computeEciState(o, dt, eci, veci);
            eciToEcefState(eci, veci, t, ecef, vecef);

            double dx = ecef[0] - sta[0];
            double dy = ecef[1] - sta[1];
            double dz = ecef[2] - sta[2];
            // Local ENU.
            double east  = -sinLam * dx + cosLam * dy;
            double north = -sinPhi * cosLam * dx - sinPhi * sinLam * dy + cosPhi * dz;
            double up    =  cosPhi * cosLam * dx + cosPhi * sinLam * dy + sinPhi * dz;
            double rho = std::sqrt(east*east + north*north + up*up);
            if (rho < 1e-6) continue;
            double el = std::asin(up / rho) * kRad2Deg;
            double az = std::fmod(std::atan2(east, north) * kRad2Deg + 360.0, 360.0);
            // Line-of-sight range rate (station stationary in ECEF).
            double vr = (vecef[0]*dx + vecef[1]*dy + vecef[2]*dz) / rho;

            // Pass gate: a track counts once the satellite rises above the
            // practical reception elevation (5 deg, matching the AIAA validation
            // convention).  AOS/LOS are reported at this gate, not at the
            // mathematical horizon, so the reported window is the actually usable
            // one.
            if (el > 5.0) {
                if (!inPass) {
                    inPass = true;
                    cur = SatPass();
                    cur.name = e.name;
                    cur.tle = e;
                    cur.aos = t;
                    cur.azAos = az;
                    cur.f0DownlinkHz = f0;
                    maxEl = el;
                    peakRangeRate = vr;
                }
                cur.track.append({az, el});
                if (el > maxEl) { maxEl = el; peakRangeRate = vr; }
            } else if (inPass) {
                inPass = false;
                cur.los = t;
                cur.maxEl = maxEl;
                cur.azLos = az;
                cur.dopplerAtPeakHz = dopplerHz(f0, peakRangeRate);
                if (cur.maxEl > 5.0) result.append(cur);
            }
        }
        // Pass still ongoing at horizon edge: close it out.
        if (inPass) {
            cur.los = t0.addMSecs(static_cast<qint64>(totalSec * 1000.0));
            cur.maxEl = maxEl;
            cur.dopplerAtPeakHz = dopplerHz(f0, peakRangeRate);
            // No LOS azimuth at horizon; reuse last track sample.
            if (!cur.track.isEmpty()) cur.azLos = cur.track.last().first;
            if (cur.maxEl > 5.0) result.append(cur);
        }
    }

    // Sort by AOS ascending.
    std::sort(result.begin(), result.end(),
              [](const SatPass& a, const SatPass& b) { return a.aos < b.aos; });
    return result;
}

Topocentric TleClient::propagateAt(const QDateTime& timeUtc, const TleEntry& e,
                                   double stationLatDeg, double stationLonDeg) const {
    Topocentric out;
    Orbit o;
    if (!buildOrbit(e, o)) return out;

    double sta[3];
    stationEcef(stationLatDeg, stationLonDeg, sta);
    double phi = stationLatDeg * kDeg2Rad;
    double lam = stationLonDeg * kDeg2Rad;
    double sPhi = std::sin(phi), cPhi = std::cos(phi);
    double sLam = std::sin(lam), cLam = std::cos(lam);

    QDateTime t = timeUtc.toUTC();
    double dt = o.epoch.secsTo(t);
    double eci[3] = {0,0,0}, veci[3] = {0,0,0};
    double ecef[3] = {0,0,0}, vecef[3] = {0,0,0};
    computeEciState(o, dt, eci, veci);
    eciToEcefState(eci, veci, t, ecef, vecef);

    double dx = ecef[0] - sta[0];
    double dy = ecef[1] - sta[1];
    double dz = ecef[2] - sta[2];
    double east  = -sLam * dx + cLam * dy;
    double north = -sPhi * cLam * dx - sPhi * sLam * dy + cPhi * dz;
    double up    =  cPhi * cLam * dx + cPhi * sLam * dy + sPhi * dz;
    double rho = std::sqrt(east*east + north*north + up*up);
    if (rho < 1e-6) return out;
    out.range = rho;
    out.el = std::asin(up / rho) * kRad2Deg;
    out.az = std::fmod(std::atan2(east, north) * kRad2Deg + 360.0, 360.0);
    // Range rate = satellite ECEF velocity dotted onto the station-to-satellite
    // unit vector (the ground station has zero ECEF velocity).
    out.rangeRateKmS = (vecef[0]*dx + vecef[1]*dy + vecef[2]*dz) / rho;
    return out;
}

TleClient::GeoCoord TleClient::ecefToLatLon(double x, double y, double z) {
    // WGS84 ellipsoid, Bowring iteration.
    const double a = kEarthRadiusKm;
    const double e2 = kEartFlatE2;
    double lon = std::atan2(y, x) * kRad2Deg;
    double p = std::sqrt(x*x + y*y);
    double lat = std::atan2(z, p * (1.0 - e2));  // initial guess
    for (int i = 0; i < 6; ++i) {
        double s = std::sin(lat);
        double N = a / std::sqrt(1.0 - e2*s*s);
        lat = std::atan2(z + e2*N*s, p);
    }
    return {lat * kRad2Deg, lon};
}

TleClient::GeoCoord TleClient::propagateLatLon(const QDateTime& timeUtc, const TleEntry& e) const {
    Orbit o;
    if (!buildOrbit(e, o)) return {};
    QDateTime t = timeUtc.toUTC();
    double dt = o.epoch.secsTo(t);
    double eci[3] = {0,0,0}, veci[3] = {0,0,0};
    double ecef[3] = {0,0,0}, vecef[3] = {0,0,0};
    computeEciState(o, dt, eci, veci);
    eciToEcefState(eci, veci, t, ecef, vecef);
    return ecefToLatLon(ecef[0], ecef[1], ecef[2]);
}

// ---- Built-in offline TLE + public downlink frequency table -------------
//
// Source: CelesTrak's public AIAA-2006-6753 SGP4 verification ephemerides
//   https://celestrak.org/publications/AIAA/2006-6753/
// (Hoots/Roeber STR#3 as revised by Vallado et al.).  The four near-Earth
// two-line elements below are copied verbatim (trailing verification columns
// stripped) from the distributed SGP4-VER.TLE, which carries a 2006 epoch.
//
// These are shipped ONLY so the sky tab can show a deterministic, honest
// offline result when there is no network and no on-disk cache.  Because the
// epoch is years old, the elements are guaranteed stale after 2006: they are
// NOT the user's sky tonight, just a worked SGP4 demo.  The UI labels this
// explicitly instead of implying fresh data.
QList<TleEntry> TleClient::builtinTle() {
    return {
        {"EXPLORER 1 DEB",
         "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
         "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"},
        {"DELTA 1 DEB",
         "1 06251U 62025E   06176.82412014  .00008885  00000-0  12808-3 0  3985",
         "2 06251  58.0579  54.0425 0030035 139.1568 221.1854 15.56387291  6774"},
        {"CBERS 2",
         "1 28057U 03049A   06177.78615833  .00000060  00000-0  35940-4 0  1836",
         "2 28057  98.4283 247.6961 0000884  88.1964 271.9322 14.35478080140550"},
        {"SL-12 DEB",
         "1 29238U 06022G   06177.28732010  .00766286  10823-4  13334-2 0   101",
         "2 29238  51.5595 213.7903 0202579  95.2503 267.9010 15.73823839  1061"},
    };
}

// Public broadcast downlink carriers used only for a Doppler reference.  Matched
// by case-insensitive substring on the satellite name.  We deliberately return
// 0 for anything we do not recognise rather than guess.
double TleClient::downlinkHzFor(const QString& name) {
    const QString n = name.toUpper();
    if (n.contains(QStringLiteral("ISS")) || n.contains(QStringLiteral("ZARYA")))
        return 145.800e6;   // ISS voice repeater downlink (public)
    if (n.contains(QStringLiteral("NOAA 15")) || n.contains(QStringLiteral("NOAA-15")))
        return 137.620e6;   // NOAA-15 APT
    if (n.contains(QStringLiteral("NOAA 18")) || n.contains(QStringLiteral("NOAA-18")))
        return 137.9125e6;  // NOAA-18 APT
    if (n.contains(QStringLiteral("NOAA 19")) || n.contains(QStringLiteral("NOAA-19")))
        return 137.9125e6;  // NOAA-19 APT
    if (n.contains(QStringLiteral("CBERS")))
        return 437.8e6;     // CBERS LEO UHF telemetry downlink (public)
    return 0.0;             // unknown: honest "no frequency", not a guess
}

bool TleClient::isNavConstellation(const QString& tleName) {
    // Standardized celestrak GNSS group names. We match by the public
    // constellation designators rather than by guessing individual NORAD IDs
    // (which overlap across operators). Real satellites only.
    const QString n = tleName.toUpper();
    return n.contains(QStringLiteral("NAVSTAR"))        // GPS (Navstar)
        || n.contains(QStringLiteral("GPS II"))         // GPS Block IIA/IIR/IIF
        || n.contains(QStringLiteral("GPS III"))
        || n.contains(QStringLiteral("GPS BII"))
        || n.contains(QStringLiteral("GLONASS"))
        || n.contains(QStringLiteral("GALILEO"))
        || n.contains(QStringLiteral("BEIDOU"))
        || n.contains(QStringLiteral("BDS"))
        || n.contains(QStringLiteral("COMPASS"))
        || n.contains(QStringLiteral("NAVIC"))
        || n.contains(QStringLiteral("IRNSS"));
}

int TleClient::catalogNumber(const TleEntry& e) {
    // TLE line 1: "1 NNNNNC ..." -- catalog number is columns 3..7 (1-based).
    const QString l1 = e.line1.trimmed();
    if (!l1.startsWith('1') || l1.length() < 7) return 0;
    bool ok = false;
    const int cat = l1.mid(2, 5).toInt(&ok);
    return ok ? cat : 0;
}

} // namespace dsp
} // namespace mbdsdr
