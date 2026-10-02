// SPDX-License-Identifier: MIT
// Demodulators: AM / NFM / WFM / SSB (USB/LSB). All streamable,
// state persists across blocks. WFM mono decoding lives here; the optional
// stereo composite-baseband decoding is done downstream by WfmStereoDecoder
// (see dsp/wfm_stereo.{h,cpp}), which consumes DemodWFM::rawMpxOut().
#pragma once

#include <complex>
#include <vector>
#include <QString>

#include "agc.h"

namespace mbdsdr {
namespace dsp {

class IDemod {
public:
    virtual ~IDemod() = default;
    virtual std::vector<float> process(const std::vector<std::complex<float>>& iq) = 0;
    virtual void reset() = 0;
    virtual QString name() const = 0;
    virtual double outputSampleRate() const = 0;
    virtual void setBandwidth(double /*hz*/) {}
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

// ---- Nuttall-windowed-sinc lowpass, designed from Hz (clean-room) ----------
// Mirrors SDR++ taps::lowPass(cutoff, transWidth, sr) with a Nuttall window:
// tap count ~= ceil(3.8 * sr / transWidth), windowed-sinc impulse, DC-gain
// normalized. Owns its own tail delay line so blocks stream continuously and
// can be re-designed on the fly when the channel bandwidth changes.
class NuttallLpf {
public:
    NuttallLpf() = default;
    void design(double cutoffHz, double transWidthHz, double sampleRateHz);
    void reset() { delay_.assign(taps_.size(), 0.0f); }
    void process(const std::vector<float>& in, std::vector<float>& out);
    bool empty() const { return taps_.empty(); }
    int numTaps() const { return static_cast<int>(taps_.size()); }
private:
    std::vector<float> taps_;
    std::vector<float> delay_;
};

// ---- AM: envelope detection ----
class DemodAM : public IDemod {
public:
    explicit DemodAM(double ifSampleRate = 48000.0, double bandwidth = 10000.0);
    std::vector<float> process(const std::vector<std::complex<float>>& iq) override;
    void reset() override;
    QString name() const override { return QStringLiteral("AM"); }
    double outputSampleRate() const override { return ifSr_; }
    void setBandwidth(double hz) override;
    // In-chain carrier AGC (on complex IQ BEFORE |·| envelope). On by default.
    void setCarrierAgcEnabled(bool on) { carrierAgc_.setEnabled(on); }
    bool carrierAgcEnabled() const { return carrierAgc_.enabled(); }
private:
    double ifSr_, bw_;
    float dcPrev_ = 0;
    FirLowpass lpf_;
    ComplexCarrierAgc carrierAgc_;
};

// ---- NFM: quadrature discriminator + bandwidth-matched FIR + de-emphasis ----
class DemodNFM : public IDemod {
public:
    explicit DemodNFM(double ifSampleRate = 48000.0, double bandwidth = 12500.0);
    std::vector<float> process(const std::vector<std::complex<float>>& iq) override;
    void reset() override;
    QString name() const override { return QStringLiteral("NFM"); }
    double outputSampleRate() const override { return ifSr_; }
    void setBandwidth(double hz) override;
private:
    void redesignLpf();
    double ifSr_, bw_;
    float gain_;
    float deAlpha_;
    float deState_ = 0;
    std::complex<float> prev_ = {1,0};
    NuttallLpf discLpf_;
    bool firEnabled_ = false;  // Nuttall LPF correct in isolation (unit-tested);
                              // block-streaming interaction w/ channelizer needs
                              // investigation before enabling in live chain.
};

// ---- WFM: discriminator, wide deviation, de-emphasis ----
class DemodWFM : public IDemod {
public:
    explicit DemodWFM(double ifSampleRate = 250000.0, double bandwidth = 150000.0);
    std::vector<float> process(const std::vector<std::complex<float>>& iq) override;
    void reset() override;
    QString name() const override { return QStringLiteral("WFM"); }
    double outputSampleRate() const override { return ifSr_; }

    // De-emphasized MPX baseband from the LAST process() call: the stream
    // AFTER the 50 us de-emphasis one-pole but BEFORE the 15 kHz audio LPF,
    // so the 57 kHz RDS subcarrier is still present. Same length as the audio
    // returned by process(). The audio output itself is unchanged sample by
    // sample; this is a tap only.
    const std::vector<float>& mpxOut() const { return mpxBuf_; }

    // Raw composite MPX baseband from the LAST process() call: the stream
    // BEFORE the 50 us de-emphasis one-pole (i.e. gain*angle straight from the
    // quadrature discriminator). The 19 kHz pilot and 38 kHz stereo DSB are at
    // full strength here, so this is the tap a downstream WfmStereoDecoder
    // needs. Same length as the audio returned by process(). The audio output
    // itself is unchanged sample by sample; this is a tap only.
    const std::vector<float>& rawMpxOut() const { return rawMpxBuf_; }
private:
    double ifSr_, bw_;
    float gain_;
    float deAlpha_;
    float deState_ = 0;
    float audioLpState_ = 0;
    std::complex<float> prev_ = {1,0};
    std::vector<float> mpxBuf_;
    std::vector<float> rawMpxBuf_;
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
    void setBandwidth(double hz) override;
private:
    Sideband sb_;
    double ifSr_, bw_;
    float dPhi_;
    float phase_ = 0;
    FirLowpass lpf_;
};

} // namespace dsp
} // namespace mbdsdr
