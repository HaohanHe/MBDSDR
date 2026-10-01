// SPDX-License-Identifier: MIT
#include "tuner_gain_table.h"

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace dsp {

void TunerGainTable::setTable(std::vector<int> gainsDb10) {
    tableDb10_ = std::move(gainsDb10);
    // Keep ascending so snap() can assume sorted order; if a future caller
    // passes an unsorted table we normalise here rather than trusting input.
    std::sort(tableDb10_.begin(), tableDb10_.end());
}

double TunerGainTable::snap(double gainDb) const {
    if (tableDb10_.empty())
        return gainDb;   // honest passthrough: no discrete info known

    const int reqDb10 = static_cast<int>(std::lround(gainDb * 10.0));

    // Clamp to [min, max] -- table is sorted after setTable().
    if (reqDb10 <= tableDb10_.front())
        return tableDb10_.front() / 10.0;
    if (reqDb10 >= tableDb10_.back())
        return tableDb10_.back() / 10.0;

    // Find the first entry >= request.
    auto it = std::lower_bound(tableDb10_.begin(), tableDb10_.end(), reqDb10);
    const int hi = *it;
    const int lo = *(it - 1);

    // Nearest. On an exact half tie, choose the HIGHER step (deterministic and
    // matches a conservative "go up" bias for received signal strength).
    const int dHi = hi - reqDb10;
    const int dLo = reqDb10 - lo;
    const int chosen = (dHi < dLo) ? hi : (dLo < dHi) ? lo : hi;

    return chosen / 10.0;
}

std::vector<double> TunerGainTable::availableGainsDb() const {
    std::vector<double> out;
    out.reserve(tableDb10_.size());
    for (int g : tableDb10_)
        out.push_back(g / 10.0);
    return out;
}

} // namespace dsp
} // namespace mbdsdr
