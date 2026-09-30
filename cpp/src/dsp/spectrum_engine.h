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
#include "dsp/iaudio_sink.h"
#include "dsp/recorder.h"
#include "dsp/gated_recorder.h"
#include "dsp/signal_watch.h"
#include "dsp/wav_writer.h"
#include "dsp/cw_decoder.h"
#include "dsp/adsb_decoder.h"
#include "dsp/apt_decoder.h"

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
    // Test injection (NOT HARDWARE): route the demodulated 48k audio stream to a
    // caller-provided sink (e.g. MemoryAudioSink) instead of the real device.
    // Pass nullptr to restore the default device sink. The engine takes
    // ownership; the previous injected sink is destroyed.
    void setTestAudioSink(std::unique_ptr<IAudioSink> sink);
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
    // ---- Unattended signal-triggered watch recording ----
    // Arm/disarm the watch (does not touch playback: listening is never
    // interrupted). While armed, real RSSI above the threshold starts a
    // segment; after the signal leaves and the end-delay passes, the file is
    // finalised and the segment count increments.
    void setWatchEnabled(bool e);
    bool watchEnabled() const { return watchEnabled_.load(); }
    void setWatchThresholdDb(float db) { watch_.setThresholdDb(db); }
    void setWatchPrerollMs(double ms)  { gatedRec_.setPreRollMs(ms); }
    void setWatchHangMs(double ms)     { gatedRec_.setHangMs(ms); }
    int  watchSegmentCount() const     { return gatedRec_.segmentCount(); }
    // User-configurable recording directory (default: program dir "record").
    void setRecordingDir(const QString& dir);
    QString recordingDir() const { return recDir_; }
    void setAdsbReferencePosition(double latDeg, double lonDeg) { adsbDecoder_.setReferencePosition(latDeg, lonDeg); }

    // ---- Multi-VFO management (all take sourceMutex_) ---------------------
    // Add a VFO at the current source center; remove (keeps >=1); select; and
    // retune an arbitrary VFO to an absolute frequency WITHOUT moving the
    // source tuner (used when dragging a non-selected band box on the panadapter).
    void vfoAdd();
    void vfoRemove(int id);
    void vfoSelect(int id);
    void vfoSetFreq(int id, double hz);
    // SDR++-style in-band IF-offset move: retune VFO `id` to an ABSOLUTE target.
    // While the target stays inside the usable central fraction of the current
    // capture band, ONLY the channelizer NCO offset changes -- the RTL tuner
    // stays parked so other VFOs keep listening uninterrupted. If the target
    // would cross the band edge, the source tuner is genuinely retuned to the
    // target (the VFO lands back near offset ~0); other VFOs keep their
    // absolute frequencies and see their offsets recomputed on the next frame.
    // Returns true iff the source tuner was actually retuned.
    bool vfoSetOffset(int id, double targetHz);
    void vfoSetBandwidth(int id, double hz);
    void vfoSetMode(int id, const QString& mode);
    void vfoSetColor(int id, const QColor& c);
    // Snapshot for the UI (VFO list + band boxes). Blocks on sourceMutex_.
    QVector<VfoMarker> vfoMarkers() const;
    int selectedVfoId() const;

    // ---- ANR (audio noise reduction on the selected VFO's audio) -------
    void setAnrEnabled(bool on);
    void setAnrStrength(float s);

    // ---- WFM FM-stereo ---------------------------------------------------
    // Force the selected WFM channel's stereo decoder down to mono (blend=0).
    // Non-WFM channels have no decoder; the flag is remembered and re-applied
    // whenever a WFM channel is (re)built.
public slots:
    void setForceMono(bool on);
    // Reconnect handler (home-thread context). Public so QTimer can invoke it.
    void handleReconnectRequested();

public:
    // *** TEST ONLY -- NOT HARDWARE *** opt-in switch on the offline
    // TestSignalSource: when on AND the selected VFO is WFM, the synthetic IQ
    // carries a real L/R split + 19 kHz pilot + 38 kHz DSB composite MPX so the
    // stereo path can be exercised end-to-end. Default off (honest mono).
    void setTestFmStereo(bool on);

    // ---- NOAA APT weather satellite ----
    // Throw away the accumulated image + sync state (panel "清除图像" button).
    // The panel emits clearRequested(); this resets the engine-side decoder.
    void resetAptDecoder();

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

    // ---- Device hotplug / liveness --------------------------------------
    // Auto-reconnect (rtl_tcp only): when ON and the last successful endpoint
    // is remembered, a dropped device is silently retried every 2 s until it
    // comes back or the user performs any manual source operation. Default ON
    // (device re-plug recovers without touching the UI). Manual
    // connect/disconnect calls always cancel the pending retry.
    bool autoReconnectEnabled() const { return autoReconnect_.load(); }
    void setAutoReconnectEnabled(bool on) { autoReconnect_.store(on); }
    // True iff the engine currently feeds the offline test-signal fallback
    // (i.e. NOT real hardware). Honest state for the UI / integration tests.
    bool isTestSignalActive() const;

