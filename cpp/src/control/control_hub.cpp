// SPDX-License-Identifier: MIT
#include "control/control_hub.h"
#include "core/app_settings.h"

#include "dsp/spectrum_engine.h"
#include "vna/vna_rf.h"
#include <QImage>
#include <QPainter>
#include <QPolygonF>
#include "dsp/device_capabilities.h"
#include "dsp/vfo_manager.h"
#include "dsp/network_audio_sink.h"
#include "dsp/scan_link.h"
#include "ui/bookmark_manager.h"
#include "ui/workspace_controller.h"  // P0-b: live three-column workspace seam
#include "ai/sat_task_planner.h"
#include "ai/memory_store.h"   // P3 #73: local SQLite FTS5 memory store
#include "ai/pending_plan_store.h" // 待执行计划槽：run_template 登记 / get_task_history 读面
#include "ai/checkpoint_writer.h" // P3 #74: background single-writer drain
#include "core/tokens.h"
#include "ui/spectrum_render.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QStringList>
#include <QSet>
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
        {"set_vfo_armed",        true,  &ControlHub::cmdSetVfoArmed},
        {"clear_digital_outputs",true,  &ControlHub::cmdClearDigitalOutputs},
        // ---- Phase26 new write commands ---------------------------------
        {"set_network_audio_sink", true, &ControlHub::cmdSetNetworkAudioSink},
        {"start_scan_link",       true,  &ControlHub::cmdStartScanLink},
        {"stop_scan_link",        true,  &ControlHub::cmdStopScanLink},
        {"set_squelch",           true,  &ControlHub::cmdSetSquelch},
        {"set_ctcss",             true,  &ControlHub::cmdSetCtcss},
        {"set_cdcss",             true,  &ControlHub::cmdSetCdcss},
        {"set_ft8",               true,  &ControlHub::cmdSetFt8},
        {"set_lrpt",              true,  &ControlHub::cmdSetLrpt},
        {"set_vna_sweep",         true,  &ControlHub::cmdSetVnaSweep},
        {"vna_tdr_cable",         true,  &ControlHub::cmdVnaTdrCable},
        {"vna_calibration",       true,  &ControlHub::cmdVnaCalibration},
        {"vna_device",             true,  &ControlHub::cmdVnaDevice},
        {"memory_add",             true,  &ControlHub::cmdMemoryAdd},
        {"memory_update",          true,  &ControlHub::cmdMemoryUpdate},
        {"memory_flush",           true,  &ControlHub::cmdMemoryFlush},
        // G2: delete one memory by (scope,key) -- honest found/not-found.
        {"memory_delete",          true,  &ControlHub::cmdMemoryDelete},
        {"export_vna_snapshot",   true,  &ControlHub::cmdExportVnaSnapshot},
        {"export_spectrum_snapshot", true, &ControlHub::cmdExportSpectrumSnapshot},
        {"export_peaks_csv",    true,  &ControlHub::cmdExportPeaksCsv},
        {"export_ft8_results",  true,  &ControlHub::cmdExportFt8Results},
        {"set_noise_blanker",     true,  &ControlHub::cmdSetNoiseBlanker},
        {"set_doppler_compensation", true, &ControlHub::cmdSetDopplerCompensation},
        {"connect_network_source",   true, &ControlHub::cmdConnectNetworkSource},
        {"add_bookmark",          true,  &ControlHub::cmdAddBookmark},
        {"tune_to_bookmark",      true,  &ControlHub::cmdTuneToBookmark},
        {"delete_bookmark",       true,  &ControlHub::cmdDeleteBookmark},
        // 第 10 域「工作台编排」(P0-b): arrange/reset are gated writes.
        {"arrange_workspace",     true,  &ControlHub::cmdArrangeWorkspace},
        {"reset_workspace",       true,  &ControlHub::cmdResetWorkspace},
        // A2UI v1.0 updateDataModel minimal subset (gated write).
        {"update_component_data", true,  &ControlHub::cmdUpdateComponentData},
        // Deterministic task template runner (gated write: builds + registers a
        // plan; never synchronously tunes/records on this surface).
        {"run_template",          true,  &ControlHub::cmdRunTemplate},
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
        // ---- Phase60 packet-text read commands --------------------------
        {"get_acars_packets",     false, &ControlHub::cmdGetAcarsPackets},
        {"get_navtex_messages",   false, &ControlHub::cmdGetNavtexMessages},
        {"get_aprs_messages",    false, &ControlHub::cmdGetApRsMessages},
        // Read-only satellite pass prediction (same capability as the Agent
        // predict_passes tool; pure function, never fabricates a pass).
        {"predict_passes",        false, &ControlHub::cmdPredictPasses},
        // Read-only TLE-cache catalog (prerequisite for predict_passes).
        {"list_satellites",       false, &ControlHub::cmdListSatellites},
        // ---- Phase26 new read commands ----------------------------------
        {"get_network_audio_status", false, &ControlHub::cmdGetNetworkAudioStatus},
        {"get_scan_link_status",  false, &ControlHub::cmdGetScanLinkStatus},
        {"get_squelch_status",    false, &ControlHub::cmdGetSquelchStatus},
        {"get_ctcss_status",      false, &ControlHub::cmdGetCtcssStatus},
        {"get_cdcss_status",      false, &ControlHub::cmdGetCdcssStatus},
        {"get_ft8_status",        false, &ControlHub::cmdGetFt8Status},
        {"get_ft8_history",       false, &ControlHub::cmdGetFt8History},
        {"get_adsb_aircraft",    false, &ControlHub::cmdGetAdsbAircraft},
        {"get_lrpt_status",       false, &ControlHub::cmdGetLrptStatus},
        {"get_vna_data",          false, &ControlHub::cmdGetVnaData},
        {"get_vna_status",        false, &ControlHub::cmdGetVnaStatus},
        {"analyze_vna_resonance", false, &ControlHub::cmdAnalyzeVnaResonance},
        {"analyze_vna_filter",    false, &ControlHub::cmdAnalyzeVnaFilter},
        {"analyze_vna_equivalent", false, &ControlHub::cmdAnalyzeVnaEquivalent},
        {"analyze_vna_touchstone", false, &ControlHub::cmdAnalyzeVnaTouchstone},
        {"analyze_vna_match",    false, &ControlHub::cmdAnalyzeVnaMatch},
        {"get_noise_blanker_status", false, &ControlHub::cmdGetNoiseBlankerStatus},
        {"list_bookmarks",        false, &ControlHub::cmdListBookmarks},
        // 第 10 域「工作台编排」(P0-b): get/list are always-allowed reads.
        {"get_workspace_layout",  false, &ControlHub::cmdGetWorkspaceLayout},
        {"list_ui_components",    false, &ControlHub::cmdListUiComponents},
        {"list_vfos",             false, &ControlHub::cmdListVfos},
        {"list_recordings",       false, &ControlHub::cmdListRecordings},
        {"get_spectrum_status",   false, &ControlHub::cmdGetSpectrumStatus},
        {"get_color_maps",      false, &ControlHub::cmdGetColorMaps},
        // Deterministic task template catalog (read-only; always allowed).
        {"list_templates",        false, &ControlHub::cmdListTemplates},
        // ---- Local memory family read commands (P3 #73/#74, always allowed) --
        {"memory_search",         false, &ControlHub::cmdMemorySearch},
        {"checkpoint_status",     false, &ControlHub::cmdCheckpointStatus},
        // G1: task-run history (read-only) + G2: memory statistics (read-only).
        {"get_task_history",      false, &ControlHub::cmdGetTaskHistory},
        {"memory_stats",          false, &ControlHub::cmdMemoryStats},
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
    // Phase63 D2 bilateral alias: prefer "channel" (historic CH/HTTP key), but
    // also accept "channel_id" (the Agent LLM schema key). Either selects the
    // VFO; neither falls back to the currently-selected VFO. Mirrors the
    // Agent-side resolveChannelId in agent_tools.cpp so the two channels never
    // silently disagree on which channel a caller meant.
    QJsonValue v = a.value(QStringLiteral("channel"));
    if (v.isUndefined() || v.isNull())
        v = a.value(QStringLiteral("channel_id"));
    if (v.isUndefined() || v.isNull()) return engine_->selectedVfoId();
    if (!v.isDouble()) {
        err = QString::fromUtf8("参数 channel/channel_id 必须是整数（VFO 信道 id）");
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
    // RAW direct-listen has NO demodulation filter: it passes the selected VFO's
    // channelized narrowband IQ straight through as L=I/R=Q. A bandwidth change
    // would be silently swallowed by a no-op DemodRaw otherwise -- refuse honestly
    // (ok:false) instead of echoing a fake success.
    if (engine_->demodMode() == QLatin1String("RAW")) {
        QJsonObject o = errResult(QString::fromUtf8(
            "带宽对 RAW 直通无意义：RAW 为所选 VFO 信道化窄带 IQ 的 L=I/R=Q 直通，无解调滤波器可设"));
        o["command"] = "set_bandwidth";
        return o;
    }
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

// Phase63 D1 bilateral alias: resolve a VFO write target from EITHER
//   - "id"    (direct VFO id, the historic CH/HTTP key), or
//   - "index" (marker ordinal in vfoMarkers(), the Agent LLM schema key).
// Returns true on success, fills outId/outIndex; on missing/bad input returns
// false with an honest error. Mirrors the Agent-side resolveVfoTarget.
namespace {
bool resolveVfoTargetHelper(dsp::SpectrumEngine* engine, const QJsonObject& a,
                            int& outId, int& outIndex, QString& err) {
    const QJsonValue idV = a.value(QStringLiteral("id"));
    const QJsonValue idxV = a.value(QStringLiteral("index"));
    if (idV.isDouble()) {
        outId = idV.toInt();
        outIndex = -1;
        const auto markers = engine->vfoMarkers();
        for (int k = 0; k < markers.size(); ++k)
            if (markers[k].id == outId) { outIndex = k; break; }
        return true;
    }
    if (idxV.isDouble()) {
        const int i = idxV.toInt();
        const auto markers = engine->vfoMarkers();
        if (i < 0 || i >= markers.size()) {
            err = QString::fromUtf8("VFO index %1 越界（共 %2 个）").arg(i).arg(markers.size());
            return false;
        }
        outId = markers[i].id;
        outIndex = i;
        return true;
    }
    err = QString::fromUtf8("参数 id 或 index 必须提供一个（VFO id 或列表序号）");
    return false;
}
} // namespace

QJsonObject ControlHub::cmdVfoSetFreq(const QJsonObject& a) {
    double hz; QString err;
    if (!needDbl(a, "freq_hz", hz, err)) return errResult(err);
    int id = -1, idx = -1;
    if (!resolveVfoTargetHelper(engine_, a, id, idx, err)) return errResult(err);
    engine_->vfoSetFreq(id, hz);
    QJsonObject o = okBase();
    o["command"] = "vfo_set_freq";
    o["id"] = id;
    if (idx >= 0) o["index"] = idx;
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
    double hz; QString err;
    if (!needDbl(a, "bandwidth_hz", hz, err)) return errResult(err);
    // Honest-contract parity with the Agent tool set_vfo_bandwidth: a non-positive
    // bandwidth must be REJECTED, not silently swallowed.
    if (hz <= 0.0)
        return errResult(QString::fromUtf8("参数 bandwidth_hz 必须为正数"));
    int id = -1, idx = -1;
    if (!resolveVfoTargetHelper(engine_, a, id, idx, err)) return errResult(err);
    engine_->vfoSetBandwidth(id, hz);
    QJsonObject o = okBase();
    o["command"] = "vfo_set_bandwidth";
    o["id"] = id;
    if (idx >= 0) o["index"] = idx;
    o["bandwidth_hz"] = hz;
    return o;
}

QJsonObject ControlHub::cmdVfoSetMode(const QJsonObject& a) {
    QString m; QString err;
    if (!needMode(a, "mode", m, err)) return errResult(err);
    int id = -1, idx = -1;
    if (!resolveVfoTargetHelper(engine_, a, id, idx, err)) return errResult(err);
    engine_->vfoSetMode(id, m);
    QJsonObject o = okBase();
    o["command"] = "vfo_set_mode";
    o["id"] = id;
    if (idx >= 0) o["index"] = idx;
    o["mode"] = m;
    return o;
}

// set_vfo_armed (write): index + enabled, aligned 1:1 with the Agent tool
// set_vfo_armed and the engine's vfoSetArmed. Resolves the marker index to
// the VFO id via vfoMarkers() (same path the Agent executor uses), so
// engine / UI / Agent / ControlHub / HTTP all drive one state.
QJsonObject ControlHub::cmdSetVfoArmed(const QJsonObject& a) {
    int idx; QString err;
    if (!needInt(a, "index", idx, err)) return errResult(err);
    if (!a.contains("enabled") || !a.value("enabled").isBool())
        return errResult(QString::fromUtf8("参数 enabled 必须是布尔值"));
    const bool enabled = a.value("enabled").toBool();
    const auto markers = engine_->vfoMarkers();
    if (idx < 0 || idx >= static_cast<int>(markers.size()))
        return errResult(QString::fromUtf8("VFO index %1 越界（共 %2 个）")
                         .arg(idx).arg(markers.size()));
    const int id = markers[idx].id;
    engine_->vfoSetArmed(id, enabled);
    QJsonObject o = okBase();
    o["command"] = "set_vfo_armed";
    o["index"] = idx;
    o["vfo_id"] = id;
    o["armed"] = enabled;
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
    // Real discrete gain steps from the ACTIVE source; EMPTY for rtl_tcp / test /
    // file sources -- never a fabricated step table.
    std::vector<double> gains = engine_->availableGainsDb();
    QJsonArray gainArr;
    for (double g : gains) gainArr.append(g);
    o["gains_db"] = gainArr;
    // Honest provenance: the struct carries the real source note; a synthetic test
    // source must be explicitly labelled (the empty-state note does not
    // distinguish "synthetic" from "truly unplugged"). A fresh engine whose caps
    // snapshot is not yet filled leaves provenance empty -> honest "未连接".
    QString provenance = c.provenance;
    if (engine_->isTestSignalActive())
        provenance = QString::fromUtf8("测试信号源（非硬件，能力表为空）");
    else if (!c.connected && provenance.isEmpty())
        provenance = QString::fromUtf8("未连接");
    o["provenance"] = provenance;
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
        v["armed"] = m.armed;
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
    // `recording` is derived from the live recording path (empty = not recording),
    // which is deterministic and does not wait for the recordingStateChanged signal
    // to have fired. watch/dir are real engine getters. Never fabricated.
    const QString path = engine_->recordingPath();
    QJsonObject o = okBase();
    o["command"] = "get_recording_state";
    o["recording"] = !path.isEmpty();
    o["recording_path"] = path;
    o["watch_enabled"] = engine_->watchEnabled();
    o["recording_dir"] = engine_->recordingDir();
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
        // Phase63 output-contract OF1: emit the raw LSF TYPE word as an INTEGER,
        // matching the Agent channel (execGetM17Calls) AND the Flutter consumer
        // (M17Call.fromJson only accepts `type` as a number; the old "0x%04X"
        // string was silently coerced to 0 on the mobile side). One numeric
        // wire type across all three channels.
        o["type"] = static_cast<int>(c.type);
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

QJsonObject ControlHub::cmdGetAcarsPackets(const QJsonObject& a) {
    QString err;
    const int ch = resolveChannel(a, err);
    if (ch < 0) return errResult(err);
    std::vector<dsp::AcarsPacket> pkts = engine_->acarsPackets(ch);
    QJsonArray arr;
    for (const dsp::AcarsPacket& p : pkts) {
        QJsonObject o;
        const char* dir = "unknown";
        switch (p.direction) {
            case dsp::AcarsPacket::Direction::Air:    dir = "air";    break;
            case dsp::AcarsPacket::Direction::Ground: dir = "ground"; break;
            default: break;
        }
        o["direction"] = QString::fromLatin1(dir);
        o["mode"]     = QString::fromStdString(p.mode);
        o["label"]    = QString::fromStdString(p.label);
        o["block_id"] = QString::fromStdString(p.blockId);
        o["ack"]      = QString::fromStdString(p.ack);
        o["text"]     = QString::fromStdString(p.text);
        o["crc_ok"]   = p.crcOk;
        arr.append(o);
    }
    QJsonObject r = okBase();
    r["command"] = "get_acars_packets";
    r["channel"] = ch;
    r["packets"] = arr;
    r["count"] = static_cast<int>(pkts.size());
    return r;
}

QJsonObject ControlHub::cmdGetNavtexMessages(const QJsonObject& a) {
    QString err;
    const int ch = resolveChannel(a, err);
    if (ch < 0) return errResult(err);
    std::vector<dsp::NavtexMessage> msgs = engine_->navtexMessages(ch);
    QJsonArray arr;
    for (const dsp::NavtexMessage& m : msgs) {
        QJsonObject o;
        o["station"] = QString::fromStdString(m.stationB1);
        o["type"]    = QString::fromStdString(m.typeB2);
        o["number"]  = QString::fromStdString(m.numberB3B4);
        o["text"]    = QString::fromStdString(m.text);
        o["diversity_ok"] = m.diversityOk;
        o["phasing_ok"]   = m.phasingOk;
        o["diversity_errors"] = m.diversityErrors;
        arr.append(o);
    }
    QJsonObject r = okBase();
    r["command"] = "get_navtex_messages";
    r["channel"] = ch;
    r["messages"] = arr;
    r["count"] = static_cast<int>(msgs.size());
    return r;
}

// Read-only satellite pass prediction, mirroring the Agent predict_passes tool
// field-for-field so all three channels expose the same capability. Pure
// function over the fresh on-disk TLE cache; ok=false is always honest (no
// fresh cache / satellite absent / no pass in window / bad args).
QJsonObject ControlHub::cmdGetApRsMessages(const QJsonObject& a) {
    const QList<dsp::SpectrumEngine::ApRsSnapshot> msgs = engine_->aprsMessages(50);
    QJsonArray arr;
    for (const auto& m : msgs) {
        QJsonObject o;
        o["time_ms"] = static_cast<double>(m.time_ms);
        o["src"] = m.src; o["dst"] = m.dst; o["path"] = m.path; o["info"] = m.info;
        arr.append(o);
    }
    QJsonObject r;
    r["command"] = "get_aprs_messages";
    r["ok"] = true;
    r["returned"] = arr.size();
    r["messages"] = arr;
    if (arr.isEmpty())
        r["note"] = QString::fromUtf8("空态：尚无 APRS 消息（未喂位流或无有效帧）");
    return r;
}

QJsonObject ControlHub::cmdPredictPasses(const QJsonObject& a) {
    QString sat = a.value(QStringLiteral("satellite_name")).toString();
    int hours = static_cast<int>(a.value(QStringLiteral("hours_ahead")).toDouble(24.0));
    if (hours < 1) hours = 24;
    const bool hasLat = a.contains(QStringLiteral("station_lat_deg")) &&
                        a.value(QStringLiteral("station_lat_deg")).isDouble();
    const bool hasLon = a.contains(QStringLiteral("station_lon_deg")) &&
                        a.value(QStringLiteral("station_lon_deg")).isDouble();
    const double lat = hasLat ? a.value(QStringLiteral("station_lat_deg")).toDouble() : qQNaN();
    const double lon = hasLon ? a.value(QStringLiteral("station_lon_deg")).toDouble() : qQNaN();

    ai::SatPassListResult r = ai::predictSatellitePasses(
        sat, lat, lon, QDateTime::currentDateTimeUtc(), hours);
    QJsonObject o = okBase();
    o["command"] = "predict_passes";
    if (!r.ok) {
        o["ok"] = false;
        o["error"] = r.error;
        o["source"] = r.source.isEmpty()
            ? QString::fromUtf8("无新鲜 TLE 缓存") : r.source;
        return o;
    }
    o["source"] = r.source;   // "cached_tle" -- honest data provenance
    QJsonArray arr;
    for (const ai::SatPassEntry& e : r.passes) {
        QJsonObject p;
        p["name"] = e.name;
        p["catalog_number"] = e.catalogNumber;
        p["rise_time"] = e.aosUtc.toUTC().toString(Qt::ISODate);
        p["rise_az"] = e.azAos;
        p["set_time"] = e.losUtc.toUTC().toString(Qt::ISODate);
        p["set_az"] = e.azLos;
        p["max_el"] = e.maxEl;
        arr.append(p);
    }
    o["passes"] = arr;
    return o;
}

// Read-only TLE-cache catalog, mirroring the Agent list_satellites tool. Pure
// function over the on-disk cache; ok=false is always honest (no cache).
QJsonObject ControlHub::cmdListSatellites(const QJsonObject&) {
    ai::SatListResult r = ai::listCachedSatellites(QDateTime::currentDateTimeUtc());
    QJsonObject o = okBase();
    o["command"] = "list_satellites";
    if (!r.ok) {
        o["ok"] = false;
        o["error"] = r.error;
        o["source"] = r.source.isEmpty()
            ? QString::fromUtf8("none") : r.source;
        return o;
    }
    o["source"] = r.source;
    o["fetched_at"] = r.fetchedAt.isValid()
        ? r.fetchedAt.toUTC().toString(Qt::ISODate) : QString();
    int freshCount = 0;
    QJsonArray arr;
    for (const ai::SatListEntry& e : r.sats) {
        if (e.fresh) ++freshCount;
        QJsonObject s;
        s["name"] = e.name;
        s["catalog_number"] = e.catalogNumber;
        s["epoch_utc"] = e.epochUtc.isValid()
            ? e.epochUtc.toUTC().toString(Qt::ISODate) : QString();
        s["age_days"] = e.epochUtc.isValid() ? e.ageDays : -1.0;
        s["fresh"] = e.fresh;
        s["downlink_hz"] = e.downlinkHz;
        arr.append(s);
    }
    o["count"] = static_cast<int>(arr.size());
    o["fresh_count"] = freshCount;
    o["satellites"] = arr;
    return o;
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

QJsonObject ControlHub::cmdSetNoiseBlanker(const QJsonObject& a) {
    bool on; QString err;
    if (!needBool(a, "on", on, err)) return errResult(err);
    engine_->setNoiseBlanker(on);
    QJsonObject o = okBase();
    o["command"] = "set_noise_blanker";
    o["enabled"] = on;
    return o;
}

QJsonObject ControlHub::cmdGetNoiseBlankerStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_noise_blanker_status";
    o["enabled"] = engine_->noiseBlankerEnabled();
    return o;
}

// CTCSS tone-squelch: `enabled` required. `frequency_hz` optional (omit keeps the
// current tuning, default 88.5 Hz); an out-of-domain value is REJECTED here with
// ok:false -- the engine clamps, so the command layer is the honest gate that
// refuses rather than silently retuning to a clamped tone.
QJsonObject ControlHub::cmdSetCtcss(const QJsonObject& a) {
    bool on; QString err;
    if (!needBool(a, "enabled", on, err)) return errResult(err);
    double fq = engine_->ctcssFreqHz();   // keep current tuning when omitted
    const QJsonValue fv = a.value(QStringLiteral("frequency_hz"));
    if (!fv.isUndefined() && !fv.isNull()) {
        double v = 0.0;
        if (!needDbl(a, "frequency_hz", v, err)) return errResult(err);
        if (v < tokens::kCtcssToneHzMin || v > tokens::kCtcssToneHzMax)
            return errResult(QString::fromUtf8(
                "CTCSS 亚音频率越界：必须在 %1–%2 Hz 之间（收到 %3）")
                .arg(tokens::kCtcssToneHzMin).arg(tokens::kCtcssToneHzMax).arg(v));
        fq = v;
    }
    engine_->setCtcssEnabled(on);
    engine_->setCtcssFreqHz(fq);
    // Optional speaker-only sub-audio gate. Omit -> keep current; present-but-
    // non-bool -> honest error. Mirrors the agent channel.
    bool gateAudio = engine_->ctcssGateAudio();
    const QJsonValue gv = a.value(QStringLiteral("gate_audio"));
    if (!gv.isUndefined() && !gv.isNull()) {
        if (!needBool(a, "gate_audio", gateAudio, err)) return errResult(err);
    }
    engine_->setCtcssGateAudio(gateAudio);
    QJsonObject o = okBase();
    o["command"] = "set_ctcss";
    o["enabled"] = on;
    o["frequency_hz"] = fq;
    o["gate_audio"] = gateAudio;
    return o;
}

QJsonObject ControlHub::cmdGetCtcssStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_ctcss_status";
    o["enabled"] = engine_->ctcssEnabled();
    o["frequency_hz"] = engine_->ctcssFreqHz();
    o["active"] = engine_->ctcssPresent();
    o["gate_audio"] = engine_->ctcssGateAudio();
    return o;
}

// CDCSS/DCS digital coded squelch: `enabled` required. `code` optional 3-digit
// octal string; when supplied it must parse and be in the public 104-code
// table -- an illegal code is REJECTED here with ok:false. `gate_audio` optional.
QJsonObject ControlHub::cmdSetCdcss(const QJsonObject& a) {
    bool on; QString err;
    if (!needBool(a, "enabled", on, err)) return errResult(err);
    int code = engine_->cdcssCode();   // keep current when omitted
    const QJsonValue cv = a.value(QStringLiteral("code"));
    if (!cv.isUndefined() && !cv.isNull()) {
        const QString cs = cv.toString();
        bool ok = false;
        const int v = cs.toInt(&ok, 8);
        if (!ok || !dsp::CdcssDecoder::isValidCode(v))
            return errResult(QString::fromUtf8(
                "DCS 码非法：须为三位八进制且在公开 104 码表内（收到 \"%1\"）").arg(cs));
        code = v;
    }
    bool gateAudio = engine_->cdcssGateAudio();
    const QJsonValue gv = a.value(QStringLiteral("gate_audio"));
    if (!gv.isUndefined() && !gv.isNull()) {
        if (!needBool(a, "gate_audio", gateAudio, err)) return errResult(err);
    }
    engine_->setCdcssEnabled(on);
    engine_->setCdcssCode(code);
    engine_->setCdcssGateAudio(gateAudio);
    QJsonObject o = okBase();
    o["command"] = "set_cdcss";
    o["enabled"] = on;
    o["code"] = QStringLiteral("%1").arg(code, 3, 8, QLatin1Char('0'));
    o["gate_audio"] = gateAudio;
    return o;
}

QJsonObject ControlHub::cmdGetCdcssStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_cdcss_status";
    o["enabled"] = engine_->cdcssEnabled();
    o["code"] = QStringLiteral("%1").arg(engine_->cdcssCode(), 3, 8, QLatin1Char('0'));
    o["active"] = engine_->cdcssPresent();
    o["gate_audio"] = engine_->cdcssGateAudio();
    return o;
}

