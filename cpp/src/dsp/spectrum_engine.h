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
#include "core/tokens.h"
#include "dsp/source.h"
#include "dsp/power_spectrum.h"
#include "dsp/noise_blanker.h"
#include "dsp/iq_frontend.h"
#include "dsp/ft8_detector.h"
#include "dsp/ft8_codec.h"
#include "dsp/vfo_manager.h"
#include "dsp/audio_resampler.h"
#include "dsp/demod.h"
#include "dsp/squelch.h"
#include "dsp/ctcss.h"
#include "dsp/cdcss.h"
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
#include "dsp/doppler_control_surface.h"

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
    // Real noise floor in the AUDIO-BLOCK RMS dBFS domain (same domain the
    // Squelch threshold lives in), asymmetrically tracked across demodulated
    // audio blocks. This is the honest basis for the "auto threshold" button --
    // it is NOT the IQ total-power / per-bin canvas noise floor. Valid once a
    // few audio blocks have flowed; before that it reads back as ~-100 dBFS.
    float audioNoiseFloorDbfs() const { return static_cast<float>(audioNfDbfs_); }

    // ---- Honest RSSI snapshot for the decoupled band-scan driver ----------
    // The real measured total-power RSSI (dBFS) of the CURRENTLY tuned channel,
    // recomputed every capture block in run() from raw capture energy (the SAME
    // value emitted as rssiLevel). This is the honest input a headless
    // ScanActivityLink / FrequencyScanner polls: the engine itself owns NO
    // scanner and fabricates neither a frequency nor a level. Before the first
    // block flows (or on the honest empty NullSource, which delivers no IQ) it
    // reads back as -100 dBFS -- a quiet-band value, never a fabricated busy
    // level. Thread-safe atomic read-back off the engine thread.
    float rssiDbfs() const { return rssiDbfs_.load(); }

    // ---- Squelch read-back (for ControlHub get_squelch_status) -------------
    // thresholdDb() is the last threshold commanded (cached here; the Squelch
    // object itself exposes no getter). enabled() mirrors the gate mode; open()
    // is the gate's last decision edge; auto() is the engine-level auto-threshold
    // latch (when on, the run loop re-derives threshold = tracked audio noise
    // floor + margin each block). All are honest read-backs, never fabricated.
    float squelchThresholdDb() const { return squelchThreshold_; }
    bool  squelchEnabled() const;
    bool  squelchOpen() const;
    bool  squelchAuto() const { return squelchAuto_.load(); }
    void  setSquelchAuto(bool on) { squelchAuto_.store(on); }

    // ---- CTCSS sub-audible tone detection (read-back) ---------------------
    // The detector taps the SELECTED VFO's post-ANR 48 kHz mono audio. All
    // read-backs are honest: disabled / no-signal / warm-up all read
    // ctcssPresent() == false (never a fabricated tone).
    bool   ctcssEnabled() const { return ctcssEnabled_.load(); }
    bool   ctcssPresent() const { return ctcss_.tonePresent(); }
    double ctcssFreqHz() const { return ctcssFreqHz_; }
    // Speaker-only sub-audio gate: when armed, the speaker (and the parallel
    // network tap) opens only while ctcssPresent() is true. The recorder/WAV
    // path is deliberately NOT gated (mirrors the squelch precedent: only the
    // speaker path is silenced). Default: off.
    bool   ctcssGateAudio() const { return ctcssGateEnabled_.load(); }

    // ---- CDCSS/DCS digital coded squelch (read-back) ----------------------
    // Parallel to CTCSS: the CdcssDecoder taps the same post-ANR 48k mono.
    // All read-backs are honest: disabled / no-carrier / warm-up all read
    // cdcssPresent() == false. cdcssCode() is the desired 12-bit DCS address.
    bool   cdcssEnabled() const { return cdcssEnabled_.load(); }
    bool   cdcssPresent() const { return cdcss_.codePresent(); }
    int    cdcssCode() const { return cdcssCode_; }
    // Speaker-only digital sub-audio gate, independent of detection enable.
    bool   cdcssGateAudio() const { return cdcssGateEnabled_.load(); }

    // ---- FT8 detection (read-back) --------------------------------------
    // Detection layer only (C++ BP decode deferred to step 4). All read-backs
    // are honest: disabled / no-signal -> ft8Present() == false, never a
    // fabricated candidate. setFt8Enabled(false) clears stats.
    bool   ft8Enabled() const { return ft8Enabled_.load(); }
    bool   ft8Present() const { return ft8Last_.valid; }
    double ft8FreqOffsetHz() const { return ft8Last_.freqOffsetHz; }
    double ft8SyncQuality() const { return ft8Last_.syncQuality; }
    int    ft8CandidateCount() const { return ft8Detector_.lastCandidateCount(); }
    // Step-4 decode readback. Empty string until detector->LLR->codec wiring is
    // enabled (honest: no fabricated frame text).
    std::string ft8DecodedText() const;

    // 喂一段 12 kS/s 复基带窗（检测→LLR→解码全链路）。未启用则直接空态返回。
    // 测试注入/引擎 run loop 共用此入口；CRC 不过/不收敛 -> ft8Decoded_ 保持空。
    void processFt8Window(const std::complex<float>* iq, std::size_t n);
    // 增量喂 12 kS/s 复基带（运行时主路径：vfo channelizer 后/run loop 调用）。
    // 内部 15 s 环形缓冲，满窗触发 processFt8Window；多帧去重（相同文本+相近频偏合并）。
    void feedFt8Baseband(const std::complex<float>* iq, std::size_t n);
    int    ft8DecodedFrameCount() const { return ft8DecodedFrames_.load(); }
    // True iff a network-audio tap is currently installed (setNetworkAudioSink).
    // The detailed stream stats live on the sink the caller installed.
    bool  networkTapActive() const { return networkTap_ != nullptr; }
    // Owned audio sink (UI-thread affinity). Exposed so the settings dialog can
    // hot-restart playback on a user-selected output device.
    AudioOutput* audioOutput() const { return audioOut_; }
    // Test injection (NOT HARDWARE): route the demodulated 48k audio stream to a
    // caller-provided sink (e.g. MemoryAudioSink) instead of the real device.
    // Pass nullptr to restore the default device sink. The engine takes
    // ownership; the previous injected sink is destroyed.
    void setTestAudioSink(std::unique_ptr<IAudioSink> sink);
    // Network audio tap: a PARALLEL branch off the demodulated-audio write point
    // (the same block that goes to audioSink_). The existing speaker/recording
    // chain is never diverted or stopped -- the tap just mirrors its frames to
    // the network. nullptr detaches and destroys the tap. Digital VFOs produce no
    // analog audio, so the tap simply stays silent in that mode (no fabricated
    // silence stream). Owned here.
    void setNetworkAudioSink(std::unique_ptr<IAudioSink> tap);
    // Sweep [lowHz, highHz] in stepHz, returning the peak RMS dBFS observed.
    // When peakFreqHzOut is non-null it is filled with the frequency at which
    // the peak occurred (so callers can retune to the hit).
    double scanBand(double lowHz, double highHz, double stepHz,
                    double* peakFreqHzOut = nullptr);

    // One-shot capture for frequency calibration (used by the calibration
    // wizard and the calibrate_frequency agent tool). When tuneHz >= 0 it first
    // tunes the ACTIVE source there, then synchronously reads ~sampleCount
    // complex samples under sourceMutex_ -- the same locking scanBand() uses.
    // Returns the number of samples placed in `out`; sampleRateHzOut and
    // centreHzOut report the nominal rate and the tuned centre. On a test /
    // offline source it returns that honestly-labelled generated data rather
    // than fabricating a device capture.
    std::size_t captureForCalibration(double tuneHz, int sampleCount,
                                      std::vector<std::complex<float>>& out,
                                      double& sampleRateHzOut,
                                      double& centreHzOut);

    // One-shot, user-initiated IQ EXPORT (the independent "dump the current IQ
    // segment to a file" entry, SDR++ iq_exporter-aligned but a FILE sink rather
    // than a network stream): capture ~sampleCount complex BASEBAND IQ and write
    // it as a cf32_le SigMF pair (.sigmf-data + .sigmf-meta) under the recording
    // dir, WITHOUT disturbing any in-flight continuous recorder (it uses its own
    // local Recorder). tuneHz<0 keeps the current source centre; tuneHz>=0 parks
    // the source there first. Returns false + an honest error string when the
    // source has no data (never fabricates a capture). On success the out-params
    // carry the REAL written path / rate / centre / sample count / byte size.
    bool exportIqSegment(int sampleCount, double tuneHz,
                         QString& pathOut, double& sampleRateHzOut, double& centerHzOut,
                         qint64& samplesOut, qint64& sizeBytesOut, QString& errorOut);
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
    // CTCSS tone squelch control. setCtcssFreqHz() clamps the requested Hz into
    // the legal PL domain (tokens kCtcssToneHzMin..Max); an out-of-domain value
    // is clamped, not invented. setCtcssEnabled(false) clears the detector so
    // ctcssPresent() reads back false. Default: disabled, 88.5 Hz.
    void setCtcssEnabled(bool on);
    void setCtcssFreqHz(double hz);
    // Arm/disarm the speaker-only sub-audio gate (independent of detection
    // enable): while on, the speaker stays silent unless a matching tone is
    // detected. Does not mute the recorder. Default off.
    void setCtcssGateAudio(bool on);
    // CDCSS/DCS digital coded squelch control. setCdcssCode() stores the desired
    // 12-bit DCS address (validated against the public 104-code table by the
    // caller); an out-of-table value is rejected here (keeps previous code).
    // setCdcssEnabled(false) clears the decoder so cdcssPresent() reads false.
    // Default: disabled, code 023.
    void setCdcssEnabled(bool on);
    void setCdcssCode(int code12);
    void setCdcssGateAudio(bool on);
    // FT8 detection enable. setFt8Enabled(false) clears ft8Last_ so ft8Present()
    // reads back false. Default disabled. Detection layer only (no C++ BP decode).
    void setFt8Enabled(bool on);
    void setBandwidth(double hz);
    bool startRecording();
    void stopRecording();
    QString recordingPath() const { return recCurrentPath_; }
    // Connect to an rtl_tcp server. Returns true on success. On failure the
    // source falls back to TestSignalSource (no fake data) and emits sourceChanged.
    bool connectRtlTcp(const QString& host, quint16 port);

    // ---- Offline file analysis (streaming, never loads the whole file) -----
    // Open a captured file as the active source through the SAME source-swap
    // path as rtl_tcp. Auto-detects SigMF (.sigmf-meta/.sigmf-data) and 16-bit
    // PCM WAV; raw complex files need rawSampleRateHz>0 (honest "原始·用户指定
    // 参数"). On failure falls back to TestSignalSource and emits sourceError
    // with the real reason. The file streams in ~25 ms chunks.
    bool openOfflineFile(const QString& path, double rawSampleRateHz = 0.0);
    // Pause/resume offline playback (file offset frozen; live sources no-op).
    void setOfflinePaused(bool paused);
    // Seek offline playback to a 0..1 fraction of the capture.
    void seekOfflineFraction(double f01);
    // Offline playback position. Returns false when the active source is not a
    // file (no offline progress to report).
    bool offlinePosition(double& curSec, double& totalSec);
    // True when the active source is the offline file player.
    bool isOfflineFileActive() const;
    // Real capabilities of the ACTIVE source (device name, tuning / sample-rate
    // range) for the UI device-info panel and dynamic sample-rate combo. For the
    // offline test-signal fallback this is the honest empty state (connected=
    // false) -- the UI then shows "RTL-SDR 未连接". Thread-safe read of the
    // active source's own accessor.
    DeviceCapabilities sourceCapabilities() const;
    // Real discrete tuner-gain steps reported by the ACTIVE source (filled from
    // rtlsdr_get_tuner_gains on a real RTL-SDR; EMPTY for rtl_tcp / test / file
    // sources -- honest empty state, never a fabricated step table). Read from a
    // capsMutex_ snapshot refreshed ~1 Hz + on source swap, so the UI never has
    // to take sourceMutex_ against the read loop.
    std::vector<double> availableGainsDb() const;
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
    // Rename channel `id` (minimal forwarder to VfoManager::renameVfo; false on
    // unknown id / empty name). Takes sourceMutex_ like the other VFO mutators.
    bool vfoRename(int id, const QString& name);
    // Enable/disable parallel demod of a non-selected channel (forwarder to
    // VfoManager::setArmed; false on unknown id). Takes sourceMutex_.
    bool vfoSetArmed(int id, bool on);
    // Snapshot for the UI (VFO list + band boxes). Blocks on sourceMutex_.
    QVector<VfoMarker> vfoMarkers() const;
    int selectedVfoId() const;

    // ---- POCSAG / m17 / VOR digital read-out (Wave1 groundwork) ----------
    // Read-only COPIES of channel `id`'s accumulated decode output. These take
    // sourceMutex_ (same lock the run loop holds) so ControlHub / Agent / UI can
    // pull a stable snapshot off the engine thread. Unknown id / non-matching
    // mode -> empty list / unlocked VorResult (honest empty state, never a
    // fabricated message / call / bearing). `clearDigitalOutputs(id)` resets the
    // queues and re-inits that channel's decoders (panel "clear" / write cmd).
    std::vector<PocsagMessage> pocsagMessages(int channelId) const;
    std::vector<M17Call>        m17Calls(int channelId) const;
    VorResult                   vorResult(int channelId) const;
    std::vector<AcarsPacket>   acarsPackets(int channelId) const;
    std::vector<NavtexMessage> navtexMessages(int channelId) const;
    void                        clearDigitalOutputs(int channelId);

    // Phase55 block2: Costas carrier-lock snapshot for the SELECTED VFO channel.
    // Returns a default (carrierLocked=false) honest empty state when the
    // selected channel is not a digital BPSK/QPSK mode -- never a fabricated
    // lock. Takes sourceMutex_; read by ControlHub / Agent get_status / UI.
    DigitalLockStatus           digitalLockStatus() const;

    // Phase55 block3: register the live Doppler control surface (MainWindow).
    // Null until a UI registers; callers get an honest "not available" otherwise.
    void setDopplerControlSurface(DopplerControlSurface* s) { dopplerSurface_ = s; }
    DopplerControlSurface* dopplerControlSurface() const { return dopplerSurface_; }

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
    // Continuous-capture auto-segment length in seconds (0 = single file).
    void setRecMaxSegmentSeconds(double secs);
    double recMaxSegmentSeconds() const { return recMaxSegSecs_; }
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
    // Readback of the cached requested AGC state (what we last commanded).
    bool rtlAgc() const { return cachedRtlAgc_; }
    bool tunerAgc() const { return cachedTunerAgc_; }
    void setBiasTee(bool on);
    void setPpm(double ppm);
    // Signal-processing options (window / average / noise blanker).
    void setWindowType(int w);     // 0=Hann 1=Flattop 2=Blackman
    void setAverageMode(int a);    // 0=Off 1=Slow 2=Fast
    void setNoiseBlanker(bool on);

    // ---- Frontend software decimation -------------------------------------
    // Integer factor D (1 = off) applied BEFORE channelization: a real decimating
    // low-pass (reuses Channelizer, NCO offset 0) so no aliasing folds back. The
    // wideband FFT and the VFO channels then run on the reduced rate sr/D, which
    // narrows the displayed band and cuts wideband FFT / channelizer compute for
    // narrow modes (CW/FT8). D=1 leaves the whole chain byte-identical to before.
    void setFrontendDecimation(int D);
    int  frontendDecimation() const { return frontendDecimation_.load(); }

    // ---- Device hotplug / liveness --------------------------------------
    // Auto-reconnect (rtl_tcp only): when ON and the last successful endpoint
    // is remembered, a dropped device is silently retried every 2 s until it
    // comes back or the user performs any manual source operation. Default ON
    // (device re-plug recovers without touching the UI). Manual
    // connect/disconnect calls always cancel the pending retry.
    bool autoReconnectEnabled() const { return autoReconnect_.load(); }
    void setAutoReconnectEnabled(bool on) { autoReconnect_.store(on); }
    // True iff the engine currently feeds the offline synthetic test signal
    // (i.e. TestSignalSource is the ACTIVE source). Honest state for the UI /
    // integration tests: on real hardware or the honest empty (NullSource) this
    // is false.
    bool isTestSignalActive() const;

    // ---- Explicit synthetic test source (opt-in debugging, never automatic)
    // The production path NEVER synthesizes IQ on its own. The synthetic
    // TestSignalSource is only installed when the caller explicitly opts in via
    // setTestSourceEnabled(true) (the app also honors the --test-source CLI flag
    // and the MBDSDR_TEST_SOURCE=1 environment variable, both read before the
    // engine is built). Turning it on while no real device / file is active
    // swaps in the synthetic source; turning it off (or having no hardware)
    // drops back to the honest empty NullSource. Enabling never kicks a live
    // real device; disabling only tears down the synthetic source.
    void setTestSourceEnabled(bool on);
    bool isTestSourceEnabled() const { return testSourceEnabled_.load(); }
    // State queries for the UI / ControlHub to render the honest empty state and
    // disable record / decode / scan controls:
    //   hasRealSource() -- a real hardware device (RTL-SDR / rtl_tcp) is live.
    //   isSynthetic()   -- the ACTIVE source is the synthetic TestSignalSource.
    //   hasData()       -- ANY live IQ producer exists (real HW, synthetic test,
    //                      or an opened offline capture file); false on empty.
    bool hasRealSource() const;
    bool isSynthetic() const;
    bool hasData() const;

    // ---- SpyServer IQ tap (read-only, push) --------------------------------
    // When `on` is true the engine re-emits each freshly-read real IQ block
    // (rtl_tcp source, or the honestly-labelled offline test signal) as the
    // iqTapReady signal (queued, engine -> UI thread). The SpyServerServer
    // consumes it to stream IQ to a remote client. Off by default so the idle
    // path copies nothing. This is a READ-only tap: it never alters the block
    // the downstream DSP chain sees.
    void setSpyServerTapRequested(bool on) { iqTapRequested_.store(on); }

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
    // Slowly-tracked audio-block RMS dBFS noise floor (SAME domain as the
    // Squelch threshold), throttled to ~5 Hz so the UI can drive an auto
    // gate = floor + margin. Emitted only after tracking has initialised.
    void audioRmsNoiseFloor(float dbfs);
    void squelchState(bool open);
    void recordingStateChanged(bool recording, const QString& path);
    // Watch state: enabled = armed (listening), recording = a segment file is
    // open right now, segmentCount = total segments saved. Emitted on arm
    // toggles, record start/stop edges and whenever a segment is finalised.
    void watchStateChanged(bool enabled, bool recording, int segmentCount);
    // 1 Hz tick while recording: current file path, elapsed wall-clock seconds,
    // and on-disk byte count. Lets the UI show a live REC timer / size.
    void recordingProgress(const QString& path, int seconds, qint64 bytes);
    // Emitted when continuous IQ recording auto-segments: the recorder just
    // finalised one capture and opened a fresh collision-avoided file.
    void recordingSegmentChanged(const QString& newPath);
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
    // POCSAG message list of the SELECTED channel. Emitted ONLY when the list
    // actually changes (a new message decoded, the channel switched, or it was
    // cleared) -- diffed against the last pushed snapshot like rdsUpdated, so
    // the UI is not flooded every block. Carries the full accumulated list so
    // the panel can repopulate; an empty vector = honest empty state.
    void pocsagMessagesChanged(const std::vector<PocsagMessage>& messages);
    // m17 call list of the SELECTED channel, same change-diff semantics. Voice
    // stream calls arrive with voiceUndecoded=true (Codec2 not bundled) -- the
    // panel must show that honestly rather than playing audio.
    void m17CallsChanged(const std::vector<M17Call>& calls);
    // Latest VOR radial of the SELECTED VOR channel. Emitted only when the
    // reading changes (lock edge / radial move >0.5 deg / new Morse ID).
    // locked=false = honest no-lock: the panel clears the instrument and never
    // shows a fabricated bearing.
    void vorRadialChanged(const VorResult& result);
    // ACARS packet list / NAVTEX message list of the SELECTED channel, same
    // change-diff semantics; empty vector = honest empty state.
    void acarsPacketsChanged(const std::vector<AcarsPacket>& packets);
    void navtexMessagesChanged(const std::vector<NavtexMessage>& messages);
    // Read-only tap of the raw engine IQ block, emitted only while a SpyServer
    // client is streaming (setSpyServerTapRequested). Carries the REAL source
    // block (complex float, native source rate); the server decimates/encodes
    // it for the wire. Never fabricated.
    void iqTapReady(const std::vector<std::complex<float>>& iq,
                    double sampleRateHz, double centerFreqHz);