signals:
    void spectrumReady(const SpectrumFrame& frame);
    void sourceChanged(const QString& name, bool connected);
    // Device hotplug events (event channel, distinct from the 1 Hz telemetry
    // poll): sourceDropped fires ONCE when the connected real device stops
    // delivering IQ (unplugged / link lost) and the engine has already fallen
    // back to the offline test source (honest "非硬件"); sourceError carries a
    // real reason string when a connect attempt fails (socket error, no
    // hardware, ...) -- never a fabricated cause.
    void sourceDropped();
    void sourceError(const QString& message);
    // Queued from the run() loop after a drop: reconnects are performed on
    // the engine's home thread (see handleReconnectRequested).
    void reconnectRequested();
    void audioLevel(float dbfs);
    void rssiLevel(float dbfs);
    // ~1 Hz readback of the ACTUAL source state (hardware values, not the UI
    // requests): the RTL driver may round the requested gain to the nearest
    // supported stage, so the UI must display these readback numbers. For the
    // offline test source `connected` is false and the values are honest
    // synthetic readbacks (labeled "非硬件" by the UI), never fake hardware.
    void sourceTelemetry(const QString& name, bool connected,
                         double centerHz, double sampleRateHz, double gainDb);
    // Real measured SNR (dB): RSSI minus a slowly-tracked noise floor estimated
    // from the median of the per-bin power spectrum (Parseval-scaled to the
    // total-power domain). No synthetic numbers.
    void snrLevel(float snrDb);
    // The slowly-tracked real noise floor itself (same domain as the SNR input),
    // so the UI can draw a baseline and compute per-bin SNR read-outs. Emitted
    // only once tracking has initialised. Purely additive -- existing behaviour
    // is unchanged.
    void noiseFloorLevel(float dbDbfs);
    void squelchState(bool open);
    void recordingStateChanged(bool recording, const QString& path);
    // Watch state: enabled = armed (listening), recording = a segment file is
    // open right now, segmentCount = total segments saved. Emitted on arm
    // toggles, record start/stop edges and whenever a segment is finalised.
    void watchStateChanged(bool enabled, bool recording, int segmentCount);
    // 1 Hz tick while recording: current file path, elapsed wall-clock seconds,
    // and on-disk byte count. Lets the UI show a live REC timer / size.
    void recordingProgress(const QString& path, int seconds, qint64 bytes);
    void cwDecoded(const QString& text, double wpm);
    void adsbAircraft(const AircraftInfo& info);
    // RDS station data from the SELECTED WFM channel's RdsDecoder, emitted only
    // when the decoded PS/PTY/RadioText actually changed. locked=false means the
    // decoder has no CRC-verified group yet (or the channel was rebuilt); the UI
    // then clears its status-bar text and never shows a fabricated station.
    void rdsUpdated(const QString& programService, int pty,
                    const QString& radioText, bool locked);
    // VFO set / selection / parameters changed: the UI should re-pull
    // vfoMarkers() and refresh the list + band boxes.
    void vfoListChanged();
    // Recovered constellation symbols from the selected digital VFO, pushed at
    // the engine cadence. isHardware = real RF (true) vs offline/test (false).
    void constellationSymbols(const std::vector<std::complex<float>>& symbols, bool isHardware);
    // Selected VFO left digital mode (or no lock): UI should clear the panel.
    void constellationCleared();
    // NOAA APT weather-satellite image progress from the SELECTED WFM channel.
    // Emitted only while the selected VFO is WFM and (a) a new row was assembled
    // or (b) the lock state changed -- throttled to a few Hz, never per-sample.
    // `image` grows one 1818-px row at a time (Format_Grayscale8); when rows==0
    // the image is null and the panel shows its honest empty state (never a
    // fabricated cloud photo). Passed by value across a queued connection.
    void aptImageReady(const QImage& image, bool locked, int rows, double syncCorr);
    // WFM stereo status from the SELECTED WFM channel's real recovered pilot,
    // throttled to a few Hz. stereo=true ONLY when the pilot is genuinely locked
    // and the smoothed blend has come up (L/R matrix engaged); otherwise false.
    // Non-WFM modes emit (false, 0, 0) so the badge honestly shows "单声道".
    // blend is the smoothed matrix coefficient 0..1; pilotQuality is the normalised
    // pilot-to-audio ratio 0..1.
    void stereoState(bool stereo, float blend, float pilotQuality);

protected:
    void run() override;

