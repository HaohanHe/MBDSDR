// SPDX-License-Identifier: MIT
// Minimal band scanner helper: given a start/stop/step, yield the frequencies
// to tune to and aggregate detected peak levels. The actual tuning/peak
// extraction is driven by the caller (engine + peak_detector); this keeps the
// stepping logic testable in isolation.
#pragma once

#include <QList>

namespace mbdsdr {
namespace dsp {

struct ScanHit { double freqHz = 0.0; float peakDb = -200.0f; };

class BandScanner {
public:
    void setRange(double startHz, double stopHz, double stepHz) {
        startHz_ = startHz; stopHz_ = stopHz; stepHz_ = stepHz;
    }
    QList<double> frequencies() const {
        QList<double> out;
        if (stepHz_ <= 0 || stopHz_ <= startHz_) return out;
        for (double f = startHz_; f <= stopHz_; f += stepHz_) out.append(f);
        return out;
    }
    void record(double freqHz, float peakDb) { hits_.append({freqHz, peakDb}); }
    const QList<ScanHit>& hits() const { return hits_; }
    void clear() { hits_.clear(); }

private:
    double startHz_ = 0.0, stopHz_ = 0.0, stepHz_ = 0.0;
    QList<ScanHit> hits_;
};

} // namespace dsp
} // namespace mbdsdr