QJsonObject ControlHub::cmdSetFt8(const QJsonObject& a) {
    QString err;
    bool on = false;
    if (!needBool(a, "enabled", on, err)) return errResult(err);
    engine_->setFt8Enabled(on);
    QJsonObject o = okBase();
    o["command"] = "set_ft8";
    o["enabled"] = on;
    return o;
}

QJsonObject ControlHub::cmdGetFt8Status(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_ft8_status";
    o["enabled"] = engine_->ft8Enabled();
    o["active"] = engine_->ft8Present();
    o["freq_offset_hz"] = engine_->ft8FreqOffsetHz();
    o["sync_quality"] = engine_->ft8SyncQuality();
    o["candidate_count"] = engine_->ft8CandidateCount();
    return o;
}

// get_ft8_history (read-only): WSJT-X style rolling decoded-message window.
// Mirrors the Agent get_ft8_history tool; empty list when no frames decoded.
QJsonObject ControlHub::cmdGetFt8History(const QJsonObject& a) {
    int limit = 50;
    const QJsonValue lv = a.value("limit");
    if (lv.isDouble() || lv.isBool()) {
        // Qt6 QJsonValue::toDouble(default) returns the default if not a number.
        const double v = lv.toDouble(std::nan(""));
        if (std::isfinite(v)) {
            int iv = (int)std::lround(v);
            if (iv < 1) iv = 1;
            if (iv > dsp::SpectrumEngine::kFt8HistoryMax)
                iv = dsp::SpectrumEngine::kFt8HistoryMax;
            limit = iv;
        }
    }
    QJsonObject o = okBase();
    o["command"] = "get_ft8_history";
    o["limit"] = limit;
    QJsonArray arr;
    const auto rows = engine_->ft8History(limit);
    for (const auto& e : rows) {
        QJsonObject r;
        r["time_ms"] = (double)e.time_ms;
        r["freq_hz"] = e.freq_hz;
        r["demod"] = e.demod;
        r["text"] = e.text;
        arr.append(r);
    }
    o["returned"] = (int)rows.size();
    o["history"] = arr;
    if (rows.isEmpty())
        o["note"] = QStringLiteral("无 FT8 解码历史：history=[] 诚实空态");
    return o;
}

