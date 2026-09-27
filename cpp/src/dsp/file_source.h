// SPDX-License-Identifier: MIT
// SigMF file replay source implementing ISource.
#pragma once

#include "source.h"
#include <fstream>
#include <complex>
#include <vector>

namespace mbdsdr {
namespace dsp {

class FileSource : public ISource {
public:
    explicit FileSource(const QString& basePath);  // e.g. "/path/recording" (no extension)
    ~FileSource() override;

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
    bool isConnected() const override { return opened_; }

private:
    QString basePath_;
    std::ifstream dataFile_;
    double sampleRate_ = 0;
    double centerFreq_ = 0;
    bool opened_ = false;
    std::uint64_t totalSamples_ = 0;
    std::uint64_t readSamples_ = 0;
};

} // namespace dsp
} // namespace mbdsdr
