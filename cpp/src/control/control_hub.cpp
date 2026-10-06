// SPDX-License-Identifier: MIT
#include "control/control_hub.h"

#include "dsp/spectrum_engine.h"
#include "dsp/device_capabilities.h"
#include "dsp/vfo_manager.h"
#include "dsp/network_audio_sink.h"
#include "dsp/scan_link.h"
#include "ui/bookmark_manager.h"
#include "core/tokens.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QStringList>
#include <QSettings>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QThread>
#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace control {

// ---------------------------------------------------------------------------
// Table of named commands. Each row maps a headless command straight onto an
// already-existing SpectrumEngine slot / getter -- NO new DSP is introduced.
// `write` is the read/write classification used by the gate; it is declared
// here, on the SAME row as the handler, so the dispatch table and the gate can
// never drift apart (same philosophy as ai/tool_schema.cpp's declarative
// `write` field). Unknown commands are an honest error, never a no-op.
// ---------------------------------------------------------------------------
namespace {

QString compact(const QJsonObject& o) {
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

// Render a decoder's raw byte vector as a bounded JSON byte array. Used for the
// m17 LSF meta / frame payload so the structured snapshot carries the real bytes
// rather than a fabricated summary; empty when the decoder produced nothing.
QJsonArray bytesToJson(const std::vector<uint8_t>& bytes) {
    QJsonArray a;
    for (uint8_t b : bytes) a.append(static_cast<int>(b));
    return a;
}

// Half-window (Hz) either side of the anchor frequency for start_scan_link.
// Operational default for the headless band walk; the step reuses the named
// kFreqStepHz token.
constexpr double kScanHalfWindowHz = 200e3;

// Resolve a bare recording `name` to an absolute path INSIDE `recDir`. Rejects
// absolute paths and any ".." escape honestly (never deletes outside the rec dir).
// Returns an empty string + fills `err` on refusal.
QString safeUnderRecDir(const QString& recDir, const QString& name, QString& err) {
    if (name.isEmpty() || QFileInfo(name).isAbsolute() ||
        name.contains(QStringLiteral(".."))) {
        err = QString::fromUtf8("拒绝：name 必须是 recDir 内的纯文件名（不允许绝对路径或 ..）");
        return {};
    }
    const QString base = QDir::cleanPath(QDir(recDir).absolutePath());
    const QString full = QDir::cleanPath(QDir(base).absoluteFilePath(name));
    if (full != base && !full.startsWith(base + QLatin1Char('/'))) {
        err = QString::fromUtf8("拒绝：解析路径越出 recDir");
        return {};
    }
    return full;
}

} // namespace

// The named command table. Each row maps a headless command straight onto an
// already-existing SpectrumEngine slot / getter -- NO new DSP is introduced.
// `write` is the read/write classification used by the gate; it is declared on
// the SAME row as the handler, so the dispatch table and the gate can never
// drift apart. Unknown commands are an honest error, never a no-op.
const QList<ControlHub::CommandRow>& ControlHub::table() {
    static const QList<CommandRow> kRows = {
        // ---- Write commands (gated) --------------------------------------
        {"tune",                 true,  &ControlHub::cmdTune},
        {"set_sample_rate",      true,  &ControlHub::cmdSetSampleRate},
        {"set_gain",             true,  &ControlHub::cmdSetGain},
        {"set_mode",             true,  &ControlHub::cmdSetMode},
        {"set_bandwidth",        true,  &ControlHub::cmdSetBandwidth},
        {"set_squelch_enabled",  true,  &ControlHub::cmdSetSquelchEnabled},
        {"set_squelch_threshold",true,  &ControlHub::cmdSetSquelchThreshold},
        {"set_muted",            true,  &ControlHub::cmdSetMuted},
        {"start_recording",      true,  &ControlHub::cmdStartRecording},
        {"stop_recording",       true,  &ControlHub::cmdStopRecording},
        {"export_iq_segment",    true,  &ControlHub::cmdExportIqSegment},
        {"set_anr",              true,  &ControlHub::cmdSetAnr},
        {"set_gated_recording",  true,  &ControlHub::cmdSetGatedRecording},
        {"set_watch",            true,  &ControlHub::cmdSetWatch},
        {"set_tuner_agc",        true,  &ControlHub::cmdSetTunerAgc},
        {"set_rtl_agc",          true,  &ControlHub::cmdSetRtlAgc},
        {"scan_band",            true,  &ControlHub::cmdScanBand},
        {"vfo_add",              true,  &ControlHub::cmdVfoAdd},
        {"vfo_remove",           true,  &ControlHub::cmdVfoRemove},
        {"vfo_select",           true,  &ControlHub::cmdVfoSelect},
        {"vfo_set_freq",         true,  &ControlHub::cmdVfoSetFreq},
        {"vfo_set_offset",       true,  &ControlHub::cmdVfoSetOffset},
        {"vfo_set_bandwidth",    true,  &ControlHub::cmdVfoSetBandwidth},
        {"vfo_set_mode",         true,  &ControlHub::cmdVfoSetMode},
        {"clear_digital_outputs",true,  &ControlHub::cmdClearDigitalOutputs},
        // ---- Phase26 new write commands ---------------------------------
        {"set_network_audio_sink", true, &ControlHub::cmdSetNetworkAudioSink},
        {"start_scan_link",       true,  &ControlHub::cmdStartScanLink},
        {"stop_scan_link",        true,  &ControlHub::cmdStopScanLink},
        {"set_squelch",           true,  &ControlHub::cmdSetSquelch},
        {"set_doppler_compensation", true, &ControlHub::cmdSetDopplerCompensation},
        {"connect_network_source",   true, &ControlHub::cmdConnectNetworkSource},
        {"add_bookmark",          true,  &ControlHub::cmdAddBookmark},
        {"tune_to_bookmark",      true,  &ControlHub::cmdTuneToBookmark},
        {"delete_bookmark",       true,  &ControlHub::cmdDeleteBookmark},
        {"add_vfo",               true,  &ControlHub::cmdAddVfo},
        {"switch_vfo",            true,  &ControlHub::cmdSwitchVfo},
        {"rename_vfo",            true,  &ControlHub::cmdRenameVfo},
        {"delete_recording",      true,  &ControlHub::cmdDeleteRecording},
        {"export_recording",      true,  &ControlHub::cmdExportRecording},
        {"set_fft_params",        true,  &ControlHub::cmdSetFftParams},
        {"set_color_map",         true,  &ControlHub::cmdSetColorMap},
        // ---- Read commands (always allowed) ------------------------------
        {"get_frequency",        false, &ControlHub::cmdGetFrequency},
        {"get_mode",             false, &ControlHub::cmdGetMode},
        {"get_bandwidth",        false, &ControlHub::cmdGetBandwidth},
        {"get_status",           false, &ControlHub::cmdGetStatus},
        {"get_telemetry",        false, &ControlHub::cmdGetTelemetry},
        {"list_gains",           false, &ControlHub::cmdListGains},
        {"get_capabilities",     false, &ControlHub::cmdGetCapabilities},
        {"get_vfos",             false, &ControlHub::cmdGetVfos},
        {"get_recording_state",   false, &ControlHub::cmdGetRecordingState},
        {"get_pocsag_messages",   false, &ControlHub::cmdGetPocsagMessages},
        {"get_m17_calls",         false, &ControlHub::cmdGetM17Calls},
        {"get_vor_radial",        false, &ControlHub::cmdGetVorRadial},
        // ---- Phase26 new read commands ----------------------------------
        {"get_network_audio_status", false, &ControlHub::cmdGetNetworkAudioStatus},
        {"get_scan_link_status",  false, &ControlHub::cmdGetScanLinkStatus},
        {"get_squelch_status",    false, &ControlHub::cmdGetSquelchStatus},
        {"list_bookmarks",        false, &ControlHub::cmdListBookmarks},
        {"list_vfos",             false, &ControlHub::cmdListVfos},
        {"list_recordings",       false, &ControlHub::cmdListRecordings},
        {"get_spectrum_status",   false, &ControlHub::cmdGetSpectrumStatus},
    };
    return kRows;
}

ControlHub::ControlHub(QObject* parent)
    : QObject(parent),
      writeEnabled_(tokens::kControlHubWriteEnabledDefault),
      bookmarks_(std::make_unique<ui::BookmarkManager>()),
      scanLink_(std::make_unique<dsp::ScanActivityLink>()) {
    bookmarks_->load();   // QSettings "ui/bookmarks" (empty by design)
    // Bind the scan-link retune seam: when the scanner asks to move, retune the
    // engine centre. The other edges (activity found / dwell ended) are left as
    // no-ops here -- recording/decode arming is a production-policy concern this
    // headless block does not invent.
    dsp::ScanLinkActions acts;
    acts.onRetune = [this](double hz) {
        if (engine_) engine_->onSetCenterFreq(hz);
    };
    scanLink_->setActions(std::move(acts));
}

ControlHub::~ControlHub() = default;

// ---------------------------------------------------------------------------
// Engine attachment + telemetry snapshot
// ---------------------------------------------------------------------------
void ControlHub::setEngine(dsp::SpectrumEngine* engine) {
    if (engine_ == engine) return;
    // Detach previous engine (disconnect every signal routed here).
    if (engine_) QObject::disconnect(engine_, nullptr, this, nullptr);
    engine_ = engine;
    // Reset the snapshot so we never report a stale previous device's values.
    {
        std::lock_guard<std::mutex> lk(snapMtx_);
        snap_ = TelemetrySnapshot{};
    }
    if (engine_) {
        QObject::connect(engine_, &dsp::SpectrumEngine::sourceTelemetry,
                         this, &ControlHub::onTelemetry);
        QObject::connect(engine_, &dsp::SpectrumEngine::squelchState,
                         this, &ControlHub::onSquelchState);
        QObject::connect(engine_, &dsp::SpectrumEngine::recordingStateChanged,
                         this, &ControlHub::onRecordingState);
        // Device-liveness event channel: a failed connect carries a real reason,
        // and sourceDropped tells us a LIVE device was lost (distinct from an
        // idle / manually disconnected state). Both feed get_status honestly.
        QObject::connect(engine_, &dsp::SpectrumEngine::sourceError,
                         this, &ControlHub::onSourceError);
        QObject::connect(engine_, &dsp::SpectrumEngine::sourceDropped,
                         this, &ControlHub::onSourceDropped);
    }
}

void ControlHub::onTelemetry(QString name, bool connected, double centerHz,
                             double sampleRateHz, double gainDb) {
    std::lock_guard<std::mutex> lk(snapMtx_);
    snap_.available = true;
    snap_.sourceName = name;
    snap_.connected = connected;
    snap_.centerHz = centerHz;
    snap_.sampleRateHz = sampleRateHz;
    snap_.gainDb = gainDb;
    // Once a real device is ACTUALLY streaming again, the previous failure/drop
    // is stale: clear it so get_status never reports an old error after recovery.
    if (connected) {
        snap_.lastError.clear();
        snap_.dropped = false;
    }
}

void ControlHub::onSourceError(const QString& message) {
    std::lock_guard<std::mutex> lk(snapMtx_);
    snap_.lastError = message;
    // NOTE: we deliberately do NOT clear `dropped` here. sourceError also fires
    // when an AUTO-RECONNECT retry fails while a live device was previously lost;
    // that retry failure is a *consequence* of the drop, not a fresh connect, so
    // the "was live, now gone" diagnosis must survive. A never-connected manual
    // connect failure simply leaves dropped=false (it was never set). Both are
    // cleared together only once a real device actually streams (connected=true).
}

void ControlHub::onSourceDropped() {
    std::lock_guard<std::mutex> lk(snapMtx_);
    // sourceDropped carries no reason -- record the event but do NOT fabricate a
    // cause (the engine already fell back to the offline/idle source).
    snap_.dropped = true;
}

void ControlHub::onSquelchState(bool open) {
    std::lock_guard<std::mutex> lk(snapMtx_);
    snap_.squelchOpen = open;
}

void ControlHub::onRecordingState(bool recording, const QString& path) {
    std::lock_guard<std::mutex> lk(snapMtx_);
    snap_.recording = recording;
    snap_.recordingPath = path;
}

TelemetrySnapshot ControlHub::telemetry() const {
    std::lock_guard<std::mutex> lk(snapMtx_);
    return snap_;
}

QList<ControlHub::CommandInfo> ControlHub::commandTable() const {
    QList<CommandInfo> out;
    for (const CommandRow& r : table())
        out.append({QString::fromUtf8(r.name), r.write});
    return out;
}

// ---------------------------------------------------------------------------
// Result builders
// ---------------------------------------------------------------------------
QJsonObject ControlHub::okBase() {
    QJsonObject o;
    o["ok"] = true;
    return o;
}

QJsonObject ControlHub::errResult(const QString& error) {
    QJsonObject o;
    o["ok"] = false;
    o["error"] = error;
    return o;
}

QJsonObject ControlHub::gatedResult(const QString& command) const {
    QJsonObject o;
    o["ok"] = false;
    o["gated"] = true;
    o["error"] = QString::fromUtf8("写入被禁止（write gate 关闭）：未执行 %1").arg(command);
    return o;
}

// ---------------------------------------------------------------------------
// Argument extraction helpers (honest about missing / wrong-typed args)
// ---------------------------------------------------------------------------
bool ControlHub::needDbl(const QJsonObject& a, const char* key, double& out, QString& err) {
    const QJsonValue v = a.value(QString::fromUtf8(key));
    if (!v.isDouble()) { err = QString::fromUtf8("缺少数字参数: %1").arg(QString::fromUtf8(key)); return false; }
    out = v.toDouble();
    if (!std::isfinite(out)) { err = QString::fromUtf8("参数非有限数: %1").arg(QString::fromUtf8(key)); return false; }
    return true;
}

bool ControlHub::needBool(const QJsonObject& a, const char* key, bool& out, QString& err) {
    const QJsonValue v = a.value(QString::fromUtf8(key));
    if (!v.isBool()) { err = QString::fromUtf8("缺少布尔参数: %1").arg(QString::fromUtf8(key)); return false; }
    out = v.toBool();
    return true;
}

bool ControlHub::needInt(const QJsonObject& a, const char* key, int& out, QString& err) {
    const QJsonValue v = a.value(QString::fromUtf8(key));
    if (!v.isDouble()) { err = QString::fromUtf8("缺少整数参数: %1").arg(QString::fromUtf8(key)); return false; }
    out = v.toInt();
    return true;
}

bool ControlHub::needMode(const QJsonObject& a, const char* key, QString& out, QString& err) {
    const QJsonValue v = a.value(QString::fromUtf8(key));
    if (!v.isString()) { err = QString::fromUtf8("缺少模式参数: %1").arg(QString::fromUtf8(key)); return false; }
    out = v.toString().toUpper();
    for (int i = 0; i < tokens::kControlHubModesCount; ++i)
        if (out == QLatin1String(tokens::kControlHubModes[i])) return true;
    err = QString::fromUtf8("未知解调模式: %1").arg(v.toString());
    return false;
}

int ControlHub::resolveChannel(const QJsonObject& a, QString& err) const {
    const QJsonValue v = a.value(QStringLiteral("channel"));
    if (v.isUndefined() || v.isNull()) return engine_->selectedVfoId();
    if (!v.isDouble()) {
        err = QString::fromUtf8("参数 channel 必须是整数（VFO 信道 id）");
        return -1;
    }
    return v.toInt();
}

// ---------------------------------------------------------------------------
// Unified entry
// ---------------------------------------------------------------------------
QString ControlHub::execute(const QString& command, const QJsonObject& args) {
    // 1) Look the command up.
    const CommandRow* row = nullptr;
    for (const CommandRow& r : table()) {
        if (command == QLatin1String(r.name)) { row = &r; break; }
    }
    if (!row) {
        QJsonObject o = errResult(QString::fromUtf8("未知命令: %1").arg(command));
        emit commandExecuted(command, false);
        return compact(o);
    }

    // 2) No engine attached: every command is an honest failure (read commands
    //    too -- there is nothing to read). Never fabricate.
    if (!engine_) {
        QJsonObject o = errResult(QString::fromUtf8("无引擎连接（ControlHub 未 attach SpectrumEngine）"));
        emit commandExecuted(command, false);
        return compact(o);
    }

    // 3) Write gate: write commands are refused when the gate is closed.
    if (row->write && !writeEnabled_.load()) {
        QJsonObject o = gatedResult(command);
        emit commandExecuted(command, false);
        return compact(o);
    }

    // 4) Dispatch. Handlers return their own result object; argument errors
    //    surface as {ok:false, error:...}.
    //
    //    THREADING: the engine slots are designed to run on the thread that owns
    //    the SpectrumEngine object (its home thread -- the application/GUI thread
    //    in the app, the test thread here). When execute() arrives on that home
    //    thread we dispatch directly (zero overhead, identical to a main-window
    //    call). When a remote/network front-end calls us on a FOREIGN thread we
    //    must NOT call those slots here on the caller's thread: it would race the
    //    engine run() loop and, for the non-locked setters (squelch/mute/watch/
    //    gated-recording), introduce a second writer. Instead we marshal the whole
    //    dispatch onto the engine's home thread with a BLOCKING queued call, which
    //    preserves the synchronous JSON return contract while guaranteeing the
    //    engine is only ever touched from its designed thread. (ControlHub is
    //    created on -- and never moved off -- that same home thread, so invoking
    //    on `this` lands exactly on engine_->thread().)
    dsp::SpectrumEngine* eng = engine_;
    const bool onHome = (QThread::currentThread() == eng->thread());
    if (onHome) {
        return dispatch(row, command, args);
    }
    QString result;
    QMetaObject::invokeMethod(this,
        [this, row, command, args, &result]() {
            result = dispatch(row, command, args);
        },
        Qt::BlockingQueuedConnection);
    return result;
}

// Matches the row's handler, runs it (on the engine's home thread), emits the
// observational commandExecuted, and returns the compact JSON result.
QString ControlHub::dispatch(const CommandRow* row, const QString& command,
                             const QJsonObject& args) {
    QJsonObject res = (this->*(row->fn))(args);
    emit commandExecuted(command, res.value("ok").toBool());
    return compact(res);
}

// ===========================================================================
// Write commands
// ===========================================================================
QJsonObject ControlHub::cmdTune(const QJsonObject& a) {
    double f; QString err;
    if (!needDbl(a, "freq_hz", f, err)) return errResult(err);
    // Elastic clamp to the hardware token range; report the effective value.
    const double eff = std::clamp(f, tokens::kFreqMinHz, tokens::kFreqMaxHz);
    engine_->onSetCenterFreq(eff);
    QJsonObject o = okBase();
    o["command"] = "tune";
    o["frequency_hz"] = eff;
    o["clamped"] = (eff != f);
    return o;
}

QJsonObject ControlHub::cmdSetSampleRate(const QJsonObject& a) {
    double sr; QString err;
    if (!needDbl(a, "rate_hz", sr, err)) return errResult(err);
    engine_->onSetSampleRate(sr);
    QJsonObject o = okBase();
    o["command"] = "set_sample_rate";
    o["sample_rate_hz"] = sr;
    return o;
}

QJsonObject ControlHub::cmdSetGain(const QJsonObject& a) {
    double g; QString err;
    if (!needDbl(a, "gain_db", g, err)) return errResult(err);
    const double eff = std::clamp(g, tokens::kGainMinDb, tokens::kGainMaxDb);
    engine_->onSetGain(eff);
    QJsonObject o = okBase();
    o["command"] = "set_gain";
    o["gain_db"] = eff;
    o["clamped"] = (eff != g);
    return o;
}

QJsonObject ControlHub::cmdSetMode(const QJsonObject& a) {
    QString m; QString err;
    if (!needMode(a, "mode", m, err)) return errResult(err);
    engine_->setDemodMode(m);
    QJsonObject o = okBase();
    o["command"] = "set_mode";
    o["mode"] = m;
    return o;
}

QJsonObject ControlHub::cmdSetBandwidth(const QJsonObject& a) {
    double bw; QString err;
    if (!needDbl(a, "bandwidth_hz", bw, err)) return errResult(err);
    engine_->setBandwidth(bw);
    QJsonObject o = okBase();
    o["command"] = "set_bandwidth";
    o["bandwidth_hz"] = bw;
    return o;
}

// Phase55 block3: toggle live Doppler compensation via the UI control surface.
// No surface (headless/test) -> honest unavailable; the UI re-checks the
// station/capture preconditions on the way through.
QJsonObject ControlHub::cmdSetDopplerCompensation(const QJsonObject& a) {
    dsp::DopplerControlSurface* surf = engine_->dopplerControlSurface();
    QJsonObject o = okBase();
    o["command"] = "set_doppler_compensation";
    if (!surf) {
        o["ok"] = false;
        o["available"] = false;
        o["error"] = QString::fromUtf8("多普勒补偿不可用（无 UI 控制面）");
        return o;
    }
    const bool on = a.value("enable").toBool(false);
    surf->setDopplerCompensationEnabled(on);
    o["available"] = surf->isDopplerCompensationAvailable();
    o["enabled"] = surf->isDopplerCompensationEnabled();
    return o;
}

// Phase58 block2: connect to an rtl_tcp network source. Real TCP handshake +
// RTL0 header; on failure the engine emits the honest socket reason.
QJsonObject ControlHub::cmdConnectNetworkSource(const QJsonObject& a) {
    const QString host = a.value("host").toString();
    if (host.isEmpty()) return errResult(QString::fromUtf8("缺少 host 参数"));
    const int port = static_cast<int>(a.value("port").toDouble(1234.0));
    QJsonObject o = okBase();
    o["command"] = "connect_network_source";
    const bool ok = engine_->connectRtlTcp(host, static_cast<quint16>(port));
    o["ok"] = ok;
    o["host"] = host;
    o["port"] = port;
    return o;
}

QJsonObject ControlHub::cmdSetSquelchEnabled(const QJsonObject& a) {
    bool e; QString err;
    if (!needBool(a, "enabled", e, err)) return errResult(err);
    engine_->setSquelchEnabled(e);
    QJsonObject o = okBase();
    o["command"] = "set_squelch_enabled";
    o["enabled"] = e;
    return o;
}

QJsonObject ControlHub::cmdSetSquelchThreshold(const QJsonObject& a) {
    double db; QString err;
    if (!needDbl(a, "threshold_db", db, err)) return errResult(err);
    const float eff = static_cast<float>(
        std::clamp(db, double(tokens::kSquelchMinDb), double(tokens::kSquelchMaxDb)));
    engine_->setSquelchThreshold(eff);
    QJsonObject o = okBase();
    o["command"] = "set_squelch_threshold";
    o["threshold_db"] = eff;
    return o;
}

QJsonObject ControlHub::cmdSetMuted(const QJsonObject& a) {
    bool m; QString err;
    if (!needBool(a, "muted", m, err)) return errResult(err);
    engine_->setMuted(m);
    QJsonObject o = okBase();
    o["command"] = "set_muted";
    o["muted"] = m;
    return o;
}

QJsonObject ControlHub::cmdStartRecording(const QJsonObject&) {
    const bool ok = engine_->startRecording();   // honest bool from the recorder
    QJsonObject o;
    o["ok"] = ok;
    if (ok) {
        o["command"] = "start_recording";
        o["path"] = engine_->recordingPath();
    } else {
        o["error"] = QString::fromUtf8("录制启动失败");
    }
    return o;
}

QJsonObject ControlHub::cmdStopRecording(const QJsonObject&) {
    engine_->stopRecording();
    QJsonObject o = okBase();
    o["command"] = "stop_recording";
    return o;
}

// One-shot IQ export. Writes a file to disk (WRITE, gated). Pulls ~sample_count
// complex baseband IQ; tune_hz<0 (default) keeps the current centre, a >=0 value
// parks the source there first. No source data -> an honest error, never a fake
// file. On success the real written path / size / sample count are returned.
QJsonObject ControlHub::cmdExportIqSegment(const QJsonObject& a) {
    int sampleCount = static_cast<int>(
        a.value(QStringLiteral("sample_count")).toDouble(65536.0));
    // tune_hz is optional; -1 = keep the current centre. A present-but-wrongly-
    // typed value is an honest error (toDouble would silently retune to 0).
    double tuneHz = -1.0;
    const QJsonValue tv = a.value(QStringLiteral("tune_hz"));
    if (tv.isDouble()) {
        tuneHz = tv.toDouble();
        if (!std::isfinite(tuneHz))
            return errResult(QString::fromUtf8("参数非有限数: tune_hz"));
    } else if (!(tv.isUndefined() || tv.isNull())) {
        return errResult(QString::fromUtf8("参数 tune_hz 必须是数字（Hz，缺省=保持当前中心）"));
    }

    QString path; double sr = 0.0, center = 0.0;
    qint64 samples = 0, bytes = 0; QString err;
    const bool ok = engine_->exportIqSegment(sampleCount, tuneHz, path,
                                             sr, center, samples, bytes, err);
    QJsonObject o;
    o["ok"] = ok;
    if (ok) {
        o["command"] = "export_iq_segment";
        o["path"] = path;
        o["sample_rate_hz"] = sr;
        o["center_hz"] = center;
        o["samples"] = samples;
        o["bytes"] = bytes;
    } else {
        o["error"] = err;
    }
    return o;
}

QJsonObject ControlHub::cmdSetAnr(const QJsonObject& a) {
    bool on; QString err;
    if (!needBool(a, "enabled", on, err)) return errResult(err);
    engine_->setAnrEnabled(on);
    QJsonObject o = okBase();
    o["command"] = "set_anr";
    o["enabled"] = on;
    return o;
}

QJsonObject ControlHub::cmdSetGatedRecording(const QJsonObject& a) {
    bool on; QString err;
    if (!needBool(a, "enabled", on, err)) return errResult(err);
    engine_->setGatedRecordingEnabled(on);
    QJsonObject o = okBase();
    o["command"] = "set_gated_recording";
    o["enabled"] = on;
    return o;
}

QJsonObject ControlHub::cmdSetWatch(const QJsonObject& a) {
    bool on; QString err;
    if (!needBool(a, "enabled", on, err)) return errResult(err);
    engine_->setWatchEnabled(on);
    QJsonObject o = okBase();
    o["command"] = "set_watch";
    o["enabled"] = on;
    return o;
}

QJsonObject ControlHub::cmdSetTunerAgc(const QJsonObject& a) {
    bool on; QString err;
    if (!needBool(a, "enabled", on, err)) return errResult(err);
    engine_->setTunerAgc(on);
    QJsonObject o = okBase();
    o["command"] = "set_tuner_agc";
    o["enabled"] = on;
    return o;
}

QJsonObject ControlHub::cmdSetRtlAgc(const QJsonObject& a) {
    bool on; QString err;
    if (!needBool(a, "enabled", on, err)) return errResult(err);
    engine_->setRtlAgc(on);
    QJsonObject o = okBase();
    o["command"] = "set_rtl_agc";
    o["enabled"] = on;
    return o;
}

QJsonObject ControlHub::cmdScanBand(const QJsonObject& a) {
    double low, high, step; QString err;
    if (!needDbl(a, "low_hz", low, err)) return errResult(err);
    if (!needDbl(a, "high_hz", high, err)) return errResult(err);
    step = a.value(QStringLiteral("step_hz")).toDouble(200000.0);
    if (step < tokens::kControlHubScanStepMinHz)
        return errResult(QString::fromUtf8("step_hz 过小（< %1 Hz）")
                             .arg(tokens::kControlHubScanStepMinHz));
    double peakFreq = 0.0;
    const double peakDbfs = engine_->scanBand(low, high, step, &peakFreq);
    QJsonObject hit;
    hit["frequency_hz"] = peakFreq;
    hit["dbfs"] = peakDbfs;
    QJsonObject o = okBase();
    o["command"] = "scan_band";
    o["low_hz"] = low;
    o["high_hz"] = high;
    o["step_hz"] = step;
    o["hits"] = QJsonArray{hit};
    // Honest provenance: on the offline test signal the "peak" is a fixed
    // baseband tone, not a real station hit.
    if (engine_->isTestSignalActive()) {
        o["synthetic"] = true;
        o["note"] = QString::fromUtf8("合成测试信号扫描：峰值为固定基带偏音，非真实电台命中");
    }
    return o;
}

QJsonObject ControlHub::cmdVfoAdd(const QJsonObject&) {
    engine_->vfoAdd();
    QJsonObject o = okBase();
    o["command"] = "vfo_add";
    o["selected_vfo_id"] = engine_->selectedVfoId();
    return o;
}

QJsonObject ControlHub::cmdVfoRemove(const QJsonObject& a) {
    int id; QString err;
    if (!needInt(a, "id", id, err)) return errResult(err);
    engine_->vfoRemove(id);
    QJsonObject o = okBase();
    o["command"] = "vfo_remove";
    o["removed_id"] = id;
    return o;
}

QJsonObject ControlHub::cmdVfoSelect(const QJsonObject& a) {
    int id; QString err;
    if (!needInt(a, "id", id, err)) return errResult(err);
    engine_->vfoSelect(id);
    QJsonObject o = okBase();
    o["command"] = "vfo_select";
    o["selected_vfo_id"] = engine_->selectedVfoId();
    return o;
}

QJsonObject ControlHub::cmdVfoSetFreq(const QJsonObject& a) {
    int id; double hz; QString err;
    if (!needInt(a, "id", id, err)) return errResult(err);
    if (!needDbl(a, "freq_hz", hz, err)) return errResult(err);
    engine_->vfoSetFreq(id, hz);
    QJsonObject o = okBase();
    o["command"] = "vfo_set_freq";
    o["id"] = id;
    o["freq_hz"] = hz;
    return o;
}

QJsonObject ControlHub::cmdVfoSetOffset(const QJsonObject& a) {
    int id; double hz; QString err;
    if (!needInt(a, "id", id, err)) return errResult(err);
    if (!needDbl(a, "freq_hz", hz, err)) return errResult(err);
    const bool retuned = engine_->vfoSetOffset(id, hz);
    QJsonObject o = okBase();
    o["command"] = "vfo_set_offset";
    o["id"] = id;
    o["freq_hz"] = hz;
    o["tuner_retuned"] = retuned;   // true only when the target crossed the band edge
    return o;
}

QJsonObject ControlHub::cmdVfoSetBandwidth(const QJsonObject& a) {
    int id; double hz; QString err;
    if (!needInt(a, "id", id, err)) return errResult(err);
    if (!needDbl(a, "bandwidth_hz", hz, err)) return errResult(err);
    engine_->vfoSetBandwidth(id, hz);
    QJsonObject o = okBase();
    o["command"] = "vfo_set_bandwidth";
    o["id"] = id;
    o["bandwidth_hz"] = hz;
    return o;
}

QJsonObject ControlHub::cmdVfoSetMode(const QJsonObject& a) {
    int id; QString m; QString err;
    if (!needInt(a, "id", id, err)) return errResult(err);
    if (!needMode(a, "mode", m, err)) return errResult(err);
    engine_->vfoSetMode(id, m);
    QJsonObject o = okBase();
    o["command"] = "vfo_set_mode";
    o["id"] = id;
    o["mode"] = m;
    return o;
}

QJsonObject ControlHub::cmdClearDigitalOutputs(const QJsonObject& a) {
    QString err;
    const int ch = resolveChannel(a, err);
    if (ch < 0) return errResult(err);
    engine_->clearDigitalOutputs(ch);
    QJsonObject o = okBase();
    o["command"] = "clear_digital_outputs";
    o["channel"] = ch;
    return o;
}

// ===========================================================================
// Read commands (always allowed, even with the write gate closed)
// ===========================================================================
QJsonObject ControlHub::cmdGetFrequency(const QJsonObject&) {
    QJsonObject o = okBase();
    o["frequency_hz"] = engine_->centerFreq();
    return o;
}

QJsonObject ControlHub::cmdGetMode(const QJsonObject&) {
    QJsonObject o = okBase();
    o["mode"] = engine_->demodMode();
    return o;
}

QJsonObject ControlHub::cmdGetBandwidth(const QJsonObject&) {
    QJsonObject o = okBase();
    o["bandwidth_hz"] = engine_->bandwidth();
    return o;
}

QJsonObject ControlHub::cmdGetStatus(const QJsonObject&) {
    TelemetrySnapshot snap = telemetry();
    QJsonObject o = okBase();
    o["command"] = "get_status";
    // Control-layer (requested/applied) values -- readable even before streaming.
    o["frequency_hz"] = engine_->centerFreq();
    o["mode"] = engine_->demodMode();
    o["bandwidth_hz"] = engine_->bandwidth();
    // Phase55 block2: Costas carrier-lock snapshot of the selected VFO. On an
    // analog channel the engine returns an honest all-false lock status.
    const dsp::DigitalLockStatus lock = engine_->digitalLockStatus();
    o["carrier_locked"] = lock.carrierLocked;
    o["symbol_locked"] = lock.symbolLocked;
    o["evm_percent"] = lock.evmPercent;
    // Phase55 block3: live Doppler compensation state via the UI control surface.
    dsp::DopplerControlSurface* dsurf = engine_->dopplerControlSurface();
    o["doppler_available"] = dsurf && dsurf->isDopplerCompensationAvailable();
    o["doppler_enabled"] = dsurf && dsurf->isDopplerCompensationEnabled();
    // Hardware readback -- only truthful once telemetry has actually arrived.
    o["telemetry_available"] = snap.available;
    o["connected"] = snap.available && snap.connected;
    o["source"] = snap.available ? snap.sourceName
                                 : QString::fromUtf8("无遥测（未连接硬件或引擎未运行）");
    // Honest coarse status derived from the telemetry bool + the liveness event
    // channel. Every state is reachable on real hardware:
    //   no_telemetry -- engine not running / no source has ever streamed;
    //   connected    -- a real device is streaming right now;
    //   dropped      -- a live device was lost (unplugged / link dropped; the
    //                   auto-reconnect retry may still be failing in the
    //                   background, and error_message then carries that retry's
    //                   reason -- but the root event is the drop);
    //   error        -- a connect attempt failed while NO live device was ever
    //                   attached (error_message = the real reason);
    //   disconnected -- idle / manually disconnected / offline fallback.
    QString status;
    if (!snap.available)                     status = QStringLiteral("no_telemetry");
    else if (snap.connected)                 status = QStringLiteral("connected");
    else if (snap.dropped)                   status = QStringLiteral("dropped");
    else if (!snap.lastError.isEmpty())      status = QStringLiteral("error");
    else                                     status = QStringLiteral("disconnected");
    o["status"] = status;
    // error_message carries the REAL reason from sourceError ("" when none). It is
    // cleared automatically once a real device streams again, so it is never stale.
    o["error_message"] = snap.lastError;
    if (snap.available) {
        o["readback_center_hz"] = snap.centerHz;
        o["readback_sample_rate_hz"] = snap.sampleRateHz;
        o["readback_gain_db"] = snap.gainDb;
    }
    return o;
}

QJsonObject ControlHub::cmdGetTelemetry(const QJsonObject&) {
    TelemetrySnapshot snap = telemetry();
    QJsonObject o = okBase();
    o["command"] = "get_telemetry";
    o["telemetry_available"] = snap.available;
    if (!snap.available) {
        // Honest empty state: no source has streamed yet.
        o["note"] = QString::fromUtf8("尚未收到 sourceTelemetry：引擎未运行或无硬件连接");
        return o;
    }
    o["source"] = snap.sourceName;
    o["connected"] = snap.connected;
    o["error_message"] = snap.lastError;
    o["dropped"] = snap.dropped;
    o["center_hz"] = snap.centerHz;
    o["sample_rate_hz"] = snap.sampleRateHz;
    o["gain_db"] = snap.gainDb;
    o["squelch_open"] = snap.squelchOpen;
    o["recording"] = snap.recording;
    o["recording_path"] = snap.recordingPath;
    return o;
}

QJsonObject ControlHub::cmdListGains(const QJsonObject&) {
    // Honest empty state: test/file sources return an empty table -- we must NOT
    // fabricate gain steps.
    std::vector<double> gains = engine_->availableGainsDb();
    QJsonArray arr;
    for (double g : gains) arr.append(g);
    QJsonObject o = okBase();
    o["command"] = "list_gains";
    o["gains_db"] = arr;
    o["count"] = static_cast<int>(gains.size());
    return o;
}

QJsonObject ControlHub::cmdGetCapabilities(const QJsonObject&) {
    dsp::DeviceCapabilities c = engine_->sourceCapabilities();
    QJsonObject o = okBase();
    o["command"] = "get_capabilities";
    o["connected"] = c.connected;
    o["device_name"] = c.deviceName;
    o["tunable_min_hz"] = c.tunableMinHz;
    o["tunable_max_hz"] = c.tunableMaxHz;
    o["sample_rate_min_hz"] = c.sampleRateMinHz;
    o["sample_rate_max_hz"] = c.sampleRateMaxHz;
    return o;
}

QJsonObject ControlHub::cmdGetVfos(const QJsonObject&) {
    QVector<dsp::VfoMarker> markers = engine_->vfoMarkers();
    QJsonArray arr;
    for (const dsp::VfoMarker& m : markers) {
        QJsonObject v;
        v["id"] = m.id;
        v["name"] = m.name;
        v["freq_hz"] = m.freqHz;
        v["bandwidth_hz"] = m.bandwidthHz;
        v["mode"] = m.mode;
        v["selected"] = m.selected;
        v["center_offset_hz"] = m.centerOffsetHz;
        arr.append(v);
    }
    QJsonObject o = okBase();
    o["command"] = "get_vfos";
    o["selected_vfo_id"] = engine_->selectedVfoId();
    o["vfos"] = arr;
    return o;
}

QJsonObject ControlHub::cmdGetRecordingState(const QJsonObject&) {
    TelemetrySnapshot snap = telemetry();
    QJsonObject o = okBase();
    o["command"] = "get_recording_state";
    // The engine always knows the current recording path; the recording bool
    // comes from the recordingStateChanged signal (false until it fires).
    o["recording"] = snap.recording;
    o["path"] = engine_->recordingPath();
    return o;
}

QJsonObject ControlHub::cmdGetPocsagMessages(const QJsonObject& a) {
    QString err;
    const int ch = resolveChannel(a, err);
    if (ch < 0) return errResult(err);
    // Real accumulated decode output of the named channel; empty list when the
    // channel does not exist / is not POCSAG / nothing decoded yet -- never fake.
    std::vector<dsp::PocsagMessage> msgs = engine_->pocsagMessages(ch);
    QJsonArray arr;
    for (const dsp::PocsagMessage& m : msgs) {
        QJsonObject o;
        o["address"] = static_cast<double>(m.address);
        o["function"] = m.function;
        o["text"] = QString::fromStdString(m.text);
        const char* t = "unknown";
        switch (m.type) {
            case dsp::PocsagMessage::Type::Numeric: t = "numeric"; break;
            case dsp::PocsagMessage::Type::Alpha:  t = "alpha";    break;
            default: break;
        }
        o["type"] = QString::fromUtf8(t);
        arr.append(o);
    }
    QJsonObject r = okBase();
    r["command"] = "get_pocsag_messages";
    r["channel"] = ch;
    r["messages"] = arr;
    r["count"] = static_cast<int>(msgs.size());
    return r;
}

QJsonObject ControlHub::cmdGetM17Calls(const QJsonObject& a) {
    QString err;
    const int ch = resolveChannel(a, err);
    if (ch < 0) return errResult(err);
    std::vector<dsp::M17Call> calls = engine_->m17Calls(ch);
    QJsonArray arr;
    for (const dsp::M17Call& c : calls) {
        QJsonObject o;
        o["src"] = QString::fromStdString(c.src);
        o["dst"] = QString::fromStdString(c.dst);
        o["type"] = QString::asprintf("0x%04X", static_cast<unsigned>(c.type));
        o["is_stream"] = c.isStream;
        o["payload_class"] = c.payloadClass;
        o["frame_kind"] = c.frameKind;
        o["crc_ok"] = c.crcOk;
        o["viterbi_cost"] = c.viterbiCost;
        // Codec2 is not bundled: voice streams are reported as metadata only,
        // honestly flagged so a client never expects decoded speech.
        o["voice_undecoded"] = c.voiceUndecoded;
        o["meta"] = bytesToJson(c.meta);
        o["payload"] = bytesToJson(c.payload);
        arr.append(o);
    }
    QJsonObject r = okBase();
    r["command"] = "get_m17_calls";
    r["channel"] = ch;
    r["calls"] = arr;
    r["count"] = static_cast<int>(calls.size());
    return r;
}

QJsonObject ControlHub::cmdGetVorRadial(const QJsonObject& a) {
    QString err;
    const int ch = resolveChannel(a, err);
    if (ch < 0) return errResult(err);
    dsp::VorResult v = engine_->vorResult(ch);
    QJsonObject r = okBase();
    r["command"] = "get_vor_radial";
    r["channel"] = ch;
    // locked=false is the honest no-lock state: a client must ignore radialDeg.
    r["locked"] = v.locked;
    r["radial_deg"] = v.radialDeg;
    r["quality"] = v.quality;
    r["morse_id"] = v.morseId;
    return r;
}

// ===========================================================================
// Phase26: 21 newly tool-ized capabilities
// ===========================================================================
QJsonObject ControlHub::cmdSetNetworkAudioSink(const QJsonObject& a) {
    bool enable; QString err;
    if (!needBool(a, "enable", enable, err)) return errResult(err);
    if (!enable) {
        engine_->setNetworkAudioSink(nullptr);   // detaches + destroys the tap
        netTapRaw_ = nullptr;
        QJsonObject o = okBase();
        o["command"] = "set_network_audio_sink";
        o["enabled"] = false;
        return o;
    }
    int port;
    if (!needInt(a, "port", port, err)) return errResult(err);
    if (port <= 0 || port > 65535)
        return errResult(QString::fromUtf8("port 越界 (1..65535): %1").arg(port));
    const QString fmt = a.value(QStringLiteral("format"))
                            .toString(QStringLiteral("udp")).toLower();
    dsp::NetAudioProtocol proto = dsp::NetAudioProtocol::UDP;
    if (fmt == QLatin1String("tcp"))      proto = dsp::NetAudioProtocol::TCP;
    else if (fmt != QLatin1String("udp"))
        return errResult(QString::fromUtf8("未知 format（udp/tcp）: %1").arg(fmt));
    const bool stereo = a.value(QStringLiteral("stereo")).toBool(false);
    const QString host = a.value(QStringLiteral("host"))
                             .toString(QStringLiteral("127.0.0.1"));

    auto s = std::make_unique<dsp::NetworkAudioSink>();
    if (!s->start(host.toStdString(), static_cast<uint16_t>(port), proto, stereo)) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromStdString(s->lastError());
        return o;
    }
    netTapRaw_ = s.get();
    engine_->setNetworkAudioSink(std::unique_ptr<dsp::IAudioSink>(std::move(s)));
    QJsonObject o = okBase();
    o["command"] = "set_network_audio_sink";
    o["enabled"] = true;
    o["port"] = port;
    o["format"] = fmt;
    o["stereo"] = stereo;
    return o;
}