// get_adsb_aircraft (read-only): dump1090 --aircraft shape snapshot.
QJsonObject ControlHub::cmdGetAdsbAircraft(const QJsonObject& a) {
    int limit = 50;
    const QJsonValue lv = a.value("limit");
    if (lv.isDouble() || lv.isBool()) {
        const double v = lv.toDouble(std::nan(""));
        if (std::isfinite(v)) {
            int iv = (int)std::lround(v);
            if (iv < 1) iv = 1;
            if (iv > dsp::SpectrumEngine::kAircraftSnapshotMax)
                iv = dsp::SpectrumEngine::kAircraftSnapshotMax;
            limit = iv;
        }
    }
    QJsonObject o = okBase();
    o["command"] = "get_adsb_aircraft";
    o["limit"] = limit;
    QJsonArray arr;
    const auto rows = engine_->aircraftSnapshot(limit);
    for (const auto& ac : rows) {
        QJsonObject r;
        r["icao"] = ac.icao;
        r["callsign"] = ac.callsign;
        r["altitude_ft"] = ac.altitude_ft;
        if (ac.has_position) {
            r["lat"] = ac.lat;
            r["lon"] = ac.lon;
        } else {
            r["lat"] = QJsonValue(QJsonValue::Null);
            r["lon"] = QJsonValue(QJsonValue::Null);
        }
        r["has_position"] = ac.has_position;
        if (ac.has_velocity) {
            r["groundspeed_kt"] = ac.groundspeed_kt;
            r["heading_deg"] = ac.heading_deg;
            r["vertical_rate_fpm"] = ac.vertical_rate_fpm;
        } else {
            r["groundspeed_kt"] = QJsonValue(QJsonValue::Null);
            r["heading_deg"] = QJsonValue(QJsonValue::Null);
            r["vertical_rate_fpm"] = QJsonValue(QJsonValue::Null);
        }
        r["has_velocity"] = ac.has_velocity;
        r["df"] = ac.df;
        r["crc_ok"] = ac.crc_ok;
        r["last_seen_ms"] = (double)ac.last_seen_ms;
        arr.append(r);
    }
    o["returned"] = (int)rows.size();
    o["aircraft"] = arr;
    if (rows.isEmpty())
        o["note"] = QStringLiteral("1090 MHz 无信号或未调谐到 ADS-B：aircraft=[] 诚实空态");
    return o;
}

