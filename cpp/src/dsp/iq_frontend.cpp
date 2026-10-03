// SPDX-License-Identifier: MIT
#include "iq_frontend.h"
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

void HighpassFilter::setSampleRate(double sr) {
    sampleRate_ = sr;
    recomputeAlpha();
}

void HighpassFilter::setCutoff(double hz) {
    cutoffHz_ = hz;
    recomputeAlpha();
}

void HighpassFilter::recomputeAlpha() {
    if (cutoffHz_ > 0.0 && sampleRate_ > 0.0) {
        const double dt = 1.0 / sampleRate_;
        const double RC = 1.0 / (2.0 * M_PI * cutoffHz_);
        alpha_ = static_cast<float>(RC / (RC + dt));
    } else {
        alpha_ = 0.0f; // bypass
    }
}

void HighpassFilter::process(std::vector<std::complex<float>>& io) {
    if (alpha_ <= 0.0f || alpha_ >= 1.0f) return; // cutoff<=0 or no rate: bypass
    for (auto& s : io) {
        const float xi = s.real(), xq = s.imag();
        const float yi = alpha_ * (yPrevI_ + xi - xPrevI_);
        const float yq = alpha_ * (yPrevQ_ + xq - xPrevQ_);
        s = {yi, yq};
        xPrevI_ = xi; xPrevQ_ = xq;
        yPrevI_ = yi; yPrevQ_ = yq;
    }
}

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

    // Maintain a rolling window of the most recent kMaxFit samples. A single
    // real-time block can be larger than kMaxFit (e.g. ~60k samples at 2.4M
    // S/s), so we must never erase more elements than the buffer actually
    // holds (the previous code erased io.size() from a smaller buffer -> OOB
    // iterator -> SIGSEGV).
    if (io.size() >= kMaxFit) {
        buf_.assign(io.end() - static_cast<long>(kMaxFit), io.end());
    } else {
        buf_.insert(buf_.end(), io.begin(), io.end());
        if (buf_.size() > kMaxFit) {
            const auto drop = buf_.size() - kMaxFit;
            buf_.erase(buf_.begin(), buf_.begin() + static_cast<long>(drop));
        }
    }

    if (!fitted_) {
        if (buf_.size() >= kMinFit) estimate();
    } else if (++blocksSinceFit_ >= kRefitEvery) {
        // Re-fit on the recent rolling window.
        blocksSinceFit_ = 0;
        estimate();
    }
    if (!fitted_) return;   // pass-through until the first valid fit

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

    // W = V diag(1/sqrt(l)) V^T whitens the imbalance (equalises the two
    // eigen-directions). BUT the raw whitener forces the OUTPUT covariance to
    // the identity matrix, i.e. unit total power -- on an EMPTY channel the
    // noise is isotropic (l1 == l2 == sigma^2), so W becomes diag(1/sigma) and
    // the quiet idle noise is boosted to full scale regardless of how weak it
    // is. That automatic power-normalisation is exactly the "sandpaper hiss on
    // a tuned-to-nothing" fault: a balance corrector must equalise I/Q gain and
    // phase while PRESERVING the absolute input level. Rescale W so the output
    // trace equals the input trace (trace C = l1 + l2). For isotropic noise
    // l1==l2 this collapses to the identity matrix (no boost at all); for a
    // real gain/phase imbalance it equalises without changing the volume.
    const double s1 = 1.0/std::sqrt(l1);
    const double s2 = 1.0/std::sqrt(l2);
    // Power-preserving scale: k^2 * trace(I) = trace(C) = l1 + l2.
    const double k = std::sqrt((l1 + l2) * 0.5);
    W_[0][0] = static_cast<float>(k * (v1x*v1x*s1 + v2x*v2x*s2));
    W_[0][1] = static_cast<float>(k * (v1x*v1y*s1 + v2x*v2y*s2));
    W_[1][0] = W_[0][1];
    W_[1][1] = static_cast<float>(k * (v1y*v1y*s1 + v2y*v2y*s2));
    fitted_ = true;
}

} // namespace dsp
} // namespace mbdsdr