QJsonObject ControlHub::cmdGetNetworkAudioStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_network_audio_status";
    const bool active = (netTapRaw_ != nullptr);
    o["enabled"] = active;
    if (!active) {
        o["note"] = QString::fromUtf8("网络音频流未开启");
        return o;
    }
    o["port"] = static_cast<int>(netTapRaw_->actualPort());
    o["protocol"] = netTapRaw_->protocol() == dsp::NetAudioProtocol::TCP
                        ? QStringLiteral("tcp") : QStringLiteral("udp");
    o["client_connected"] = netTapRaw_->clientConnected();
    o["bytes_sent"] = static_cast<qint64>(netTapRaw_->bytesSent());
    o["frames_dropped"] = static_cast<qint64>(netTapRaw_->framesDropped());
    o["last_error"] = QString::fromStdString(netTapRaw_->lastError());
    return o;
}

QJsonObject ControlHub::cmdStartScanLink(const QJsonObject& a) {
    double center; QString err;
    if (!needDbl(a, "target_freq_hz", center, err)) return errResult(err);
    dsp::ScanConfig cfg;
    cfg.source = dsp::ScanSource::Range;
    cfg.startHz = center - kScanHalfWindowHz;
    cfg.stopHz  = center + kScanHalfWindowHz;
    cfg.stepHz  = tokens::kFreqStepHz;
    cfg.thresholdDb = static_cast<float>(tokens::kSquelchDefaultDb);
    scanLink_->setConfig(cfg);
    scanLink_->start();
    QJsonObject o = okBase();
    o["command"] = "start_scan_link";
    o["target_freq_hz"] = center;
    o["status"] = QString::fromUtf8("scanning");
    return o;
}

