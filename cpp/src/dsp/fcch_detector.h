// GSM FCCH (Frequency Correction CHannel) detector + clock-ppm estimator.
//
// This is a PURE OFFLINE algorithm ported (clean-room) from the kalibrate-rtl
// study notes (docs/learn/kalibrate.md). It touches NO hardware: given a block
// of complex baseband IQ it looks for the GSM FCCH pure tone, which lands at
// GSM_RATE/4 = 67708.333 Hz above the carrier, and from the measured tone
// offset estimates the local LO/clock ppm offset against a known ARFCN carrier.
//
// NOT HARDWARE / 非硬件: there is no RTL-SDR interaction here. The synthetic
// tests feed it fabricated IQ; with no real GSM signal the UI must stay honest
// (idle / disabled), never reporting a measured ppm.
#pragma once

#include <complex>
#include <cstddef>
#include <vector>

namespace mbdsdr {
namespace dsp {

// GSM symbol rate = 1625000/6 symbols/sec; FCCH burst modulates to a pure tone
// at 1/4 of that (kalibrate kal.cc:70, fcch_detector.h:68).
constexpr double kGsmSymbolRate = 1625000.0 / 6.0;     // 270833.333…
constexpr double kFcchToneHz     = kGsmSymbolRate / 4.0; // 67708.333…

struct FcchResult {
    bool   detected = false;     // a FCCH-like strong pure tone was found
    double measuredToneHz = 0.0; // measured tone position in baseband (Hz)
    double ppm = 0.0;            // estimated clock offset vs carrier
    double peakToAvgDb = 0.0;    // peak / mean-spectrum ratio (confidence)
};

// Runs the offline FCCH detection on one IQ block.
//   sampleRateHz   : real capture rate the IQ was taken at (e.g. 270833 Hz)
//   centerFreqHz   : the ARFCN carrier the receiver is tuned to (ppm reference)
// Never touches hardware; returns detected=false on noise-only / too-short input.
FcchResult detectFcch(const std::vector<std::complex<float>>& iq,
                      double sampleRateHz, double centerFreqHz);

}  // namespace dsp
}  // namespace mbdsdr
