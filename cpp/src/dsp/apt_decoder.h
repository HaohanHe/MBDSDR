// SPDX-License-Identifier: MIT
#pragma once

#include <QImage>

#include <cstdint>
#include <vector>

#include "dsp/demod.h"   // mbdsdr::dsp::FirLowpass

namespace mbdsdr {
namespace dsp {

// ---------------------------------------------------------------------------
// NOAA APT (Automatic Picture Transmission) line-layout constants.
//
// Verified against the reference implementation noaa-apt
// (GPL-3.0-or-later, repos/noaa-apt/src/decode.rs). Clean-room reimplementation;
// no source code copied, only constants/algorithm aligned. The reference works at a fixed "pixel rate" of FINAL_RATE = 4160 Hz
// (one sample per pixel), i.e. 2080 pixels/row x 2 rows/second.
//
// One 2080-pixel row = two 1040-pixel channels (A then B). Each channel is:
//
//   [ sync frame 39 | space 47 | video 909 | telemetry wedge 45 ] = 1040
//
// The 2400 Hz subcarrier is amplitude-modulated by this video envelope.
// ---------------------------------------------------------------------------

constexpr int    APT_PX_SYNC_FRAME  = 39;   // sync word length (pixels)
constexpr int    APT_PX_SPACE       = 47;   // deep-space / minute markers
constexpr int    APT_PX_VIDEO       = 909;  // image video per channel
constexpr int    APT_PX_TELEMETRY   = 45;   // telemetry wedge per channel
constexpr int    APT_PX_PER_CHANNEL = 1040;
constexpr int    APT_PX_PER_ROW     = 2080; // two channels
constexpr double APT_PIXEL_RATE_HZ  = 4160.0;  // pixels per second
constexpr double APT_CARRIER_HZ     = 2400.0;  // APT subcarrier (AM)

// Byte offsets of the video bands inside one 2080-pixel row.
//   channel A video: offset 39 + 47 = 86, length 909
//   channel B video: offset 1040 + 86 = 1126, length 909
constexpr int APT_VIDEO_A_OFFSET = APT_PX_SYNC_FRAME + APT_PX_SPACE;          // 86
constexpr int APT_VIDEO_B_OFFSET = APT_PX_PER_CHANNEL + APT_VIDEO_A_OFFSET;   // 1126

// Assembled output image: channel A on the left, channel B on the right.
constexpr int APT_IMAGE_WIDTH = APT_PX_VIDEO * 2;  // 1818

/// Streaming, hardware-independent NOAA APT image decoder.
///
/// Feed the *already FM-demodulated* baseband audio (real float samples, e.g.
/// 48 kHz) that contains the 2400 Hz AM subcarrier. The decoder:
///   1. mixes the 2400 Hz subcarrier to baseband (I/Q arms) and low-passes to
///      recover the video envelope (AM envelope detection);
///   2. runs a sliding normalized cross-correlation against the 7-pulse sync
///      word to lock onto the start of every row;
///   3. strobes pixels at an exact 4160 pixels/sec fractional accumulator and
///      assembles the A/B video bands into a growing grayscale QImage.
///
/// State persists across feed() calls; the decoder can lose and re-acquire
/// lock on the fly (streaming, no whole-file buffering).
class AptDecoder {
public:
    /// audioSampleRateHz: sample rate of the FM-demodulated baseband audio.
    explicit AptDecoder(double audioSampleRateHz);

    /// Feed a chunk of baseband audio (real float, 2400 Hz AM subcarrier).
    void feed(const std::vector<float>& baseband);

    /// Assembled grayscale image (Format_Grayscale8), grows by one row per
    /// locked line. Width = APT_IMAGE_WIDTH (1818 = A 909 + B 909).
    const QImage& image() const { return image_; }

    /// True once the sync word has been found and the pixel clock is running.
    bool isLocked() const { return locked_; }

    /// Number of complete rows assembled so far.
    int rowCount() const { return rows_; }

    /// Most recent sliding sync correlation coefficient (0..1), for diagnostics.
    double lastSyncCorrelation() const { return lastSyncCorr_; }

    /// Latest channel-A telemetry wedge samples (APT_PX_TELEMETRY = 45 pixels),
    /// raw envelope. Optional; empty until a row with a wedge is assembled.
    const std::vector<float>& telemetryWedgeA() const { return wedgeA_; }

    /// Reset all DSP state (NCO, filters, sync, image) to a fresh decoder.
    void reset();

private:
    // ---- configuration ----
    double fs_ = 48000.0;           // audio sample rate (Hz)
    double pps_ = 4160.0 / 48000.0; // pixels advanced per audio sample

    // ---- 2400 Hz AM mix-down NCO + baseband low-pass ----
    float ncoPhase_ = 0.0f;         // radians, running
    float ncoDPhi_  = 0.0f;         // radians per audio sample
    // Streaming low-pass (properly normalized windowed-sinc; the shared
    // FirLowpass has a unity center tap and insufficient stopband for the
    // 2x-carrier at 4.8 kHz, so we design our own).
    std::vector<float> lpfTapsI_, lpfTapsQ_;
    std::vector<float> lpfDelayI_, lpfDelayQ_;
    int lpfN_ = 0;

    // ---- sync template, upsampled to the audio rate ----
    std::vector<float> templ_;      // bipolar +/-1 guard, length L_
    int L_ = 0;                     // template length in audio samples
    double templMean_ = 0.0;        // precomputed mean of templ_
    double templNorm_ = 0.0;        // precomputed sqrt(sum (templ-mean)^2)

    // ---- sliding envelope history (ring buffer) for correlation ----
    std::vector<float> hist_;
    int histTail_ = 0;              // next write slot
    int histCount_ = 0;             // samples buffered (<= L_)

    // ---- lock state machine ----
    enum class SyncState { Search, Locked };
    SyncState state_ = SyncState::Search;
    bool   locked_ = false;
    long long sampleCounter_ = 0;   // envelope samples processed
    long long lastLockSample_ = -1000000000LL;
    double lastSyncCorr_ = 0.0;

    // pending peak (tracked while the correlation lobe is above threshold)
    bool   pendingLock_ = false;
    double pendingCorr_ = 0.0;
    long long pendingSample_ = 0;

    int missCount_ = 0;             // consecutive missed row-boundary syncs
    long long nextCheckSample_ = 0; // envelope sample of the next expected sync peak

    // ---- pixel strobe (fractional accumulator, drift-free) ----
    double pixAcc_ = 0.0;           // fractional pixel accumulator
    long long globalPixel_ = 0;     // pixels strobed since lock

    // ---- row assembly ----
    int rows_ = 0;
    std::vector<uint8_t> imgData_;  // row-major, width APT_IMAGE_WIDTH
    QImage image_;
    std::vector<float> rowA_;       // current channel-A video row (909)
    std::vector<float> rowB_;       // current channel-B video row (909)
    int rowACount_ = 0;
    int rowBCount_ = 0;
    std::vector<float> wedgeA_;     // latest channel-A telemetry wedge (45)
    int wedgeCount_ = 0;
    int prevCol_ = -1;

    // ---- helpers ----
    void buildTemplate();
    void onEnvelope(float env);
    void lockAt(long long peakSample);
    void emitPixel(long long globalPixel, float env);
    void finalizeRow();
};

} // namespace dsp
} // namespace mbdsdr
