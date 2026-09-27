// SPDX-License-Identifier: MIT
// SigMF recorder: writes cf32_le binary + JSON sidecar.
#pragma once

#include <QString>
#include <QJsonObject>
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
    void stop();
    bool isRecording() const { return recording_; }
    void writeIQ(const std::vector<std::complex<float>>& data);
    QString currentFilePath() const { return currentDataPath_; }

private:
    std::ofstream dataFile_;
    QString currentDataPath_;
    QString currentMetaPath_;
    QJsonObject meta_;
    std::atomic<bool> recording_{false};
    std::uint64_t sampleCount_ = 0;
    double sampleRate_ = 0;
    double centerFreq_ = 0;
};

} // namespace dsp
} // namespace mbdsdr
