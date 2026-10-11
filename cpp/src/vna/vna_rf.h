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
    std::vector<double> peak_distances_m;  // all detected reflection-peak distances (one-way), honest
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

// ---- L-network matching (EFHW / antenna matching) -------------------------
// Single-section L match between a complex load Zload and a target R0 (Ohm,
// e.g. 50). Given a design quality factor Q, returns the series + shunt L/C
// values (Henry / Farad) for the two canonical topologies. Pure textbook
// matching equations; no device specifics.
struct LMatchResult {
    bool valid = false;
    double q = 0;
    double series_henry = 0;   // series arm inductor (>0) / capacitor
    double series_farad = 0;   // series arm value (whichever applies)
    double shunt_henry = 0;
    double shunt_farad = 0;
    std::string topology;      // "series_L_shunt_C" / "shunt_L_series_C" / ""
};
// f_hz = design frequency. Honest: valid=false when R0<=0, f<=0, or load R<=0.
LMatchResult lMatchNetwork(std::complex<double> z_load, double f_hz, double r0 = 50.0);

// EFHW / end-fed half-wave feedpoint matching: load Rload is typically much
// higher than r0 (kOhm -> 50). Inverse L topology (shunt on load, series on
// source). Pure textbook matching equations.
LMatchResult efhwMatch(std::complex<double> z_load, double f_hz, double r0 = 50.0);

// ---- Multi-reactance matching topologies (clean-room closed forms) --------
// topology (lower-case string):
//   "xps" = shunt reactance across the load, then series reactance to the
//           source (parallel-first / inverse-L; valid when R_load > r0).
//   "xs"  = series reactance on the load, then shunt reactance at the source
//           (series-first / canonical L; valid when R_load < r0).
//   "xpl" = shunt--series--shunt Pi (low-pass: shunt C at both ends, series L
//           middle; valid when R_load > r0). The Pi needs a chosen loaded Q;
//           this implementation picks Q = 2*sqrt(R_load/r0 - 1) (a documented
//           design choice, NOT a measured fact) and says so in note.
// series_*   = the middle/only series arm (Henry when inductive, Farad when
//              capacitive; the other unit is 0).
// shunt_*    = the first shunt arm (load side for xps/xpl).
// shunt2_*   = the second shunt arm (source side; xpl only, 0 otherwise).
// Honest: valid=false (+ note) when f/r0/R out of range, the topology is the
// wrong shape for this load, or the topology string is unknown -- never a
// fabricated number. r_comp is a non-negative series loss resistance folded
// into the load R before solving.
struct MatchTopologyResult {
    bool valid = false;
    bool closed_form = true;   // false when the asked topology has no closed form
    double q = 0;
    double series_henry = 0.0;
    double series_farad = 0.0;
    double shunt_henry = 0.0;
    double shunt_farad = 0.0;
    double shunt2_henry = 0.0;
    double shunt2_farad = 0.0;
    std::string topology;      // echoed + solved, e.g. "xps"
    std::string note;          // honest invalidity / design-choice explanation
};
MatchTopologyResult analyzeMatchTopology(const std::string& topology,
                                         std::complex<double> z_load,
                                         double f_hz, double r0 = 50.0,
                                         double r_comp = 0.0);

// ---- Touchstone s1p/s2p ---------------------------------------------------
// Independent clean-room reader/writer for the Touchstone 1.x/2.0 text format:
// '!' comments, # FREQUENCY-UNIT S R Z R (frequencies in Hz after unit scaling),
// then rows "f  re im [re im ...]". Only R/I (real-imaginary) format supported;
// MA/DB are reported unsupported honestly rather than mis-parsed.
struct TouchstoneData {
    bool valid = false;
    std::vector<double> freq_hz;
    std::vector<std::complex<double>> s11;
    std::vector<std::complex<double>> s21;
    int n_ports = 1;          // 1 = s1p (S11 only), 2 = s2p
    std::string note;
};
TouchstoneData parseTouchstone(const std::string& text);
// Write a Touchstone file (Hz, R/I). Returns the file text.
std::string writeTouchstone(double start_hz, double stop_hz,
                            const std::vector<std::complex<double>>& s11,
                            const std::vector<std::complex<double>>& s21);

// ---- Group delay & permeability -------------------------------------------
// Group delay (seconds) = -d(phase)/d(omega) from S21 unwrapped phase.
// Returns one value per interior frequency (centred difference); empty on <2 pts.
std::vector<double> groupDelayS21(const std::vector<double>& freq_hz,
                                  const std::vector<std::complex<double>>& s21);
// Permeability from a coaxial short-circuit (1) fixture, per the standard
// "NRW" / Nicolson-Ross-Weir two-port (S11/S21) approximation for a material
// slab of thickness t (m) in a Z0 air line. Returns mu' and mu'' arrays.
// Honest: valid=false when points<3 or the derivation is ill-conditioned.
struct PermeabilityResult {
    bool valid = false;
    std::vector<double> mu_real;   // mu_r'
    std::vector<double> mu_imag;  // mu_r''
    std::string note;
};
PermeabilityResult permeabilityNRW(const std::vector<double>& freq_hz,
                                    const std::vector<std::complex<double>>& s11,
                                    const std::vector<std::complex<double>>& s21,
                                    double slab_thickness_m);

// Equivalent-circuit extraction from measured S11 (series RLC at resonance).
// Self-written (public RLC / resonant-impedance formulas); no code copy.
struct EquivalentResult {
    bool valid = false;
    double series_r_ohm = 0.0;   // ESR at series resonance
    double series_l_h = 0.0;     // series inductance near resonance
    double series_c_f = 0.0;     // series capacitance near resonance
    double series_q = 0.0;       // loaded Q at resonance
    double resonance_hz = 0.0;
    std::string note;
};
EquivalentResult equivalentRlc(const std::vector<double>& freq_hz,
                               const std::vector<std::complex<double>>& s11,
                               double z0_ohm = 50.0);

} // namespace vna
} // namespace mbdsdr
