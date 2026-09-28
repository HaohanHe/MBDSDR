// SPDX-License-Identifier: GPL-3.0-or-later
// Transmit backend abstraction. Mirrors the receive source contract but for TX.
// Implementations MUST emit nothing unless PTT is explicitly asserted.
#pragma once

#include <QString>
#include <complex>

namespace mbdsdr {
namespace tx {

class ITxBackend {
public:
    virtual ~ITxBackend() = default;

    virtual bool open() = 0;
    virtual void close() = 0;
    virtual bool isOpen() const = 0;

    virtual bool setFrequencyHz(double hz) = 0;
    virtual bool setSampleRate(double sps) = 0;
    virtual bool setGainDb(double db) = 0;

    // Assert/deassert transmit. Returns false when the request is disallowed
    // (not open, invalid frequency, hardware refusing). While false, the device
    // must not radiate and writeComplex() must discard data.
    virtual bool setPtt(bool on) = 0;
    virtual bool pttActive() const = 0;

    // Push complex samples. Returns the number accepted; zero when PTT is off.
    virtual int writeComplex(const std::complex<float>* buf, int n) = 0;

    virtual double frequencyHz() const = 0;
    virtual double sampleRate() const = 0;
    virtual QString errorString() const = 0;
};

} // namespace tx
} // namespace mbdsdr
