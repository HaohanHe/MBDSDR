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

    // Hardware-specific tuning overrides. These always store their value; the
    // librtlsdr push is compiled in only under HAVE_RTLSDR (see .cpp), so an
    // off-stub build keeps them as safe no-ops that inherit ISource's defaults.
    void setDirectSampling(int mode) override;   // 0=off, 1=I-ADC, 2=Q-ADC
    void setOffsetTuning(bool on) override;
    void setRtlAgc(bool on) override;            // RTL2832 internal AGC
    void setTunerAgc(bool on) override;          // tuner AGC (auto) vs manual
    void setBiasTee(bool on) override;
    void setPpm(double ppm) override;
    void setGainStage(int stage, double gainDb) override;

    double centerFreq() const override { return f0_; }
    double sampleRate() const override { return fs_; }
    double gain() const override { return gainDb_; }

    QString name() const override;
    bool isConnected() const override;

    /// Human-readable summary of the front-end options, e.g.
    /// "DS=off AGC=off TunerAGC=manual BiasT=off PPM=0.0". Purely diagnostic.
    QString rtlOptionsSummary() const;

private:
#ifdef HAVE_RTLSDR
    rtlsdr_dev_t* dev_ = nullptr;
#endif
    double f0_ = 98.5e6;
    double fs_ = 2.4e6;
    double gainDb_ = 20.0;
    bool   running_ = false;

    // Tuning state -- always stored, then (re)applied to the device on start()
    // and pushed live whenever the device is already open.
    int    directSampling_ = 0;   // 0=off, 1=I, 2=Q
    bool   offsetTuning_   = false;
    bool   rtlAgc_         = false;
    bool   tunerAgc_       = false;   // true => tuner gain automatic
    bool   biasTee_        = false;
    double ppm_            = 0.0;
};

} // namespace dsp
} // namespace mbdsdr
