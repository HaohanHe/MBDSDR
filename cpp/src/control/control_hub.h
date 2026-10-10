// SPDX-License-Identifier: MIT
//
// ControlHub -- the GUI-decoupled headless control layer.
//
// It sits ON TOP of the existing mbdsdr::dsp::SpectrumEngine and exposes a flat,
// named command table that dispatches straight to the engine's already-existing
// slots. It does NOT re-implement any DSP, does NOT open any device, and does
// NOT depend on any QWidget / main-window / settings-dialog header -- the GUI,
// the AI tool loop and (later) a remote/JSON front-end are all just clients of
// this one object.
//
// Design (clean-room; own naming/structure; the read/write split and the gated
// refusal are the SAME philosophy as ai/agent_tools.{h,cpp} but are written
// fresh here):
//
//   * READ/WRITE SPLIT. Every command is declared read or write in the table.
//     Read commands (get_*, list_*, status, telemetry) are ALWAYS allowed, even
//     when the write gate is closed. Write commands (tune/set_*/recording/VFO/
//     scan) are gated by a single master switch setWriteEnabled(...).
//   * HONEST GATE. When the gate is closed a write command is refused with
//     {ok:false, gated:true} and the engine is NEVER touched.
//   * HONEST ERRORS. An unknown command, a missing/typed-wrong argument, or a
//     write with no engine attached returns a structured error string -- it
//     never crashes and never pretends success.
//   * STATE SNAPSHOT. ControlHub subscribes to the engine's async telemetry
//     (sourceTelemetry ~1 Hz, squelchState, recordingStateChanged, sourceChanged)
//     and keeps the latest values. get_telemetry / get_status read that snapshot;
//     when no telemetry has ever arrived (engine not streaming / no hardware)
//     the snapshot is returned as an explicit empty state (telemetry_available=
//     false), never fabricated.
//
// Threading: the engine slots are the same ones the main window calls directly,
// and they are designed to run on the thread that OWNS the SpectrumEngine object
// (the application/GUI thread; the engine's run() loop is a SEPARATE worker
// thread and serialises the DSP work behind sourceMutex_). execute() dispatches
// on that home thread: when called from the home thread it dispatches directly,
// but when a remote/network front-end calls it from a FOREIGN thread it marshals
// the whole engine dispatch onto the engine's home thread with a BLOCKING queued
// call -- preserving the synchronous JSON return while guaranteeing the engine is
// never touched off its designed thread. The telemetry/liveness snapshot is the
// only cross-thread shared state and is guarded by a mutex.
#pragma once

#include <QObject>
#include <QString>
#include <QJsonObject>
#include <QList>
#include <atomic>
#include <memory>
#include <mutex>
#include <vector>

namespace mbdsdr {
namespace dsp {
class SpectrumEngine;
class NetworkAudioSink;   // owned here, handed to the engine as an IAudioSink tap
class ScanActivityLink;   // headless band-scan state machine (start/stop/status)
}
namespace ui { class BookmarkManager; }   // pure data + QSettings persistence

namespace control {

// Latest hardware readback captured from the engine's async signals. This is
// DISTINCT from the control-layer getters (centerFreq() etc. are the *requested*
// values); these fields only become truthful once a real (or synthetic) source
// actually streams and sourceTelemetry has fired at least once.
struct TelemetrySnapshot {
    bool    available = false;   // false until the first sourceTelemetry arrives
    QString sourceName;          // e.g. "RTL-SDR ..." / "Test Signal"
    bool    connected = false;   // real hardware actually streaming
    double  centerHz = 0.0;      // hardware readback of the tuned center
    double  sampleRateHz = 0.0;  // hardware readback of the active rate
    double  gainDb = 0.0;        // hardware readback of the (rounded) gain
    bool    squelchOpen = false; // last squelchState edge
    bool    recording = false;   // last recordingStateChanged edge
    QString recordingPath;       // file currently being written ("" when idle)
    // Honest device-liveness bookkeeping beyond the ~1 Hz telemetry bool. A real
    // reason string only ever arrives from engine sourceError (a connect attempt
    // failed); `dropped` is set by engine sourceDropped when a LIVE device was
    // detected as lost and the engine already fell back. Both are cleared once a
    // real device is actually streaming again (connected=true telemetry) so the
    // reported state is never stale. Neither is ever fabricated.
    QString lastError;           // last sourceError reason ("" = none)
    bool    dropped = false;     // a live device was detected as lost
};

class ControlHub : public QObject {
    Q_OBJECT
public:
    explicit ControlHub(QObject* parent = nullptr);
    ~ControlHub() override;

