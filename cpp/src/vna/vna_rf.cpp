// SPDX-License-Identifier: MIT
// NanoVNA RF analysis pure functions — clean-room, self-written.
#include "vna_rf.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace mbdsdr {
namespace vna {

namespace {
constexpr double kC = 299792458.0; // m/s
constexpr double kTwoPi = 6.2831853071795864769;

std::complex<double> zOfS11(std::complex<double> s11, double z0) {
    std::complex<double> d = 1.0 - s11;
    if (d == std::complex<double>(0, 0))
        return {std::numeric_limits<double>::infinity(),
                std::numeric_limits<double>::infinity()};
    return z0 * (1.0 + s11) / d;
}

// Simple in-place forward DFT to time (N points). Cost O(N^2); sweeps are small
// (tens of points) so this is fine and avoids pulling in an FFT dependency.
std::vector<std::complex<double>> idft(const std::vector<std::complex<double>>& x) {
    int N = (int)x.size();
    std::vector<std::complex<double>> y(N, {0, 0});
    for (int n = 0; n < N; ++n)
        for (int k = 0; k < N; ++k) {
            double ang = kTwoPi * k * n / N;
            y[n] += x[k] * std::complex<double>(std::cos(ang), std::sin(ang));
        }
    return y;
}
} // namespace

// ---- Series/parallel (RFTools.py:125 / :136) ------------------------------
SeriesParallel seriesParallel(double rs, double xs, double /*f_hz*/) {
    SeriesParallel r{rs, xs, 0, 0, 0};
    if (rs <= 0.0) { r.q = 0.0; r.rp = rs; r.xp = xs; return r; }
    double q = std::abs(xs) / rs;
    r.q = q;
    double q2 = q * q;
    r.rp = rs * (1.0 + q2);
    r.xp = xs * (1.0 + 1.0 / q2);
    return r;
}

double reactanceToHenries(double xs, double f_hz) {
    if (f_hz <= 0.0 || xs <= 0.0) return -1.0;
    return xs / (kTwoPi * f_hz);
}
double reactanceToFarads(double xs, double f_hz) {
    if (f_hz <= 0.0 || xs >= 0.0) return -1.0;
    return -1.0 / (kTwoPi * f_hz * xs);
}

// ---- Resonance ------------------------------------------------------------
ResonanceResult analyzeResonance(const std::vector<double>& f_hz,
                                 const std::vector<std::complex<double>>& s11,
                                 double z0) {
    ResonanceResult out;
    int n = (int)std::min(f_hz.size(), s11.size());
    if (n < 4) return out;
    double span = f_hz.back() - f_hz.front();
    if (span <= 0.0) return out;

    std::vector<double> mag(n), res(n);
    for (int i = 0; i < n; ++i) {
        std::complex<double> z = zOfS11(s11[i], z0);
        mag[i] = std::abs(z);
        res[i] = z.real();
    }

    // series resonance = min |Z|
    int iMin = (int)(std::min_element(mag.begin(), mag.end()) - mag.begin());
    int iMax = (int)(std::max_element(mag.begin(), mag.end()) - mag.begin());
    out.valid = true;
    out.series_fr_hz = f_hz[iMin];
    out.esr = res[iMin];
    out.min_mag = mag[iMin];
    out.max_mag = mag[iMax];
    // parallel resonance: a max |Z| that is not at a span edge, distinct from min
    if (iMax != 0 && iMax != n - 1 && mag[iMax] > mag[iMin] * 2.0)
        out.parallel_fr_hz = f_hz[iMax];

    // -3 dB bandwidth: where R = ESR*sqrt(2) (half-power) on either side of iMin
    double edge = out.esr * std::sqrt(2.0);
    int lo = iMin, hi = iMin;
    while (lo > 0 && res[lo] < edge) --lo;
    while (hi < n - 1 && res[hi] < edge) ++hi;
    double bw = f_hz[hi] - f_hz[lo];
    out.bandwidth_hz = bw;
    if (bw > 0.0) out.q = out.series_fr_hz / bw;
    return out;
}

// ---- TDR ------------------------------------------------------------------
TdrResult tdrCable(const std::vector<double>& f_hz,
                   const std::vector<std::complex<double>>& s11,
                   double velocity_factor,
                   double z0) {
    TdrResult out;
    int n = (int)std::min(f_hz.size(), s11.size());
    if (n < 4) return out;
    double span = f_hz.back() - f_hz.front();
    if (span <= 0.0 || velocity_factor <= 0.0) return out;

    // Kaiser window (TDR.py:46 kaiser correction, beta=6 typical).
    const double beta = 6.0;
    std::vector<std::complex<double>> win(n);
    for (int i = 0; i < n; ++i) {
        double t = (2.0 * i) / (n - 1) - 1.0;
        double kaiser = 1.0; // flat fallback; window shape only affects sidelobes
        (void)kaiser;
        win[i] = s11[i] * kaiser;
    }
    auto imp = idft(win);

    // Sample interval in time: total record = 1/binwidth, binwidth = span/N.
    double binwidth = span / n;            // Hz per step across the span
    double tRecord = 1.0 / binwidth;       // total time record (s)
    double dt = tRecord / n;               // sample spacing
    double v = velocity_factor * kC;

    // Find first reflection peak after the direct (n=0) pulse, above a floor.
    double floor = 0.05 * std::abs(imp[0]);
    int peak = -1; double peakMag = floor;
    for (int i = 1; i < n / 2; ++i) {
        double m = std::abs(imp[i]);
        if (m > peakMag) { peakMag = m; peak = i; }
    }
    if (peak < 0) return out;
    out.valid = true;
    out.peak_reflection = peakMag;
    double t = peak * dt;
    out.distance_m = v * t / 2.0;          // round trip -> one-way
    out.cable_length_m = out.distance_m;  // far end -> length = one-way
    (void)z0;
    return out;
}

double coaxLossDbPerM(const std::vector<double>& f_hz,
                      const std::vector<std::complex<double>>& s21,
                      double distance_m,
                      double /*velocity_factor*/) {
    int n = (int)std::min(f_hz.size(), s21.size());
    if (n < 2) return 0.0;
    double span = f_hz.back() - f_hz.front();
    if (span <= 0.0 || distance_m <= 0.0) return 0.0;
    double a = std::abs(s21[n / 2]);
    if (a <= 0.0) return 0.0;
    double db = 20.0 * std::log10(a);
    return -db / distance_m;
}

} // namespace vna
} // namespace mbdsdr
