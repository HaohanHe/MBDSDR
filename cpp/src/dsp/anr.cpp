// SPDX-License-Identifier: MIT
#include "anr.h"

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstring>

namespace mbdsdr {
namespace dsp {

namespace {

// Self-contained iterative radix-2 Cooley-Tukey FFT (n must be power of two).
// Duplicated locally so this module has ZERO link-time dependencies beyond the
// C++ standard library — it must compile with a bare `g++ anr.cpp`.
void fftInPlace(std::vector<std::complex<float>>& a) {
    const std::size_t n = a.size();
    for (std::size_t i = 1, j = 0; i < n; ++i) {
        std::size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(a[i], a[j]);
    }
    for (std::size_t len = 2; len <= n; len <<= 1) {
        const float ang = -2.0f * static_cast<float>(M_PI) / static_cast<float>(len);
        const std::complex<float> wlen(std::cos(ang), std::sin(ang));
        for (std::size_t i = 0; i < n; i += len) {
            std::complex<float> w(1.0f, 0.0f);
            for (std::size_t j = 0; j < len / 2; ++j) {
                const std::complex<float> u = a[i + j];
                const std::complex<float> v = a[i + j + len / 2] * w;
                a[i + j]         = u + v;
                a[i + j + len/2] = u - v;
                w *= wlen;
            }
        }
    }
}

void ifftInPlace(std::vector<std::complex<float>>& a) {
    for (auto& x : a) x = std::conj(x);
    fftInPlace(a);
    const float inv = 1.0f / static_cast<float>(a.size());
    for (auto& x : a) x = std::conj(x) * inv;
}

} // namespace

AudioNoiseReduction::AudioNoiseReduction() {
    configure(48000.0, 512);
}

void AudioNoiseReduction::configure(double sampleRateHz, int fftSize) {
    fs_   = sampleRateHz > 0.0 ? sampleRateHz : 48000.0;
    n_    = (fftSize >= 16) ? fftSize : 512;
    // Round up to a power of two.
    int p = 16;
    while (p < n_) p <<= 1;
    n_   = p;
    hop_ = n_ / 2;

    window_.assign(n_, 0.0f);
    psSmoothed_.assign(n_, 1e-10f);
    psVar_.assign(n_, 1e-6f); // start with high CV -> first frames track noise
    pn_.assign(n_, 1e-10f);
    xi_.assign(n_, 0.0f);
    gPrev_.assign(n_, 1.0f);
    outAcc_.assign(n_, 0.0f);
    outNorm_.assign(n_, 0.0f);
    inQueue_.clear();
    outQueue_.clear();
    noiseFloorDb_ = -120.0f;
    avgGainDb_    = 0.0f;
    buildWindow();
}

void AudioNoiseReduction::buildWindow() {
    // Periodic Hann window: w[k] = 0.5 - 0.5*cos(2*pi*k/N). With 50% overlap
    // the weighted OLA below normalizes w^2 explicitly, so reconstruction is
    // exact regardless of the window choice.
    for (int k = 0; k < n_; ++k) {
        window_[k] = 0.5f - 0.5f * std::cos(2.0f * static_cast<float>(M_PI) * k / n_);
    }
}

void AudioNoiseReduction::setEnabled(bool on) {
    if (on && !enabled_) reset(); // clean state when (re)armed
    enabled_ = on;
}

void AudioNoiseReduction::setStrength(float s) {
    strength_ = std::min(1.0f, std::max(0.0f, s));
}

void AudioNoiseReduction::reset() {
    configure(fs_, n_);
    enabled_ = false;
}

void AudioNoiseReduction::processOneFrame() {
    // Analysis FFT of the first N queued samples.
    thread_local std::vector<std::complex<float>> fftBuf;
    if (static_cast<int>(fftBuf.size()) != n_) fftBuf.assign(n_, 0.0f);
    for (int k = 0; k < n_; ++k) {
        fftBuf[k] = std::complex<float>(inQueue_[k] * window_[k], 0.0f);
    }
    fftInPlace(fftBuf);

    // Per-band VAD via short-term CV (coefficient of variation) of the
    // smoothed power spectrum: white-noise bins fluctuate wildly frame to
    // frame (periodogram variance == mean^2), whereas a steady tone bin is
    // rock-stable. A steady bin whose level exceeds its noise estimate is
    // treated as SIGNAL -> freeze the noise estimate (slow downward drift
    // only); fluctuating bins are NOISE -> track the floor fast.
    // This discriminates a sustained tone from steady noise, which a global
    // frame-energy VAD cannot do.
    // Spectral floor: strength 0 -> 1.0 (transparent), strength 1 -> 0.5^4.
    const float floorAmp = std::pow(0.5f, strength_ * 4.0f);

    double meanPn = 0.0;
    double meanG  = 0.0;

    for (int k = 0; k < n_; ++k) {
        const float re = fftBuf[k].real();
        const float im = fftBuf[k].imag();
        const float p  = re * re + im * im;

        // Temporal smoothing of the power spectrum + its variance.
        // psVar uses a fast-release EMA: on a noise bin it stays ~= mean^2
        // (CV ~ 1, as expected for an exponential periodogram), but once a
        // steady tone enters the bin the variance collapses within ~15 frames,
        // which is what the VAD below keys on.
        float& psm = psSmoothed_[k];
        float& psv = psVar_[k];
        const float err = p - psm;
        psm = 0.90f * psm + 0.10f * p;
        psv = 0.50f * psv + 0.50f * err * err;
        const float psMean = std::max(psm, 1e-12f);
        const float cv    = std::sqrt(std::max(psv, 0.0f)) / psMean;

        float& pn = pn_[k];
        if (pn < 1e-9f) pn = psMean; // first-time anchoring
        // Signal bin = very steady spectrum AND above the noise floor.
        // Noise periodogram bins have CV ~= 1 by construction and therefore
        // never match; a tone bin does once its variance estimate decays.
        const bool signalBin = (cv < 0.35f) && (psMean > pn * 1.5f);
        if (signalBin) {
            pn *= 0.9995f; // freeze: only a very slow downward drift
        } else {
            // Slow noise-floor tracker (tau ~ 0.5 s): it must not chase the
            // fast EMA psMean, otherwise a newly started tone drags the floor
            // up before its low-CV signature is detected.
            pn = 0.995f * pn + 0.005f * psMean;
        }
        const float pnEff = std::max(pn, 1e-12f);

        // Decision-directed a-priori SNR.
        const float snrPost = std::max(psMean / pnEff - 1.0f, 0.0f);
        xi_[k] = 0.90f * xi_[k] + 0.10f * snrPost;

        // Wiener gain, clipped to the spectral floor.
        float g = xi_[k] / (xi_[k] + 1.0f);
        g = std::max(g, floorAmp);
        g = std::min(g, 1.0f);

        // Frequency smoothing of the gain -> kills musical noise.
        // (bin k and its neighbours; the spectrum is conjugate-symmetric.)
        const int km = (k - 1 + n_) % n_;
        const int kp = (k + 1) % n_;
        float gAvg = 0.5f * g + 0.25f * gPrev_[km] + 0.25f * gPrev_[kp];
        gPrev_[k] = gAvg;

        // Zero-phase magnitude scaling.
        fftBuf[k] *= gAvg;

        meanPn += pnEff;
        meanG  += gAvg;
    }

    ifftInPlace(fftBuf);

    // Weighted overlap-add: accumulate windowed synthesis frames, then emit the
    // hop-length block after dividing by the accumulated window energy.
    for (int k = 0; k < n_; ++k) {
        const float w = window_[k];
        outAcc_[k]  += fftBuf[k].real() * w;
        outNorm_[k] += w * w;
    }
    for (int k = 0; k < hop_; ++k) {
        const float v = outNorm_[k] > 1e-9f ? outAcc_[k] / outNorm_[k] : 0.0f;
        outQueue_.push_back(v);
    }
    // Shift accumulators left by hop.
    std::memmove(outAcc_.data(),  outAcc_.data()  + hop_, sizeof(float) * (n_ - hop_));
    std::memmove(outNorm_.data(), outNorm_.data() + hop_, sizeof(float) * (n_ - hop_));
    std::fill_n(outAcc_.data()  + (n_ - hop_), hop_, 0.0f);
    std::fill_n(outNorm_.data() + (n_ - hop_), hop_, 0.0f);

    // Consume the hop of input that this frame advanced by.
    inQueue_.erase(inQueue_.begin(), inQueue_.begin() + hop_);

    noiseFloorDb_ = static_cast<float>(10.0 * std::log10(meanPn / n_ + 1e-12));
    avgGainDb_    = static_cast<float>(20.0 * std::log10(meanG  / n_ + 1e-12));
}

std::vector<float> AudioNoiseReduction::process(const std::vector<float>& in) {
    // Disabled: exact identity passthrough.
    if (!enabled_) return in;

    inQueue_.insert(inQueue_.end(), in.begin(), in.end());
    while (static_cast<int>(inQueue_.size()) >= n_) {
        processOneFrame();
    }

    // Emit exactly in.size() samples. The pipeline has a fixed latency of
    // (N - hop) samples; pad zeros at the front of the very first calls so the
    // returned length always matches the request.
    const std::size_t want = in.size();
    scratchOut_.clear();
    if (outQueue_.size() < want) {
        scratchOut_.assign(want - outQueue_.size(), 0.0f);
        scratchOut_.insert(scratchOut_.end(), outQueue_.begin(), outQueue_.end());
        outQueue_.clear();
    } else {
        scratchOut_.assign(outQueue_.begin(), outQueue_.begin() + want);
        outQueue_.erase(outQueue_.begin(), outQueue_.begin() + want);
    }
    return scratchOut_;
}

} // namespace dsp
} // namespace mbdsdr
