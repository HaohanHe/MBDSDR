// SPDX-License-Identifier: MIT
// Demodulators: AM / NFM / WFM / SSB (USB/LSB). All streamable,
// state persists across blocks. Mono only; stereo TODO for WFM.
#pragma once

#include <complex>
#include <vector>
#include <QString>

namespace mbdsdr {
namespace dsp {

class IDemod {
public:
    virtual ~IDemod() = default;
    virtual std::vector<float> process(const std::vector<std::complex<float>>& iq) = 0;
    virtual void reset() = 0;
    virtual QString name() const = 0;
    virtual double outputSampleRate() const = 0;
};

// ---- FIR lowpass with tail state (overlap-save style) ----
class FirLowpass {
public:
    FirLowpass(double cutoffNormalized, int numTaps = 63);
    void reset() { delayLine_.assign(numTaps_, 0.0f); }
    void process(const std::vector<float>& in, std::vector<float>& out);
private:
    std::vector<float> taps_;
    std::vector<float> delayLine_;
    int numTaps_;
};

// ---- AM: envelope detection ----
class DemodAM : public IDemod {
public:
    explicit DemodAM(double ifSampleRate = 48000.0, double bandwidth = 10000.0);
    std::vector<float> process(const std::vector<std::complex<float>>& iq) override;
    void reset() override;
    QString name() const override { return QStringLiteral("AM"); }
    double outputSampleRate() const override { return ifSr_; }
private:
    double ifSr_, bw_;
    float dcPrev_ = 0;
    FirLowpass lpf_;
};

// ---- NFM: quadrature discriminator + de-emphasis ----
class DemodNFM : public IDemod {
public:
    explicit DemodNFM(double ifSampleRate = 48000.0, double bandwidth = 12500.0);
    std::vector<float> process(const std::vector<std::complex<float>>& iq) override;
    void reset() override;
    QString name() const override { return QStringLiteral("NFM"); }
    double outputSampleRate() const override { return ifSr_; }
private:
    double ifSr_, bw_;
    float gain_;
    float deAlpha_;
    float deState_ = 0;
    std::complex<float> prev_ = {1,0};
};

// ---- WFM: discriminator, wide deviation, de-emphasis ----
class DemodWFM : public IDemod {
public:
    explicit DemodWFM(double ifSampleRate = 250000.0, double bandwidth = 150000.0);
    std::vector<float> process(const std::vector<std::complex<float>>& iq) override;
    void reset() override;
    QString name() const override { return QStringLiteral("WFM"); }
    double outputSampleRate() const override { return ifSr_; }
private:
    double ifSr_, bw_;
    float gain_;
    float deAlpha_;
    float deState_ = 0;
    float audioLpState_ = 0;
    std::complex<float> prev_ = {1,0};
};

// ---- SSB: BFO shift + real part + LPF ----
class DemodSSB : public IDemod {
public:
    enum class Sideband { USB, LSB };
    explicit DemodSSB(Sideband sb = Sideband::USB, double ifSampleRate = 48000.0,
                      double bandwidth = 2800.0);
    std::vector<float> process(const std::vector<std::complex<float>>& iq) override;
    void reset() override;
    QString name() const override;
    double outputSampleRate() const override { return ifSr_; }
private:
    Sideband sb_;
    double ifSr_, bw_;
    float dPhi_;
    float phase_ = 0;
    FirLowpass lpf_;
};

} // namespace dsp
} // namespace mbdsdr