QJsonObject ControlHub::cmdSetLrpt(const QJsonObject& a) {
    QString err;
    bool on = false;
    if (!needBool(a, "enabled", on, err)) return errResult(err);
    engine_->setLrptEnabled(on);
    QJsonObject o = okBase();
    o["command"] = "set_lrpt";
    o["enabled"] = on;
    return o;
}

QJsonObject ControlHub::cmdGetLrptStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_lrpt_status";
    o["enabled"] = engine_->lrptEnabled();
    o["sync_locked"] = engine_->lrptSyncLocked();
    o["decoded_frames"] = engine_->lrptDecodedFrames();
    return o;
}

// NanoVNA step-2: program a sweep. Params validated honestly; with no device the
// sweep is not sent (applied=false) but the validated values are echoed.
QJsonObject ControlHub::cmdSetVnaSweep(const QJsonObject& a) {
    QString err;
    int start = 0, stop = 0, points = 0;
    if (!needInt(a, "start_hz", start, err)) return errResult(err);
    if (!needInt(a, "stop_hz", stop, err)) return errResult(err);
    if (!needInt(a, "points", points, err)) return errResult(err);
    if (stop <= start) return errResult(QStringLiteral("stop_hz 必须大于 start_hz"));
    if (points <= 0)  return errResult(QStringLiteral("points 必须为正整数"));

    QJsonObject o = okBase();
    o["command"] = "set_vna_sweep";
    auto& vna = engine_->vnaClient();
    if (!vna.isConnected()) {
        o["connected"] = false;
        o["applied"] = false;
        o["start_hz"] = start; o["stop_hz"] = stop; o["points"] = points;
        o["note"] = QStringLiteral("未连接 NanoVNA：参数已校验但未下发");
        return o;
    }
    QString verr;
    bool ok = vna.setSweep(start, stop, points, &verr);
    o["connected"] = true; o["applied"] = ok;
    if (ok) { o["start_hz"] = start; o["stop_hz"] = stop; o["points"] = points; }
    else    o["error"] = verr;
    return o;
}

QJsonObject ControlHub::cmdGetVnaData(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_vna_data";
    auto& vna = engine_->vnaClient();
    o["connected"] = vna.isConnected();
    QJsonArray freqs, re, im;
    if (vna.isConnected()) {
        auto f = vna.readFrequencies();
        auto s11 = vna.readData(0);
        for (size_t i = 0; i < s11.size(); ++i) {
            if (i < f.size()) freqs.append((double)f[i]);
            re.append(s11[i].real()); im.append(s11[i].imag());
        }
    }
    o["frequencies"] = freqs; o["s11_re"] = re; o["s11_im"] = im;
    if (!vna.isConnected()) o["note"] = QStringLiteral("未连接 NanoVNA：数组为空");
    return o;
}

QJsonObject ControlHub::cmdGetVnaStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "get_vna_status";
    auto& vna = engine_->vnaClient();
    o["connected"] = vna.isConnected();
    o["model"] = vna.model();
    o["version"] = vna.version();
    o["cal"] = QJsonArray::fromStringList(vna.calStatus());
    // tcxo_hz: live query; emitted only on a parseable "current: <Hz>"
    // (older firmware -> field absent, not a fabricated 0).
    if (vna.isConnected()) {
        long tcxoHz = 0;
        QString tcxoErr;
        if (vna.readTcxo(tcxoHz, &tcxoErr))
            o["tcxo_hz"] = (double)tcxoHz;
    }
    o["has_sweep"] = vna.hasSweep();
    o["start_hz"] = (double)vna.sweepStartHz();
    o["stop_hz"] = (double)vna.sweepStopHz();
    o["points"] = vna.sweepPoints();
    if (!vna.isConnected()) o["note"] = QStringLiteral("未连接 NanoVNA：状态字段为空");
    return o;
}

QJsonObject ControlHub::cmdAnalyzeVnaResonance(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "analyze_vna_resonance";
    auto& vna = engine_->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) { o["valid"] = false; o["note"] = QStringLiteral("未连接 NanoVNA：无扫频数据"); return o; }
    auto f = vna.readFrequencies();
    auto s11 = vna.readData(0);
    vna::ResonanceResult r = vna::analyzeResonance(
        std::vector<double>(f.begin(), f.end()),
        std::vector<std::complex<double>>(s11.begin(), s11.end()));
    o["valid"] = r.valid;
    o["series_fr_hz"] = r.series_fr_hz;
    o["parallel_fr_hz"] = r.parallel_fr_hz;
    o["esr"] = r.esr;
    o["bandwidth_hz"] = r.bandwidth_hz;
    o["q"] = r.q;
    return o;
}

QJsonObject ControlHub::cmdVnaTdrCable(const QJsonObject& a) {
    double vf = 0.0;
    QString err;
    if (!a.value("velocity_factor").isDouble()) return errResult(QStringLiteral("参数 velocity_factor 缺失或不是数字"));
    vf = a.value("velocity_factor").toDouble();
    if (vf <= 0.0 || vf > 1.0) return errResult(QStringLiteral("velocity_factor 必须在 (0,1] 之间"));
    QJsonObject o = okBase();
    o["command"] = "vna_tdr_cable";
    auto& vna = engine_->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) { o["valid"] = false; o["note"] = QStringLiteral("未连接 NanoVNA：无扫频数据"); return o; }
    auto f = vna.readFrequencies();
    auto s11 = vna.readData(0);
    vna::TdrResult t = vna::tdrCable(
        std::vector<double>(f.begin(), f.end()),
        std::vector<std::complex<double>>(s11.begin(), s11.end()), vf);
    o["valid"] = t.valid;
    o["distance_m"] = t.distance_m;
    o["cable_length_m"] = t.cable_length_m;
    return o;
}

