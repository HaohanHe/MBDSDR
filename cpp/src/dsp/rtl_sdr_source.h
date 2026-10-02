// SPDX-License-Identifier: MIT
// RTL-SDR source implementing ISource via the librtlsdr C API.
//
// All librtlsdr calls are routed through the RtlLibOps seam (rtl_sdr_ops.h):
// production binds the real C symbols under HAVE_RTLSDR; without the macro the
// ops table is nullptr and start() fails gracefully so the engine falls back to
// TestSignalSource. Tests install an in-memory fake ops table (test double, not
// a hardware mock) for deterministic branch coverage.
#pragma once

#include "source.h"
#include "tuner_gain_table.h"
#include "stream_watchdog.h"
#include <complex>
#include <vector>
#include <cstddef>

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
    double gain() const override;

    /// Legal discrete tuner gain steps in dB (empty when the table could not
    /// be read -- honest empty state, passthrough slider).
    std::vector<double> availableGainsDb() const override;

    QString name() const override;
    bool isConnected() const override;

    /// Human-readable summary of the front-end options, e.g.
    /// "DS=off AGC=off TunerAGC=manual BiasT=off PPM=0.0". Purely diagnostic.
    QString rtlOptionsSummary() const;

    /// Honest telemetry from the last center-frequency push: how many write
    /// attempts the retry budget consumed, and whether the hardware actually
    /// read back the requested frequency. A false convergence means the tune
    /// failed loudly (qWarning) and the request is kept for the next attempt --
    /// the caller can surface it instead of showing a tuned lie.
    int  tuneAttemptsLast() const { return tuneAttemptsLast_; }
    bool lastTuneConverged() const { return tuneConverged_; }

private:
    /// Push freqHz to the open device with the PLL write-loss defence: up to
    /// kRtlMaxTuneAttempts writes, each followed by a get_center_freq readback.
    /// Returns true iff the hardware actually reports the requested frequency;
    /// on exhaustion it logs a loud warning and returns false (never masked).
    bool pushCenterFreq(double freqHz);

    // Opaque librtlsdr handle; the RtlLibOps adapters cast it to rtlsdr_dev_t*.
    // Null whenever no device is open -- in stub builds it stays null forever.
    void* dev_ = nullptr;
    double f0_ = 98.5e6;
    double fs_ = 2.4e6;
    double gainDb_ = 20.0;
    bool   running_ = false;

    // Discrete gain table (filled from rtlsdr_get_tuner_gains on start under
    // HAVE_RTLSDR; empty in stub/offline builds). setGain() snaps into it.
    TunerGainTable gainTable_;
    // Consecutive-read-failure watchdog. When it latches DEAD the source closes
    // the device and isConnected() flips false -- no more silent zero reads.
    StreamWatchdog readWatchdog_{kReadFailThreshold};

    // Tuning state -- always stored, then (re)applied to the device on start()
    // and pushed live whenever the device is already open.
    int    tuneAttemptsLast_ = 0;   // attempts consumed by the last push
    bool   tuneConverged_    = false; // last push read back == requested
    int    directSampling_ = 0;   // 0=off, 1=I, 2=Q
    bool   offsetTuning_   = false;
    bool   rtlAgc_         = false;
    bool   tunerAgc_       = false;   // true => tuner gain automatic
    bool   biasTee_        = false;
    double ppm_            = 0;

    // Consecutive rtlsdr_read_sync failures before the stream is declared dead
    // and the device is closed. ~0.4 s at the engine's ~20 ms loop cadence,
    // matching the engine's own kMaxZeroReadBeforeDrop.
    static constexpr int kReadFailThreshold = 20;
};

} // namespace dsp
} // namespace mbdsdr