QJsonObject ControlHub::cmdStopScanLink(const QJsonObject&) {
    scanLink_->stop();
    QJsonObject o = okBase();
    o["command"] = "stop_scan_link";
    o["status"] = QString::fromUtf8("idle");
    return o;
}

QJsonObject ControlHub::cmdGetScanLinkStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_scan_link_status";
    const dsp::ScanLinkState st = scanLink_->state();
    const char* s = "idle";
    if (st == dsp::ScanLinkState::Scanning)      s = "scanning";
    else if (st == dsp::ScanLinkState::Dwell)    s = "dwell";
    o["status"] = QString::fromUtf8(s);
    o["dwell_count"] = scanLink_->dwellCount();
    o["parked_freq_hz"] = scanLink_->parkedFrequency();
    o["retune_count"] = scanLink_->retuneLog().size();
    return o;
}

QJsonObject ControlHub::cmdSetSquelch(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "set_squelch";
    bool has = false;
    const QJsonValue en = a.value(QStringLiteral("enabled"));
    if (en.isBool()) { engine_->setSquelchEnabled(en.toBool()); o["enabled"] = en.toBool(); has = true; }
    const QJsonValue th = a.value(QStringLiteral("threshold_db"));
    if (th.isDouble()) {
        const double eff = std::clamp(th.toDouble(),
                                      double(tokens::kSquelchMinDb),
                                      double(tokens::kSquelchMaxDb));
        engine_->setSquelchThreshold(static_cast<float>(eff));
        o["threshold_db"] = eff;
        has = true;
    }
    const QJsonValue au = a.value(QStringLiteral("auto"));
    if (au.isBool()) { engine_->setSquelchAuto(au.toBool()); o["auto"] = au.toBool(); has = true; }
    if (!has)
        return errResult(QString::fromUtf8("set_squelch 需要至少一个参数: enabled/threshold_db/auto"));
    return o;
}