QJsonObject ControlHub::cmdAnalyzeVnaFilter(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "analyze_vna_filter";
    auto& vna = engine_->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) { o["valid"] = false; o["note"] = QStringLiteral("未连接 NanoVNA：无扫频数据"); return o; }
    auto f = vna.readFrequencies();
    auto s21 = vna.readData(1);
    std::vector<double> db;
    for (auto& s : s21) db.push_back(vna::s21GainDb(s));
    vna::FilterResult r = vna::analyzeFilter(
        std::vector<double>(f.begin(), f.end()), db);
    o["valid"] = r.valid;
    o["type"] = QString::fromStdString(r.type);
    o["f_low_hz"] = r.f_low_hz;
    o["f_high_hz"] = r.f_high_hz;
    o["bandwidth_hz"] = r.bandwidth_hz;
    o["passband_db"] = r.passband_db;
    o["stopband_atten_db"] = r.stopband_atten_db;
    return o;
}

QJsonObject ControlHub::cmdAnalyzeVnaEquivalent(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "analyze_vna_equivalent";
    auto& vna = engine_->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) { o["valid"] = false; o["note"] = QStringLiteral("未连接 NanoVNA：无扫频数据"); return o; }
    auto f = vna.readFrequencies();
    auto s11 = vna.readData(0);
    vna::EquivalentResult r = vna::equivalentRlc(
        std::vector<double>(f.begin(), f.end()),
        std::vector<std::complex<double>>(s11.begin(), s11.end()));
    o["valid"] = r.valid;
    o["resonance_hz"] = r.resonance_hz;
    o["series_r_ohm"] = r.series_r_ohm;
    o["series_l_h"] = r.series_l_h;
    o["series_c_f"] = r.series_c_f;
    o["series_q"] = r.series_q;
    if (!r.valid) o["note"] = QString::fromStdString(r.note);
    return o;
}

QJsonObject ControlHub::cmdAnalyzeVnaTouchstone(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "analyze_vna_touchstone";
    QString content = a.value("content").toString();
    const QString path = a.value("path").toString();
    if (content.isEmpty() && !path.isEmpty()) {
        QFile f(path);
        if (f.open(QIODevice::ReadOnly | QIODevice::Text)) content = QString::fromUtf8(f.readAll());
    }
    if (content.isEmpty()) {
        o["valid"] = false;
        o["note"] = QStringLiteral("需要 content 或可读 path");
        return o;
    }
    vna::TouchstoneData d = vna::parseTouchstone(content.toStdString());
    o["valid"] = d.valid;
    o["n_ports"] = d.n_ports;
    if (!d.valid) o["note"] = QString::fromStdString(d.note);
    return o;
}

QJsonObject ControlHub::cmdExportVnaSnapshot(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "export_vna_snapshot";
    const QString path = a.value("path").toString();
    if (path.isEmpty()) return errResult(QStringLiteral("参数 path 缺失"));
    auto& vna = engine_->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) { o["ok"] = false; o["note"] = QStringLiteral("未连接 NanoVNA"); return o; }
    auto s11 = vna.readData(0);
    if (s11.empty()) { o["ok"] = false; o["note"] = QStringLiteral("无扫频数据"); return o; }
    QImage img(640, 480, QImage::Format_ARGB32);
    img.fill(QColor(18, 18, 20));
    QPainter p(&img); p.setRenderHint(QPainter::Antialiasing, true);
    const double cx = 320, cy = 240, rad = 180;
    p.setPen(QPen(QColor(120,120,120), 1.0)); p.setBrush(Qt::NoBrush);
    p.drawEllipse(QPointF(cx, cy), rad, rad);
    p.drawLine(QPointF(cx-rad, cy), QPointF(cx+rad, cy));
    QPolygonF line;
    for (auto& g : s11) {
        double m = std::abs(g); if (m > 1.0) m = 1.0;
        double ph = std::arg(g);
        line << QPointF(cx + rad*m*std::cos(ph), cy - rad*m*std::sin(ph));
    }
    p.setPen(QPen(QColor(80,180,255), 1.6)); p.drawPolyline(line);
    p.end();
    if (!img.save(path, "PNG")) { o["ok"] = false; o["note"] = QStringLiteral("写 PNG 失败"); return o; }
    o["path"] = path;
    o["points"] = (int)s11.size();
    return o;
}

QJsonObject ControlHub::cmdExportSpectrumSnapshot(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "export_spectrum_snapshot";
    const QString path = a.value("path").toString();
    if (path.isEmpty()) return errResult(QStringLiteral("参数 path 缺失"));
    const double center = engine_->centerFreq();
    QImage img(960, 480, QImage::Format_ARGB32);
    img.fill(QColor(18, 18, 20));
    QPainter p(&img); p.setRenderHint(QPainter::Antialiasing, true);
    p.setPen(QPen(QColor(120,120,120), 1.0)); p.drawRect(40, 30, 900, 400);
    p.setPen(QPen(QColor(80,180,255), 1.6));
    const double cx = 490;
    p.drawLine(QPointF(cx, 30), QPointF(cx, 430));
    p.setPen(QPen(QColor(220,220,220), 1.0));
    p.drawText(QPointF(cx-40, 460), QString::number(center, 'f', 1));
    p.end();
    if (!img.save(path, "PNG")) { o["ok"] = false; o["note"] = QStringLiteral("写 PNG 失败"); return o; }
    o["path"] = path;
    o["center_hz"] = center;
    return o;
}

QJsonObject ControlHub::cmdExportPeaksCsv(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "export_peaks_csv";
    const QString path = a.value("path").toString();
    if (path.isEmpty()) return errResult(QStringLiteral("参数 path 缺失"));
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Text)) { o["ok"] = false; o["note"] = QStringLiteral("写 CSV 失败"); return o; }
    f.write("freq_hz,db\n");
    f.close();
    o["path"] = path;
    o["rows"] = 0;
    o["note"] = QStringLiteral("引擎峰值列表读访问器未接线：仅写表头（空数据）");
    return o;
}

QJsonObject ControlHub::cmdExportFt8Results(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "export_ft8_results";
    const QString path = a.value("path").toString();
    if (path.isEmpty()) return errResult(QStringLiteral("参数 path 缺失"));
    const bool allHistory = a.value("all_history").toBool(false);
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Text)) { o["ok"] = false; o["note"] = QStringLiteral("写文件失败"); return o; }
    int rows = 0;
    if (allHistory) {
        f.write("time_ms,freq_hz,demod,text\n");
        const auto hist = engine_->ft8History(dsp::SpectrumEngine::kFt8HistoryMax);
        for (int i = hist.size() - 1; i >= 0; --i) {
            const auto& e = hist.at(i);
            const QString t = QString(e.text).replace('"', "\"\"");
            f.write((QString::number(e.time_ms) + "," +
                     QString::number(e.freq_hz, 'f', 1) + "," +
                     e.demod + ",\"" + t + "\"\n").toUtf8());
            ++rows;
        }
    } else {
        f.write("freq_hz,crc_ok,costas_score,rejected,reject_reason,iters\n");
        auto cands = engine_->ft8ExportCandidates();
        for (const auto& c : cands) {
            f.write((QString::number(c.freq_hz, 'f', 1) + "," +
                     (c.crc_ok ? "1" : "0") + "," +
                     QString::number(c.costas_score, 'f', 3) + "," +
                     (c.rejected ? "1" : "0") + ",\"" +
                     QString::fromStdString(c.reject_reason) + "\"," +
                     QString::number(c.iters) + "\n").toUtf8());
            ++rows;
        }
    }
    f.close();
    o["path"] = path;
    o["rows"] = rows;
    o["all_history"] = allHistory;
    if (rows == 0)
        o["note"] = allHistory
            ? QStringLiteral("无 FT8 解码历史：仅写表头")
            : QStringLiteral("无 FT8 候选（无信号/未检出）：仅写表头");
    return o;
}

QJsonObject ControlHub::cmdAnalyzeVnaMatch(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "analyze_vna_match";
    double r = a.value("load_r").toDouble(std::nan(""));
    double x = a.value("load_x").toDouble(0.0);
    double f = a.value("f_hz").toDouble(std::nan(""));
    if (!std::isfinite(r) || !std::isfinite(f) || r <= 0.0 || f <= 0.0) {
        o["valid"] = false; o["note"] = QStringLiteral("需要 load_r(>0) 与 f_hz(>0)"); return o;
    }
    vna::LMatchResult m = vna::efhwMatch({r, x}, f, 50.0);
    o["valid"] = m.valid;
    o["q"] = m.q;
    o["series_henry"] = m.series_henry;
    o["series_farad"] = m.series_farad;
    o["shunt_henry"] = m.shunt_henry;
    o["shunt_farad"] = m.shunt_farad;
    o["topology"] = QString::fromStdString(m.topology);
    if (!m.valid) o["note"] = QStringLiteral("负载电阻需 > 50Ω");
    return o;
}

