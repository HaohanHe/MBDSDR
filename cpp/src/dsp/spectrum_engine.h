// SPDX-License-Identifier: MIT
// SpectrumEngine: runs in a dedicated QThread, pulls IQ from the (offline)
// TestSignalGenerator, computes the power spectrum, and emits a ready
// SpectrumFrame. The UI thread connects to spectrumReady() and paints.
//
// *** TEST DATA -- NOT HARDWARE ***
// In Phase 1 the source is TestSignalGenerator. Phase 2 will swap this
// source for a real SDR device (librtlsdr / SoapySDR) behind the same
// interface, without touching the UI side.
#pragma once

#include <QThread>
#include <atomic>
#include <vector>
#include <complex>

#include "core/spectrum_frame.h"
#include "dsp/test_signal.h"
#include "dsp/power_spectrum.h"

namespace mbdsdr {
namespace dsp {

class SpectrumEngine : public QThread {
    Q_OBJECT
public:
    explicit SpectrumEngine(QObject* parent = nullptr);
    ~SpectrumEngine() override;

    /// Set FFT size (must be power of two; applied on next frame).
    void setFftSize(int n);
    int  fftSize() const { return fftSize_.load(); }

    /// Stop the thread loop. Safe to call from any thread.
    void shutdown();

signals:
    /// Emitted every time a new spectrum frame is ready.
    /// Delivered to the UI thread via queued connection.
    void spectrumReady(const SpectrumFrame& frame);

protected:
    void run() override;

private:
    std::atomic<int>  fftSize_{2048};
    std::atomic<bool> running_{true};

    // DSP pieces reused as-is (not a separate data model -- just buffers)
    TestSignalGenerator generator_;
};

} // namespace dsp
} // namespace mbdsdr
