// SPDX-License-Identifier: MIT
// Offline test IQ source implementing ISource.
//
// *** TEST DATA -- NOT HARDWARE ***
// Supports three modulation modes for verifying the demod chain:
//   "tone" -- dual-tone spectrum signal (default, for FFT display)
//   "am"   -- carrier + 1kHz AM modulation (for AM demod test)
//   "fm"   -- carrier + 1kHz FM modulation (for NFM/WFM demod test)
#pragma once

#include "source.h"
#include <cstdint>
#include <complex>
#include <vector>
#include <QString>

#include "dsp/digital_demod.h"

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

    void setModulation(const QString& m) { modulation_ = m; digSymbols_.clear(); }
    QString modulation() const { return modulation_; }

private:
    double fs_;
    double f0_;
    double gainDb_ = 20.0;
    std::uint64_t counter_ = 0;
    QString modulation_ = "tone";

    bool   haveSpare_ = false;
    float  spare_ = 0.0f;
    float  nextGaussian();

    // --- Offline digital (BPSK/QPSK) transmit fixture -----------------------
    // Precomputed differential symbols (via DigitalDemod::diffEncode) played as
    // rectangular pulses at fs_/symbolRate SPS. *** SYNTHETIC -- NOT HARDWARE ***
    std::vector<std::complex<float>> digSymbols_;
    std::size_t digSymIdx_ = 0;
    double digFrac_ = 0.0;     // fractional sample within current symbol
    double digSymbolRate_ = 2400.0;
    void rebuildDigitalSymbols();
};

} // namespace dsp
} // namespace mbdsdr
