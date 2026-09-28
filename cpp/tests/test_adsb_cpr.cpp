// SPDX-License-Identifier: MIT
// CPR round-trip: encode a known lat/lon into even/odd CPR slots, decode back.
#include <cmath>
#include <cstdio>
#include "dsp/adsb_decoder.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

static void roundTrip(double lat, double lon) {
    int nl = cprNL(lat);
    int latE = (int)std::fmod(std::floor(std::fmod(lat,360.0)/6.0 * 131072.0), 131072);
    int latO = (int)std::fmod(std::floor(std::fmod(lat,360.0)/(360.0/59.0) * 131072.0), 131072);
    int lonE = (int)std::fmod(std::floor(std::fmod(lon,360.0)/(360.0/nl) * 131072.0), 131072);
    int lonO = (int)std::fmod(std::floor(std::fmod(lon,360.0)/(360.0/(nl-1)) * 131072.0), 131072);
    CprPair p; p.latEven=latE; p.lonEven=lonE; p.latOdd=latO; p.lonOdd=lonO;
    p.haveEven=p.haveOdd=true;
    double la, lo;
    bool ok = cprGlobalDecode(p, la, lo);
    std::printf("lat=%.1f lon=%.1f -> decoded lat=%.4f lon=%.4f (NL=%d)\n",
                lat, lon, la, lo, nl);
    check(ok, "decode returned true");
    check(std::abs(la-lat) < 0.01, "lat within 0.01 deg");
    check(std::abs(lo-lon) < 0.01, "lon within 0.01 deg");
}

int main() {
    // Sanity: NL formula at known latitudes.
    std::printf("NL(0)=%d NL(10)=%d NL(40)=%d NL(80)=%d\n",
                cprNL(0), cprNL(10), cprNL(40), cprNL(80));
    check(cprNL(0)==59, "NL(0)=59");
    check(cprNL(10)==59, "NL(10)=59");
    check(cprNL(40)>=40 && cprNL(40)<=50, "NL(40)~45");
    check(cprNL(80)>=8 && cprNL(80)<=12, "NL(80)~10");

    roundTrip(10.0, 20.0);
    roundTrip(40.0, -74.0);
    roundTrip(-33.0, 151.0);

    if (failures==0) std::printf("test_adsb_cpr: ALL PASS\n");
    else std::printf("test_adsb_cpr: %d FAILURE(S)\n", failures);
    return failures?1:0;
}
