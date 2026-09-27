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

    virtual double centerFreq() const = 0;
    virtual double sampleRate() const = 0;
    virtual double gain() const = 0;

    /// Display name shown in the UI, e.g. "RTL-SDR" or "Test Signal".
    virtual QString name() const = 0;
    /// True only when a real hardware device is actually connected.
    /// Test sources must return false.
    virtual bool isConnected() const = 0;
};

} // namespace dsp
} // namespace mbdsdr
