// SPDX-License-Identifier: MIT
// SoapySDR transmit backend, loaded at runtime via dlopen so the project builds
// without SoapySDR installed. Supports any TX-capable Soapy device (PlutoSDR,
// HackRF, LimeSDR, ...). If the library or device is missing, open() fails with
// an honest error instead of crashing.
#pragma once

#include "itx_backend.h"

#include <QString>

namespace mbdsdr {
namespace tx {

class SoapyTxBackend : public ITxBackend {
public:
    // driverArgs is a Soapy kwargs string, e.g. "driver=plutosdr" or
    // "driver=hackrf,serial=...".
    explicit SoapyTxBackend(const QString& driverArgs);
    ~SoapyTxBackend() override;

    bool open() override;
    void close() override;
    bool isOpen() const override;

    bool setFrequencyHz(double hz) override;
    bool setSampleRate(double sps) override;
    bool setGainDb(double db) override;

    bool setPtt(bool on) override;
    bool pttActive() const override;

    int writeComplex(const std::complex<float>* buf, int n) override;

    double frequencyHz() const override;
    double sampleRate() const override;
    QString errorString() const override { return err_; }

private:
    struct Impl;
    Impl* d_;
    QString args_;
    QString err_;
};

} // namespace tx
} // namespace mbdsdr
