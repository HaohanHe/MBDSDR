// SPDX-License-Identifier: MIT
// SigMF recorder: writes cf32_le binary + JSON sidecar.
#pragma once

#include <QString>
#include <QJsonObject>
#include <QJsonArray>
#include <fstream>
#include <complex>
#include <vector>
#include <atomic>

namespace mbdsdr {
namespace dsp {

class Recorder {
public:
    Recorder();
    ~Recorder();

    bool start(const QString& dir, double sampleRate, double centerFreq,
               double gainDb, const QString& hardware);
    /// Start with an explicit output base path (no extension). The engine
    /// expands the user filename template into `basePath`; the .sigmf-data /
    /// .sigmf-meta sidecars are derived from it.
    bool startWithBase(const QString& basePath, double sampleRate, double centerFreq,
                       double gainDb, const QString& hardware);
    void stop();
    bool isRecording() const { return recording_; }
    void writeIQ(const std::vector<std::complex<float>>& data);
    QString currentFilePath() const { return currentDataPath_; }

    // ---- auto-segmentation (continuous capture) ---------------------------
    // After `seconds` of captured samples the recorder finalises the current
    // SigMF capture and opens a fresh collision-avoided file. 0 disables
    // segmentation (single file). The limit comes from tokens/settings.
    void setMaxSegmentSeconds(double seconds);
    double maxSegmentSeconds() const { return maxSegmentSeconds_; }
    void setSegmentContext(const QString& dir, double freqHz, double gainDb,
                           const QString& hardware);
    int segmentCount() const { return segmentIndex_; }
    // True iff a new segment file was opened since the last call (consumed).
    bool takeSegmentRotated();

private:
    void finalizeSegmentLocked();           // write meta + close current file
    bool openDataFileLocked();              // open data file + (re)build meta
    bool openNewSegmentLocked();             // fresh stamp + collision avoidance

    std::ofstream dataFile_;
    QString currentDataPath_;
    QString currentMetaPath_;
    QJsonObject meta_;
    std::atomic<bool> recording_{false};
    std::uint64_t sampleCount_ = 0;
    double sampleRate_ = 0;
    double centerFreq_ = 0;

    // Segmentation context (sample-clock cap).
    double maxSegmentSeconds_ = 0.0;        // 0 = unlimited
    std::uint64_t maxSegmentSamples_ = 0;
    QString segDir_;
    double segFreq_ = 0.0;
    double segGainDb_ = 0.0;
    QString segHardware_;
    int segmentIndex_ = 1;
    std::atomic<bool> segmentRotated_{false};
};

} // namespace dsp
} // namespace mbdsdr