// P0 #59: single-tool NanoVNA calibration state machine. Mirrors the Agent
// vna_calibration tool. step=status (or omitted) is read-only objective guidance;
// write steps are gated by the write table. slot is only for save/load (1..4).
QJsonObject ControlHub::cmdVnaCalibration(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "vna_calibration";
    auto& vna = engine_->vnaClient();
    const QString step = a.value("step").toString(QStringLiteral("status"));

    if (!vna.isConnected()) {
        o["ok"] = false;
        o["error"] = QStringLiteral("NanoVNA 未连接");
        o["suggestion"] = QStringLiteral(
            "排查 ①USB/CDC 接线（Linux 需 dialout/udev，Windows 见设备管理器 COMx）"
            "②设备是否进入扫频模式 ③串口路径（/dev/ttyACM0 或 COMx）；连接后先 set_vna_sweep 再校准");
        return o;
    }

    // Read-only guidance branch.
    if (step == QStringLiteral("status")) {
        vna.readCalStatus();
        const QStringList cal = vna.calStatus();
        QSet<QString> have(cal.begin(), cal.end());
        QJsonObject steps;
        steps["open"] = have.contains("open"); steps["short"] = have.contains("short");
        steps["load"] = have.contains("load"); steps["isolation"] = have.contains("isolation");
        steps["thru"] = have.contains("thru"); steps["done"] = have.contains("done");
        o["cal_steps"] = steps;
        o["cal_complete"] = have.contains("done");
        if (have.contains("done") && have.contains("open") &&
            have.contains("short") && have.contains("load")) {
            o["next_step"] = QStringLiteral("calibration complete（可用 step=save slot=1..4 保存校准集）");
        } else {
            static const QList<QPair<QString,QString>> kGuide = {
                {"open",      "接 OPEN 标准件到 CH0 后 step=open"},
                {"short",     "接 SHORT 标准件到 CH0 后 step=short"},
                {"load",      "接 LOAD 50Ω 到 CH0 后 step=load"},
                {"isolation", "CH0 接 SOL、CH1 开路后 step=isolation"},
                {"thru",      "CH0–CH1 用 through 直通后 step=thru"},
                {"done",      "就绪后 step=done 完成校准"},
            };
            for (const auto& g : kGuide)
                if (!have.contains(g.first)) { o["next_step"] = g.second; break; }
        }
        return o;
    }

    // slot validation for save/load.
    if (step == QStringLiteral("save") || step == QStringLiteral("load")) {
        if (!a.value("slot").isDouble())
            return errResult(QStringLiteral("save/load 需要 slot（1..4 整数）"));
        const int slot = a.value("slot").toInt();
        if (slot < 1 || slot > 4)
            return errResult(QStringLiteral("slot 必须在 1..4 之间"));
    }

    QString err;
    bool ok = false;
    if (step == QStringLiteral("reset"))           ok = vna.calReset(&err);
    else if (step == QStringLiteral("open"))       ok = vna.calOpen(&err);
    else if (step == QStringLiteral("short"))      ok = vna.calShort(&err);
    else if (step == QStringLiteral("load"))       ok = vna.calLoad(&err);
    else if (step == QStringLiteral("isolation")) ok = vna.calIsolation(&err);
    else if (step == QStringLiteral("thru"))       ok = vna.calThru(&err);
    else if (step == QStringLiteral("done"))       ok = vna.calDone(&err);
    else if (step == QStringLiteral("save"))        ok = vna.calSave(a.value("slot").toInt(), &err);
    else if (step == QStringLiteral("load"))       ok = vna.calLoadSlot(a.value("slot").toInt(), &err);
    else if (step == QStringLiteral("scan"))       ok = vna.scan(&err);
    else if (step == QStringLiteral("pause"))      ok = vna.pauseScan(&err);
    else if (step == QStringLiteral("resume"))     ok = vna.resumeScan(&err);
    else return errResult(QStringLiteral("未知 step，合法值：status/reset/open/short/load/isolation/thru/done/save/load/scan/pause/resume"));

    vna.readCalStatus();
    o["ok"] = ok;
    o["step"] = step;
    if (!ok) o["error"] = err;
    const QStringList cal = vna.calStatus();
    QSet<QString> have(cal.begin(), cal.end());
    QJsonObject steps;
    steps["open"] = have.contains("open"); steps["short"] = have.contains("short");
    steps["load"] = have.contains("load"); steps["isolation"] = have.contains("isolation");
    steps["thru"] = have.contains("thru"); steps["done"] = have.contains("done");
    o["cal_steps"] = steps;
    o["cal_complete"] = have.contains("done");
    o["coverage_recorded"] = vna.calCoverageKnown();
    return o;
}

// P1 #70: device-level misc facade mirror. status/vbat/freq_offset-read are read
// actions; freq_offset-set/edelay/averaging are writes (gated by the write table).
QJsonObject ControlHub::cmdVnaDevice(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "vna_device";
    auto& vna = engine_->vnaClient();
    const QString action = a.value("action").toString(QStringLiteral("status"));

    if (!vna.isConnected()) {
        o["ok"] = false;
        o["error"] = QStringLiteral("NanoVNA 未连接");
        o["suggestion"] = QStringLiteral(
            "排查 ①USB/CDC 接线（Linux 需 dialout/udev，Windows 见设备管理器 COMx）"
            "②设备是否进入扫频模式 ③串口路径（/dev/ttyACM0 或 COMx）；连接后先 set_vna_sweep");
        return o;
    }

    if (action == QStringLiteral("status")) {
        o["connected"] = true;
        o["has_sweep"] = vna.hasSweep();
        o["start_hz"] = (double)vna.sweepStartHz();
        o["stop_hz"] = (double)vna.sweepStopHz();
        o["points"] = vna.sweepPoints();
        o["electrical_delay_ps"] = 0;
        o["averaging"] = 0;
        o["freq_offset_hz"] = 0;
        o["note"] = QStringLiteral(
            "edelay/averaging/freq_offset 当前值本会话未读回，0 不代表设备真实设置");
        return o;
    }

    if (action == QStringLiteral("vbat")) {
        double volts = 0.0; QString err;
        const bool ok = vna.readVbat(volts, &err);
        o["ok"] = ok;
        if (ok) o["volts"] = volts;
        else    o["error"] = err.isEmpty() ? QStringLiteral("读 vbat 失败") : err;
        return o;
    }

    if (action == QStringLiteral("freq_offset")) {
        if (!a.contains("value")) {
            long hz = 0; QString err;
            const bool ok = vna.readFreqOffset(hz, &err);
            o["ok"] = ok;
            if (ok) o["freq_offset_hz"] = (double)hz;
            else    o["error"] = err.isEmpty() ? QStringLiteral("读 freqoffset 失败") : err;
            return o;
        }
        if (!a.value("value").isDouble())
            return errResult(QStringLiteral("freq_offset 写需要 value（Hz 整数）"));
        const long hz = (long)a.value("value").toDouble();
        QString err;
        const bool ok = vna.setFreqOffset(hz, &err);
        o["ok"] = ok;
        o["value"] = (double)hz;
        if (!ok) o["error"] = err.isEmpty() ? QStringLiteral("写 freqoffset 失败") : err;
        return o;
    }

    if (action == QStringLiteral("edelay")) {
        if (!a.value("value").isDouble())
            return errResult(QStringLiteral("edelay 需要 value（电延迟 ps 整数）"));
        const int ps = a.value("value").toInt();
        QString err;
        const bool ok = vna.setElectricalDelay(ps, &err);
        o["ok"] = ok;
        o["value"] = ps;
        if (!ok) o["error"] = err.isEmpty() ? QStringLiteral("写 edelay 失败") : err;
        return o;
    }

    if (action == QStringLiteral("averaging")) {
        if (!a.value("value").isDouble())
            return errResult(QStringLiteral("averaging 需要 value（平均次数 1..64 整数）"));
        const int n = a.value("value").toInt();
        if (n < 1 || n > 64)
            return errResult(QStringLiteral("averaging 必须在 1..64 之间"));
        QString err;
        const bool ok = vna.setAveraging(n, &err);
        o["ok"] = ok;
        o["value"] = n;
        if (!ok) o["error"] = err.isEmpty() ? QStringLiteral("写 averages 失败") : err;
        return o;
    }

    return errResult(QStringLiteral("未知 action，合法值：status/vbat/freq_offset/edelay/averaging"));
}

// ---- P3 #73: local memory family (SQLite FTS5; add/update gated writes) ----
namespace {
bool memoryHubArgs(const QJsonObject& a, QString* scope, QString* scopeId,
                   QString* key, QString* content, QString* bad) {
    *scope = a.value(QStringLiteral("scope")).toString();
    *scopeId = a.value(QStringLiteral("scope_id")).toString();
    *key = a.value(QStringLiteral("key")).toString();
    *content = a.value(QStringLiteral("content")).toString();
    if (*scope != QStringLiteral("user") && *scope != QStringLiteral("agent")
        && *scope != QStringLiteral("session")) {
        *bad = QStringLiteral("scope 必填枚举 user/agent/session"); return false;
    }
    if (scopeId->isEmpty()) { *bad = QStringLiteral("scope_id 必填"); return false; }
    if (key->isEmpty())     { *bad = QStringLiteral("key 必填"); return false; }
    if (content->isEmpty()) { *bad = QStringLiteral("content 必填"); return false; }
    return true;
}
} // namespace

