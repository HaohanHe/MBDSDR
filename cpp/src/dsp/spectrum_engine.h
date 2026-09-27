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
#include "dsp/recorder.h"
#include "dsp/gated_recorder.h"
#include "dsp/cw_decoder.h"
#include "dsp/adsb_decoder.h"

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
    double scanBand(double lowHz, double highHz, double stepHz);
    bool tryConnectRtl();
    void disconnectSource();  // returns peak dBFS

public slots:
    void onSetCenterFreq(double freqHz);
    void onSetSampleRate(double rateHz);
    void onSetGain(double gainDb);
    void setDemodMode(const QString& mode);
    void setSquelchThreshold(float db);
    void setSquelchEnabled(bool e);
    void setBandwidth(double hz);
    void startRecording();
    void stopRecording();
    void setGatedRecordingEnabled(bool e);

signals:
    void spectrumReady(const SpectrumFrame& frame);
    void sourceChanged(const QString& name, bool connected);
    void audioLevel(float dbfs);
    void rssiLevel(float dbfs);
    void squelchState(bool open);
    void recordingStateChanged(bool recording, const QString& path);
    void cwDecoded(const QString& text, double wpm);
    void adsbAircraft(const AircraftInfo& info);

protected:
    void run() override;

private:
    std::unique_ptr<ISource> source_;
    std::unique_ptr<IDemod> demod_;
    IQFrontend frontend_;
    Squelch squelch_;
    Agc agc_;
    AudioOutput* audioOut_ = nullptr;
    Recorder recorder_;
    GatedRecorder gatedRec_;
    CWDecoder cwDecoder_;
    ADSBDecoder adsbDecoder_;

    std::atomic<int> fftSize_{2048};
    std::atomic<bool> running_{true};
    QString demodMode_ = "NFM";
    double bandwidth_ = 12500.0;
    bool needDemodReset_ = false;

    void rebuildDemod();
};

} // namespace dsp
} // namespace mbdsdr
