// SPDX-License-Identifier: MIT
// Offline test IQ source implementing ISource.
//
// *** TEST DATA -- NOT HARDWARE ***
// isConnected() always returns false. The UI MUST show this as a
// non-hardware source.
#pragma once

#include "source.h"
#include <cstdint>
#include <complex>
#include <vector>

namespace mbdsdr {
namespace dsp {

class TestSignalSource : public ISource {
public:
    explicit TestSignalSource(double sampleRateHz = 2.4e6,
                              double centerFreqHz = 98.5e6);

    bool start() override;
    void stop() override;
    std::size_t readIQ(std::vector<std::complex<float>>& out) override;

    void setCenterFreq(double freqHz) override;
    void setSampleRate(double rateHz) override;
    void setGain(double gainDb) override;

    double centerFreq() const override { return f0_; }
    double sampleRate() const override { return fs_; }
    double gain() const override { return gainDb_; }

    QString name() const override { return QStringLiteral("Test Signal"); }
    bool isConnected() const override { return false; }

private:
    double fs_;
    double f0_;
    double gainDb_ = 20.0;
    std::uint64_t counter_ = 0;

    bool   haveSpare_ = false;
    float  spare_ = 0.0f;
    float  nextGaussian();
};

} // namespace dsp
} // namespace mbdsdr
