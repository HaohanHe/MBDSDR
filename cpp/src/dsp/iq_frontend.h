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

class IQFrontend {
public:
    void setDcBlockerEnabled(bool e) { dcOn_ = e; }
    void setBalanceEnabled(bool e) { balOn_ = e; }
    void reset() { dcBlocker_.reset(); balance_.reset(); }
    void process(std::vector<std::complex<float>>& io) {
        if (dcOn_) dcBlocker_.process(io);
        if (balOn_) balance_.process(io);
    }
private:
    bool dcOn_ = true, balOn_ = true;
    DCBlocker dcBlocker_;
    IQBalanceCorrector balance_;
};

} // namespace dsp
} // namespace mbdsdr
