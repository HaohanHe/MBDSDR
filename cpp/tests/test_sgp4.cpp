// SPDX-License-Identifier: MIT
//
// Validation of the original near-Earth SGP4 against the published Vallado
// verification set (Vallado, Crawford, Hujsak & Kelso, "Revisiting Spacetrack
// Report #3", AIAA 2006-6753; distributed via CelesTrak).
//
// Reference ephemerides (TEME position km, velocity km/s at tsince minutes
// after epoch) are taken verbatim from the published tforverf.out / STK .e
// ephemeris that ship with that distribution.  Source:
//   https://celestrak.org/publications/AIAA/2006-6753/  (AIAA-2006-6753.zip)
//
// We only exercise the NEAR-EARTH branch (period < 225 min).  Deep-space
// (SDP4) cases are out of scope for this module.
//
// Acceptance (per project spec): |dpos| <= 1e-3 km per component,
// |dvel| <= 1e-4 km/s per component.

#include "dsp/sgp4.h"

#include <cmath>
#include <cstdio>
#include <string>

using namespace mbdsdr::dsp;

namespace {

struct Fixture {
    const char* name;
    const char* line1;
    const char* line2;
    double tsinceMin;
    double ex, ey, ez;     // expected TEME position, km
    double vx, vy, vz;     // expected TEME velocity, km/s
};

// Five near-earth cases spanning a range of inclinations (34..98 deg) and
// eccentricities (8.8e-5 .. 0.186), including moderate/low-perigee drag.
const Fixture kCases[] = {
    // 00005 (EXPLORER 1-type debris): i=34.27, e=0.186, period ~133 min.
    {"00005",
     "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
     "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667",
     0.0, 7022.46529266, -1400.08296755, 0.03995155,
     1.893841015, 6.405893759, 4.534807250},
    {"00005",
     "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
     "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667",
     360.0, -7154.03120202, -3783.17682504, -3536.19412294,
     4.741887409, -4.151817765, -2.093935425},
    {"00005",
     "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
     "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667",
     720.0, -7134.59340119, 6531.68641334, 3260.27186483,
     -4.113793027, -2.911922039, -2.557327851},

    // 06251 (DELTA 1 DEB): i=58.06, e=0.0030, moderate drag, period ~92 min.
    {"06251",
     "1 06251U 62025E   06176.82412014  .00008885  00000-0  12808-3 0  3985",
     "2 06251  58.0579  54.0425 0030035 139.1568 221.1854 15.56387291  6774",
     0.0, 3988.31022699, 5498.96657235, 0.90055879,
     -3.290032738, 2.357652820, 6.496623475},
    {"06251",
     "1 06251U 62025E   06176.82412014  .00008885  00000-0  12808-3 0  3985",
     "2 06251  58.0579  54.0425 0030035 139.1568 221.1854 15.56387291  6774",
     120.0, -3935.69800083, 409.10980837, 5471.33577327,
     -3.374784183, -6.635211043, -1.942056221},
    {"06251",
     "1 06251U 62025E   06176.82412014  .00008885  00000-0  12808-3 0  3985",
     "2 06251  58.0579  54.0425 0030035 139.1568 221.1854 15.56387291  6774",
     240.0, -1675.12766915, -5683.30432352, -3286.21510937,
     5.282496925, 1.508674259, -5.354872978},

    // 88888 (original STR#3 SGP4 case): i=72.84, e=0.0087, period ~90 min.
    {"88888",
     "1 88888U          80275.98708465  .00073094  13844-3  66816-4 0    87",
     "2 88888  72.8435 115.9689 0086731  52.6988 110.5714 16.05824518  1058",
     0.0, 2328.96975262, -5995.22051338, 1719.97297192,
     2.912073281, -0.983417956, -7.090816210},
    {"88888",
     "1 88888U          80275.98708465  .00073094  13844-3  66816-4 0    87",
     "2 88888  72.8435 115.9689 0086731  52.6988 110.5714 16.05824518  1058",
     120.0, 1020.69234558, 2286.56260634, -6191.55565927,
     -3.746543902, 6.467532721, 1.827985678},
    {"88888",
     "1 88888U          80275.98708465  .00073094  13844-3  66816-4 0    87",
     "2 88888  72.8435 115.9689 0086731  52.6988 110.5714 16.05824518  1058",
     240.0, -3226.54349155, 3503.70977525, 4532.80979343,
     1.000992116, -5.788042888, 5.162585826},

    // 28057 (CBERS 2): i=98.43 (sun-synchronous), e=8.84e-5 (near-circular).
    {"28057",
     "1 28057U 03049A   06177.78615833  .00000060  00000-0  35940-4 0  1836",
     "2 28057  98.4283 247.6961 0000884  88.1964 271.9322 14.35478080140550",
     0.0, -2715.28237486, -6619.26436889, -0.01341443,
     -1.008587273, 0.422782003, 7.385272942},
    {"28057",
     "1 28057U 03049A   06177.78615833  .00000060  00000-0  35940-4 0  1836",
     "2 28057  98.4283 247.6961 0000884  88.1964 271.9322 14.35478080140550",
     120.0, -1816.87920942, -1835.78762132, 6661.07926465,
     2.325140071, 6.655669329, 2.463394512},
    {"28057",
     "1 28057U 03049A   06177.78615833  .00000060  00000-0  35940-4 0  1836",
     "2 28057  98.4283 247.6961 0000884  88.1964 271.9322 14.35478080140550",
     240.0, 1483.17364291, 5395.21248786, 4448.65907172,
     2.560540387, 4.039025766, -5.736648561},

    // 29238 (SL-12 DEB): i=51.56, e=0.020, low perigee, simplified drag.
    {"29238",
     "1 29238U 06022G   06177.28732010  .00766286  10823-4  13334-2 0   101",
     "2 29238  51.5595 213.7903 0202579  95.2503 267.9010 15.73823839  1061",
     0.0, -5566.59512819, -3789.75991159, 67.60382245,
     2.873759367, -3.825340523, 6.023253926},
    {"29238",
     "1 29238U 06022G   06177.28732010  .00766286  10823-4  13334-2 0   101",
     "2 29238  51.5595 213.7903 0202579  95.2503 267.9010 15.73823839  1061",
     120.0, 4474.27915495, -1447.72286142, 4619.83927235,
     4.712595822, 5.668306153, -2.701606741},
    {"29238",
     "1 29238U 06022G   06177.28732010  .00766286  10823-4  13334-2 0   101",
     "2 29238  51.5595 213.7903 0202579  95.2503 267.9010 15.73823839  1061",
     240.0, 1922.17712474, 5113.01138342, -4087.08470203,
     -6.490769651, -0.522350158, -3.896001154},
};

constexpr double kPosTol = 1e-3;  // km
constexpr double kVelTol = 1e-4;  // km/s

int failures = 0;

void checkCase(const Fixture& f) {
    Sgp4 sat;
    if (!sat.loadTle(f.line1, f.line2)) {
        std::printf("FAIL [%s t=%.0f] loadTle rejected\n", f.name, f.tsinceMin);
        ++failures;
        return;
    }
    if (sat.isDeepSpace()) {
        std::printf("FAIL [%s] unexpectedly flagged deep space\n", f.name);
        ++failures;
        return;
    }
    double r[3] = {0,0,0}, v[3] = {0,0,0};
    int err = sat.propagate(f.tsinceMin, r, v);
    if (err != 0) {
        std::printf("FAIL [%s t=%.0f] propagate error=%d\n", f.name, f.tsinceMin, err);
        ++failures;
        return;
    }
    double dp[3] = {r[0]-f.ex, r[1]-f.ey, r[2]-f.ez};
    double dv[3] = {v[0]-f.vx, v[1]-f.vy, v[2]-f.vz};
    double maxDp = std::fabs(dp[0]);
    for (int i = 1; i < 3; ++i) maxDp = std::max(maxDp, std::fabs(dp[i]));
    double maxDv = std::fabs(dv[0]);
    for (int i = 1; i < 3; ++i) maxDv = std::max(maxDv, std::fabs(dv[i]));

    bool ok = maxDp <= kPosTol && maxDv <= kVelTol;
    std::printf("[%s t=%6.1f min] dpos=(%+.2e,%+.2e,%+.2e) km |dpos|max=%.2e  "
                "dvel=(%+.2e,%+.2e,%+.2e) km/s |dvel|max=%.2e  %s\n",
                f.name, f.tsinceMin, dp[0], dp[1], dp[2], maxDp,
                dv[0], dv[1], dv[2], maxDv, ok ? "PASS" : "**FAIL**");
    if (!ok) {
        std::printf("         got r=(%.6f, %.6f, %.6f) v=(%.6f, %.6f, %.6f)\n",
                    r[0], r[1], r[2], v[0], v[1], v[2]);
        ++failures;
    }
}

} // namespace

int main() {
    std::printf("SGP4 near-earth validation vs Vallado tforver.out "
                "(pos<=%.0e km, vel<=%.0e km/s)\n", kPosTol, kVelTol);
    for (const Fixture& f : kCases) checkCase(f);

    if (failures == 0) std::printf("test_sgp4: ALL PASS (%zu states)\n",
                                   (sizeof(kCases)/sizeof(kCases[0])));
    else std::printf("test_sgp4: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
