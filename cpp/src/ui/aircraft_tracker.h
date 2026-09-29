// SPDX-License-Identifier: MIT
//
// ADS-B aircraft tracker (pure logic, no QObject): merges decoded
// mbdsdr::dsp::AircraftInfo frames keyed by ICAO into per-aircraft state,
// keeps a bounded position tail (track), expires silent aircraft after a TTL,
// and produces map-ready AircraftPoint list.
//
// Honest-data policy:
//   * points() ONLY contains aircraft that carry a real decoded position
//     (hasPosition). A callsign-only message with no fix is kept in aircraft()
//     for the table, but NEVER yields a fake (0,0)-style map point.
//   * Field merge is "last-write-wins only for fields the frame actually
//     carries". Absent fields keep their previous value -- we never stamp a
//     missing altitude/velocity/position with a 0 default.
#pragma once

#include <QString>
#include <QStringList>
#include <QList>
#include <QHash>
#include <QDateTime>
#include <QPair>
#include <limits>

#include "world_view.h"          // mbdsdr::ui::AircraftPoint
#include "dsp/adsb_decoder.h"    // mbdsdr::dsp::AircraftInfo

namespace mbdsdr {
namespace ui {

class AircraftTracker {
public:
    AircraftTracker();

    // TTL: aircraft whose lastSeen is older than now-TTL are pruned().
    void setTtlSeconds(int s);
    int  ttlSeconds() const { return ttlSec_; }

    // Merge one decoded frame, keyed by upper-cased ICAO.
    // Returns true iff this ICAO was not already tracked (a brand-new aircraft).
    // Only fields the frame really carries overwrite existing state; missing
    // fields are preserved. A position frame appends to the bounded track tail.
    bool upsert(const mbdsdr::dsp::AircraftInfo& info, const QDateTime& now);

    // Drop aircraft with lastSeen strictly earlier than (now - TTL).
    // Returns the removed ICAO codes.
    QStringList prune(const QDateTime& now);

    void clear();
    int  count() const { return static_cast<int>(aircraft_.size()); }
    bool isEmpty() const { return aircraft_.isEmpty(); }
    bool contains(const QString& icao) const;

    // Latest snapshot per tracked aircraft (fix or no fix) -- for a table.
    QList<mbdsdr::dsp::AircraftInfo> aircraft() const;

    // Map-ready points: ONLY aircraft with a real decoded position. Never
    // synthesises points for position-less callsigns.
    QList<AircraftPoint> points() const;

    // Optional receiver (station) location used by distanceKm. NaN clears it.
    void setStation(double latDeg, double lonDeg);
    // Haversine station->aircraft distance in km. Returns NaN when the station
    // is unset, the ICAO is unknown, or the aircraft has no position yet.
    double distanceKm(const QString& icao) const;

private:
    struct Entry {
        mbdsdr::dsp::AircraftInfo info;
        QList<QPair<double, double>> track;   // position tail, oldest..newest
    };

    static constexpr int kDefaultTtlSec   = 30;   // ~real ADS-B receiver scale
    static constexpr int kMaxTrackPoints  = 12;   // trailing streak length

    int ttlSec_ = kDefaultTtlSec;
    QHash<QString, Entry> aircraft_;

    double stationLat_ = std::numeric_limits<double>::quiet_NaN();
    double stationLon_ = std::numeric_limits<double>::quiet_NaN();
};

} // namespace ui
} // namespace mbdsdr
