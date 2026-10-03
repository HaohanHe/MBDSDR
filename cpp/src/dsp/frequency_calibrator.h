// SPDX-License-Identifier: MIT
//
// Frequency calibration (晶振 ppm 误差自动测量) -- pure, offline, no hardware.
//
// A cheap SDR dongle's reference crystal is typically off by tens of ppm, which
// shifts every tuned frequency and every baseband tone by the same fractional
// amount. This module measures that error against a signal whose frequency is
// known EXACTLY, the same mechanism SDR++ / GQRX / kalibrate use:
//
//   1. take a block of complex IQ and find the spectral peak of the reference
//      with a dependency-free radix-2 FFT,
//   2. refine the peak to a fractional bin by parabolic interpolation,
//   3. compare the measured baseband position with the expected one and turn
//      the residual into a ppm correction,
//   4. repeat over several blocks and average with a consistency check, also
//      reporting the spread and a confidence value.
//
// SIGN CONVENTION (learned from kalibrate offset.cc, clean-room):
//   Let r = measured baseband position - expected baseband position (Hz) and
//   Fc = the nominal tuned RF centre (Hz). The correction value to hand to the
//   source is
//       ppmCorrection = -r / Fc * 1e6.
//   A crystal running FAST (positive error) drags an external reference to a
//   LOWER baseband position (r < 0), so the correction is positive; applying it
//   re-centres the reference (residual -> 0). This value equals the crystal
//   error and is what rtlsdr_set_freq_correction expects.
//
// The module never invents a reading: when no reference carrier rises above the
// (data-derived) peak threshold it honestly returns detected = false.
#pragma once

#include <complex>
#include <vector>
#include <QString>

namespace mbdsdr {
namespace dsp {

// The kind of known-accurate reference the measurement is anchored to. The
// capability is fully generic -- any SDR and any signal of exactly known
// frequency works; these three only differ in how the expected baseband
// position is derived and how the user is guided.
enum class CalibrationReference {
    HandheldGuided,  // (a) user keys a handheld on a named exact frequency
    GsmFcch,         // (b) GSM FCCH pure tone (expected +kFcchToneHz)
    Manual           // (c) any user-supplied exact frequency
};

// Human-readable name of a reference kind (for the wizard / agent payload).
QString calibrationReferenceName(CalibrationReference ref);

// One independent carrier measurement (a single block / segment).
struct CalibrationMeasurement {
    bool detected = false;        // a reference carrier actually crossed the gate
    double measuredFreqHz = 0.0;  // sub-bin carrier position in baseband (Hz)
    double expectedFreqHz = 0.0;  // expected baseband position (Hz)
    double offsetHz = 0.0;        // measuredFreqHz - expectedFreqHz
    double ppm = 0.0;             // -offsetHz / centreHz * 1e6 (correction value)
    double peakSnrDb = 0.0;       // peak bin above the median bin (honest strength)
    int fftSize = 0;              // FFT points used for this measurement
};

// Aggregated result over several independent measurements.
struct CalibrationResult {
    bool detected = false;        // at least one consistent carrier was measured
    CalibrationReference reference = CalibrationReference::Manual;

    double ppm = 0.0;             // mean accepted correction (the value to apply)
    double confidence = 0.0;      // 0..1 -- from spread, SNR and accepted count
    double spreadPpm = 0.0;       // sample standard deviation of accepted ppm
    double meanOffsetHz = 0.0;    // mean accepted baseband residual
    double measuredFreqHz = 0.0;  // mean accepted measured baseband position
    double expectedFreqHz = 0.0;  // expected baseband position
    double worstSnrDb = 0.0;      // lowest SNR among accepted measurements

    int measurementsUsed = 0;     // accepted (in-consistency) measurements
    int measurementsAttempted = 0;

    // Every per-block measurement, accepted or not, for the UI / diagnostics.
    std::vector<CalibrationMeasurement> segments;

    // Honest, user-facing summary (Chinese in the UI); states "no reference
    // carrier detected" rather than guessing when nothing was found.
    QString status;
};

// Parameters governing a measurement. Defaults are derived from the design
// tokens (peak threshold) and from the data (FFT sizing); construct with the
// RF centre used for the ppm denominator.
struct CalibratorConfig {
    double centreFreqHz = 0.0;     // nominal tuned RF centre (ppm denominator)
    double expectedBasebandHz = 0.0;// expected baseband position of the reference
    double thresholdDb = 15.0;     // peak must exceed the median by this many dB
    int fftSize = 0;               // 0 = auto (largest power-of-two <= the block)
    int minFftSize = 512;          // below this the block is honestly rejected
    int maxFftSize = 16384;        // caps the dependency-free FFT
    // Reject a per-block measurement whose ppm differs from the median by more
    // than this many ppm (consistency / outlier guard for mis-locked peaks).
    double consistencyPpm = 5.0;
};

// Measure a single complex block: peak search around the expected baseband
// position, parabolic sub-bin interpolation, SNR vs the median bin. Pure.
CalibrationMeasurement measureCarrier(
        const std::vector<std::complex<float>>& iq,
        double sampleRateHz, const CalibratorConfig& cfg);

// Aggregate several independent blocks: measure each, drop below-threshold and
// out-of-consistency measurements, then average and score confidence. Pure.
CalibrationResult calibrateFromBlocks(
        const std::vector<std::vector<std::complex<float>>>& blocks,
        double sampleRateHz, CalibrationReference ref,
        const CalibratorConfig& cfg);

// Convenience: split one long capture into `segments` equal, non-overlapping
// pieces and run calibrateFromBlocks. Pure.
CalibrationResult calibrateFromCapture(
        const std::vector<std::complex<float>>& iq,
        double sampleRateHz, CalibrationReference ref,
        const CalibratorConfig& cfg, int segments = 4);

// Build the power spectrum (magnitude in dB, DC centred) for a live preview.
// Returns fftN bins ordered from -Fs/2..+Fs/2; each value is 20*log10|X[k]|
// normalised to the block, plus the fractional bin of the peak near
// expectedBasebandHz and the peak's dB. Used by the wizard's spectrum panel;
// fftN follows the same auto-sizing as measureCarrier.
struct SpectrumPreview {
    std::vector<float> dbBins;    // fftN values, DC centred
    int fftSize = 0;
    int peakBin = -1;             // integer bin of the strongest nearby peak
    double peakFractionalBin = -1.0; // sub-bin position (DC-centred coordinates)
    double peakDb = -200.0;
};
SpectrumPreview buildSpectrumPreview(
        const std::vector<std::complex<float>>& iq,
        double sampleRateHz, const CalibratorConfig& cfg);

// Persist + apply helpers, kept side-effect-only and engine-free so the math
// above stays pure and the DSP test stays light (QtCore only).
//
// savePpmSetting writes the SAME QSettings key the main window reads
// ("rtl/ppm", see main_window.cpp); currentPpmSetting reads it back. The
// caller (wizard / agent) then forwards the value to engine->setPpm().
// predictedResidualHz gives the residual expected AFTER applying: adding a
// +ppm correction shifts the measured baseband up by centre*ppm/1e6, so with
// the measured correction this is ~0 (the before/after comparison).
void savePpmSetting(double ppm);
double currentPpmSetting();
double predictedResidualHz(double measuredResidualHz,
                          double centreHz, double ppmToApply);

} // namespace dsp
} // namespace mbdsdr
