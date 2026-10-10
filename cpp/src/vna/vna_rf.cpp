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

// ---- Smith / Polar --------------------------------------------------------
std::complex<double> smithNormalizedZ(std::complex<double> s11, double z0) {
    (void)z0; // normalized already: z_norm = (1+g)/(1-g)
    std::complex<double> d = 1.0 - s11;
    if (d == std::complex<double>(0, 0))
        return {std::numeric_limits<double>::infinity(),
                std::numeric_limits<double>::infinity()};
    return (1.0 + s11) / d;
}

PolarXY polarToXY(std::complex<double> s) {
    double m = std::abs(s);
    double ph = std::arg(s);
    return {m * std::cos(ph), m * std::sin(ph)};
}

// ---- Filter analysis ------------------------------------------------------
// Strategy: take |S21| dB. The "passband" level = the most common (mode) level.
// Find where the trace drops 3 dB below that. Classify by which side(s) roll off.
FilterResult analyzeFilter(const std::vector<double>& f_hz,
                           const std::vector<double>& s21_db) {
    FilterResult out;
    int n = (int)std::min(f_hz.size(), s21_db.size());
    if (n < 4) return out;
    double span = f_hz.back() - f_hz.front();
    if (span <= 0.0) return out;

    double mn = *std::min_element(s21_db.begin(), s21_db.end());
    double mx = *std::max_element(s21_db.begin(), s21_db.end());
    // Flat trace (not a filter): <6 dB peak-to-peak -> honest not-detected.
    if (mx - mn < 6.0) return out;

    // Passband reference = max level (lowest loss); stopband = min.
    out.passband_db = mx;
    out.stopband_atten_db = -(mn - mx);   // positive dB attenuation
    double edge = mx - 3.0;               // -3 dB level

    // Walk out from the band of max level to find -3 dB crossings.
    int iMax = (int)(std::max_element(s21_db.begin(), s21_db.end()) - s21_db.begin());
    int lo = iMax, hi = iMax;
    while (lo > 0 && s21_db[lo] > edge) --lo;
    while (hi < n - 1 && s21_db[hi] > edge) ++hi;
    out.f_low_hz = f_hz[lo];
    out.f_high_hz = f_hz[hi];
    out.bandwidth_hz = f_hz[hi] - f_hz[lo];

    // Classify by where the high/low attenuation sits.
    double lowEdgeDb = s21_db[0];
    double highEdgeDb = s21_db[n - 1];
    bool lowIsStop  = (lowEdgeDb  < edge);  // low-f side attenuated
    bool highIsStop = (highEdgeDb < edge);  // high-f side attenuated
    if (lowIsStop && highIsStop)      out.type = "bandpass";
    else if (!lowIsStop && !highIsStop) out.type = "bandstop";  // pass on both outer edges
    else if (lowIsStop && !highIsStop) out.type = "highpass";
    else                              out.type = "lowpass";
    out.valid = true;
    return out;
}

// ---- L-network matching ---------------------------------------------------
LMatchResult lMatchNetwork(std::complex<double> z_load, double f_hz, double r0) {
    LMatchResult out;
    double R = z_load.real();
    double X = z_load.imag();
    if (R <= 0.0 || f_hz <= 0.0 || r0 <= 0.0) return out;
    if (r0 <= R) return out;   // canonical series-on-load L match assumes R0>R
    double q2 = r0 / R - 1.0;
    if (q2 <= 0.0) return out;
    double q = std::sqrt(q2);
    out.q = q;
    double Xs = q * R - X;     // series reactance on the load side
    double Xp = r0 / q;        // shunt reactance on the source side
    const double w = kTwoPi * f_hz;

    if (Xs >= 0.0) { out.series_henry = Xs / w; out.topology = "series_L"; }
    else           { out.series_farad = -1.0 / (w * Xs); out.topology = "series_C"; }
    if (Xp >= 0.0) out.shunt_henry = Xp / w;
    else           out.shunt_farad = -1.0 / (w * Xp);
    out.valid = true;
    return out;
}

// ---- EFHW matching (inverse L, load R >> R0) ------------------------------
LMatchResult efhwMatch(std::complex<double> z_load, double f_hz, double r0) {
    LMatchResult out;
    double R = z_load.real();
    double X = z_load.imag();
    if (R <= 0.0 || f_hz <= 0.0 || r0 <= 0.0) return out;
    if (R <= r0) return out;   // EFHW canonical assumes load R > R0
    double q2 = R / r0 - 1.0;
    if (q2 <= 0.0) return out;
    double q = std::sqrt(q2);
    out.q = q;
    double Xp = R / q;         // shunt reactance on the load (high-Z) side
    double Xs = q * r0 - X;    // series reactance on the source (50) side
    const double w = kTwoPi * f_hz;
    if (Xp >= 0.0) out.shunt_henry = Xp / w;
    else           out.shunt_farad = -1.0 / (w * Xp);
    if (Xs >= 0.0) { out.series_henry = Xs / w; out.topology = "series_L"; }
    else           { out.series_farad = -1.0 / (w * Xs); out.topology = "series_C"; }
    out.valid = true;
    return out;
}


#include <cstdio>
#include <sstream>

