// SPDX-License-Identifier: MIT
// Transmit modulators: the inverse of the analog demodulators in demod.{h,cpp}.
//
// Each modulator turns a block of real audio (sampled at the IQ rate passed to
// the constructor) into complex baseband IQ. AM/NFM/WFM parameters mirror the
// matching Demod* classes so that a Modulator -> Demod loopback reconstructs
// the original audio. SSB uses a Hilbert (phasing) method. CW is a keyed carrier.
#pragma once

#include <complex>
#include <vector>

namespace mbdsdr {
namespace tx {

class IModulator {
public:
    virtual ~IModulator() = default;
    virtual std::vector<std::complex<float>> process(const std::vector<float>& audio) = 0;
    virtual void reset() = 0;
};

// Real FIR used for the Hilbert 90-degree network and alignment delay.
class HilbertFir {
public:
    explicit HilbertFir(int numTaps);
    // Push one real sample, return filtered (90-degree shifted) value.
    float pushQ(float x);
    // Push one real sample, return group-delay-aligned direct-path value.
    float pushI(float x);
    void reset();
    int groupDelay() const { return center_; }

private:
    int numTaps_;
    int center_;
    std::vector<float> taps_;
    std::vector<float> line_;
};

class ModulatorAM : public IModulator {
public:
    ModulatorAM(double sampleRate, double bandwidth);
    std::vector<std::complex<float>> process(const std::vector<float>& audio) override;
    void reset() override {}
private:
    float depth_;
};

class ModulatorFM : public IModulator {
public:
    // wide=true uses 75 kHz deviation (WFM), otherwise bandwidth/2 (NFM).
    ModulatorFM(double sampleRate, double bandwidth, bool wide);
    std::vector<std::complex<float>> process(const std::vector<float>& audio) override;
    void reset() override;
private:
    float dphiPerUnit_;   // phase increment per unit message sample
    float preAlpha_;      // de-emphasis alpha (pre-emphasis is its inverse)
    float phase_;
    float prePrev_;
};

class ModulatorSSB : public IModulator {
public:
    enum class Sideband { USB, LSB };
    ModulatorSSB(Sideband sb, double sampleRate, double bandwidth);
    std::vector<std::complex<float>> process(const std::vector<float>& audio) override;
    void reset() override;
private:
    Sideband sb_;
    float dphi_;
    float phase_;
    HilbertFir hilbert_;
};

// CW: keyed carrier; audio carries the key envelope (1 = mark, 0 = space),
// the carrier is placed at toneOffsetHz (baseband sidetone offset).
class ModulatorCW : public IModulator {
public:
    ModulatorCW(double sampleRate, double toneOffsetHz);
    std::vector<std::complex<float>> process(const std::vector<float>& key) override;
    void reset() override;
private:
    float dphi_;
    float phase_;
};

} // namespace tx
} // namespace mbdsdr
