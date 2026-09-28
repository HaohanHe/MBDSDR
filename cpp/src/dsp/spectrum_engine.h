// SPDX-License-Identifier: MIT
#pragma once

#include <QThread>
#include <QMutex>
#include <QElapsedTimer>
#include <QString>
#include <QColor>
#include <atomic>
#include <memory>
#include <vector>
#include <complex>

#include "core/spectrum_frame.h"
#include "dsp/source.h"
#include "dsp/power_spectrum.h"
#include "dsp/noise_blanker.h"
#include "dsp/iq_frontend.h"
#include "dsp/vfo_manager.h"
#include "dsp/audio_resampler.h"
#include "dsp/demod.h"
#include "dsp/squelch.h"
#include "dsp/agc.h"
#include "dsp/anr.h"
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
    // Read-only state getters for integration tests / status display.
    QString demodMode() const { return demodMode_; }
    double bandwidth() const { return bandwidth_; }
    double centerFreq() const;
    bool noiseBlankerEnabled() const;
    int windowType() const;
    int averageMode() const;
    // Owned audio sink (UI-thread affinity). Exposed so the settings dialog can
    // hot-restart playback on a user-selected output device.
    AudioOutput* audioOutput() const { return audioOut_; }
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
    QString recordingPath() const { return recCurrentPath_; }
    // Connect to an rtl_tcp server. Returns true on success. On failure the
    // source falls back to TestSignalSource (no fake data) and emits sourceChanged.
    bool connectRtlTcp(const QString& host, quint16 port);
    void setGatedRecordingEnabled(bool e);
    void setAdsbReferencePosition(double latDeg, double lonDeg) { adsbDecoder_.setReferencePosition(latDeg, lonDeg); }

    // ---- Multi-VFO management (all take sourceMutex_) ---------------------
    // Add a VFO at the current source center; remove (keeps >=1); select; and
    // retune an arbitrary VFO to an absolute frequency WITHOUT moving the
    // source tuner (used when dragging a non-selected band box on the panadapter).
    void vfoAdd();
    void vfoRemove(int id);
    void vfoSelect(int id);
    void vfoSetFreq(int id, double hz);
    void vfoSetBandwidth(int id, double hz);
    void vfoSetMode(int id, const QString& mode);
    void vfoSetColor(int id, const QColor& c);
    // Snapshot for the UI (VFO list + band boxes). Blocks on sourceMutex_.
    QVector<VfoMarker> vfoMarkers() const;
    int selectedVfoId() const;

    // ---- ANR (audio noise reduction on the selected VFO's audio) -------
    void setAnrEnabled(bool on);
    void setAnrStrength(float s);

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
    // Signal-processing options (window / average / noise blanker).
    void setWindowType(int w);     // 0=Hann 1=Flattop 2=Blackman
    void setAverageMode(int a);    // 0=Off 1=Slow 2=Fast
    void setNoiseBlanker(bool on);

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
    // VFO set / selection / parameters changed: the UI should re-pull
    // vfoMarkers() and refresh the list + band boxes.
    void vfoListChanged();
    // Recovered constellation symbols from the selected digital VFO, pushed at
    // the engine cadence. isHardware = real RF (true) vs offline/test (false).
    void constellationSymbols(const std::vector<std::complex<float>>& symbols, bool isHardware);
    // Selected VFO left digital mode (or no lock): UI should clear the panel.
    void constellationCleared();

protected:
    void run() override;

private:
    std::unique_ptr<ISource> source_;
    IQFrontend frontend_;
    VfoManager vfoManager_;
    Squelch squelch_;
    Agc agc_;
    AudioOutput* audioOut_ = nullptr;
    Recorder recorder_;
    GatedRecorder gatedRec_;
    WavWriter wavWriter_;
    PowerSpectrum powerSpectrum_;
    NoiseBlanker noiseBlanker_;
    AudioNoiseReduction anr_;
    CWDecoder cwDecoder_;
    ADSBDecoder adsbDecoder_;

    std::atomic<int> fftSize_{2048};
    std::atomic<bool> running_{true};
    std::atomic<bool> needDemodReset_{false};
    QString demodMode_ = "NFM";
    double bandwidth_ = 12500.0;
    bool wasDigital_ = false;   // last loop's selected-VFO digital-ness (edge trigger)

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
