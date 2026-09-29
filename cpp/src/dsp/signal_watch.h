// SPDX-License-Identifier: MIT
// Signal presence trigger for unattended (watch) recording.
//
// The watch mode does NOT record all the time: it is fed the REAL measured
// RSSI (dBFS from raw capture energy, see SpectrumEngine::run) once per audio
// block, and reports whether a genuine signal above the user threshold is
// present. The GatedRecorder consumes that boolean: it starts a file when the
// trigger opens (with pre-roll) and finalises the file after an end-delay once
// the trigger closes. This class is detection only -- it never writes audio and
// never fabricates levels.
#pragma once

namespace mbdsdr {
namespace dsp {

class SignalWatch {
public:
    explicit SignalWatch(double blockMs = 20.0);

    // Trigger level in dBFS (same total-power domain as the engine RSSI).
    void  setThresholdDb(float db) { thresholdDb_ = db; }
    float thresholdDb() const { return thresholdDb_; }

    // Current audio block duration (the engine audio rate is fixed at 48 kHz;
    // block length varies slightly with the source read size).
    void setBlockMs(double ms);

    void reset();
    bool active() const { return active_; }
    float smoothedDb() const { return smoothDb_; }

    /// Feed one REAL measured RSSI block. Returns true while a signal above the
    /// threshold is judged present.
    bool update(float levelDb);

private:
    float thresholdDb_;
    double blockMs_;
    float smoothDb_ = -150.0f;
    // How long the smoothed level has continuously held at/above threshold.
    double aboveHeldMs_ = 0.0;
    bool active_ = false;
    float attackAlpha_ = 0.0f;
    float decayAlpha_ = 0.0f;

    void recomputeAlphas();
};

} // namespace dsp
} // namespace mbdsdr
