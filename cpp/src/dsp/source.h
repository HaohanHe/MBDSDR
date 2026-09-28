// SPDX-License-Identifier: MIT
// Unified SDR source interface. Every IQ producer (test signal, RTL-SDR,
// SoapySDR, file replay, ...) implements this. The engine holds a
// std::unique_ptr<ISource> and never cares which concrete backend it is.
#pragma once

#include <QString>
#include <complex>
#include <vector>

namespace mbdsdr {
namespace dsp {

class ISource {
public:
    virtual ~ISource() = default;

    /// Open device / start streaming. Returns true on success.
    virtual bool start() = 0;
    /// Stop streaming and release resources.
    virtual void stop() = 0;

    /// Read one frame of complex float IQ samples into out (resized as needed).
    /// Returns the number of samples read. May block up to a short timeout.
    virtual std::size_t readIQ(std::vector<std::complex<float>>& out) = 0;

    virtual void setCenterFreq(double freqHz) = 0;
    virtual void setSampleRate(double rateHz) = 0;
    virtual void setGain(double gainDb) = 0;

    // ---- Optional RF front-end tuning (empty default = unsupported) ----
    // Only hardware with a tuner/ADC front-end (RTL-SDR, ...) overrides these.
    // TestSignalSource / FileSource inherit the no-op defaults, so the engine
    // can forward UI requests blindly without knowing the concrete backend.
    /// Direct ADC sampling: 0=off, 1=I branch, 2=Q branch.
    virtual void setDirectSampling(int mode) { (void)mode; }
    /// Offset tuning (PLL offset from center) for tuners that support it.
    virtual void setOffsetTuning(bool on) { (void)on; }
    /// RTL2832 baseband chip AGC.
    virtual void setRtlAgc(bool on) { (void)on; }
    /// Tuner gain AGC: true => automatic (manual gain ignored), false => manual.
    virtual void setTunerAgc(bool on) { (void)on; }
    /// Bias-T tee power on the antenna port.
    virtual void setBiasTee(bool on) { (void)on; }
    /// Frequency correction in PPM.
    virtual void setPpm(double ppm) { (void)ppm; }

    virtual double centerFreq() const = 0;
    virtual double sampleRate() const = 0;
    virtual double gain() const = 0;

    /// Display name shown in the UI, e.g. "RTL-SDR" or "Test Signal".
    virtual QString name() const = 0;
    /// True only when a real hardware device is actually connected.
    /// Test sources must return false.
    virtual bool isConnected() const = 0;

    // ---- Optional multi-stage gain (default no-op) ----
    /// Multi-stage gain: stage 0 = LNA, 1 = MIX, 2 = VGA (RTL2832U+E4000/R820T).
    /// Single-gain sources can map this to setGain().
    virtual void setGainStage(int /*stage*/, double /*gainDb*/) {}
};

} // namespace dsp
} // namespace mbdsdr