    // Attach (or detach, with nullptr) the engine. ControlHub does NOT take
    // ownership. Connecting the telemetry signals happens here; calling with a
    // new engine disconnects the previous one.
    void setEngine(dsp::SpectrumEngine* engine);
    dsp::SpectrumEngine* engine() const { return engine_; }

    // Write gate. When disabled, write commands are refused honestly and the
    // engine is never touched; read commands are unaffected. Default comes from
    // tokens::kControlHubWriteEnabledDefault.
    void setWriteEnabled(bool on) { writeEnabled_.store(on); }
    bool writeEnabled() const { return writeEnabled_.load(); }

    // Unified entry point. Returns a compact JSON object as a string:
    //   success: {"ok":true, ...echoed/applied fields...}
    //   gate:    {"ok":false,"gated":true,"error":...}
    //   error:   {"ok":false,"error":...}   (unknown command / bad args / no engine)
    QString execute(const QString& command, const QJsonObject& args);

    // Introspection: every registered command name and its read/write class.
    // Used by tests to assert the table is complete and correctly classified.
    struct CommandInfo { QString name; bool write; };
    QList<CommandInfo> commandTable() const;

    // The current telemetry snapshot (thread-safe copy). Empty until a source
    // actually streams; never fabricated.
    TelemetrySnapshot telemetry() const;

signals:
    // Fired after every execute() dispatch (whether ok, gated or error) so a
    // remote/text front-end can log. Purely observational; ControlHub works
    // fine with no one connected.
    void commandExecuted(const QString& command, bool ok);

private slots:
    void onTelemetry(QString name, bool connected, double centerHz,
                     double sampleRateHz, double gainDb);
    void onSquelchState(bool open);
    void onRecordingState(bool recording, const QString& path);
    // Device-liveness event channel (distinct from the 1 Hz telemetry poll).
    void onSourceError(const QString& message);
    void onSourceDropped();

private:
    using Handler = QJsonObject (ControlHub::*)(const QJsonObject&);
    struct CommandRow { const char* name; bool write; Handler fn; };
    // The named command table (see .cpp). A private static so it may address
    // the private handler member functions; never exposed to clients.
    static const QList<CommandRow>& table();

    // Runs one matched row's handler and emits commandExecuted, returning the
    // compact JSON result. This is the ONLY place that touches the engine, and it
    // is always executed on the engine's HOME thread (see execute()).
    QString dispatch(const CommandRow* row, const QString& command,
                     const QJsonObject& args);

    dsp::SpectrumEngine* engine_ = nullptr;
    std::atomic<bool>   writeEnabled_{true};
    mutable std::mutex  snapMtx_;
    TelemetrySnapshot   snap_;

    // Phase26 owned helpers. Held via unique_ptr so their (heavier) headers stay
    // out of this lightweight header; the dtor is out-of-line in the .cpp.
    std::unique_ptr<ui::BookmarkManager>   bookmarks_;
    // Network-audio tap: ControlHub creates + starts it, then hands ownership to
    // the engine's parallel-write seam (engine_->setNetworkAudioSink). We keep an
    // observing raw pointer for status; the engine destroys it on disable/replace.
    dsp::NetworkAudioSink*                 netTapRaw_ = nullptr;
    std::unique_ptr<dsp::ScanActivityLink> scanLink_;

    // Result builders.
    static QJsonObject okBase();
    static QJsonObject errResult(const QString& error);
    QJsonObject gatedResult(const QString& command) const;

