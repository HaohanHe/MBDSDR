// SPDX-License-Identifier: MIT
// Rational (fractional) sample-rate converter for complex baseband.
//
// Clean-room implementation of the two-rate design learned from the multirate
// chain (mechanism only, no upstream code reused):
//
//   outRate / inRate  ==  L / M  after reducing the rounded rates by GCD,
//   i.e. the converter upsamples by L, low-pass anti-images, then decimates by
//   M. The power-of-two pre-decimation that shrinks the working rate is done by
// the owning Channelizer (its integer decimating FIR); this stage only handles
// the residual rational ratio between the intermediate rate and the target.
//
//   1) reduce  a=round(in), b=round(out)  by g = gcd(a,b)  ->  L=b/g, M=a/g
//   2) windowed-sinc prototype at rate in*L, cutoff = min(in,out)/2, gain = L
//   3) split the prototype into L polyphase phases e_p[n] = h[n*L + p]
//   4) commutator, per output:  phase += M;  offset += phase/L;  phase %= L
//
// Streaming: the filter history (last tapsPerPhase-1 input samples) and the
// commutator (phase, resume offset) persist across blocks, so a long stream
// produces exactly out = in * L / M samples asymptotically, with no pitch
// drift. Header-only (inline) so it needs no separate translation unit in the
// many per-test target source lists.
#pragma once

#include <complex>
#include <vector>
#include <numeric>
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

class RationalResampler {
public:
    enum class Mode { Passthrough, Rational };

    RationalResampler() = default;

    // Configure the residual rational ratio. tapsPerPhase is the polyphase bank
    // depth per phase (prototype length = tapsPerPhase * L).
    void configure(double inRateHz, double outRateHz, int tapsPerPhase = 32) {
        inSr_ = inRateHz;
        outSr_ = outRateHz;
        const long a = static_cast<long>(std::lround(inRateHz));
        const long b = static_cast<long>(std::lround(outRateHz));
        const long g = std::gcd(a, b > 0 ? b : 1);
        L_ = static_cast<int>(b / g);
        M_ = static_cast<int>(a / g);
        if (L_ == M_ || L_ <= 0 || M_ <= 0) {
            // Net ratio is 1:1 (or degenerate) -> no rate conversion.
            mode_ = Mode::Passthrough;
            L_ = M_ = 1;
            tapsPerPhase_ = 0;
            bank_.clear();
            reset();
            return;
        }
        mode_ = Mode::Rational;
        tapsPerPhase_ = tapsPerPhase;
        buildBank();
        reset();
    }

    void reset() {
        phase_ = 0;
        resume_ = 0;
        const int H = tapsPerPhase_ - 1;
        hist_.assign(H > 0 ? static_cast<std::size_t>(H) : 0,
                     std::complex<float>(0.0f, 0.0f));
    }

    // Process one block at input rate; returns a block at output rate.
    std::vector<std::complex<float>> process(const std::vector<std::complex<float>>& in) {
        if (mode_ == Mode::Passthrough || tapsPerPhase_ <= 0) return in;

        const int H = tapsPerPhase_ - 1;
        const std::size_t B = in.size();

        // Splice retained history with the new block: data = [hist | in].
        std::vector<std::complex<float>> data;
        data.reserve(hist_.size() + B);
        data = hist_;
        data.insert(data.end(), in.begin(), in.end());

        std::vector<std::complex<float>> out;
        long offset = resume_;  // next output window start, relative to block 0
        while (offset + tapsPerPhase_ <= static_cast<long>(B)) {
            const std::size_t base =
                static_cast<std::size_t>(H + offset);  // window in `data`
            const std::vector<float>& ph =
                bank_[static_cast<std::size_t>(phase_)];
            std::complex<float> acc(0.0f, 0.0f);
            for (int k = 0; k < tapsPerPhase_; ++k)
                acc += ph[static_cast<std::size_t>(k)] * data[base + k];
            out.push_back(acc);

            // Commutator advance (phase += M; offset += phase/L; phase %= L).
            phase_ += M_;
            offset += phase_ / L_;
            phase_ %= L_;
        }

        resume_ = offset - static_cast<long>(B);

        // Retain the last H samples of the concatenated stream for next block.
        if (H > 0) {
            hist_.assign(data.end() - static_cast<std::ptrdiff_t>(H), data.end());
        }
        return out;
    }

    Mode mode() const { return mode_; }
    int interp() const { return L_; }
    int decim() const { return M_; }
    double inputRateHz() const { return inSr_; }
    double outputRateHz() const { return outSr_; }

private:
    void buildBank() {
        // Prototype low-pass designed at the upsampled rate in*L.
        const double designRate = inSr_ * L_;
        const double cutoff = std::min(inSr_, outSr_) / 2.0;
        const long N = static_cast<long>(tapsPerPhase_) * L_;
        const double mid = (N - 1) / 2.0;

        std::vector<double> h(static_cast<std::size_t>(N));
        double sum = 0.0;
        for (long i = 0; i < N; ++i) {
            const double x = i - mid;
            const double u = (2.0 * cutoff / designRate) * x;
            const double s = (u == 0.0) ? 1.0 : std::sin(M_PI * u) / (M_PI * u);
            const double win = 0.5 * (1.0 - std::cos(2.0 * M_PI * i / (N - 1)));
            h[static_cast<std::size_t>(i)] = (2.0 * cutoff / designRate) * s * win;
            sum += h[static_cast<std::size_t>(i)];
        }
        // Normalize to unity DC gain, then apply the L-fold interpolation gain
        // that compensates for zero-stuffing in the up-sampler.
        const double gain = (sum != 0.0) ? (L_ / sum) : 1.0;
        for (long i = 0; i < N; ++i) h[static_cast<std::size_t>(i)] *= gain;

        // Polyphase split: e_p[n] = h[n*L + p].
        bank_.assign(static_cast<std::size_t>(L_),
                     std::vector<float>(static_cast<std::size_t>(tapsPerPhase_), 0.0f));
        for (int p = 0; p < L_; ++p)
            for (int n = 0; n < tapsPerPhase_; ++n) {
                const long idx = static_cast<long>(n) * L_ + p;
                bank_[static_cast<std::size_t>(p)][static_cast<std::size_t>(n)] =
                    (idx < N) ? static_cast<float>(h[static_cast<std::size_t>(idx)])
                              : 0.0f;
            }
    }

    Mode mode_ = Mode::Passthrough;
    double inSr_ = 0.0;
    double outSr_ = 0.0;
    int L_ = 1;   // interpolation factor
    int M_ = 1;   // decimation factor
    int tapsPerPhase_ = 0;
    std::vector<std::vector<float>> bank_;          // L_ phases
    std::vector<std::complex<float>> hist_;          // H previous input samples
    int phase_ = 0;                                 // commutator phase (0..L-1)
    long resume_ = 0;                              // overshoot into next block
};

} // namespace dsp
} // namespace mbdsdr