QJsonObject ControlHub::cmdGetSquelchStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_squelch_status";
    o["enabled"] = engine_->squelchEnabled();
    o["threshold_db"] = engine_->squelchThresholdDb();
    o["auto"] = engine_->squelchAuto();
    o["open"] = engine_->squelchOpen();
    return o;
}

QJsonObject ControlHub::cmdListBookmarks(const QJsonObject&) {
    QJsonArray arr;
    for (const ui::Bookmark& b : bookmarks_->list()) {
        QJsonObject v;
        v["name"] = b.name;
        v["freq_hz"] = b.frequencyHz;
        v["mode"] = b.mode;
        v["bandwidth_hz"] = b.bandwidthHz;
        v["group"] = b.group;
        arr.append(v);
    }
    QJsonObject o = okBase();
    o["command"] = "list_bookmarks";
    o["bookmarks"] = arr;
    o["count"] = arr.size();
    return o;
}

QJsonObject ControlHub::cmdAddBookmark(const QJsonObject& a) {
    double f; QString err;
    if (!needDbl(a, "freq_hz", f, err)) return errResult(err);
    ui::Bookmark b;
    b.frequencyHz = f;
    b.name = a.value(QStringLiteral("name")).toString(
        QString::number(f / 1e6, 'f', 3));
    b.mode = a.value(QStringLiteral("mode")).toString();
    b.bandwidthHz = a.value(QStringLiteral("bandwidth_hz")).toDouble(0.0);
    const int idx = bookmarks_->add(b);   // auto-saves to QSettings
    if (idx < 0)
        return errResult(QString::fromUtf8("freq_hz 必须 >0"));
    QJsonObject o = okBase();
    o["command"] = "add_bookmark";
    o["index"] = idx;
    o["freq_hz"] = f;
    return o;
}