    // Dispatch helpers (each assumes engine_ != nullptr and the gate already
    // passed for writes). Return the structured result.
    QJsonObject cmdTune(const QJsonObject& a);
    QJsonObject cmdSetSampleRate(const QJsonObject& a);
    QJsonObject cmdSetGain(const QJsonObject& a);
    QJsonObject cmdSetMode(const QJsonObject& a);
    QJsonObject cmdSetBandwidth(const QJsonObject& a);
    QJsonObject cmdSetDopplerCompensation(const QJsonObject& a);
    QJsonObject cmdConnectNetworkSource(const QJsonObject& a);
    QJsonObject cmdSetSquelchEnabled(const QJsonObject& a);
    QJsonObject cmdSetSquelchThreshold(const QJsonObject& a);
    QJsonObject cmdSetMuted(const QJsonObject& a);
    QJsonObject cmdStartRecording(const QJsonObject& a);
    QJsonObject cmdStopRecording(const QJsonObject& a);
    // One-shot user export: dump the current (or a tuned) baseband IQ segment to
    // a cf32_le SigMF file, independent of the continuous recorder. WRITE gated.
    QJsonObject cmdExportIqSegment(const QJsonObject& a);
    QJsonObject cmdSetAnr(const QJsonObject& a);
    QJsonObject cmdSetGatedRecording(const QJsonObject& a);
    QJsonObject cmdSetWatch(const QJsonObject& a);
    QJsonObject cmdSetTunerAgc(const QJsonObject& a);
    QJsonObject cmdSetRtlAgc(const QJsonObject& a);
    QJsonObject cmdScanBand(const QJsonObject& a);
    QJsonObject cmdVfoAdd(const QJsonObject& a);
    QJsonObject cmdVfoRemove(const QJsonObject& a);
    QJsonObject cmdVfoSelect(const QJsonObject& a);
    QJsonObject cmdVfoSetFreq(const QJsonObject& a);
    QJsonObject cmdVfoSetOffset(const QJsonObject& a);
    QJsonObject cmdVfoSetBandwidth(const QJsonObject& a);
    QJsonObject cmdVfoSetMode(const QJsonObject& a);
    // set_vfo_armed (write -- gated): index + enabled, aligned 1:1 with the
    // Agent tool set_vfo_armed; keeps a VFO demodulated in the background.
    QJsonObject cmdSetVfoArmed(const QJsonObject& a);
    // POCSAG / m17 / VOR digital output reset (write -- gated): empties the
    // named channel's decode queues and re-inits its decoders. Mirrors the UI
    // panel "clear" button through the same gate as every other write command.
    QJsonObject cmdClearDigitalOutputs(const QJsonObject& a);

    QJsonObject cmdGetFrequency(const QJsonObject&);
    QJsonObject cmdGetMode(const QJsonObject&);
    QJsonObject cmdGetBandwidth(const QJsonObject&);
    QJsonObject cmdGetStatus(const QJsonObject&);
    QJsonObject cmdGetTelemetry(const QJsonObject&);
    QJsonObject cmdListGains(const QJsonObject&);
    QJsonObject cmdGetCapabilities(const QJsonObject&);
    QJsonObject cmdGetVfos(const QJsonObject&);
    QJsonObject cmdGetRecordingState(const QJsonObject&);
    // Read-only decode snapshots (ALWAYS allowed, even with the gate closed).
    // These pull the engine's real accumulated decode output for the current (or
    // the explicitly-named) channel. No data / non-matching mode -> an explicit
    // empty state (empty list / locked=false); nothing is ever fabricated.
    QJsonObject cmdGetPocsagMessages(const QJsonObject&);
    QJsonObject cmdGetM17Calls(const QJsonObject&);
    QJsonObject cmdGetVorRadial(const QJsonObject&);
    QJsonObject cmdGetAcarsPackets(const QJsonObject&);
    QJsonObject cmdGetNavtexMessages(const QJsonObject&);
    // Read-only pass prediction: mirrors the Agent predict_passes tool so the
    // three channels expose the same satellite capability. Pure function over
    // the fresh on-disk TLE cache; never fabricates a pass.
    QJsonObject cmdPredictPasses(const QJsonObject&);