private:
    std::unique_ptr<ISource> source_;
    IQFrontend frontend_;
    VfoManager vfoManager_;
    Squelch squelch_;
    Agc agc_;
    AudioOutput* audioOut_ = nullptr;
    // Active 48k write path. Defaults to audioOut_; an injected test sink
    // (setTestAudioSink) redirects it without touching the settings-dialog
    // handle above.
    IAudioSink* audioSink_ = nullptr;
    std::unique_ptr<IAudioSink> testSink_;
    Recorder recorder_;
    GatedRecorder gatedRec_;
    // Real-RSSI trigger for the unattended watch mode.
    SignalWatch watch_;
    std::atomic<bool> watchEnabled_{false};
    bool gatedEnabled_ = false;
    // Recording edge tracking for watchStateChanged emission.
    bool gatedWasRecording_ = false;
    // Output directory shared by the main record button and the gated/watch
    // recorder. User-configurable; defaults to program-dir "record".
    QString recDir_ = "record";
    WavWriter wavWriter_;
    PowerSpectrum powerSpectrum_;
    NoiseBlanker noiseBlanker_;
    AudioNoiseReduction anr_;
    CWDecoder cwDecoder_;
    ADSBDecoder adsbDecoder_;
    // NOAA APT image decoder. Fed the SELECTED VFO's 48 kHz demodulated baseband
    // audio ONLY while that VFO is WFM. Constructed at the fixed 48 kHz audio
    // rate (every VFO resampler output is 48000 Hz; see vfo_manager.cpp).
    AptDecoder aptDecoder_{48000.0};

    std::atomic<int> fftSize_{2048};
    std::atomic<bool> running_{true};
    std::atomic<bool> needDemodReset_{false};
    QString demodMode_ = "NFM";
    double bandwidth_ = 12500.0;
    bool wasDigital_ = false;   // last loop's selected-VFO digital-ness (edge trigger)

    // Last RDS snapshot pushed to the UI. Used to emit rdsUpdated only on real
    // changes. A rebuilt WFM channel comes back with haveAny=false; that honest
    // "no data" edge is pushed too, but repeated identical states are not.
    QString lastRdsPs_;
    int     lastRdsPty_ = -999;
    QString lastRdsRt_;
    bool    lastRdsLocked_ = false;

    // APT streaming state (engine thread only). aptActive_ tracks whether the
    // previous loop iteration was feeding the decoder; a transition out of WFM
    // resets it and pushes one cleared frame so the panel drops the stale image.
    // aptLastFreqHz_ detects retunes (user moved to a different satellite) and
    // resets the accumulator so two passes never stitch into one photo.
    bool    aptActive_ = false;
    double  aptLastFreqHz_ = 0.0;
    int     aptLastRows_ = -1;
    bool    aptLastLocked_ = false;
    QElapsedTimer aptEmitClock_;
    qint64  aptLastEmitMs_ = -1;

    // WFM stereo: user-forced mono flag (re-applied to the live WFM channel each
    // block so rebuilds pick it up) and the throttled stereoState emitter clock.
    bool    forceMono_ = false;
    bool    testFmStereo_ = false;   // *** TEST ONLY, not hardware ***
    QElapsedTimer stereoEmitClock_;
    qint64 stereoLastEmitMs_ = -1;

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

    // ~1 Hz source telemetry throttle (hardware readback push to the status bar).
    QElapsedTimer telemetryClock_;
    qint64 lastTelemetryMs_ = -1;

    // Real measured noise floor (total-power domain, dBFS), slowly tracked by an
    // exponential average across frames so strong signals don't lift it. Used to
    // derive snrLevel() = RSSI - noiseFloor.
    double noiseFloorTrackDb_ = 0.0;
    bool noiseFloorInit_ = false;

    // Demod-audio WAV sidecar (.json): the metadata is captured at start and
    // written when the WAV is closed, mirroring the SigMF recorder's stop-time
    // sidecar write so it never references a truncated file.
    QString wavSidecarPath_;
    double wavSidecarCenterHz_ = 0.0;
    double wavSidecarGainDb_ = 0.0;
    QString wavSidecarHardware_;
    QString wavSidecarMode_;

    // Guards source_/demod_/demodMode_/bandwidth_ against concurrent access
    // between the engine run() thread and UI-thread connect/disconnect calls.
    QMutex sourceMutex_;

    // Device-liveness bookkeeping (engine thread only). A real source that
    // returns zero IQ reads for a short grace period is treated as dropped:
    // the engine falls back to the offline test source and fires sourceDropped.
    // If auto-reconnect is ON and the last successful rtl_tcp endpoint is
    // remembered, a 2 s retry loop silently reconnects until the device comes
    // back or the user performs a manual source operation.
    static constexpr int kMaxZeroReadBeforeDrop = 20;   // ~0.4 s of 20 ms loops
    static constexpr qint64 kReconnectIntervalMs = 2000;
    int zeroReadFrames_ = 0;
    std::atomic<bool> autoReconnect_{true};
    bool reconnectPending_ = false;
    // "A real device source is currently expected to be active" -- set by a
    // successful connectRtlTcp / auto-reconnect, cleared by a manual
    // disconnect or by dropSourceLocked. Unlike ISource::isConnected() this
    // survives an RST (Qt flips the socket to UnconnectedState on reset), so
    // the zero-read drop detector keeps counting even after a hard drop.
    bool realSourceActive_ = false;
    QString tcpHost_;
    quint16 tcpPort_ = 0;
    QElapsedTimer reconnectClock_;
    qint64 lastReconnectMs_ = 0;
    // Caller must hold sourceMutex_. Swaps the live source to the offline test
    // signal (honest fallback) and emits sourceDropped + sourceChanged.
    void dropSourceLocked();

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
