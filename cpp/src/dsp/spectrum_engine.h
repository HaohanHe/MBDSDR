// SPDX-License-Identifier: MIT
// SpectrumEngine: runs in a dedicated QThread. Owns an ISource (RTL-SDR if
// available, otherwise TestSignalSource), reads IQ, computes power spectrum,
// and emits SpectrumFrame to the UI.
#pragma once

#include <QThread>
#include <atomic>
#include <memory>
#include <vector>
#include <complex>

#include "core/spectrum_frame.h"
#include "dsp/source.h"

namespace mbdsdr {
namespace dsp {

class SpectrumEngine : public QThread {
    Q_OBJECT
public:
    explicit SpectrumEngine(QObject* parent = nullptr);
    ~SpectrumEngine() override;

    void setFftSize(int n);
    int  fftSize() const { return fftSize_.load(); }

    void shutdown();

public slots:
    void onSetCenterFreq(double freqHz);
    void onSetSampleRate(double rateHz);
    void onSetGain(double gainDb);

signals:
    void spectrumReady(const SpectrumFrame& frame);
    /// Emitted when the active source changes (at startup, or on user switch).
    void sourceChanged(const QString& name, bool connected);

protected:
    void run() override;

private:
    std::unique_ptr<ISource> source_;
    std::atomic<int> fftSize_{2048};
    std::atomic<bool> running_{true};
};

} // namespace dsp
} // namespace mbdsdr