    // ---- Phase26: 21 newly tool-ized capabilities -------------------------
    // Network audio tap (ControlHub owns the sink lifecycle; hands it to the
    // engine's parallel-audio-write seam). WRITE gates enable/disable.
    QJsonObject cmdSetNetworkAudioSink(const QJsonObject& a);
    QJsonObject cmdGetNetworkAudioStatus(const QJsonObject&);
    // Headless band-scan link (ControlHub owns the ScanActivityLink instance;
    // onRetune drives the engine's centre). WRITE gates start/stop.
    QJsonObject cmdStartScanLink(const QJsonObject& a);
    QJsonObject cmdStopScanLink(const QJsonObject&);
    QJsonObject cmdGetScanLinkStatus(const QJsonObject&);
    // Unified squelch set + read-back.
    QJsonObject cmdSetSquelch(const QJsonObject& a);
    QJsonObject cmdGetSquelchStatus(const QJsonObject&);
    // Noise blanker toggle + real read-back (engine exposes both).
    QJsonObject cmdSetNoiseBlanker(const QJsonObject& a);
    QJsonObject cmdGetNoiseBlankerStatus(const QJsonObject&);
    // CTCSS tone-squelch set (enabled required; frequency_hz optional, out-of-
    // domain rejected here, not silently clamped) + real read-back.
    QJsonObject cmdSetCtcss(const QJsonObject& a);
    QJsonObject cmdGetCtcssStatus(const QJsonObject&);
    // CDCSS/DCS digital coded squelch (mirrors CTCSS; code is a 3-digit octal
    // string validated against the public 104-code table).
    QJsonObject cmdSetCdcss(const QJsonObject& a);
    QJsonObject cmdGetCdcssStatus(const QJsonObject&);
    QJsonObject cmdSetFt8(const QJsonObject& a);
    QJsonObject cmdGetFt8Status(const QJsonObject&);
    QJsonObject cmdSetLrpt(const QJsonObject& a);
    QJsonObject cmdGetLrptStatus(const QJsonObject&);
    QJsonObject cmdSetVnaSweep(const QJsonObject&);
    QJsonObject cmdGetVnaData(const QJsonObject&);
    QJsonObject cmdGetVnaStatus(const QJsonObject&);
    // Bookmarks (ControlHub holds a ui::BookmarkManager; same QSettings key).
    QJsonObject cmdListBookmarks(const QJsonObject&);
    QJsonObject cmdAddBookmark(const QJsonObject& a);
    QJsonObject cmdTuneToBookmark(const QJsonObject& a);
    QJsonObject cmdDeleteBookmark(const QJsonObject& a);
    // VFO list / add / switch / rename.
    QJsonObject cmdListVfos(const QJsonObject&);
    QJsonObject cmdAddVfo(const QJsonObject&);
    QJsonObject cmdSwitchVfo(const QJsonObject& a);
    QJsonObject cmdRenameVfo(const QJsonObject& a);
    // Recordings on disk (honest empty state; deletion is path-contained).
    QJsonObject cmdListRecordings(const QJsonObject&);
    QJsonObject cmdDeleteRecording(const QJsonObject& a);
    QJsonObject cmdExportRecording(const QJsonObject& a);
    // FFT / spectrum parameters + waterfall colormap preference.
    QJsonObject cmdSetFftParams(const QJsonObject& a);
    QJsonObject cmdSetColorMap(const QJsonObject& a);
    QJsonObject cmdGetSpectrumStatus(const QJsonObject&);

    // Argument extraction: fills `out` and returns true, else fills `err`.
    static bool needDbl(const QJsonObject& a, const char* key, double& out, QString& err);
    static bool needBool(const QJsonObject& a, const char* key, bool& out, QString& err);
    static bool needInt(const QJsonObject& a, const char* key, int& out, QString& err);
    static bool needMode(const QJsonObject& a, const char* key, QString& out, QString& err);

    // Resolve the optional "channel" argument for the digital read/reset
    // commands. When the arg is absent/null the SELECTED VFO is used; a
    // wrong-typed value is an honest error. Returns the effective channel id,
    // or -1 (with err filled) on a bad argument. Assumes engine_ != nullptr.
    int resolveChannel(const QJsonObject& a, QString& err) const;
};

} // namespace control
} // namespace mbdsdr
