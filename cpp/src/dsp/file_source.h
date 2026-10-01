// SPDX-License-Identifier: MIT
// Offline file replay source implementing ISource. Streams a captured file in
// fixed ~decisecond chunks the SAME way the live sources do -- the whole file
// is NEVER loaded into memory (the historical FileIQ 2.3 GB-for-1-min bug).
// Supports three honest formats:
//   - SigMF (.sigmf-meta/.sigmf-data, cf32_le complex float)
//   - 16-bit mono PCM WAV (demod-audio captures; fed as real-valued complex)
//   - raw cf32_le complex IQ (sample rate supplied by the user; labelled
//     "原始·用户指定参数")
// Pause / seek / progress are real (the file offset moves), never a fake loop.
#pragma once

#include "source.h"
#include <QString>
#include <cstdint>
#include <fstream>
#include <complex>
#include <vector>
#include <atomic>

namespace mbdsdr {
namespace dsp {

class FileSource : public ISource {
public:
    // Back-compat: open a SigMF capture by base path (no extension).
    explicit FileSource(const QString& basePath);
    ~FileSource() override;

    // ---- explicit openers (call one before start()) ----------------------
    // SigMF: base path without extension; reads .sigmf-meta + .sigmf-data.
    bool openSigmf(const QString& basePath);
    // 16-bit mono PCM WAV. sampleRate/centerFreq/mode come from the header +
    // sidecar (the UI passes them in after RecordingLibrary::probeWav +
    // readSidecar). Returns false on a bad/non-PCM header.
    bool openWav(const QString& wavPath, double centerFreqHint, const QString& mode);
    // Raw cf32_le complex IQ. sample rate is user-supplied (honest label).
    bool openRaw(const QString& path, double sampleRateHz);

    bool start() override;
    void stop() override;
    std::size_t readIQ(std::vector<std::complex<float>>& out) override;

    void setCenterFreq(double) override {}
    void setSampleRate(double) override {}
    void setGain(double) override {}

    double centerFreq() const override { return centerFreq_; }
    double sampleRate() const override { return sampleRate_; }
    double gain() const override { return 0; }

    QString name() const override;
    // Offline file is NOT hardware: must stay false so the engine does not
    // treat EOF/pause (zero reads) as a device drop.
    bool isConnected() const override { return false; }

    // ---- transport control (real file offset, not a fake loop) -----------
    void setPaused(bool p) { paused_.store(p); }
    bool isPaused() const { return paused_.load(); }
    // Seek to a fraction 0..1 of the capture.
    void seekFraction(double f01);
    double progressFraction() const;          // 0..1
    qint64 totalSamples() const { return totalSamples_; }
    qint64 playedSamples() const { return playedSamples_; }
    QString errorString() const { return error_; }
    QString mode() const { return mode_; }
    bool isOpen() const { return opened_; }

private:
    enum class Fmt { Sigmf, Wav, Raw };

    bool openDataFile();   // opens dataFile_ and validates sizes
    long dataOffsetForSample(qint64 sample) const;

    QString basePath_;
    QString dataPath_;
    Fmt     fmt_ = Fmt::Sigmf;
    std::ifstream dataFile_;
    QString error_;
    double sampleRate_ = 0;
    double centerFreq_ = 0;
    QString mode_;
    bool opened_ = false;
    qint64 totalSamples_ = 0;    // complex frames (WAV: mono frames)
    qint64 playedSamples_ = 0;
    long dataStartOffset_ = 0;         // bytes from file start to first sample
    std::atomic<bool> paused_{false};
};

} // namespace dsp
} // namespace mbdsdr