QJsonObject ControlHub::cmdTuneToBookmark(const QJsonObject& a) {
    int idx; QString err;
    if (!needInt(a, "index", idx, err)) return errResult(err);
    if (idx < 0 || idx >= bookmarks_->count())
        return errResult(QString::fromUtf8("书签下标越界: %1").arg(idx));
    const ui::Bookmark b = bookmarks_->list().at(idx);
    engine_->onSetCenterFreq(b.frequencyHz);
    if (!b.mode.isEmpty()) engine_->setDemodMode(b.mode);
    QJsonObject o = okBase();
    o["command"] = "tune_to_bookmark";
    o["index"] = idx;
    o["freq_hz"] = b.frequencyHz;
    o["mode"] = b.mode;
    return o;
}

QJsonObject ControlHub::cmdDeleteBookmark(const QJsonObject& a) {
    int idx; QString err;
    if (!needInt(a, "index", idx, err)) return errResult(err);
    if (idx < 0 || idx >= bookmarks_->count())
        return errResult(QString::fromUtf8("书签下标越界: %1").arg(idx));
    bookmarks_->removeAt(idx);   // auto-saves
    QJsonObject o = okBase();
    o["command"] = "delete_bookmark";
    o["index"] = idx;
    return o;
}

QJsonObject ControlHub::cmdListVfos(const QJsonObject&) {
    QJsonObject o = cmdGetVfos(QJsonObject());
    o["command"] = "list_vfos";
    return o;
}