TouchstoneData parseTouchstone(const std::string& text) {
    TouchstoneData out;
    double unitScale = 1.0;   // default Hz
    bool riFormat = true;
    std::istringstream is(text);
    std::string line;
    while (std::getline(is, line)) {
        if (line.empty()) continue;
        if (line[0] == '!') continue;            // comment
        if (line[0] == '[') continue;            // Touchstone 2.0 block header
        if (line[0] == '#') {
            std::istringstream hs(line.substr(1));
            std::string tok;
            while (hs >> tok) {
                if (tok == "GHz") unitScale = 1e9;
                else if (tok == "MHz") unitScale = 1e6;
                else if (tok == "kHz") unitScale = 1e3;
                else if (tok == "Hz")  unitScale = 1.0;
                else if (tok == "MA" || tok == "DB") {
                    out.note = "MA/DB 格式未支持（仅 R/I）";
                    return out;
                }
            }
            continue;
        }
        std::istringstream ls(line);
        double f; double a,b,c,d;
        ls >> f;
        if (!ls) continue;
        ls >> a >> b;
        if (!ls) continue;
        out.freq_hz.push_back(f * unitScale);
        out.s11.emplace_back(a, b);
        if (ls >> c >> d) { out.s21.emplace_back(c, d); out.n_ports = 2; }
    }
    if (out.freq_hz.empty()) { out.note = "无数据行"; return out; }
    out.valid = true;
    return out;
}

std::string writeTouchstone(double start_hz, double stop_hz,
                            const std::vector<std::complex<double>>& s11,
                            const std::vector<std::complex<double>>& s21) {
    std::ostringstream os;
    os << "! MBDSDR Touchstone export (clean-room, R/I)\n";
    os << "# Hz S R Z R\n";
    const size_t n = std::min(s11.size(), size_t(1));
    for (size_t i = 0; i < s11.size(); ++i) {
        double f = (n == 1) ? start_hz
                  : start_hz + (stop_hz - start_hz) * double(i) / double(n - 1);
        os << (long long)f << " " << s11[i].real() << " " << s11[i].imag();
        if (i < s21.size()) os << " " << s21[i].real() << " " << s21[i].imag();
        os << "\n";
    }
    return os.str();
}

// ---- Group delay ----------------------------------------------------------
std::vector<double> groupDelayS21(const std::vector<double>& freq_hz,
                                  const std::vector<std::complex<double>>& s21) {
    std::vector<double> gd;
    if (s21.size() < 2 || freq_hz.size() != s21.size()) return gd;
    std::vector<double> ph(s21.size());
    for (size_t i = 0; i < s21.size(); ++i) ph[i] = std::arg(s21[i]);
    // unwrap phase jumps
    for (size_t i = 1; i < ph.size(); ++i) {
        while (ph[i] - ph[i-1] > kTwoPi / 2.0) ph[i] -= kTwoPi;
        while (ph[i] - ph[i-1] < -kTwoPi / 2.0) ph[i] += kTwoPi;
    }
    gd.reserve(s21.size());
    for (size_t i = 0; i < s21.size(); ++i) {
        double w1, w2, p1, p2;
        if (i == 0) { w1 = freq_hz[0]; w2 = freq_hz[1]; p1 = ph[0]; p2 = ph[1]; }
        else if (i + 1 == ph.size()) { w1 = freq_hz[i-1]; w2 = freq_hz[i]; p1 = ph[i-1]; p2 = ph[i]; }
        else { w1 = freq_hz[i-1]; w2 = freq_hz[i+1]; p1 = ph[i-1]; p2 = ph[i+1]; }
        double dw = kTwoPi * (w2 - w1);
        gd.push_back(std::abs(dw) > 0.0 ? -(p2 - p1) / dw : 0.0);
    }
    return gd;
}

// ---- Permeability (Nicolson-Ross-Weir, thin slab) --------------------------
PermeabilityResult permeabilityNRW(const std::vector<double>& freq_hz,
                                   const std::vector<std::complex<double>>& s11,
                                   const std::vector<std::complex<double>>& s21,
                                   double t) {
    PermeabilityResult out;
    const size_t n = s11.size();
    if (n < 3 || s21.size() < n || t <= 0.0 || freq_hz.size() < n) {
        out.note = "点数不足或厚度非法"; return out;
    }
    const double mu0 = 4.0e-7 * kTwoPi / 2.0;
    out.mu_real.reserve(n); out.mu_imag.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        // Reduced reflection coefficient for a symmetric slab: Γ ≈ S11.
        // Transmission factor T ≈ (S11^2 - S21^2 + 1) / (2·S21), clamped.
        std::complex<double> g = s11[i];
        std::complex<double> s2 = s21[i];
        if (std::abs(s2) < 1e-9) { out.mu_real.push_back(std::nan("")); out.mu_imag.push_back(std::nan("")); continue; }
        std::complex<double> X = (g * g - s2 * s2 + 1.0) / (2.0 * s2);
        std::complex<double> Y = std::sqrt(1.0 - X * X);      // |Γ| = sqrt(1-X^2)
        // μr (approx, lossy) = (1+Γ)/((1-Γ)·(Y·Z0/(ω μ0 t)))
        double w = kTwoPi * freq_hz[i];
        if (std::abs(1.0 - g) < 1e-9 || std::abs(w) < 1e-9) { out.mu_real.push_back(std::nan("")); out.mu_imag.push_back(std::nan("")); continue; }
        std::complex<double> mur = (1.0 + g) / ((1.0 - g) * (Y * 50.0 / (w * mu0 * t)));
        out.mu_real.push_back(mur.real());
        out.mu_imag.push_back(-mur.imag());   // convention: mu'' = loss (positive)
    }
    out.valid = true;
    return out;
}

} // namespace vna
} // namespace mbdsdr
