// SPDX-License-Identifier: MIT
//
// LEO/GNSS PNT GEOMETRY AVAILABILITY -- a simplified, predictive geometry
// metric derived purely from the real propagated az/el of currently-visible
// navigation satellites. This is NOT a position solution: no pseudorange,
// no receiver measurement, no 3D fix. It answers only "how spread-out / how
// good are the visible satellites' angles", which predicts how well a real
// receiver COULD solve if it had range measurements. UI must label it
// "几何预测" (geometry prediction), never "定位".
#pragma once

#include <QList>
#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace geo {

// Simplified PDOP-style metric. For n satellites with unit line-of-sight
// vectors, the diagonal-azimuth assumption gives
//     DOP ~ sqrt( 1 / sum_i sin^2(el_i) )
// (low-elevation satellites are weighted down by sin^2, matching the common
// elevation-weighted GDOP approximation). Satellites below kGeoMinElevationDeg
// are masked out (multipath). Returns a large number when too few usable sats.
inline double simplifiedDop(const QList<double>& elevationsDeg,
                            double minElDeg) {
    double s = 0.0;
    for (double el : elevationsDeg) {
        if (el < minElDeg) continue;
        const double r = el * M_PI / 180.0;
        s += std::sin(r) * std::sin(r);
    }
    if (s <= 1e-9) return 1e9;
    return std::sqrt(1.0 / s);
}

// Four-state geometry quality, driven by named tokens in tokens.h.
enum class GeoQuality { Insufficient, Poor, Fair, Good };

inline const char* geoQualityToString(GeoQuality q) {
    switch (q) {
    case GeoQuality::Good:        return "好";
    case GeoQuality::Fair:       return "中";
    case GeoQuality::Poor:        return "差";
    case GeoQuality::Insufficient: return "不足";
    }
    return "不足";
}

// visible = usable (el>=minEl) count; dop = simplifiedDop.
inline GeoQuality classifyGeometry(int visible, double dop,
                                   int goodMinVisible, double dopGood,
                                   double dopFair, double dopPoor) {
    if (visible < 4 || !std::isfinite(dop) || dop >= dopPoor)
        return GeoQuality::Insufficient;
    if (visible >= goodMinVisible && dop <= dopGood) return GeoQuality::Good;
    if (dop <= dopFair)                              return GeoQuality::Fair;
    return GeoQuality::Poor;
}

} // namespace geo
} // namespace mbdsdr