protected:
    void run() override;

private:
    // ---- Asynchronous control mailbox ------------------------------------
    // Hardware / shared-state set commands must NOT run on the caller's thread
    // (the GUI thread, when a user clicks the panadapter or drags a slider):
    // taking sourceMutex_ there contends with the run() loop, and the actual
    // USB tuning (especially slow tuners like FC0012) then blocks/freezes the
    // UI. Instead every set command only records its LATEST value + a dirty bit
    // under the lightweight ctrlMutex_ and returns immediately; the run() loop
    // drains and applies them ON THE ENGINE THREAD (which already owns the
    // device and holds sourceMutex_). High-frequency drags naturally coalesce
    // to the newest value. GUI and headless/Agent callers share this one path.
    struct PendingControls {
        double centerFreqHz = 0.0;   bool dCenterFreq = false;
        double sampleRateHz = 0.0;   bool dSampleRate = false;
        double gainDb = 0.0;         bool dGain = false;
        QString demodMode;           bool dDemodMode = false;
        double bandwidthHz = 0.0;    bool dBandwidth = false;
        int directSampling = 0;      bool dDirectSampling = false;
        bool offsetTuning = false;   bool dOffsetTuning = false;
        bool rtlAgc = false;         bool dRtlAgc = false;
        bool tunerAgc = false;       bool dTunerAgc = false;
        bool biasTee = false;        bool dBiasTee = false;
        double ppm = 0.0;            bool dPpm = false;
        bool any() const {
            return dCenterFreq || dSampleRate || dGain || dDemodMode ||
                   dBandwidth || dDirectSampling || dOffsetTuning || dRtlAgc ||
                   dTunerAgc || dBiasTee || dPpm;
        }
    };
    QMutex ctrlMutex_;
    PendingControls pending_;
    // Called by run() while holding sourceMutex_: drains pending_ and applies
    // every command to the hardware / engine state on the engine thread.
    void applyControlCommandsLocked();
    // When the engine thread is NOT running (headless unit tests), drain the
    // control mailbox synchronously on the caller's thread so set-then-readback
    // stays consistent (legacy behavior). While running (the real app) this is a
    // no-op: the run() loop drains asynchronously, keeping USB tuning off the UI
    // thread (the reason the mailbox exists).
    void applyIfIdle();

    std::unique_ptr<ISource> source_;
    IQFrontend frontend_;
    VfoManager vfoManager_;
    // Phase55 block3: optional live Doppler control surface (set by MainWindow).
    // Null in headless/test runs -> honest "not available" from the tool layer.
    DopplerControlSurface* dopplerSurface_ = nullptr;
    Squelch squelch_;
    Agc agc_;
    // CTCSS tone detector fed the selected VFO's post-ANR 48 kHz mono audio
    // each block (engine thread). ctcssEnabled_ / ctcssFreqHz_ are the desired
    // state set from any thread; the run loop applies them to ctcss_ there.
    CtcssToneDetector ctcss_;
    std::atomic<bool> ctcssEnabled_{false};
    double ctcssFreqHz_ = tokens::kCtcssToneHzDefault;
    // Speaker-only sub-audio gate desired state (set from any thread; applied
    // in the run loop). When true, the speaker open requires ctcss_.tonePresent().
    std::atomic<bool> ctcssGateEnabled_{false};

    // CDCSS/DCS digital coded squelch, parallel to CTCSS. cdcss_ is fed the same
    // post-ANR 48k mono each block (engine thread). cdcssEnabled_ / cdcssCode_ /
    // cdcssGateEnabled_ are desired state set from any thread; the run loop
    // applies them to cdcss_ there so the Golay/Manchester state is never raced.
    CdcssDecoder cdcss_;
    std::atomic<bool> cdcssEnabled_{false};
    int    cdcssCode_ = 023;                 // octal 023 (default DCS code)
    std::atomic<bool> cdcssGateEnabled_{false};

    // FT8 detection layer (step 3). ft8Detector_ is fed 12k complex baseband by
    // the run loop when enabled; ft8Last_ holds the honest last candidate.
    Ft8Detector ft8Detector_;
    Ft8Codec    ft8Codec_;
    std::atomic<bool> ft8Enabled_{false};
    Ft8Candidate ft8Last_;
    std::string ft8Decoded_;   // 最近一次解码帧文本（空=诚实空态）
    // 15 s 环形缓冲（12 kS/s × 15 s = 180000 复样本）+ 多帧去重状态。
    std::vector<std::complex<float>> ft8Ring_;
    int  ft8RingFilled_ = 0;
    std::atomic<int> ft8DecodedFrames_{0};
    std::string ft8LastDedupKey_;   // 去重键（文本+频偏粗桶）
    AudioOutput* audioOut_ = nullptr;
    // Active 48k write path. Defaults to audioOut_; an injected test sink
    // (setTestAudioSink) redirects it without touching the settings-dialog
    // handle above.
    IAudioSink* audioSink_ = nullptr;
    std::unique_ptr<IAudioSink> testSink_;
    // Parallel network mirror of the demodulated-audio write point (Phase24).
    std::unique_ptr<IAudioSink> networkTap_;
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
    // Frontend software decimator (real decimating low-pass, NCO offset 0).
    Channelizer frontendDecim_;
    std::atomic<int> frontendDecimation_{1};
    double frontendDecimConfiguredSr_ = 0.0;   // rate the decimator was built for
    double lastEffSrForVfo_ = 0.0;           // last rate handed to vfoManager_
    AudioNoiseReduction anr_;
    CWDecoder cwDecoder_;
    ADSBDecoder adsbDecoder_;
    // NOAA APT image decoder. Fed the SELECTED VFO's 48 kHz demodulated baseband
    // audio ONLY while that VFO is WFM. Constructed at the fixed 48 kHz audio
    // rate (every VFO resampler output is 48000 Hz; see vfo_manager.cpp).
    AptDecoder aptDecoder_{48000.0};

    std::atomic<int> fftSize_{2048};
    // Honest measured total-power RSSI (dBFS) of the currently tuned channel,
    // refreshed in run() every block; -100 until the first block flows. The
    // decoupled ScanActivityLink driver polls this (see rssiDbfs()).
    std::atomic<float> rssiDbfs_{-100.0f};
    std::atomic<bool> running_{true};
    std::atomic<bool> needDemodReset_{false};
    // Set by the SpyServerServer via setSpyServerTapRequested: when true, each
    // successfully-read IQ block is re-emitted as iqTapReady (queued copy).
    std::atomic<bool> iqTapRequested_{false};
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

    // POCSAG / m17 / VOR snapshot change-diff state (engine thread only).
    // Mirrors the rdsUpdated pattern: emit only on a real change, and push one
    // honest empty/cleared edge when the user leaves the mode. Counts are the
    // last pushed list sizes; VOR fields mirror the last pushed reading.
    std::size_t lastPocsagCount_ = 0;
    std::size_t lastM17Count_    = 0;
    std::size_t lastAcarsCount_  = 0;
    std::size_t lastNavtexCount_ = 0;
    bool        lastVorLocked_    = false;
    double      lastVorRadialDeg_ = 0.0;
    QString     lastVorMorseId_;

    // Recording options (see setRec* above).
    RecTarget recTarget_ = RecTarget::BasebandIQ;
    QString recTemplate_ = "{time}_{freq}_{mode}";
    bool recStereo_ = false;
    bool recIgnoreSquelch_ = false;
    // Auto-segment length (seconds). 0 = unlimited. Loaded from QSettings
    // rec/max_seg_s, default tokens::kRecMaxSegmentSecondsDefault.
    double recMaxSegSecs_ = 0.0;

    // Last commanded squelch threshold (dBFS), mirrored from setSquelchThreshold
    // so ControlHub can read it back (the Squelch object exposes no getter).
    // Default -50 dBFS matches Squelch::thresholdDb_ / tokens kSquelchDefaultDb.
    float squelchThreshold_ = -50.0f;
    // When set, the run loop re-derives the threshold as tracked audio noise
    // floor + kSquelchAutoMarginDb each block.
    std::atomic<bool> squelchAuto_{false};

    // Expand recTemplate_ against the current source/demod state.
    QString expandRecTemplate() const;
    // True when a live data producer exists (real HW, the synthetic test signal,
    // or an opened offline file). Called with sourceMutex_ held (internal use);
    // the public hasData() takes the lock and delegates here.
    bool hasDataLocked() const;

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

    // Audio-block RMS dBFS noise floor (SAME domain as the Squelch threshold),
    // asymmetrically followed: fast toward quieter, slow toward louder so a
    // real signal never lifts the floor. Used to derive the auto squelch gate.
    // Engine-thread only; read back via audioNoiseFloorDbfs().
    double audioNfDbfs_ = -100.0;
    bool   audioNfInit_ = false;
    QElapsedTimer audioNfEmitClock_;
    qint64        audioNfLastEmitMs_ = -1;

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

    // Lock-free-to-UI device-capability snapshot. The run() loop holds
    // sourceMutex_ for a whole DSP block (readIQ through the chain), so a UI
    // thread calling sourceCapabilities() and taking sourceMutex_ would starve
    // against a tight real-source loop. Instead the engine refreshes this
    // snapshot WHILE holding sourceMutex_, and UI reads go through capsMutex_
    // only -- never contending with the read stream on sourceMutex_.
    mutable QMutex capsMutex_;
    DeviceCapabilities capsSnapshot_;
    // Snapshot of the active source's discrete gain table (tenths-free dB steps).
    // Empty unless a real local RTL-SDR reported rtlsdr_get_tuner_gains. Refreshed
    // together with capsSnapshot_ under capsMutex_.
    std::vector<double> gainTableSnapshot_;
    // Caller must hold sourceMutex_. Copies the live source's capabilities into
    // capsSnapshot_ (guarded by capsMutex_). Cheap; called on source swap + once
    // per loop iteration.
    void updateCapsSnapshotLocked();

    // Lock-free-to-UI VFO snapshot. vfoMarkers()/selectedVfoId() used to take
    // sourceMutex_ directly on the UI thread; because run() holds sourceMutex_
    // across a whole block including the (potentially blocking) source read, a
    // posted refreshVfoUi() MetaCall could stall the UI thread indefinitely and
    // starve heartbeat/snapshot timers. run() now publishes this snapshot while
    // holding sourceMutex_, and UI reads go through vfoMutex_ only.
    mutable QMutex vfoMutex_;
    QVector<VfoMarker> vfoSnapshot_;
    int vfoSelectedSnapshot_ = 0;
    // Caller must hold sourceMutex_ (or be the single-threaded constructor).
    // Copies live VFO markers + selected id into vfoSnapshot_ under vfoMutex_.
    // Cheap; called once at open and after each block's VFO processing.
    void publishVfoSnapshotLocked();

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
    // Explicit opt-in to the synthetic test source. Default false; set true by
    // setTestSourceEnabled(true) or by the MBDSDR_TEST_SOURCE=1 environment
    // variable (read in the constructor; the --test-source CLI flag sets this
    // variable in main.cpp). When a real source is absent, this decides whether
    // the idle source is the synthetic TestSignalSource or the honest empty
    // NullSource. It NEVER auto-synthesizes on the production path.
    std::atomic<bool> testSourceEnabled_{false};
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
    // Caller must hold sourceMutex_. Installs the idle (no-real-device) source:
    // the explicitly-enabled synthetic TestSignalSource, or -- by default -- the
    // honest empty NullSource that produces no IQ. Used after every real-source
    // open / drop / disconnect failure. Emits no sourceChanged itself; callers
    // emit the appropriate event.
    void installIdleSourceLocked();

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
