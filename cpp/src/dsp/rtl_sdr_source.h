// SPDX-License-Identifier: MIT
// RTL-SDR source implementing ISource via librtlsdr C API.
//
// Compiled only when HAVE_RTLSDR is defined (CMake detects librtlsdr).
// Without the macro, this file provides a stub whose start() always fails,
// so the engine falls back to TestSignalSource gracefully.
#pragma once

#include "source.h"
#include <complex>
#include <vector>
#include <cstddef>

#ifdef HAVE_RTLSDR
#include <rtl-sdr.h>
#endif

namespace mbdsdr {
namespace dsp {

class RtlSdrSource : public ISource {
public:
    RtlSdrSource();
    ~RtlSdrSource() override;

    bool start() override;
    void stop() override;
    std::size_t readIQ(std::vector<std::complex<float>>& out) override;

    void setCenterFreq(double freqHz) override;
    void setSampleRate(double rateHz) override;
    void setGain(double gainDb) override;

    double centerFreq() const override { return f0_; }
    double sampleRate() const override { return fs_; }
    double gain() const override { return gainDb_; }

    QString name() const override;
    bool isConnected() const override;

private:
#ifdef HAVE_RTLSDR
    rtlsdr_dev_t* dev_ = nullptr;
#endif
    double f0_ = 98.5e6;
    double fs_ = 2.4e6;
    double gainDb_ = 20.0;
    bool   running_ = false;
};

} // namespace dsp
} // namespace mbdsdr