QJsonObject ControlHub::cmdAddVfo(const QJsonObject&) {
    engine_->vfoAdd();
    QJsonObject o = okBase();
    o["command"] = "add_vfo";
    o["selected_vfo_id"] = engine_->selectedVfoId();
    return o;
}

QJsonObject ControlHub::cmdSwitchVfo(const QJsonObject& a) {
    QJsonValue v = a.value(QStringLiteral("index"));
    if (v.isUndefined()) v = a.value(QStringLiteral("id"));
    if (!v.isDouble())
        return errResult(QString::fromUtf8("缺少整数参数: index"));
    engine_->vfoSelect(v.toInt());
    QJsonObject o = okBase();
    o["command"] = "switch_vfo";
    o["selected_vfo_id"] = engine_->selectedVfoId();
    return o;
}

QJsonObject ControlHub::cmdRenameVfo(const QJsonObject& a) {
    QJsonValue v = a.value(QStringLiteral("index"));
    if (v.isUndefined()) v = a.value(QStringLiteral("id"));
    if (!v.isDouble())
        return errResult(QString::fromUtf8("缺少整数参数: index"));
    const QString name = a.value(QStringLiteral("name")).toString();
    if (name.trimmed().isEmpty())
        return errResult(QString::fromUtf8("缺少非空参数: name"));
    if (!engine_->vfoRename(v.toInt(), name))
        return errResult(QString::fromUtf8("VFO 不存在或名为空: id=%1").arg(v.toInt()));
    QJsonObject o = okBase();
    o["command"] = "rename_vfo";
    o["index"] = v.toInt();
    o["name"] = name;
    return o;
}

