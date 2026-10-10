// SPDX-License-Identifier: MIT
// NanoVNA RF analysis pure functions — clean-room, self-written.
//
// These are standard RF engineering formulas (impedance transforms, resonance,
// TDR). The *mechanism* was learned from public upstreams (nanovna-saver
// RFTools.py, NanoVNA-App OneOfEleven Graphs.h, TDR.py) as functional math;
// the code below is independently written and copies no upstream implementation.
// No Qt widgets here — plain std:: so it is trivially QTest-able and reusable.
#pragma once

#include <complex>
#include <string>
#include <vector>

namespace mbdsdr {
namespace vna {

// ---- Impedance transforms (series <-> parallel; Z->L/C) -------------------
// Given a measured series impedance Z = Rs + jXs at frequency f Hz.
struct SeriesParallel {
    double rs, xs;   // series
    double rp, xp;    // parallel (Rp || jXp)
    double q;        // quality factor |X|/R
};
// RFTools.py:125 seriesToParallel; :136 parallelToSeries (public formulas).
SeriesParallel seriesParallel(double rs, double xs, double f_hz);

// RFTools.py:101-112: reactance to component value at f Hz.
// Inductive reactance Xs>0 -> L = X/(2 pi f); capacitive Xs<0 -> C = 1/(2 pi f |X|).
// Returns -1 (invalid sentinel) when the sign does not match / f<=0.
double reactanceToHenries(double xs, double f_hz);   // inductive
double reactanceToFarads(double xs, double f_hz);     // capacitive (xs<0)

// ---- Resonance analysis ---------------------------------------------------
struct ResonanceResult {
    bool valid = false;
    double series_fr_hz = 0;   // series resonance (min |Z|)
    double parallel_fr_hz = 0;  // parallel resonance (max |Z|, crystals); 0 if none
    double esr = 0;            // series-equivalent resistance at series_fr (Ohm)
    double bandwidth_hz = 0;   // -3 dB bandwidth around series_fr
    double q = 0;              // loaded Q = fr / BW
    double min_mag = 0;        // |Z| at series_fr
    double max_mag = 0;        // |Z| peak (parallel resonance)
};
// f_hz / s11 are equal-length (>=4). Computes |Z| via s11ToImpedance(freq,Z0).
// Honest: valid=false when span<=0, too few points, or no extremum.
ResonanceResult analyzeResonance(const std::vector<double>& f_hz,
                                 const std::vector<std::complex<double>>& s11,
                                 double z0 = 50.0);

// ---- TDR ------------------------------------------------------------------
struct TdrResult {
    bool valid = false;
    double distance_m = 0;      // distance to first reflection peak (one-way)
    double cable_length_m = 0;  // = distance_m / 2
    double peak_reflection = 0; // magnitude of the first reflection peak
};
// S11(freq) -> time-domain impulse response via windowed IFFT; locate the first
// reflection peak (after the direct pulse). distance = v_f * c * t / 2.
// Honest: valid=false with <4 points, span<=0, or no peak above floor.
TdrResult tdrCable(const std::vector<double>& f_hz,
                   const std::vector<std::complex<double>>& s11,
                   double velocity_factor,
                   double z0 = 50.0);

// ---- Coax loss ------------------------------------------------------------
// Insertion loss from S21 over the span, in dB per metre (uses distance derived
// from velocity_factor). Honest: 0 when points<2 / span<=0.
double coaxLossDbPerM(const std::vector<double>& f_hz,
                      const std::vector<std::complex<double>>& s21,
                      double distance_m,
                      double velocity_factor);

// ---- Smith / Polar normalized coordinates ----------------------------------
// Normalized impedance z = (1+g)/(1-g) with g = S11 (Smith chart plane).
std::complex<double> smithNormalizedZ(std::complex<double> s11, double z0 = 50.0);
// Polar: complex -> (x,y) = mag*(cos ph, sin ph) for plotting.
struct PolarXY { double x = 0, y = 0; };
PolarXY polarToXY(std::complex<double> s);

// ---- Filter analysis ------------------------------------------------------
struct FilterResult {
    bool valid = false;
    std::string type;          // bandpass/bandstop/highpass/lowpass
    double f_low_hz = 0;       // -3 dB lower edge (BP/BS); lower edge for HP
    double f_high_hz = 0;      // -3 dB upper edge (BP/BS); upper edge for LP
    double bandwidth_hz = 0;
    double passband_db = 0;        // reference passband insertion loss (dB, <=0)
    double stopband_atten_db = 0;  // best stopband attenuation (positive dB)
};
// Given freqs + |S21| in dB, auto-detect response form and report -3 dB edges,
// bandwidth, insertion loss, stopband attenuation. Honest: valid=false when
// points<4, span<=0, or the trace is flat (not a filter shape).
FilterResult analyzeFilter(const std::vector<double>& f_hz,
                           const std::vector<double>& s21_db);

} // namespace vna
} // namespace mbdsdr
