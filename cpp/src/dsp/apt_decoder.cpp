// SPDX-License-Identifier: MIT
//
// NOAA APT streaming image decoder (DSP core only; no UI).
//
// Pipeline (all in the audio baseband domain, sample rate fs_):
//   1) The 2400 Hz APT subcarrier is AM-modulated by the video envelope.
//      Mix to baseband with a running NCO (I/Q arms), low-pass both arms, and
//      take the magnitude -> video envelope (phase-invariant AM envelope
//      detection; equivalent to noaa-apt dsp::demodulate but coherent I/Q).
//   2) Sliding normalized cross-correlation of the envelope against the
//      7-pulse sync word (template aligned with noaa-apt generate_sync_frame)
//      locates the start of every row.
//   3) Once locked, a fractional accumulator strobes pixels at exactly
//      4160 px/s (pixel period = fs_/4160 s), drift-free, and the A/B video
//      bands are assembled into a growing grayscale QImage.
//
// Clean-room reimplementation from the APT standard + the reference
// implementation noaa-apt (GPL-3.0-or-later, repos/noaa-apt/src/decode.rs);
// no source code copied, only constants/algorithm aligned.
// NOT HARDWARE: all inputs are synthetic.

#include "dsp/apt_decoder.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace mbdsdr {
namespace dsp {

namespace {

// Sync correlation thresholds (normalized Pearson, -1..1).
constexpr double kAcquireCorr = 0.45;  // rising-edge arm threshold
constexpr double kAcquireFall  = 0.27;  // falling-edge commits the pending peak
constexpr double kLoseCorr     = 0.18;  // below this at a row boundary = miss
constexpr int    kMaxMisses    = 3;     // consecutive misses -> relock
// Reject near-flat windows (std of the envelope window). A real 7-pulse sync
// window has std ~0.2 (envelope spans ~0.1..0.5); a flat sync-less tone ~0.
constexpr double kMinWindowStd = 0.05;

// Final video low-pass: Nyquist of the 4160 px/s pixel clock is 2080 Hz.
constexpr double kVideoLpHz = 2000.0;
constexpr int    kLpTaps    = 127;

} // namespace

AptDecoder::AptDecoder(double audioSampleRateHz)
    : fs_(audioSampleRateHz),
      pps_(APT_PIXEL_RATE_HZ / audioSampleRateHz) {
    ncoDPhi_ = (float)(2.0 * M_PI * APT_CARRIER_HZ / fs_);

    // Design a properly normalized windowed-sinc low-pass. The shared
    // FirLowpass hardcodes a unity center tap (no 2*fc scaling), which makes
    // it near-allpass and leaves the 2x-carrier (4.8 kHz) ripple in the
    // envelope. We need ~40 dB stopband at 4.8 kHz, so use a long Hann window.
    const double fc = kVideoLpHz / fs_;          // cycles/sample
    lpfN_ = kLpTaps;
    const double M = lpfN_ - 1;
    lpfTapsI_.assign(lpfN_, 0.0f);
    double sum = 0.0;
    for (int i = 0; i < lpfN_; ++i) {
        const double x = i - M / 2.0;
        double sinc = (x == 0.0) ? 2.0 * fc        // limit sin(2πfc x)/(πx) = 2fc
                                 : std::sin(2.0 * M_PI * fc * x) / (M_PI * x);
        double hann = 0.5 * (1.0 - std::cos(2.0 * M_PI * i / M));
        lpfTapsI_[i] = (float)(sinc * hann);
        sum += lpfTapsI_[i];
    }
    for (int i = 0; i < lpfN_; ++i) lpfTapsI_[i] /= (float)sum;  // unity DC gain
    lpfTapsQ_ = lpfTapsI_;   // identical filter on both arms

    rowA_.assign(APT_PX_VIDEO, 0.0f);
    rowB_.assign(APT_PX_VIDEO, 0.0f);
    wedgeA_.assign(APT_PX_TELEMETRY, 0.0f);
    buildTemplate();
    reset();
}

// Streaming FIR: push one sample, return the filtered sample.
static inline float firRun(std::vector<float>& delay, const std::vector<float>& taps,
                          float x) {
    std::copy_backward(delay.begin(), delay.end() - 1, delay.end());
    delay[0] = x;
    float acc = 0.0f;
    for (std::size_t i = 0; i < taps.size(); ++i) acc += taps[i] * delay[i];
    return acc;
}

// Build the bipolar sync guard at the audio rate.
//
// At 1 sample/pixel the guard is 38 samples (NOT 39): two leading low samples,
// then the cycle [-1,-1,+1,+1] repeated 7 times (7 high pulses, 2 pixels wide,
// 4-pixel period), then 8 trailing low samples. This matches
// noaa-apt generate_sync_frame exactly.
void AptDecoder::buildTemplate() {
    std::vector<int> s(38, -1);
    for (int c = 0; c < 7; ++c) {
        const int base = 2 + c * 4;     // cycle start
        s[base + 2] = +1;               // high pulse (2 pixels)
        s[base + 3] = +1;
    }

    const double spp = fs_ / APT_PIXEL_RATE_HZ;   // audio samples per pixel
    L_ = (int)std::lround(38.0 * spp);
    if (L_ < 38) L_ = 38;
    templ_.assign(L_, 0.0f);
    for (int m = 0; m < L_; ++m) {
        int p = (int)std::floor((double)m / spp);
        if (p < 0) p = 0;
        if (p > 37) p = 37;
        templ_[m] = (float)s[p];
    }

    // Precompute zero-mean template statistics for normalized correlation.
    double sum = 0.0;
    for (float v : templ_) sum += v;
    templMean_ = sum / L_;
    double var = 0.0;
    for (float v : templ_) var += (v - templMean_) * (v - templMean_);
    templNorm_ = std::sqrt(var);

    hist_.assign(L_, 0.0f);
}

void AptDecoder::reset() {
    ncoPhase_ = 0.0f;
    lpfDelayI_.assign(lpfN_, 0.0f);
    lpfDelayQ_.assign(lpfN_, 0.0f);

    histTail_ = 0;
    histCount_ = 0;

    state_ = SyncState::Search;
    locked_ = false;
    sampleCounter_ = 0;
    lastLockSample_ = -1000000000LL;
    lastSyncCorr_ = 0.0;
    pendingLock_ = false;
    pendingCorr_ = 0.0;
    pendingSample_ = 0;
    missCount_ = 0;
    nextCheckSample_ = 0;

    pixAcc_ = 0.0;
    globalPixel_ = 0;

    rows_ = 0;
    imgData_.clear();
    image_ = QImage();
    rowACount_ = 0;
    rowBCount_ = 0;
    wedgeCount_ = 0;
    prevCol_ = -1;
}

void AptDecoder::feed(const std::vector<float>& baseband) {
    if (baseband.empty()) return;

    for (float sample : baseband) {
        // 1) Mix the 2400 Hz subcarrier to baseband (I/Q arms, phase-invariant).
        const float c = std::cos(ncoPhase_);
        const float s = std::sin(ncoPhase_);
        const float mixI = sample * c;
        const float mixQ = -sample * s;
        ncoPhase_ += ncoDPhi_;
        while (ncoPhase_ > (float)M_PI) ncoPhase_ -= 2.0f * (float)M_PI;
        while (ncoPhase_ < -(float)M_PI) ncoPhase_ += 2.0f * (float)M_PI;

        // 2) Low-pass each arm to the video band (rejects the 2x-carrier at 4.8k).
        const float ilp = firRun(lpfDelayI_, lpfTapsI_, mixI);
        const float qlp = firRun(lpfDelayQ_, lpfTapsQ_, mixQ);

        // 3) Envelope = magnitude, then run the streaming sync/strobe state machine.
        const float env = std::sqrt(std::max(0.0f, ilp * ilp + qlp * qlp));
        onEnvelope(env);
    }
}

void AptDecoder::onEnvelope(float env) {
    // Push into the ring buffer (oldest at histTail_ before overwrite).
    hist_[histTail_] = env;
    histTail_ = (histTail_ + 1) % L_;
    if (histCount_ < L_) ++histCount_;
    ++sampleCounter_;
    if (histCount_ < L_) return;   // need a full template window first

    // --- Normalized (Pearson) cross-correlation of window vs template ---
    double sumW = 0.0, sumW2 = 0.0, sumTW = 0.0;
    for (int i = 0; i < L_; ++i) {
        const float w = hist_[(histTail_ + i) % L_];   // i=0 = oldest sample
        sumW  += w;
        sumW2 += w * w;
        sumTW += (double)templ_[i] * w;
    }
    const double meanW = sumW / L_;
    const double cov   = sumTW - L_ * templMean_ * meanW;
    const double varW  = sumW2 - L_ * meanW * meanW;
    // Variance gate: a near-flat window (e.g. the flat 0.5 sync-less tone) has
    // ~zero variance, making Pearson an undefined 0/0 that would spuriously
    // spike to ±1. A real 7-pulse sync window has substantial modulation
    // (std ~0.2 of full scale); reject flat windows outright.
    const double stdW = std::sqrt(std::max(0.0, varW));
    double corr = 0.0;
    if (stdW > kMinWindowStd)
        corr = cov / (templNorm_ * stdW + 1e-9);
    if (corr > 1.0) corr = 1.0;
    if (corr < -1.0) corr = -1.0;
    lastSyncCorr_ = corr;

    if (state_ == SyncState::Search) {
        // Arm on the rising edge, track the peak, commit on the falling edge.
        if (corr > kAcquireCorr) {
            if (!pendingLock_) {
                pendingLock_ = true;
                pendingCorr_ = corr;
                pendingSample_ = sampleCounter_;
            } else if (corr > pendingCorr_) {
                pendingCorr_ = corr;
                pendingSample_ = sampleCounter_;
            }
        } else if (pendingLock_ && corr < kAcquireFall) {
            pendingLock_ = false;
            const double samplesPerRow = fs_ / APT_PIXEL_RATE_HZ * APT_PX_PER_ROW;
            if ((double)(pendingSample_ - lastLockSample_) > samplesPerRow * 0.8)
                lockAt(pendingSample_);
        }
        return;
    }

    // LOCKED: strobe pixels at the exact 4160 px/s fractional accumulator.
    pixAcc_ += pps_;
    while (pixAcc_ >= 1.0) {
        pixAcc_ -= 1.0;
        ++globalPixel_;
        emitPixel(globalPixel_, env);
    }

    // Row-boundary sync health check: the correlation peak for row k is
    // observed one template-length after the row start, i.e. exactly one row
    // period after the peak that locked us. Count misses and re-acquire if
    // several expected peaks are weak.
    if (sampleCounter_ >= nextCheckSample_) {
        if (lastSyncCorr_ < kLoseCorr) ++missCount_;
        else missCount_ = 0;
        if (missCount_ >= kMaxMisses) {
            state_ = SyncState::Search;
            locked_ = false;
            pendingLock_ = false;
            lastLockSample_ = sampleCounter_;   // don't immediately relock
        }
        const double samplesPerRow = fs_ / APT_PIXEL_RATE_HZ * APT_PX_PER_ROW;
        nextCheckSample_ += (long long)std::lround(samplesPerRow);
    }
}

void AptDecoder::lockAt(long long peakSample) {
    // The peak was observed when the newest window sample was peakSample; the
    // template[0] (sync pixel 0) aligns L_-1 samples earlier in the ENVELOPE domain.
    long long lineStart = peakSample - (L_ - 1);
    // The envelope itself is delayed by the linear-phase LPF group delay
    // D=(lpfN_-1)/2 samples; because the strobe reads the delayed envelope, we
    // must advance the strobe origin by D so that pixel k samples the video
    // that was actually emitted at input time k*spp (otherwise the whole row
    // sits ~D/spp pixels early, measured ~5.5 px at 48 kHz).
    lineStart += (lpfN_ - 1) / 2;
    // Number of pixels already elapsed between lineStart and now.
    const double phasePixels = (double)(sampleCounter_ - lineStart) * pps_;
    globalPixel_ = (long long)std::floor(phasePixels);
    pixAcc_ = phasePixels - (double)globalPixel_;

    prevCol_ = (int)(globalPixel_ % APT_PX_PER_ROW);
    rowACount_ = 0;
    rowBCount_ = 0;
    wedgeCount_ = 0;
    missCount_ = 0;

    state_ = SyncState::Locked;
    locked_ = true;
    lastLockSample_ = peakSample;
    pendingLock_ = false;

    // The next expected sync peak is one row period after the current peak.
    const double samplesPerRow = fs_ / APT_PIXEL_RATE_HZ * APT_PX_PER_ROW;
    nextCheckSample_ = peakSample + (long long)std::lround(samplesPerRow);
}

void AptDecoder::emitPixel(long long globalPixel, float env) {
    const int col = (int)(globalPixel % APT_PX_PER_ROW);

    // Row wrap: a completed row ends when col returns to 0 after video B.
    if (col == 0 && prevCol_ > 1000)
        finalizeRow();

    if (col == APT_VIDEO_A_OFFSET) rowACount_ = 0;
    if (col >= APT_VIDEO_A_OFFSET && col < APT_VIDEO_A_OFFSET + APT_PX_VIDEO)
        rowA_[rowACount_++] = env;

    if (col == APT_VIDEO_A_OFFSET + APT_PX_VIDEO) wedgeCount_ = 0;  // telem A start
    if (col >= APT_VIDEO_A_OFFSET + APT_PX_VIDEO &&
        col < APT_VIDEO_A_OFFSET + APT_PX_VIDEO + APT_PX_TELEMETRY)
        wedgeA_[wedgeCount_++] = env;

    if (col == APT_VIDEO_B_OFFSET) rowBCount_ = 0;
    if (col >= APT_VIDEO_B_OFFSET && col < APT_VIDEO_B_OFFSET + APT_PX_VIDEO)
        rowB_[rowBCount_++] = env;

    prevCol_ = col;
}

void AptDecoder::finalizeRow() {
    if (rowACount_ != APT_PX_VIDEO || rowBCount_ != APT_PX_VIDEO) return;

    imgData_.reserve(imgData_.size() + APT_IMAGE_WIDTH);
    for (int i = 0; i < APT_PX_VIDEO; ++i) {
        float v = rowA_[i] * 255.0f;
        if (v < 0.0f) v = 0.0f;
        if (v > 255.0f) v = 255.0f;
        imgData_.push_back((uint8_t)v);
    }
    for (int i = 0; i < APT_PX_VIDEO; ++i) {
        float v = rowB_[i] * 255.0f;
        if (v < 0.0f) v = 0.0f;
        if (v > 255.0f) v = 255.0f;
        imgData_.push_back((uint8_t)v);
    }
    ++rows_;

    // Rebuild an owned QImage over the (possibly reallocated) pixel buffer.
    image_ = QImage(imgData_.data(), APT_IMAGE_WIDTH, rows_, APT_IMAGE_WIDTH,
                    QImage::Format_Grayscale8)
                 .copy();
}

} // namespace dsp
} // namespace mbdsdr
