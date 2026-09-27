// SPDX-License-Identifier: MIT
#pragma once

#include <QThread>
#include <atomic>
#include <memory>
#include <vector>
#include <complex>

#include "core/spectrum_frame.h"
#include "dsp/source.h"
#include "dsp/iq_frontend.h"
#include "dsp/demod.h"
#include "dsp/squelch.h"
#include "dsp/agc.h"
#include "dsp/audio_output.h"

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
    void setDemodMode(const QString& mode);
    void setSquelchThreshold(float db);
    void setSquelchEnabled(bool e);

signals:
    void spectrumReady(const SpectrumFrame& frame);
    void sourceChanged(const QString& name, bool connected);
    void audioLevel(float dbfs);
    void squelchState(bool open);

protected:
    void run() override;

private:
    std::unique_ptr<ISource> source_;
    std::unique_ptr<IDemod> demod_;
    IQFrontend frontend_;
    Squelch squelch_;
    Agc agc_;
    AudioOutput* audioOut_ = nullptr;

    std::atomic<int> fftSize_{2048};
    std::atomic<bool> running_{true};
    QString demodMode_ = "NFM";
    bool needDemodReset_ = false;

    void rebuildDemod();
};

} // namespace dsp
} // namespace mbdsdr
