// SPDX-License-Identifier: MIT
// Real signal peak detection on a power-spectrum (dBFS) frame.
//
// The algorithm works purely on the measured dbfs values -- no synthetic
// peaks are ever invented. Steps:
//   1. Estimate the noise floor as the MEDIAN of the frame (robust to a few
//      strong carriers, unlike the mean).
//   2. Candidate peaks = local maxima strictly above neighbours AND above
//      (median + thresholdDb).
//   3. Non-maximum suppression: greedily keep the tallest peaks, skipping any
//      candidate within `minSeparationBins` of an already-kept one, so a
//      single wide peak is not reported as several.
//   4. Bandwidth estimate: walk left/right from the peak down to the -3 dB
//      points and convert the bin width to Hz.
#pragma once

#include <QList>
#include <vector>

namespace mbdsdr {
namespace dsp {

struct PeakInfo {
    double freqHz = 0.0;     // absolute center frequency of the peak
    float  dbfs   = 0.0;     // peak power (dBFS)
    double bandwidthHz = 0.0;// estimated -3 dB bandwidth
};

/// Detect peaks in an fft-shifted dBFS frame (bin 0 = f0 - fs/2).
/// sampleRateHz / centerFreqHz map bin index -> absolute frequency.
/// thresholdDb is the offset ABOVE the spectral median (noise floor).
/// absFloorDbfs is an absolute dBFS floor: a candidate must exceed BOTH the
/// relative gate and this floor, suppressing sidelobes / numerical noise that
/// sit far below any real received signal.
QList<PeakInfo> detectPeaks(const std::vector<float>& dbfs,
                            double sampleRateHz,
                            double centerFreqHz,
                            double thresholdDb,
                            double absFloorDbfs = -100.0);

} // namespace dsp
} // namespace mbdsdr
