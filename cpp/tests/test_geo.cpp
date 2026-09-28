// SPDX-License-Identifier: MIT
// ECEF -> WGS84 lat/lon correctness.
#include <cmath>
#include <cstdio>
#include "dsp/tle_client.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

int main() {
    // (a, 0, 0) -> lat=0, lon=0
    auto g1 = TleClient::ecefToLatLon(6378.137, 0.0, 0.0);
    std::printf("(a,0,0) -> lat=%.6f lon=%.6f\n", g1.latDeg, g1.lonDeg);
    check(std::abs(g1.latDeg) < 1e-6 && std::abs(g1.lonDeg) < 1e-6, "equator lon=0");

    // (0, a, 0) -> lat=0, lon=90
    auto g2 = TleClient::ecefToLatLon(0.0, 6378.137, 0.0);
    std::printf("(0,a,0) -> lat=%.6f lon=%.6f\n", g2.latDeg, g2.lonDeg);
    check(std::abs(g2.latDeg) < 1e-6 && std::abs(g2.lonDeg - 90.0) < 1e-6, "equator lon=90");

    // North pole (0,0,b) -> lat=90
    const double b = 6378.137 * std::sqrt(1.0 - 0.00669437999014);
    auto g3 = TleClient::ecefToLatLon(0.0, 0.0, b);
    std::printf("(0,0,b) -> lat=%.6f\n", g3.latDeg);
    check(std::abs(g3.latDeg - 90.0) < 1e-4, "north pole");

    if (failures == 0) std::printf("test_geo: ALL PASS\n");
    else std::printf("test_geo: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
