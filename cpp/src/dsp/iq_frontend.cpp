// SPDX-License-Identifier: MIT
#include "iq_frontend.h"
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

void DCBlocker::process(std::vector<std::complex<float>>& io) {
    for (auto& s : io) {
        const float xi = s.real(), xq = s.imag();
        const float yi = xi - xPrevI_ + static_cast<float>(R_) * yPrevI_;
        const float yq = xq - xPrevQ_ + static_cast<float>(R_) * yPrevQ_;
        s = {yi, yq};
        xPrevI_ = xi; xPrevQ_ = xq;
        yPrevI_ = yi; yPrevQ_ = yq;
    }
}

IQBalanceCorrector::IQBalanceCorrector() {
    buf_.reserve(kMaxFit);
}

void IQBalanceCorrector::reset() {
    buf_.clear();
    fitted_ = false;
    blocksSinceFit_ = 0;
    W_[0][0] = 1; W_[0][1] = 0; W_[1][0] = 0; W_[1][1] = 1;
}

void IQBalanceCorrector::process(std::vector<std::complex<float>>& io) {
    if (!enabled_) return;
    if (!fitted_) {
        for (auto s : io) {
            buf_.push_back(s);
            if (buf_.size() > kMaxFit) buf_.erase(buf_.begin(), buf_.begin() + io.size());
        }
        if (buf_.size() >= kMinFit) estimate();
    }
    if (!fitted_) return;

    // Re-fit every N blocks
    if (++blocksSinceFit_ >= kRefitEvery) { blocksSinceFit_ = 0; estimate(); }

    for (auto& s : io) {
        const float i = s.real(), q = s.imag();
        s = {W_[0][0]*i + W_[0][1]*q,
             W_[1][0]*i + W_[1][1]*q};
    }
}

void IQBalanceCorrector::estimate() {
    if (buf_.size() < kMinFit) return;
    // De-mean, compute 2x2 covariance
    double mi = 0, mq = 0;
    for (auto s : buf_) { mi += s.real(); mq += s.imag(); }
    mi /= buf_.size(); mq /= buf_.size();
    double c00 = 0, c01 = 0, c11 = 0;
    for (auto s : buf_) {
        double di = s.real() - mi, dq = s.imag() - mq;
        c00 += di*di; c01 += di*dq; c11 += dq*dq;
    }
    const double n = buf_.size();
    c00 /= n; c01 /= n; c11 /= n;

    // 2x2 symmetric eigendecomposition (closed form)
    const double tr = c00 + c11;
    const double det = c00*c11 - c01*c01;
    const double disc = std::sqrt(std::max(0.0, (tr*tr)/4 - det));
    const double l1 = tr/2 + disc;
    const double l2 = tr/2 - disc;
    if (l1 <= 1e-12 || l2 <= 1e-12) return;

    // Eigenvectors
    double v1x, v1y, v2x, v2y;
    if (std::abs(c01) > 1e-12) {
        v1x = c01; v1y = l1 - c00;
        v2x = c01; v2y = l2 - c00;
    } else {
        v1x = 1; v1y = 0; v2x = 0; v2y = 1;
    }
    const double n1 = std::sqrt(v1x*v1x + v1y*v1y);
    const double n2 = std::sqrt(v2x*v2x + v2y*v2y);
    v1x/=n1; v1y/=n1; v2x/=n2; v2y/=n2;

    // W = V diag(1/sqrt(l)) V^T
    const double s1 = 1.0/std::sqrt(l1);
    const double s2 = 1.0/std::sqrt(l2);
    W_[0][0] = static_cast<float>(v1x*v1x*s1 + v2x*v2x*s2);
    W_[0][1] = static_cast<float>(v1x*v1y*s1 + v2x*v2y*s2);
    W_[1][0] = W_[0][1];
    W_[1][1] = static_cast<float>(v1y*v1y*s1 + v2y*v2y*s2);
    fitted_ = true;
}

} // namespace dsp
} // namespace mbdsdr
