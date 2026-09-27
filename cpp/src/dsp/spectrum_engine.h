// SPDX-License-Identifier: MIT
#pragma once

#include <QThread>
#include <QMutex>
#include <QElapsedTimer>
#include <QString>
#include <atomic>
#include <memory>
#include <vector>
#include <complex>

#include "core/spectrum_frame.h"
#include "dsp/source.h"
#include "dsp/iq_frontend.h"
#include "dsp/channelizer.h"
#include "dsp/audio_resampler.h"
#include "dsp/demod.h"
#include "dsp/squelch.h"
#include "dsp/agc.h"
#include "dsp/audio_output.h"
#include "dsp/recorder.h"
#include "dsp/gated_recorder.h"
#include "dsp/wav_writer.h"
#include "dsp/cw_decoder.h"
#include "dsp/adsb_decoder.h"

namespace mbdsdr {
namespace dsp {

// What the main record button captures: raw baseband IQ (SigMF) or the
// demodulated audio out (continuous WAV).
enum class RecTarget { BasebandIQ, DemodAudio };

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
    void disconnectSource();
    void setMuted(bool m);  // returns peak dBFS

public slots:
    void onSetCenterFreq(double freqHz);
    void onSetSampleRate(double rateHz);
    void onSetGain(double gainDb);
    void setDemodMode(const QString& mode);
    void setSquelchThreshold(float db);
    void setSquelchEnabled(bool e);
    void setBandwidth(double hz);
    bool startRecording();
    void stopRecording();
    void setGatedRecordingEnabled(bool e);

    // ---- Recording options (SDR++-aligned) ----
    void setRecTarget(RecTarget t) { recTarget_ = t; }
    RecTarget recTarget() const { return recTarget_; }
    // Filename template with {time} {freq} {mode} placeholders.
    void setRecFilenameTemplate(const QString& tpl) { recTemplate_ = tpl; }
    QString recFilenameTemplate() const { return recTemplate_; }
    void setRecStereo(bool s) { recStereo_ = s; }
    bool recStereo() const { return recStereo_; }
    // Audio recording: keep writing during squelch-closed gaps.
    void setRecIgnoreSquelch(bool ignore) { recIgnoreSquelch_ = ignore; }
    bool recIgnoreSquelch() const { return recIgnoreSquelch_; }

    // ---- RTL-SDR front-end tuning passthroughs ----
    // These forward straight to the current ISource (a no-op on test/file
    // sources, which inherit the empty ISource defaults). The UI owns the
    // enable/disable state of the gain slider based on setTunerAgc.
    void setDirectSampling(int mode);
    void setOffsetTuning(bool on);
    void setRtlAgc(bool on);
    void setTunerAgc(bool on);
    void setBiasTee(bool on);
    void setPpm(double ppm);

signals:
    void spectrumReady(const SpectrumFrame& frame);
    void sourceChanged(const QString& name, bool connected);
    void audioLevel(float dbfs);
    void rssiLevel(float dbfs);
    void squelchState(bool open);
    void recordingStateChanged(bool recording, const QString& path);
    // 1 Hz tick while recording: current file path, elapsed wall-clock seconds,
    // and on-disk byte count. Lets the UI show a live REC timer / size.
    void recordingProgress(const QString& path, int seconds, qint64 bytes);
    void cwDecoded(const QString& text, double wpm);
    void adsbAircraft(const AircraftInfo& info);

protected:
    void run() override;

private:
    std::unique_ptr<ISource> source_;
    std::unique_ptr<IDemod> demod_;
    IQFrontend frontend_;
    Channelizer channelizer_;
    AudioResampler audioRes_;
    Squelch squelch_;
    Agc agc_;
    AudioOutput* audioOut_ = nullptr;
    Recorder recorder_;
    GatedRecorder gatedRec_;
    WavWriter wavWriter_;
    CWDecoder cwDecoder_;
    ADSBDecoder adsbDecoder_;

    std::atomic<int> fftSize_{2048};
    std::atomic<bool> running_{true};
    std::atomic<bool> needDemodReset_{false};
    QString demodMode_ = "NFM";
    double bandwidth_ = 12500.0;

    // Recording options (see setRec* above).
    RecTarget recTarget_ = RecTarget::BasebandIQ;
    QString recTemplate_ = "{time}_{freq}_{mode}";
    bool recStereo_ = false;
    bool recIgnoreSquelch_ = false;

    // Expand recTemplate_ against the current source/demod state.
    QString expandRecTemplate() const;
    // True when a live data producer exists (real HW or the test signal).
    bool hasData() const;

    // Live REC progress bookkeeping (reset in startRecording, sampled at 1 Hz
    // in run()). recCurrentPath_ is the file currently being written.
    QElapsedTimer recClock_;
    QString recCurrentPath_;
    int lastRecSecond_ = -1;

    // Guards source_/demod_/demodMode_/bandwidth_ against concurrent access
    // between the engine run() thread and UI-thread connect/disconnect calls.
    QMutex sourceMutex_;

    // Cached RTL front-end options. The pass-through slots update these AND
    // forward to the live source_. On reconnect (tryConnectRtl) the cache is
    // re-applied to the freshly-opened device so options survive reconnects.
    int    cachedDirectSampling_ = 0;
    bool   cachedOffsetTuning_  = false;
    bool   cachedRtlAgc_        = false;
    bool   cachedTunerAgc_       = false;
    bool   cachedBiasTee_        = false;   // default OFF
    double cachedPpm_            = 0.0;

    void rebuildDemod();
};

} // namespace dsp
} // namespace mbdsdr