QJsonObject ControlHub::cmdMemoryAdd(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "memory_add";
    QString scope, scopeId, key, content, bad;
    if (!memoryHubArgs(a, &scope, &scopeId, &key, &content, &bad)) return errResult(bad);
    QString err;
    if (!mbdsdr::ai::memoryWriter().submit(mbdsdr::ai::MemoryWriteRequest::Add,
                                            scope, scopeId, key, content, &err)) {
        o["ok"] = false; o["error"] = err;
    } else {
        o["queued"] = true;
        o["pending_count"] = mbdsdr::ai::memoryWriter().pendingCount();
        o["scope"] = scope; o["scope_id"] = scopeId; o["key"] = key;
    }
    return o;
}

QJsonObject ControlHub::cmdMemorySearch(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "memory_search";
    const QString query = a.value(QStringLiteral("query")).toString().trimmed();
    if (query.isEmpty()) return errResult(QStringLiteral("query 必填且不可为空"));
    mbdsdr::ai::MemoryStore store(mbdsdr::ai::defaultMemoryDbPath());
    QString err;
    if (!store.open(&err)) { o["ok"] = false; o["error"] = QStringLiteral("记忆库不可用：%1").arg(err); return o; }
    const int limit = (int)a.value(QStringLiteral("limit")).toDouble(20.0);
    QList<mbdsdr::ai::MemoryHit> hits = store.search(query, a.value(QStringLiteral("scope")).toString(), limit, &err);
    if (hits.isEmpty() && !err.isEmpty()) { o["ok"] = false; o["error"] = err; return o; }
    QJsonArray arr;
    for (const mbdsdr::ai::MemoryHit& h : hits) {
        QJsonObject hh;
        hh["scope"] = h.scope; hh["scope_id"] = h.scopeId;
        hh["key"] = h.key; hh["content"] = h.content; hh["snippet"] = h.snippet;
        arr.append(hh);
    }
    o["hits"] = arr;
    o["count"] = arr.size();
    if (arr.isEmpty()) o["note"] = QStringLiteral("无命中（检索词：%1）").arg(query);
    return o;
}

QJsonObject ControlHub::cmdMemoryUpdate(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "memory_update";
    QString scope, scopeId, key, content, bad;
    if (!memoryHubArgs(a, &scope, &scopeId, &key, &content, &bad)) return errResult(bad);
    QString err;
    if (!mbdsdr::ai::memoryWriter().submit(mbdsdr::ai::MemoryWriteRequest::Update,
                                            scope, scopeId, key, content, &err)) {
        o["ok"] = false; o["error"] = err;
    } else {
        o["queued"] = true;
        o["pending_count"] = mbdsdr::ai::memoryWriter().pendingCount();
        o["scope"] = scope; o["scope_id"] = scopeId; o["key"] = key;
    }
    return o;
}

QJsonObject ControlHub::cmdCheckpointStatus(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "checkpoint_status";
    o["pending_count"] = mbdsdr::ai::memoryWriter().pendingCount();
    o["queue_depth"] = mbdsdr::ai::memoryWriter().queueDepth();
    o["last_flush_ms"] = (double)mbdsdr::ai::memoryWriter().lastFlushMs();
    return o;
}

QJsonObject ControlHub::cmdMemoryFlush(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "memory_flush";
    QString err;
    const int written = mbdsdr::ai::memoryWriter().flush(&err);
    o["written"] = written;
    o["checkpoint_rewritten"] = true;
    if (!err.isEmpty()) {
        o["ok"] = false; o["error"] = err;
        o["note"] = QStringLiteral("部分条目落库失败；已处理 %1 条").arg(written);
    }
    return o;
}

// G1: read-only task-run history (mirrors the Agent get_task_history tool).
QJsonObject ControlHub::cmdGetTaskHistory(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "get_task_history";
    mbdsdr::ai::MemoryStore store(mbdsdr::ai::defaultMemoryDbPath());
    QString err;
    if (!store.open(&err)) { o["ok"] = false; o["error"] = QStringLiteral("任务历史库不可用：%1").arg(err); return o; }
    const QJsonValue idVal = a.value(QStringLiteral("id"));
    if (!idVal.isNull() && !idVal.isUndefined()) {
        const qint64 id = (qint64)idVal.toDouble(-1.0);
        mbdsdr::ai::TaskRunRecord rec;
        if (!store.getTaskRun(id, &rec, &err)) {
            o["ok"] = false; o["error"] = err;
            o["note"] = QStringLiteral("没有该 id 的任务记录；用列表模式先查最近任务");
            return o;
        }
        QJsonObject run;
        run["id"] = (double)rec.id;
        run["name"] = rec.name;
        run["state"] = rec.state;
        run["finished_ms"] = (double)rec.finishedMs;
        run["step_count"] = rec.stepCount;
        run["summary"] = rec.summary;
        const QJsonDocument sd = QJsonDocument::fromJson(rec.stepsJson.toUtf8());
        run["steps"] = sd.isArray() ? sd.array() : QJsonArray{};
        o["run"] = run;
    } else {
        const int limit = (int)a.value(QStringLiteral("limit")).toDouble(10.0);
        const QList<mbdsdr::ai::TaskRunRecord> runs = store.listTaskRuns(limit, &err);
        if (runs.isEmpty() && !err.isEmpty()) { o["ok"] = false; o["error"] = err; return o; }
        QJsonArray arr;
        for (const mbdsdr::ai::TaskRunRecord& r : runs) {
            QJsonObject rr;
            rr["id"] = (double)r.id;
            rr["name"] = r.name;
            rr["state"] = r.state;
            rr["finished_ms"] = (double)r.finishedMs;
            rr["step_count"] = r.stepCount;
            rr["summary"] = r.summary;
            arr.append(rr);
        }
        o["runs"] = arr;
        o["count"] = arr.size();
        // 待执行计划槽快照（run_template 登记、执行后状态真实回写），与 Agent
        // get_task_history 同读面。空槽 plans=[] 诚实空态。
        const QJsonObject pending = mbdsdr::ai::PendingPlanStore::instance().toJson();
        o["pending_plans"] = pending.value("plans");
        o["pending_count"] = pending.value("count");
        if (arr.isEmpty()) o["note"] = QStringLiteral("无任务历史（TaskRunner 尚未完成过任何任务）");
    }
    return o;
}

// G2: synchronous delete one memory by (scope,key); honest found/not-found.
QJsonObject ControlHub::cmdMemoryDelete(const QJsonObject& a) {
    QJsonObject o = okBase();
    o["command"] = "memory_delete";
    const QString scope = a.value(QStringLiteral("scope")).toString();
    const QString key = a.value(QStringLiteral("key")).toString();
    if (scope != QStringLiteral("user") && scope != QStringLiteral("agent")
        && scope != QStringLiteral("session"))
        return errResult(QStringLiteral("scope 必填枚举 user/agent/session"));
    if (key.isEmpty()) return errResult(QStringLiteral("key 必填（scope 内唯一键）"));
    mbdsdr::ai::MemoryStore store(mbdsdr::ai::defaultMemoryDbPath());
    QString err;
    if (!store.open(&err)) { o["ok"] = false; o["error"] = QStringLiteral("记忆库不可用：%1").arg(err); return o; }
    bool found = false;
    if (!store.deleteKey(scope, key, &found, &err)) {
        o["ok"] = false; o["error"] = err;
    } else if (!found) {
        o["deleted"] = false;
        o["scope"] = scope; o["key"] = key;
        o["note"] = QStringLiteral("该 (scope,key) 不存在，删除为空操作；不编造已删条目");
    } else {
        o["deleted"] = true;
        o["scope"] = scope; o["key"] = key;
    }
    return o;
}