QJsonObject ControlHub::cmdListRecordings(const QJsonObject&) {
    const QString dir = engine_->recordingDir();
    QDir d(dir);
    const QFileInfoList files =
        d.entryInfoList(QDir::Files | QDir::NoSymLinks, QDir::Name);
    QJsonArray arr;
    for (const QFileInfo& fi : files) {
        QJsonObject v;
        v["name"] = fi.fileName();
        v["bytes"] = static_cast<qint64>(fi.size());
        arr.append(v);
    }
    QJsonObject o = okBase();
    o["command"] = "list_recordings";
    o["dir"] = dir;
    o["recordings"] = arr;
    o["count"] = arr.size();
    return o;
}

QJsonObject ControlHub::cmdDeleteRecording(const QJsonObject& a) {
    if (!a.value(QStringLiteral("name")).isString())
        return errResult(QString::fromUtf8("缺少字符串参数: name"));
    const QString name = a.value(QStringLiteral("name")).toString();
    QString err;
    const QString full = safeUnderRecDir(engine_->recordingDir(), name, err);
    if (full.isEmpty()) return errResult(err);
    if (!QFileInfo::exists(full))
        return errResult(QString::fromUtf8("录制不存在: %1").arg(name));
    if (!QFile::remove(full))
        return errResult(QString::fromUtf8("删除失败: %1").arg(name));
    QJsonObject o = okBase();
    o["command"] = "delete_recording";
    o["name"] = name;
    return o;
}

