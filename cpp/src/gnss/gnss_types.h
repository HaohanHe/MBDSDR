// SPDX-License-Identifier: MIT
// GNSS subsystem core data types (namespace mbdsdr::gnss).
//
// These types carry only real receiver data. Any recorded / synthetic fixture
// used in tests MUST be annotated "录制样例·非硬件 NOT HARDWARE".
#pragma once

#include <QDateTime>
#include <QString>
#include <vector>

namespace mbdsdr {
namespace gnss {

// GGA fix quality indicator (field 6).
enum class FixQuality : int {
    Invalid = 0,   // no fix
    GpsFix  = 1,   // standard GPS fix
    DgpsFix = 2,   // DGPS
    PpsFix  = 3,   // PPS
    RtkFixed = 4,  // RTK fixed integers
    RtkFloat = 5,  // RTK float
    Estimated = 6, // dead reckoning
    Manual = 7,
    Simulation = 8
};

// GSA 3D fix type (field 2).
enum class FixType : int {
    Invalid = 0, // unknown / not yet received
    NoFix   = 1,
    Fix2D   = 2,
    Fix3D   = 3
};

// One satellite as reported by GSV, with its "used in fix" flag merged from GSA.
struct GnssSatellite {
    int prn = 0;        // satellite PRN / number
    int elevation = 0;  // degrees (0..90)
    int azimuth = 0;    // degrees true (0..359)
    int snr = -1;       // dB-Hz, -1 = not measured
    bool used = false;  // contributing to the navigation solution (GSA)
};

// A merged fix: the parser accumulates fields across GGA/RMC/GSA/GSV/ZDA into
// one of these. It is emitted (by value) on every accepted sentence so the UI
// always sees the latest fully-populated snapshot.
struct GnssFix {
    // --- position (WGS84) ---
    double latitude  = 0.0; // degrees, signed (+ = North)
    double longitude = 0.0; // degrees, signed (+ = East)
    double altitudeMsl = 0.0; // metres, MSL (GGA field 9)
    double geoidSeparation = 0.0; // metres (GGA field 11)
    bool hasPosition = false;

    // --- time ---
    QDateTime utc;        // full UTC timestamp (date from RMC/ZDA, time from GGA/RMC/ZDA)
    bool hasUtc = false;

    // --- solution quality ---
    FixQuality fixQuality = FixQuality::Invalid;
    FixType    fixType    = FixType::Invalid;
    int satellitesInUse   = 0;   // GGA field 7 / count of GSA used PRNs

    // --- DOP ---
    double pdop = 0.0;
    double hdop = 0.0;
    double vdop = 0.0;
    bool hasDop = false;

    // --- motion (RMC) ---
    double speedKn   = 0.0; // speed over ground, knots
    double courseDeg = 0.0; // track made good, degrees true
    bool hasMotion = false;

    // --- sky ---
    std::vector<GnssSatellite> visibleSatellites; // merged GSV packets

    // --- wall clock (host) ---
    QDateTime receivedAt; // when this snapshot was processed

    bool isValid() const { return hasPosition; }

    // Index of a satellite by PRN, or -1.
    int indexOfPrn(int prn) const {
        for (size_t i = 0; i < visibleSatellites.size(); ++i)
            if (visibleSatellites[i].prn == prn) return static_cast<int>(i);
        return -1;
    }
};

// Helpers kept here so callers don't duplicate string conversions.
QString fixQualityToString(FixQuality q);
QString fixTypeToString(FixType t);

} // namespace gnss
} // namespace mbdsdr