// G2: read-only memory statistics.
QJsonObject ControlHub::cmdMemoryStats(const QJsonObject&) {
    QJsonObject o = okBase();
    o["command"] = "memory_stats";
    mbdsdr::ai::MemoryStore store(mbdsdr::ai::defaultMemoryDbPath());
    QString err;
    if (!store.open(&err)) { o["ok"] = false; o["error"] = QStringLiteral("记忆库不可用：%1").arg(err); return o; }
    QJsonObject st = store.stats(&err);
    if (st.isEmpty() && !err.isEmpty()) { o["ok"] = false; o["error"] = err; return o; }
    o["stats"] = st;
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
    // Phase63 D4: consume `group` so the CH/HTTP channel and the Agent channel
    // agree on which fields a bookmark carries. The Agent executor has read
    // group all along (agent_tools.cpp execAddBookmark); CH was silently dropping
    // it.
    b.group = a.value(QStringLiteral("group")).toString();
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

// ---- 第 10 域「工作台编排」(AIGUI P0-b) -----------------------------------
// These mirror the four agent workspace tools through the injected
// WorkspaceController. When the controller is not injected (headless tests) they
// return an honest error; they never touch DSP / audio / hardware.
QJsonObject ControlHub::cmdArrangeWorkspace(const QJsonObject& a) {
    if (!workspace_)
        return errResult(QString::fromUtf8("工作台控制器未注入（MainWindow 未创建 WorkspaceController）"));
    QJsonObject o = workspace_->applyOverlay(a);
    o["command"] = "arrange_workspace";
    return o;
}

QJsonObject ControlHub::cmdGetWorkspaceLayout(const QJsonObject&) {
    if (!workspace_)
        return errResult(QString::fromUtf8("工作台控制器未注入（MainWindow 未创建 WorkspaceController）"));
    QJsonObject o = workspace_->currentSpec();
    o["command"] = "get_workspace_layout";
    return o;
}

QJsonObject ControlHub::cmdListUiComponents(const QJsonObject&) {
    if (!workspace_)
        return errResult(QString::fromUtf8("工作台控制器未注入（MainWindow 未创建 WorkspaceController）"));
    QJsonObject o = workspace_->componentCatalog();
    o["command"] = "list_ui_components";
    return o;
}

QJsonObject ControlHub::cmdResetWorkspace(const QJsonObject&) {
    if (!workspace_)
        return errResult(QString::fromUtf8("工作台控制器未注入（MainWindow 未创建 WorkspaceController）"));
    QJsonObject o = workspace_->reset();
    o["command"] = "reset_workspace";
    return o;
}

QJsonObject ControlHub::cmdUpdateComponentData(const QJsonObject& a) {
    if (!workspace_)
        return errResult(QString::fromUtf8("工作台控制器未注入（MainWindow 未创建 WorkspaceController）"));
    const QString componentId = a.value(QStringLiteral("component_id")).toString();
    if (componentId.isEmpty())
        return errResult(QString::fromUtf8("缺少 component_id（白名单组件 id）"));
    if (!a.contains(QStringLiteral("data")))
        return errResult(QString::fromUtf8("缺少 data（object=upsert，null=清空）"));
    QJsonObject o = workspace_->updateComponentData(componentId, a.value(QStringLiteral("data")));
    o["command"] = "update_component_data";
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
    // Phase63 D3 bilateral dual-type: accept BOTH the raw int (historic CH
    // contract) AND the string enum ("Hann"/"Flattop"/"Blackman", the Agent
    // LLM schema contract). Either lands on the same engine setter.
    const QJsonValue w = a.value(QStringLiteral("window"));
    if (w.isDouble()) {
        engine_->setWindowType(w.toInt()); o["window"] = w.toInt(); has = true;
    } else if (w.isString()) {
        const QString ws = w.toString();
        int wi = 0;   // 0=Hann 1=Flattop 2=Blackman
        if (ws == QLatin1String("Flattop")) wi = 1;
        else if (ws == QLatin1String("Blackman")) wi = 2;
        engine_->setWindowType(wi); o["window"] = ws; has = true;
    }
    const QJsonValue av = a.value(QStringLiteral("average"));
    if (av.isDouble()) {
        engine_->setAverageMode(av.toInt()); o["average"] = av.toInt(); has = true;
    } else if (av.isString()) {
        const QString avs = av.toString();
        int ai = 0;   // 0=Off 1=Slow 2=Fast
        if (avs == QLatin1String("Slow")) ai = 1;
        else if (avs == QLatin1String("Fast")) ai = 2;
        engine_->setAverageMode(ai); o["average"] = avs; has = true;
    }
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
    ui::ParsedColormap parsed;
    QString err;
    if (!ui::loadColormapFileForTool(path, &parsed, &err))
        return errResult(err);
    mbdsdr::core::AppSettings()
        .setValue(QLatin1String(tokens::kSettingsKeyColormapFile), path);
    QJsonObject o = okBase();
    o["command"] = "set_color_map";
    o["file_path"] = path;
    o["name"] = QString::fromUtf8(parsed.name.c_str());
    o["stops"] = static_cast<int>(parsed.stops.size());
    o["note"] = QString::fromUtf8("色板文件已加载校验通过并持久化（UI 启动时重应用同一文件）");
    return o;
}

QJsonObject ControlHub::cmdGetColorMaps(const QJsonObject&) {
    QJsonArray builtin;
    QJsonObject b1; b1["name"] = QStringLiteral("经典");   b1["palette"] = 0; builtin.append(b1);
    QJsonObject b2; b2["name"] = QStringLiteral("单色");   b2["palette"] = 1; builtin.append(b2);
    QJsonObject b3; b3["name"] = QStringLiteral("Viridis"); b3["palette"] = 2; builtin.append(b3);
    QJsonObject o = okBase();
    o["command"] = "get_color_maps";
    o["builtin"] = builtin;
    const QString path = mbdsdr::core::AppSettings()
        .value(QLatin1String(tokens::kSettingsKeyColormapFile)).toString();
    if (path.isEmpty()) {
        o["external"] = QJsonValue::Null;
        o["note"] = QString::fromUtf8("未配置外部色板文件");
    } else {
        ui::ParsedColormap parsed;
        QString err;
        if (ui::loadColormapFileForTool(path, &parsed, &err)) {
            QJsonObject e;
            e["path"] = path;
            e["name"] = QString::fromUtf8(parsed.name.c_str());
            e["stops"] = static_cast<int>(parsed.stops.size());
            o["external"] = e;
        } else {
            o["external"] = QJsonValue::Null;
            o["note"] = QString::fromUtf8("配置的外部色板文件当前不可用：%1").arg(err);
        }
    }
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

// Deterministic task templates. Mirrors the Agent list_templates / run_template
// tools by reusing the internal plan* builders (no reimplementation). The
// engine guard in execute() already rejected headless callers before we get here.
QJsonObject ControlHub::cmdListTemplates(const QJsonObject&) {
    QJsonArray arr;
    for (const ai::TaskTemplateInfo& t : ai::taskTemplateCatalog()) {
        QJsonObject e;
        e["name"] = t.key;
        e["description"] = t.description;
        e["params"] = QJsonArray::fromStringList(t.params);
        e["steps"] = t.stepCount;
        arr.append(e);
    }
    QJsonObject o = okBase();
    o["command"] = "list_templates";
    o["templates"] = arr;
    o["count"] = arr.size();
    return o;
}

QJsonObject ControlHub::cmdRunTemplate(const QJsonObject& a) {
    const QString name = a.value(QStringLiteral("template_name")).toString();
    if (name.isEmpty())
        return errResult(QString::fromUtf8("缺少 template_name（用 list_templates 查看可用模板）"));

    QString err;
    ai::TaskPlan plan;
    if (name == QLatin1String("sweep_find_record")) {
        double lowHz, highHz, stepHz;
        if (!needDbl(a, "low_hz", lowHz, err)) return errResult(err);
        if (!needDbl(a, "high_hz", highHz, err)) return errResult(err);
        if (!needDbl(a, "step_hz", stepHz, err)) return errResult(err);
        QString mode;
        if (!needMode(a, "mode", mode, err)) return errResult(err);
        const QString book = a.value(QStringLiteral("book_name")).toString();
        if (book.isEmpty()) return errResult(QString::fromUtf8("缺少书签名: book_name"));
        plan = ai::planSweepFindAndRecord(lowHz, highHz, stepHz, mode, book);
    } else if (name == QLatin1String("target_capture")) {
        double targetHz;
        if (!needDbl(a, "target_hz", targetHz, err)) return errResult(err);
        QString mode;
        if (!needMode(a, "mode", mode, err)) return errResult(err);
        plan = ai::planTargetCapture(targetHz, mode);
    } else if (name == QLatin1String("fixed_freq_record")) {
        double freqHz, bwHz;
        if (!needDbl(a, "freq_hz", freqHz, err)) return errResult(err);
        if (!needDbl(a, "bandwidth_hz", bwHz, err)) return errResult(err);
        QString mode;
        if (!needMode(a, "mode", mode, err)) return errResult(err);
        plan = ai::planFixedFrequencyRecord(freqHz, mode, bwHz);
    } else {
        return errResult(QString::fromUtf8("未知 template_name: %1（合法: sweep_find_record/target_capture/fixed_freq_record）")
                             .arg(name));
    }

    QJsonArray steps;
    for (const ai::TaskStep& s : plan.steps) {
        QJsonObject step;
        step["tool"] = s.tool;
        step["description"] = s.description;
        step["arg_keys"] = QJsonArray::fromStringList(s.args.keys());
        steps.append(step);
    }
    // 真实登记进待执行计划槽（与 Agent run_template 同一张登记表）：回执携带
    // 真实 pending_plan_id；执行仍需用户确认后经 TaskRunner::runPendingPlan。
    const int planId = ai::PendingPlanStore::instance().submit(plan, name);
    QJsonObject o = okBase();
    o["command"] = "run_template";
    o["template_name"] = name;
    o["plan_name"] = plan.name;
    o["steps"] = steps;
    o["step_count"] = plan.steps.size();
    o["executed"] = false;
    o["pending_plan_id"] = planId;
    o["plan_status"] = QString::fromUtf8("pending");
    o["consumed"] = false;
    o["note"] = QString::fromUtf8(
        "计划已登记为待执行计划 #%1（PendingPlanStore 真实登记表，状态 pending）。"
        "本通道不做同步调谐/录制（executed=false）；需用户确认后经 AI 面板「执行待执行计划」"
        "按钮或 TaskRunner::runPendingPlan(#%1) 异步执行，执行状态真实回写登记表，"
        "可经 get_task_history 的 pending_plans 查询进度。").arg(planId);
    return o;
}

} // namespace control
} // namespace mbdsdr
