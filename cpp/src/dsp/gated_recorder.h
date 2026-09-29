// SPDX-License-Identifier: MIT
// Gated recorder: segment audio by a signal gate, with pre-roll, envelope
// shaping and an end-delay (hang). Outputs one WAV file (48 kHz mono int16)
// plus a JSON sidecar per talk-spurt / signal burst.
//
// Used by both the squelch-gated segmented recording and the unattended
// signal-triggered watch recording -- it is the single segment writer, so the
// two modes never produce parallel/conflicting files.
#pragma once

#include <QString>
#include <vector>
#include <deque>
#include <atomic>

namespace mbdsdr {
namespace dsp {

// Per-segment labelling context, refreshed by the engine every loop so the
// saved file is stamped with the channel that actually produced it.
struct SegmentContext {
    QString mode = "NFM";
    double channelFreqHz = 98.5e6;   // selected VFO absolute frequency
    double centerFreqHz = 98.5e6;    // source tuner / capture centre
    double gainDb = 0.0;
    float triggerThresholdDb = -50.0f;
    QString hardware = "Test Signal";
    bool hardwareConnected = false;  // false -> sidecar is marked NOT HARDWARE
};

class GatedRecorder {
public:
    explicit GatedRecorder(double sampleRate = 48000.0);
    void setOutputDir(const QString& dir) { outDir_ = dir; }
    QString outputDir() const { return outDir_; }
    void setEnabled(bool e) { enabled_ = e; }
    bool enabled() const { return enabled_; }
    bool isRecording() const { return state_ == State::REC; }

    // Full context (watch + squelch-gated recording).
    void setContext(const SegmentContext& ctx) { ctx_ = ctx; }
    // Legacy context: mode + the selected VFO's absolute frequency.
    void setContext(const QString& mode, double freqHz) {
        ctx_.mode = mode;
        ctx_.channelFreqHz = freqHz;
        ctx_.centerFreqHz = freqHz;
    }

    // Pre-roll captured before the trigger opens (signal onset is not clipped).
    void   setPreRollMs(double ms);
    double preRollMs() const { return preRollMs_; }
    // End-delay: once the gate closes, keep recording this long before the
    // segment is finalised, bridging short gaps inside one call.
    void   setHangMs(double ms);
    double hangMs() const { return hangMs_; }

    // Number of segments successfully written since construction.
    int segmentCount() const { return segmentCount_; }

    /// Feed one audio block + gate state. Returns list of saved WAV paths.
    std::vector<QString> feed(const std::vector<float>& audio, bool gate);

    /// Finish the current segment (if any) and return its saved path.
    std::vector<QString> flush();

private:
    enum class State { IDLE, PRE_ROLL, REC };
    State state_ = State::IDLE;
    bool enabled_ = true;
    double sr_;

    // Pre-roll ring buffer.
    std::deque<float> preRoll_;
    double preRollMs_;
    static constexpr double kPreRollMinMs = 50.0;
    static constexpr double kPreRollMaxMs = 2000.0;

    // Segment accumulation
    std::vector<float> segmentBuf_;
    double hangLeftMs_ = 0;
    double segmentLenMs_ = 0;
    double hangMs_;
    static constexpr double kHangMinMs = 100.0;
    static constexpr double kHangMaxMs = 30000.0;
    static constexpr double kMaxSegMs = 300000.0;
    static constexpr double kMinSegMs = 250.0;

    // Envelope shaper
    float env_ = 0;
    float attackAlpha_, releaseAlpha_;

    SegmentContext ctx_;
    QString outDir_ = "record";
    QString lastSavedPath_;
    int segmentCount_ = 0;

    // UTC time at which the trigger opened for the current segment.
    qint64 triggerEpochMs_ = 0;

    void startSegment();
    bool endSegment();   // true iff a file was written
    QString writeWav(const std::vector<float>& samples, const QString& basePath);
    void writeSidecar(const QString& wavPath, const std::vector<float>& samples,
                      qint64 startEpochMs, qint64 endEpochMs);
    QString uniqueBasePath() const;
};

} // namespace dsp
} // namespace mbdsdr
