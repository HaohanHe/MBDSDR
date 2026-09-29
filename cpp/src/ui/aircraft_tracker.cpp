// SPDX-License-Identifier: MIT
#include "ui/aircraft_tracker.h"

#include <cmath>

namespace mbdsdr {
namespace ui {

AircraftTracker::AircraftTracker() = default;

void AircraftTracker::setTtlSeconds(int s) {
    if (s >= 0) ttlSec_ = s;
}

bool AircraftTracker::upsert(const mbdsdr::dsp::AircraftInfo& info, const QDateTime& now) {
    const QString key = info.icao.toUpper();
    auto it = aircraft_.find(key);
    const bool isNew = (it == aircraft_.end());

    if (isNew) {
        Entry e;
        e.info = info;
        e.info.icao = key;
        e.info.firstSeen = now;
        e.info.lastSeen = now;
        // Seed the tail only if this very first frame already carries a fix.
        if (info.hasPosition) {
            e.track.append({info.lat, info.lon});
            if (!info.positionTime.isValid()) e.info.positionTime = now;
        }
        aircraft_.insert(key, e);
        return true;
    }

    mbdsdr::dsp::AircraftInfo& cur = it->info;

    // callsign: overwrite only when the frame actually decodes one.
    if (!info.callsign.isEmpty()) cur.callsign = info.callsign;

    // altitude: overwrite only when the frame carries a real (>0) altitude.
    if (info.altitudeFt > 0) cur.altitudeFt = info.altitudeFt;

    // velocity block: overwrite only when the frame has it.
    if (info.hasVelocity) {
        cur.groundspeedKt = info.groundspeedKt;
        cur.headingDeg    = info.headingDeg;
        cur.hasVelocity   = true;
    }
    // vertical rate: overwrite only when the frame has it.
    if (info.hasVerticalRate) {
        cur.verticalRateFpm = info.verticalRateFpm;
        cur.hasVerticalRate = true;
    }

    // position block: overwrite + extend tail only when the frame has a fix.
    if (info.hasPosition) {
        cur.lat         = info.lat;
        cur.lon         = info.lon;
        cur.hasPosition = true;
        cur.positionTime = info.positionTime.isValid() ? info.positionTime : now;
        it->track.append({info.lat, info.lon});
        while (it->track.size() > kMaxTrackPoints) it->track.takeFirst();
    }

    // Per-frame metadata (every decoded frame carries these).
    cur.df     = info.df;
    cur.crcOk  = info.crcOk;
    cur.lastSeen = now;
    return false;
}

QStringList AircraftTracker::prune(const QDateTime& now) {
    QStringList removed;
    const QDateTime cutoff = now.addSecs(-ttlSec_);
    for (auto it = aircraft_.begin(); it != aircraft_.end(); ) {
        // Expire when lastSeen is strictly earlier than (now - TTL).
        if (!it->info.lastSeen.isValid() || it->info.lastSeen < cutoff) {
            removed.append(it.key());
            it = aircraft_.erase(it);
        } else {
            ++it;
        }
    }
    return removed;
}

void AircraftTracker::clear() {
    aircraft_.clear();
}

bool AircraftTracker::contains(const QString& icao) const {
    return aircraft_.contains(icao.toUpper());
}

QList<mbdsdr::dsp::AircraftInfo> AircraftTracker::aircraft() const {
    QList<mbdsdr::dsp::AircraftInfo> out;
    out.reserve(aircraft_.size());
    for (auto it = aircraft_.begin(); it != aircraft_.end(); ++it)
        out.append(it->info);
    return out;
}

QList<AircraftPoint> AircraftTracker::points() const {
    QList<AircraftPoint> out;
    for (auto it = aircraft_.begin(); it != aircraft_.end(); ++it) {
        const mbdsdr::dsp::AircraftInfo& a = it->info;
        if (!a.hasPosition) continue;   // honest: never emit a fake (0,0) point
        AircraftPoint p;
        p.icao       = a.icao;
        p.callsign   = a.callsign;
        p.lat        = a.lat;
        p.lon        = a.lon;
        p.altitudeFt = a.altitudeFt;
        p.headingDeg = a.headingDeg;
        p.track      = it->track;
        out.append(p);
    }
    return out;
}

void AircraftTracker::setStation(double latDeg, double lonDeg) {
    stationLat_ = latDeg;
    stationLon_ = lonDeg;
}

double AircraftTracker::distanceKm(const QString& icao) const {
    const double nan = std::numeric_limits<double>::quiet_NaN();
    const bool stationKnown =
        !std::isnan(stationLat_) && !std::isnan(stationLon_);
    if (!stationKnown) return nan;

    auto it = aircraft_.find(icao.toUpper());
    if (it == aircraft_.end()) return nan;

    const mbdsdr::dsp::AircraftInfo& a = it->info;
    if (!a.hasPosition || std::isnan(a.lat) || std::isnan(a.lon)) return nan;

    auto toRad = [](double deg) { return deg * std::acos(-1.0) / 180.0; };
    const double R = 6371.0; // Earth mean radius, km
    const double dLat = toRad(a.lat - stationLat_);
    const double dLon = toRad(a.lon - stationLon_);
    double h = std::sin(dLat / 2.0) * std::sin(dLat / 2.0)
             + std::cos(toRad(stationLat_)) * std::cos(toRad(a.lat))
             * std::sin(dLon / 2.0) * std::sin(dLon / 2.0);
    if (h < 0.0) h = 0.0;
    if (h > 1.0) h = 1.0;
    return 2.0 * R * std::asin(std::sqrt(h));
}

} // namespace ui
} // namespace mbdsdr
