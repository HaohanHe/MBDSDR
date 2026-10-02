// SPDX-License-Identifier: MIT
//
// SSTV pure functions: frequency -> pixel mapping, VIS code decode, and the
// zero-crossing instantaneous-frequency estimator. Clean-room re-derivation of
// the public SSTV waveform conventions (1500 Hz = black, 2300 Hz = white;
// VIS: 30 ms bits, 1100 Hz = mark/1, 1300 Hz = space/0, even parity).
// These are the stateless, fully testable core of mbdsdr_ai/sstv_decoder.py;
// the line-sync / mode-identification state machines stay on the Python side
// for now.
#pragma once

#include <cstddef>
#include <vector>

namespace mbdsdr {
namespace dsp {

/// 1500 Hz -> 0 (black), 2300 Hz -> 255 (white), linear, clamped.
int sstvFreqToPixel(float freqHz);

/// Result of decoding the 7 VIS data bits + even-parity bit.
struct SstvVisResult {
    int  code = -1;        // 7-bit VIS code, -1 when any bit is out of band
    bool parityOk = false;
    bool allInBand = false;
};

/// Decode VIS bits from the per-bit mean frequencies.
/// dataBitFreqs: 7 entries, LSB first (matches sstv_decoder.py bit loop).
/// parityBitFreq: the 8th (parity) bit mean frequency.
/// A bit is mark (1) inside (1050,1150) Hz, space (0) inside (1250,1350) Hz;
/// anything else marks the bit as undecodable.
SstvVisResult sstvDecodeVisBits(const float dataBitFreqs[7], float parityBitFreq);

/// Zero-crossing instantaneous-frequency estimator (real audio in, Hz out).
/// Removes the mean, linearly interpolates zero crossings, and assigns
/// sr/(2*gap) Hz to every sample between two consecutive crossings. Samples
/// outside any crossing pair are filled with 1500 Hz (neutral). No median
/// smoothing: the Python scipy median filter is intentionally omitted so the
/// estimator stays a tiny, deterministic, unit-testable core.
void sstvInstFreqZeroCrossing(const float* samples, std::size_t n,
                              double sampleRateHz, std::vector<float>& out);

} // namespace dsp
} // namespace mbdsdr
