// SPDX-License-Identifier: MIT
// Gated recorder: segment audio by squelch gate, pre-roll, envelope fade.
// Outputs WAV files (48kHz mono int16) per talk-spurt.
#pragma once

#include <QString>
#include <vector>
#include <deque>
#include <atomic>

namespace mbdsdr {
namespace dsp {

class GatedRecorder {
public:
    explicit GatedRecorder(double sampleRate = 48000.0);
    void setOutputDir(const QString& dir) { outDir_ = dir; }
    void setEnabled(bool e) { enabled_ = e; }
    bool enabled() const { return enabled_; }
    bool isRecording() const { return state_ == State::REC; }

    // Filename context: the selected VFO's mode and absolute frequency. The
    // engine calls this every loop so saved talk-spurts are labelled with the
    // channel that actually produced them (instead of hard-coded NFM/98.5M).
    void setContext(const QString& mode, double freqHz) {
        currentMode_ = mode;
        currentFreq_ = freqHz;
    }

    /// Feed one audio block + gate state. Returns list of saved file paths.
    std::vector<QString> feed(const std::vector<float>& audio, bool gate);

    /// Finish current segment if any.
    std::vector<QString> flush();

private:
    enum class State { IDLE, PRE_ROLL, REC };
    State state_ = State::IDLE;
    bool enabled_ = true;
    double sr_;

    // Pre-roll ring buffer (150ms)
    std::deque<float> preRoll_;
    static constexpr double kPreRollMs = 150.0;

    // Segment accumulation
    std::vector<float> segmentBuf_;
    double hangLeftMs_ = 0;
    double segmentLenMs_ = 0;
    static constexpr double kHangMs = 1500.0;
    static constexpr double kMaxSegMs = 300000.0;
    static constexpr double kMinSegMs = 250.0;

    // Envelope shaper
    float env_ = 0;
    float attackAlpha_, releaseAlpha_;

    QString outDir_ = "recordings";
    QString currentMode_ = "NFM";
    double currentFreq_ = 98.5e6;
    QString lastSavedPath_;

    void startSegment();
    void endSegment();
    QString writeWav(const std::vector<float>& samples, const QString& mode, double freq);
};

} // namespace dsp
} // namespace mbdsdr
