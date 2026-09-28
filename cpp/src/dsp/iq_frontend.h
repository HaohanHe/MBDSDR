// SPDX-License-Identifier: MIT
// IQ front-end correction: DC blocker (first-order IIR) + IQ balance
// whitening (covariance-based). Streamable, state persists across blocks.
#pragma once

#include <complex>
#include <vector>
#include <deque>

namespace mbdsdr {
namespace dsp {

// y[n] = x[n] - x[n-1] + R * y[n-1], R=0.998
class DCBlocker {
public:
    void setR(double r) { R_ = r; }
    void reset() { xPrevI_ = xPrevQ_ = yPrevI_ = yPrevQ_ = 0.0f; }
    void process(std::vector<std::complex<float>>& io);
private:
    double R_ = 0.998;
    float xPrevI_ = 0, xPrevQ_ = 0, yPrevI_ = 0, yPrevQ_ = 0;
};

// Covariance-based IQ balance whitening. Accumulates samples until enough,
// then estimates 2x2 covariance and applies a whitening matrix. Until fit,
// pass-through.
class IQBalanceCorrector {
public:
    IQBalanceCorrector();
    void setEnabled(bool e) { enabled_ = e; }
    void reset();
    void process(std::vector<std::complex<float>>& io);
private:
    bool enabled_ = true;
    bool fitted_  = false;
    std::vector<std::complex<float>> buf_;
    static constexpr std::size_t kMinFit   = 4096;
    static constexpr std::size_t kMaxFit   = 32768;
    static constexpr std::size_t kRefitEvery = 50; // blocks between re-estimates
    std::size_t blocksSinceFit_ = 0;
    float W_[2][2] = {{1,0},{0,1}}; // whitening matrix (identity until fit)

    void estimate();
};

// First-order IIR highpass applied after the DC blocker and before IQ balance.
//   y[n] = alpha * (y[n-1] + x[n] - x[n-1]), alpha = RC/(RC+dt),
//   RC = 1/(2*pi*cutoff). cutoff <= 0 bypasses the stage. I and Q each keep
// their own state.
class HighpassFilter {
public:
    void setSampleRate(double sr);
    void setCutoff(double hz);
    double cutoff() const { return cutoffHz_; }
    void reset() { xPrevI_ = xPrevQ_ = yPrevI_ = yPrevQ_ = 0.0f; }
    void process(std::vector<std::complex<float>>& io);
private:
    void recomputeAlpha();
    double sampleRate_ = 0.0;
    double cutoffHz_ = 0.0; // 0 = bypass
    float alpha_ = 0.0f;
    float xPrevI_ = 0, xPrevQ_ = 0, yPrevI_ = 0, yPrevQ_ = 0;
};

class IQFrontend {
public:
    void setDcBlockerEnabled(bool e) { dcOn_ = e; }
    void setBalanceEnabled(bool e) { balOn_ = e; }
    // Input sample rate in Hz; required to turn the highpass cutoff into alpha.
    void setSampleRate(double sr) { highpass_.setSampleRate(sr); }
    // Highpass cutoff in Hz; 0 disables/bypasses the highpass stage.
    void setHighpassCutoff(double hz) { highpass_.setCutoff(hz); }
    double highpassCutoff() const { return highpass_.cutoff(); }
    void reset() { dcBlocker_.reset(); highpass_.reset(); balance_.reset(); }
    void process(std::vector<std::complex<float>>& io) {
        if (dcOn_) dcBlocker_.process(io);
        highpass_.process(io); // bypasses itself when cutoff <= 0
        if (balOn_) balance_.process(io);
    }
private:
    bool dcOn_ = true, balOn_ = true;
    DCBlocker dcBlocker_;
    HighpassFilter highpass_;
    IQBalanceCorrector balance_;
};

} // namespace dsp
} // namespace mbdsdr
