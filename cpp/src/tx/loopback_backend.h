// SPDX-License-Identifier: GPL-3.0-or-later
// In-memory loopback TX backend: captures IQ while PTT is asserted, used to
// verify the transmit chain without hardware. Can also stand in for a file sink.
#pragma once

#include "itx_backend.h"

#include <complex>
#include <vector>

namespace mbdsdr {
namespace tx {

class LoopbackTxBackend : public ITxBackend {
public:
    bool open() override;
    void close() override;
    bool isOpen() const override { return open_; }

    bool setFrequencyHz(double hz) override;
    bool setSampleRate(double sps) override;
    bool setGainDb(double db) override;

    bool setPtt(bool on) override;
    bool pttActive() const override { return ptt_; }

    int writeComplex(const std::complex<float>* buf, int n) override;

    double frequencyHz() const override { return freq_; }
    double sampleRate() const override { return rate_; }
    QString errorString() const override { return err_; }

    const std::vector<std::complex<float>>& captured() const { return cap_; }
    void clearCaptured() { cap_.clear(); }

private:
    bool open_ = false;
    bool ptt_ = false;
    double freq_ = 0.0;
    double rate_ = 48000.0;
    double gain_ = 0.0;
    QString err_;
    std::vector<std::complex<float>> cap_;
};

} // namespace tx
} // namespace mbdsdr
