// SPDX-License-Identifier: MIT
//
// Near-Earth SGP4 orbit propagator, written from the public equations in
//   - Hoots, Roeber, "Spacetrack Report #3" (1980), and
//   - Vallado, Crawford, Hujsak & Kelso, "Revisiting Spacetrack Report #3"
//     (AIAA 2006-6753).
//
// This is an original implementation: the mathematics above is published by
// the United States Government (public-domain report), and this file was
// written directly from those equations.  No Copyleft-licensed source was
// copied; variable naming and code structure are our own.
//
// Internal units follow the classic STR#3 convention: distances in Earth
// radii, time in minutes, mean motion in rad/min.  WGS-72 ("str#3 low
// precision") gravity constants are used exactly as the published SGP4
// verification ephemerides assume.  Output inertial state is in the TEME
// frame: position in km, velocity in km/s.
//
// Scope: NEAR-EARTH only (orbital period < 225 min).  Deep-space targets
// (period >= 225 min) are detected and flagged; this class deliberately does
// NOT extend the LEO theory to them.  Callers must route deep-space targets
// to a separate SDP4 propagator or an honest fallback.
#pragma once

#include <string>

namespace mbdsdr {
namespace dsp {

class Sgp4 {
public:
    Sgp4();

    // Parse a NORAD two-line element set.  Returns false if the lines are too
    // short, unreadable, or give unusable elements (ecc>=1, etc.).
    // line1/line2 are the standard TLE lines (extra trailing columns after
    // the mean-motion field, as used in some test harnesses, are ignored).
    bool loadTle(const std::string& line1, const std::string& line2);

    // Propagate to tsince minutes after the TLE epoch.
    // On success writes TEME position into posKm[3] (km) and velocity into
    // velKmS[3] (km/s), and returns 0.  Returns a non-zero NORAD-style code
    // (1 bad mean elements, 2 mean motion <= 0, 4 semi-latus rectum < 0,
    // 6 decayed / below Earth) on failure, leaving outputs undefined.
    int  propagate(double tsinceMin, double posKm[3], double velKmS[3]) const;

    bool   isDeepSpace() const { return deepSpace_; } // period >= 225 min
    double periodMin()  const { return periodMin_; }

private:
    bool initializeNearEarth();

    bool   loaded_      = false;
    bool   deepSpace_    = false;
    double periodMin_    = 0.0;

    // Epoch mean elements.
    double bstar_  = 0.0;   // SGP4 B* drag term (1/Earth-radii)
    double ecco_   = 0.0;   // eccentricity
    double inclo_  = 0.0;   // inclination, rad
    double mo_     = 0.0;   // mean anomaly at epoch, rad
    double nodeo_  = 0.0;   // RAAN at epoch, rad
    double argpo_  = 0.0;   // argument of perigee at epoch, rad
    double no_     = 0.0;   // un-kozai mean motion, rad/min

    // Derived near-earth coefficients (STR#3 init).
    double ao_      = 0.0;  // semi-major axis, Earth radii
    double con41_   = 0.0;  // 3 cos^i2 - 1
    double con42_   = 0.0;  // 1 - 5 cos^i2
    double cosio_   = 0.0;
    double cosio2_  = 0.0;
    double rteosq_  = 0.0;  // sqrt(1 - e^2)
    double omeosq_  = 0.0;  // 1 - e^2
    double posq_    = 0.0;  // semi-latus rectum squared
    double sinio_   = 0.0;
    double x1mth2_  = 0.0;  // 1 - cos^i2
    double x7thm1_  = 0.0;  // 7 cos^i2 - 1
    double eta_     = 0.0;

    double cc1_  = 0.0, cc4_  = 0.0, cc5_  = 0.0;
    double d2_   = 0.0, d3_   = 0.0, d4_   = 0.0;
    double mdot_ = 0.0, argpdot_ = 0.0, nodedot_ = 0.0;
    double omgcof_ = 0.0, xmcof_ = 0.0, nodecf_ = 0.0, t2cof_ = 0.0;
    double xlcof_ = 0.0, aycof_ = 0.0, delmo_ = 0.0, sinmao_ = 0.0;
    double t3cof_ = 0.0, t4cof_ = 0.0, t5cof_ = 0.0;

    bool simplifiedDrag_ = false; // perigee < 220 km  => skip d2..d5 terms

    // Conversion factors (WGS-72 / STR#3).
    double reKm_        = 6378.135;
    double xke_         = 0.0743669161;
    double vkmpersec_   = 0.0;     // reKm_*xke_/60
};

} // namespace dsp
} // namespace mbdsdr