QJsonObject ControlHub::cmdExportRecording(const QJsonObject& a) {
    if (!a.value(QStringLiteral("name")).isString())
        return errResult(QString::fromUtf8("缺少字符串参数: name"));
    if (!a.value(QStringLiteral("out_path")).isString())
        return errResult(QString::fromUtf8("缺少字符串参数: out_path"));
    const QString name = a.value(QStringLiteral("name")).toString();
    const QString out = a.value(QStringLiteral("out_path")).toString();
    QString err;
    const QString full = safeUnderRecDir(engine_->recordingDir(), name, err);
    if (full.isEmpty()) return errResult(err);
    if (!QFileInfo::exists(full))
        return errResult(QString::fromUtf8("录制不存在: %1").arg(name));
    QFileInfo outFi(out);
    if (!outFi.absolutePath().isEmpty()) QDir().mkpath(outFi.absolutePath());
    if (QFile::exists(out)) QFile::remove(out);
    if (!QFile::copy(full, out))
        return errResult(QString::fromUtf8("导出复制失败: %1").arg(out));
    QJsonObject o = okBase();
    o["command"] = "export_recording";
    o["out_path"] = out;
    return o;
}

QJsonObject ControlHub::cmdSetFftParams(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "set_fft_params";
    bool has = false;
    const QJsonValue sz = a.value(QStringLiteral("fft_size"));
    if (sz.isDouble()) { engine_->setFftSize(sz.toInt()); o["fft_size"] = sz.toInt(); has = true; }
    const QJsonValue w = a.value(QStringLiteral("window"));
    if (w.isDouble()) { engine_->setWindowType(w.toInt()); o["window"] = w.toInt(); has = true; }
    const QJsonValue av = a.value(QStringLiteral("average"));
    if (av.isDouble()) { engine_->setAverageMode(av.toInt()); o["average"] = av.toInt(); has = true; }
    if (!has)
        return errResult(QString::fromUtf8("set_fft_params 需要至少一个: fft_size/window/average"));
    return o;
}

QJsonObject ControlHub::cmdSetColorMap(const QJsonObject& a) {
    if (!a.value(QStringLiteral("file_path")).isString())
        return errResult(QString::fromUtf8("缺少字符串参数: file_path"));
    const QString path = a.value(QStringLiteral("file_path")).toString();
    if (path.isEmpty())
        return errResult(QString::fromUtf8("file_path 不能为空"));
    QSettings(QString::fromUtf8("MBDSDR"), QString::fromUtf8("MBDSDR"))
        .setValue(QLatin1String(tokens::kSettingsKeyColormapFile), path);
    QJsonObject o = okBase();
    o["command"] = "set_color_map";
    o["file_path"] = path;
    o["note"] = QString::fromUtf8("已持久化到 QSettings view/wfColormapFile；重渲染由 UI 启动时重应用，headless 仅保存偏好");
    return o;
}

QJsonObject ControlHub::cmdGetSpectrumStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_spectrum_status";
    o["fft_size"] = engine_->fftSize();
    o["window"] = engine_->windowType();
    o["average"] = engine_->averageMode();
    return o;
}

} // namespace control
} // namespace mbdsdr
