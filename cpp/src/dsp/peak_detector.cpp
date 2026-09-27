// SPDX-License-Identifier: MIT
#include "peak_detector.h"

#include <algorithm>
#include <cmath>
#include <vector>

namespace mbdsdr {
namespace dsp {

QList<PeakInfo> detectPeaks(const std::vector<float>& dbfs,
                            double sampleRateHz,
                            double centerFreqHz,
                            double thresholdDb,
                            double absFloorDbfs) {
    QList<PeakInfo> out;
    const std::size_t n = dbfs.size();
    if (n < 5 || sampleRateHz <= 0.0) return out;

    // --- 1. Noise floor = median of the frame ------------------------------
    std::vector<float> sorted(dbfs.begin(), dbfs.end());
    std::sort(sorted.begin(), sorted.end());
    const float median = sorted[n / 2];
    const float levelGate = median + static_cast<float>(thresholdDb);
    // Absolute floor: even if the relative gate is very low (near-silent test
    // signal), nothing below this counts as a real signal.
    const float absFloor = static_cast<float>(absFloorDbfs);

    // Bin -> frequency: bin 0 sits at centerFreq - fs/2 (already fft-shifted).
    const double binHz = sampleRateHz / static_cast<double>(n - 1);
    const double fLowEdge = centerFreqHz - sampleRateHz / 2.0;
    auto binFreq = [&](std::size_t i) { return fLowEdge + binHz * static_cast<double>(i); };

    // --- 2. Local-maximum candidates above the gate ------------------------
    struct Cand { std::size_t idx; float v; };
    std::vector<Cand> cands;
    cands.reserve(64);
    for (std::size_t i = 1; i + 1 < n; ++i) {
        const float v = dbfs[i];
        if (v > dbfs[i - 1] && v >= dbfs[i + 1]
            && v > levelGate && v > absFloor)
            cands.push_back({i, v});
    }
    if (cands.empty()) return out;

    // --- 3. Greedy non-maximum suppression (tallest first, min separation) -
    std::sort(cands.begin(), cands.end(),
              [](const Cand& a, const Cand& b) { return a.v > b.v; });
    const auto minSep = static_cast<std::size_t>(
        std::max<std::size_t>(1, static_cast<std::size_t>(n / 100))); // ~1% of N

    std::vector<std::size_t> kept;
    kept.reserve(cands.size());
    for (const Cand& c : cands) {
        bool tooClose = false;
        for (std::size_t k : kept) {
            const std::size_t d = (c.idx > k) ? (c.idx - k) : (k - c.idx);
            if (d < minSep) { tooClose = true; break; }
        }
        if (!tooClose) kept.push_back(c.idx);
    }

    // --- 4. Build PeakInfo with -3 dB bandwidth ----------------------------
    for (std::size_t idx : kept) {
        const float peak = dbfs[idx];
        const float halfDb = peak - 3.0f;

        std::size_t lo = idx;
        while (lo > 0 && dbfs[lo] > halfDb) --lo;
        std::size_t hi = idx;
        while (hi + 1 < n && dbfs[hi] > halfDb) ++hi;

        PeakInfo pk;
        pk.freqHz = binFreq(idx);
        pk.dbfs   = peak;
        pk.bandwidthHz = static_cast<double>(hi - lo) * binHz;
        out.push_back(pk);
    }

    // Loudest first -- a signal finder surfaces the strongest carrier on top.
    std::sort(out.begin(), out.end(),
              [](const PeakInfo& a, const PeakInfo& b) { return a.dbfs > b.dbfs; });
    return out;
}

} // namespace dsp
} // namespace mbdsdr
