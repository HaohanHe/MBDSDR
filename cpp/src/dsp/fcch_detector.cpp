#include "fcch_detector.h"

#include <algorithm>
#include <cmath>
#include <numeric>

namespace mbdsdr {
namespace dsp {
namespace {

// Small iterative radix-2 FFT (in-place, decimation-in-time). Length must be a
// power of two; kept dependency-free so the offline detector needs no fftw.
void fftRadix2(std::vector<std::complex<float>>& x) {
    const std::size_t N = x.size();
    if (N < 2) return;
    // Bit-reversal permutation.
    for (std::size_t i = 1, j = 0; i < N; ++i) {
        std::size_t bit = N >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(x[i], x[j]);
    }
    for (std::size_t len = 2; len <= N; len <<= 1) {
        const double angle = -2.0 * M_PI / static_cast<double>(len);
        const std::complex<float> wlen(static_cast<float>(std::cos(angle)),
                                        static_cast<float>(std::sin(angle)));
        for (std::size_t i = 0; i < N; i += len) {
            std::complex<float> w(1.0f, 0.0f);
            for (std::size_t k = 0; k < len / 2; ++k) {
                const std::complex<float> u = x[i + k];
                const std::complex<float> v = x[i + k + len / 2] * w;
                x[i + k] = u + v;
                x[i + k + len / 2] = u - v;
                w *= wlen;
            }
        }
    }
}

std::size_t nextPow2(std::size_t n) {
    std::size_t p = 1;
    while (p < n) p <<= 1;
    return p;
}

}  // namespace

FcchResult detectFcch(const std::vector<std::complex<float>>& iq,
                      double sampleRateHz, double centerFreqHz) {
    FcchResult out;
    // Need enough bandwidth-time to resolve the ~67.7 kHz tone.
    if (iq.size() < 512 || sampleRateHz <= 0.0 || centerFreqHz <= 0.0)
        return out;

    std::size_t N = nextPow2(iq.size());
    if (N > 16384) N = 16384;
    std::vector<std::complex<float>> spec(N, std::complex<float>(0.0f, 0.0f));
    for (std::size_t i = 0; i < iq.size() && i < N; ++i) spec[i] = iq[i];
    fftRadix2(spec);

    // |X[k]| for the positive-frequency half (bin k = k*sr/N).
    std::vector<float> mag(N / 2, 0.0f);
    double sum = 0.0;
    for (std::size_t k = 0; k < N / 2; ++k) {
        mag[k] = std::abs(spec[k]);
        sum += mag[k];
    }
    const double avg = sum / static_cast<double>(mag.size());
    if (avg <= 1e-12f) return out;

    // Expected bin of the FCCH tone (positive side only, per kalibrate).
    const double expectedBin = kFcchToneHz / sampleRateHz * static_cast<double>(N);
    // Search a +/-~10 kHz window around the expected tone (ppm range + FFT slew).
    const double binHz = sampleRateHz / static_cast<double>(N);
    const double searchBins = 12000.0 / binHz;
    const auto lo = static_cast<std::size_t>(std::max(0.0, expectedBin - searchBins));
    const auto hi = static_cast<std::size_t>(
        std::min<double>(static_cast<double>(mag.size() - 1), expectedBin + searchBins));

    std::size_t peakBin = lo;
    float peakVal = 0.0f;
    for (std::size_t k = lo; k <= hi; ++k) {
        if (mag[k] > peakVal) { peakVal = mag[k]; peakBin = k; }
    }
    if (peakBin == 0 || peakBin >= mag.size() - 1) return out;

    // Parabolic sub-bin interpolation on |X|^2 around the peak.
    const float a = mag[peakBin - 1];
    const float b = mag[peakBin];
    const float c = mag[peakBin + 1];
    const float denom = (a - 2.0f * b + c);
    const float delta = (std::fabs(denom) > 1e-9f)
                            ? (0.5f * (a - c) / denom) : 0.0f;
    const double fracBin = static_cast<double>(peakBin) + static_cast<double>(delta);
    const double toneHz = fracBin * binHz;

    // Peak-to-average ratio: a real FCCH pure tone dominates its neighborhood.
    const double ratio = static_cast<double>(peakVal) / avg;
    out.peakToAvgDb = 10.0 * std::log10(ratio + 1e-12);
    // kalibrate uses an arbitrary PM threshold; ~13 dB peak/avg is a confident
    // single-tone on noise. Below that we honestly report "not detected".
    out.detected = out.peakToAvgDb > 13.0;
    out.measuredToneHz = toneHz;
    out.ppm = (toneHz - kFcchToneHz) / centerFreqHz * 1.0e6;
    return out;
}

}  // namespace dsp
}  // namespace mbdsdr
