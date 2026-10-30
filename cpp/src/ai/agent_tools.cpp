// SPDX-License-Identifier: MIT
#include "agent_tools.h"
#include "core/app_settings.h"
#include "tool_schema.h"
#include "sat_task_planner.h"
#include "ai_config.h"           // AiConfig: set_station_position persistence (D1-H1)
#include "core/tokens.h"
#include "ui/spectrum_render.h"
#include "dsp/spectrum_engine.h"
#include "dsp/control_surface.h"  // UiControlSurface seam (D1 round 3 tools)
// (file named control_surface.h, NOT ui_*.h: the ui_ prefix is reserved for
//  uic-generated headers and trips CMake AUTOUIC into hunting a .ui file)
#include "vna/vna_rf.h"
#include "dsp/device_capabilities.h"
#include "dsp/frequency_calibrator.h"   // calibrateFromCapture / savePpmSetting
#include "dsp/fcch_detector.h"          // kFcchToneHz
#include "ui/bookmark_manager.h"   // ui::BookmarkManager / ui::Bookmark (real Agent-layer store)
#include "ui/workspace_controller.h"  // aigui::WorkspaceController (P0-b workspace seam)
#include "memory_store.h"         // SQLite FTS5 local memory store
#include "checkpoint_writer.h"     // background single-writer drain for memory writes
#include "pending_plan_store.h"    // 待执行计划槽：run_template 登记 / get_task_history 读面

#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QSet>
#include <QStringList>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSettings>
#include <QImage>
#include <QPainter>
#include <QPen>
#include <QPolygonF>
#include <QApplication>   // set_ui_scale: qApp->setStyleSheet (mirror main_window.cpp:3435)
#include <QAudioDevice>   // set_audio_output: QAudioDevice() = system default
#include <QDateTime>      // set_sat_pass_template: currentDateTimeUtc
#include <QMediaDevices>  // set_audio_output: audioOutputs() description match
#include <cmath>
#include <limits>

namespace mbdsdr {
namespace ai {

QString gatedToolResult(const QString& toolName) {
    QJsonObject o;
    o["ok"] = false;
    o["gated"] = true;
    o["error"] = QString("手动模式：未执行 %1").arg(toolName);
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

// Honest source snapshot shared by every tool result, so the model always knows
// whether it is acting on real hardware or the offline test signal (A4 P1/P2:
// the source layer already knows this; the tool result must TELL the model).
namespace {
struct SourceInfo {
    bool connected = false;
    bool testSignal = false;
    QString sourceName;   // human label: real device / 测试信号 / 未连接
};

SourceInfo readSourceInfo(dsp::SpectrumEngine* engine) {
    SourceInfo s;
    const dsp::DeviceCapabilities caps = engine->sourceCapabilities();
    s.connected = caps.connected;
    s.testSignal = engine->isTestSignalActive();
    if (s.testSignal) {
        s.sourceName = QString::fromUtf8("测试信号（合成 IQ，非真实硬件）");
    } else if (s.connected) {
        s.sourceName = caps.deviceName.isEmpty()
            ? QString::fromUtf8("已连接设备") : caps.deviceName;
    } else {
        s.sourceName = QString::fromUtf8("未连接");
    }
    return s;
}

void addSourceFields(QJsonObject& o, const SourceInfo& s) {
    o["connected"] = s.connected;
    o["test_signal"] = s.testSignal;
    o["source"] = s.sourceName;
}

QString compact(const QJsonObject& o) {
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

// cal_ readback tokens from the firmware are space-split set items. A
// compensation-set token matches ^c\d+$ (e.g. "c1"..="calibration set 1 applied").
// Hand-rolled so we need no QRegularExpression include on this hot path.
bool isCalCompensationSet(const QString& t) {
    if (t.size() < 2 || t.at(0) != QLatin1Char('c')) return false;
    for (int i = 1; i < t.size(); ++i)
        if (!t.at(i).isDigit()) return false;
    return true;
}

// The six canonical SOLT calibration step words, in NanoVNA protocol order.
const QStringList& calStepWords() {
    static const QStringList k{QStringLiteral("open"), QStringLiteral("short"),
                               QStringLiteral("load"), QStringLiteral("isolation"),
                               QStringLiteral("thru"), QStringLiteral("done")};
    return k;
}

// Honest calibration confidence + coverage-out-of-range interpretation, shared
// by get_vna_data / get_vna_status / vna_calibration so the three faces never
// disagree. Nothing is fabricated: unconnected or bare "done" -> "unknown".
void addCalInterpretation(QJsonObject& o, const vna::NanoVnaClient& vna) {
    const QStringList cal = vna.calStatus();
    const QSet<QString> have(cal.begin(), cal.end());
    const bool hasDone   = have.contains(QStringLiteral("done"));
    const bool haveOpen  = have.contains(QStringLiteral("open"));
    const bool haveShort = have.contains(QStringLiteral("short"));
    const bool haveLoad  = have.contains(QStringLiteral("load"));

    QString confidence;
    if (!vna.isConnected()) {
        confidence = QString::fromUtf8("unknown");
    } else if (hasDone && haveOpen && haveShort && haveLoad) {
        confidence = QString::fromUtf8("calibrated");
    } else if (haveOpen || haveShort || haveLoad) {
        // At least one SOL measurement recorded but the set is incomplete.
        QStringList missing;
        for (const QString& w : calStepWords())
            if (!have.contains(w)) missing << w;
        confidence = QString::fromUtf8("partial: %1").arg(missing.join(QLatin1Char(',')));
    } else {
        // Empty cal_ or a bare "done" (no SOL steps verifiable) -> unknown, never
        // claim calibrated on a token we cannot prove.
        confidence = QString::fromUtf8("unknown");
    }
    o["cal_confidence"] = confidence;

    bool outOfRange = false;
    QString oorNote;
    if (vna.calCoverageKnown()) {
        const long curStart = vna.sweepStartHz();
        const long curStop  = vna.sweepStopHz();
        if (curStart != vna.lastCalStartHz() || curStop != vna.lastCalStopHz()) {
            outOfRange = true;
            oorNote = QString::fromUtf8(
                          "扫频范围已改变（当前 %1–%2 Hz vs 校准时 %3–%4 Hz），"
                          "校准可能失效，建议重校或恢复原范围")
                          .arg(curStart).arg(curStop)
                          .arg(vna.lastCalStartHz()).arg(vna.lastCalStopHz());
        } else {
            oorNote = QString::fromUtf8("扫频范围与校准时一致，校准覆盖有效");
        }
    } else {
        oorNote = QString::fromUtf8("校准覆盖范围未知（本会话未在校准时记录扫频）");
    }
    o["cal_out_of_range"] = outOfRange;
    o["cal_out_of_range_note"] = oorNote;
}

// Build the structured cal_steps / missing_steps / compensation-sets / complete
// object from the current (already refreshed) cal_ readback. Shared by
// get_vna_status and the vna_calibration status + post-write readback.
void fillCalSteps(QJsonObject& o, const vna::NanoVnaClient& vna) {
    const QStringList cal = vna.calStatus();
    const QSet<QString> have(cal.begin(), cal.end());
    QJsonObject steps;
    steps["open"]      = have.contains(QStringLiteral("open"));
    steps["short"]     = have.contains(QStringLiteral("short"));
    steps["load"]      = have.contains(QStringLiteral("load"));
    steps["isolation"] = have.contains(QStringLiteral("isolation"));
    steps["thru"]      = have.contains(QStringLiteral("thru"));
    steps["done"]      = have.contains(QStringLiteral("done"));
    o["cal_steps"] = steps;

    QStringList missing;
    for (const QString& w : calStepWords())
        if (!have.contains(w)) missing << w;
    o["missing_steps"] = QJsonArray::fromStringList(missing);

    QJsonArray comp;
    for (const QString& t : cal)
        if (isCalCompensationSet(t)) comp.append(t);
    o["cal_compensation_sets"] = comp;

    o["cal_complete"] = have.contains(QStringLiteral("done"));

    QJsonArray unknown;
    for (const QString& t : cal) {
        if (!calStepWords().contains(t) && !isCalCompensationSet(t))
            unknown.append(t);
    }
    o["cal_unknown_items"] = unknown;
}

// Forward declaration: the honest-error helper is defined in the Phase26 block
// below (~line 570); the early executors (tune_frequency / set_mode) reuse it.
namespace {
QString errResult(const QString& msg);
}

// ---- Built-in tool executors ------------------------------------------------
// Each was one branch of the old if-else chain in executeTool(). They are now
// plain functions bound into the registry table below; the bodies are verbatim
// (same JSON keys, same order, same messages) so on-wire output never drifts.
// `src` is the honest source snapshot read once per call and injected here.

QString execTuneFrequency(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double f = args["freq_hz"].toDouble();
    // Honest guard (parity with the engine guard spectrum_engine.cpp:214 and CH
    // needDbl): a non-positive / non-finite frequency must be REJECTED here with
    // ok:false -- otherwise the engine drops it silently while we echo f back as
    // success and the next get_status still reports the old value (fake success).
    if (!(f > 0.0) || !std::isfinite(f))
        return errResult(QString::fromUtf8("频率必须为正的有限数"));
    engine->onSetCenterFreq(f);
    QJsonObject o;
    o["ok"] = true;
    o["frequency_hz"] = f;
    o["message"] = QString("已调谐到 %1 MHz").arg(f / 1e6, 0, 'f', 3);
    addSourceFields(o, src);
    return compact(o);
}

QString execSetMode(const QJsonObject& args, dsp::SpectrumEngine* engine,
                    const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    // Whitelist (parity with CH needMode and execSetVfoMode): the engine's
    // VfoManager accepts ANY mode string, so without this gate an unknown mode
    // would be echoed as applied while the demod never really changes -- reject
    // it honestly. Case-insensitive match against the token table, but the
    // canonical SPELLING from the table is what gets applied/echoed: the
    // engine's decoder key-matching is exact ("m17" stays lowercase,
    // "POCSAG" stays uppercase) -- the old toUpper() made "m17" unreachable (D2-1).
    QString canonical;
    for (int k = 0; k < tokens::kControlHubModesCount; ++k) {
        if (args["mode"].toString().compare(QLatin1String(tokens::kControlHubModes[k]),
                                            Qt::CaseInsensitive) == 0) {
            canonical = QString::fromUtf8(tokens::kControlHubModes[k]);
            break;
        }
    }
    if (canonical.isEmpty())
        return errResult(QString::fromUtf8("未知解调模式: %1")
                             .arg(args["mode"].toString()));
    engine->setDemodMode(canonical);
    QJsonObject o;
    o["ok"] = true;
    o["mode"] = canonical;
    o["message"] = QString("解调模式切换为 %1").arg(canonical);
    addSourceFields(o, src);
    return compact(o);
}

QString execStartRecording(const QJsonObject& /*args*/, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    // Propagate the engine's real bool: a failed recorder must NOT be
    // reported as "开始录制" (A4 P1-4). On the offline test source the
    // recorder really does write a SigMF file (sidecar labels it
    // "Test Signal"); that success is honest, but a failure is now honest too.
    bool ok = engine->startRecording();
    QJsonObject o;
    o["ok"] = ok;
    if (ok) {
        o["message"] = QString("开始录制");
        o["path"] = engine->recordingPath();
        addSourceFields(o, src);
    } else {
        o["error"] = QString("录制启动失败");
    }
    return compact(o);
}

QString execStopRecording(const QJsonObject& /*args*/, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    engine->stopRecording();
    QJsonObject o;
    o["ok"] = true;
    o["message"] = QString("停止录制");
    addSourceFields(o, src);
    return compact(o);
}

// WRITE action: one-shot IQ export to a cf32_le SigMF file on disk (gated in
// manual mode). Distinct from start_recording: it dumps a bounded, on-demand
// window NOW and returns, rather than recording continuously until stopped.
// No source data -> honest ok:false (never a fabricated empty file).
QString execExportIqSegment(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    int sampleCount = -1;
    if (args.contains(QStringLiteral("sample_count")) &&
        args.value(QStringLiteral("sample_count")).isDouble()) {
        sampleCount = static_cast<int>(args["sample_count"].toDouble());
    } else if (args.contains(QStringLiteral("seconds")) &&
               args.value(QStringLiteral("seconds")).isDouble()) {
        // Duration form (D2-7): the UI dialog exports by seconds; convert
        // through the engine's real sample rate. sample_count wins when both
        // are supplied (documented in the schema).
        const double secs = args.value(QStringLiteral("seconds")).toDouble();
        const double sr = engine->latestSampleRateHz();
        if (!(sr > 0.0) || !std::isfinite(sr))
            return errResult(QString::fromUtf8(
                "无法按 seconds 换算：引擎尚无有效采样率（源未运行）——"
                "改用 sample_count，或先连接/启动信号源"));
        sampleCount = static_cast<int>(sr * secs + 0.5);
    } else {
        sampleCount = 65536;   // documented default
    }
    double tuneHz = -1.0;   // default: keep the current centre
    if (args.contains("tune_hz") && !args["tune_hz"].isNull()) {
        if (!args["tune_hz"].isDouble()) {
            QJsonObject o;
            o["ok"] = false;
            o["error"] = QString::fromUtf8("参数 tune_hz 必须是数字（Hz，缺省=保持当前中心）");
            return compact(o);
        }
        tuneHz = args["tune_hz"].toDouble();
    }

    QString path; double sr = 0.0, center = 0.0;
    qint64 samples = 0, bytes = 0; QString err;
    const bool ok = engine->exportIqSegment(sampleCount, tuneHz, path,
                                            sr, center, samples, bytes, err);
    QJsonObject o;
    o["ok"] = ok;
    if (ok) {
        o["message"] = QString::fromUtf8("已导出 IQ 段：%1 个复样本（%2 字节）-> %3")
                           .arg(samples).arg(bytes).arg(path);
        o["path"] = path;
        o["sample_rate_hz"] = sr;
        o["center_hz"] = center;
        o["samples"] = samples;
        o["bytes"] = bytes;
        addSourceFields(o, src);
    } else {
        o["error"] = err;
    }
    return compact(o);
}

QString execScanBand(const QJsonObject& args, dsp::SpectrumEngine* engine,
                     const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double low = args["low_hz"].toDouble();
    double high = args["high_hz"].toDouble();
    double step = args["step_hz"].toDouble(200000);
    double peakFreq = 0.0;
    double peak = engine->scanBand(low, high, step, &peakFreq);
    // Structured result so downstream task steps can reference the real hit
    // frequency via JSON path (hits[0].frequencyHz).
    QJsonObject hit;
    hit["frequencyHz"] = peakFreq;
    hit["dbfs"] = peak;
    QJsonObject o;
    o["ok"] = true;
    o["lowHz"] = low;
    o["highHz"] = high;
    o["stepHz"] = step;
    o["peakDbfs"] = peak;
    o["hits"] = QJsonArray{hit};
    addSourceFields(o, src);
    // On the offline test signal the synth tone sits at a fixed baseband
    // offset independent of the swept center frequency, so the "peak" has no
    // physical meaning as an RF station hit. Label it honestly (A4 P1-3).
    if (src.testSignal) {
        o["synthetic"] = true;
        o["note"] = QString::fromUtf8(
            "合成测试信号扫描：峰值为固定基带偏音，与扫频中心无关，非真实电台命中");
    }
    return compact(o);
}

QString execSetBandwidth(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double bw = args["bandwidth_hz"].toDouble();
    // RAW direct-listen has NO demodulation filter: channelized IQ is passed
    // straight through as L=I/R=Q. Refuse honestly (ok:false) rather than
    // echoing a bandwidth that DemodRaw would ignore.
    if (engine->demodMode() == QLatin1String("RAW")) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8(
            "带宽对 RAW 直通无意义：RAW 为所选 VFO 信道化窄带 IQ 的 L=I/R=Q 直通，无解调滤波器可设");
        addSourceFields(o, src);
        return compact(o);
    }
    engine->setBandwidth(bw);
    QJsonObject o;
    o["ok"] = true;
    o["bandwidth_hz"] = bw;
    o["message"] = QString("带宽设为 %1 Hz").arg(bw, 0, 'f', 0);
    addSourceFields(o, src);
    return compact(o);
}

QString execGetStatus(const QJsonObject& /*args*/, dsp::SpectrumEngine* engine,
                      const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    // Aligned with Flutter ai_tools.dart get_status: connected + source, plus
    // the read-back values. We do NOT fabricate gain/sample-rate the engine
    // does not expose. A human-readable summary is kept so existing UI copy
    // and callers still find "频率"/mode text.
    QJsonObject o;
    o["ok"] = true;
    o["connected"] = src.connected;
    o["test_signal"] = src.testSignal;
    o["source"] = src.sourceName;
    o["frequency_hz"] = engine->centerFreq();
    o["mode"] = engine->demodMode();
    o["bandwidth_hz"] = engine->bandwidth();
    // Phase55 block2: Costas carrier-lock snapshot of the selected VFO. On an
    // analog (non-BPSK/QPSK) channel the engine returns an honest all-false
    // lock status -- we never fabricate a lock.
    const dsp::DigitalLockStatus lock = engine->digitalLockStatus();
    o["carrier_locked"] = lock.carrierLocked;
    o["symbol_locked"] = lock.symbolLocked;
    o["evm_percent"] = lock.evmPercent;
    // Phase55 block3: live Doppler compensation state via the UI control surface.
    auto* surf = engine->dopplerControlSurface();
    o["doppler_available"] = surf && surf->isDopplerCompensationAvailable();
    o["doppler_enabled"] = surf && surf->isDopplerCompensationEnabled();
    o["summary"] = QString("频率=%1MHz 模式=%2 带宽=%3Hz 连接=%4")
        .arg(engine->centerFreq() / 1e6, 0, 'f', 3)
        .arg(engine->demodMode())
        .arg(engine->bandwidth(), 0, 'f', 0)
        .arg(src.connected ? QString::fromUtf8("已连接")
                           : QString::fromUtf8("未连接"));
    return compact(o);
}

QString execPredictPasses(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                           const SourceInfo& /*src*/,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    // Read-only: does NOT touch the radio. Uses the FRESH on-disk TLE cache
    // via the planner; no builtin/demo TLE is reported as a real pass.
    QString sat = args["satellite_name"].toString();
    int hours = static_cast<int>(args["hours_ahead"].toDouble(24.0));
    if (hours < 1) hours = 24;
    bool hasLat = args.contains("station_lat_deg") && args["station_lat_deg"].isDouble();
    bool hasLon = args.contains("station_lon_deg") && args["station_lon_deg"].isDouble();
    double lat = hasLat ? args["station_lat_deg"].toDouble() : qQNaN();
    double lon = hasLon ? args["station_lon_deg"].toDouble() : qQNaN();

    SatPassListResult r = predictSatellitePasses(
        sat, lat, lon, QDateTime::currentDateTimeUtc(), hours);
    QJsonObject o;
    if (!r.ok) {
        o["ok"] = false;
        o["error"] = r.error;
        o["source"] = r.source.isEmpty()
            ? QString::fromUtf8("无新鲜 TLE 缓存") : r.source;
        return compact(o);
    }
    o["ok"] = true;
    o["source"] = r.source;   // "cached_tle" -- honest about the data provenance
    QJsonArray arr;
    for (const SatPassEntry& e : r.passes) {
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
    return compact(o);
}

// list_satellites (read-only): catalog the on-disk TLE cache. Prerequisite for
// predict_passes -- the model needs to know which satellite names are actually
// cached before it can predict a pass. Uses ONLY the on-disk cache; the stale
// builtinTle() (epoch 2006) is never listed. Honest empty state when no cache.
QString execListSatellites(const QJsonObject& /*args*/, dsp::SpectrumEngine* /*engine*/,
                           const SourceInfo& /*src*/,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const SatListResult r = listCachedSatellites(QDateTime::currentDateTimeUtc());
    QJsonObject o;
    if (!r.ok) {
        o["ok"] = false;
        o["error"] = r.error;
        o["source"] = r.source.isEmpty() ? QString::fromUtf8("none") : r.source;
        return compact(o);
    }
    o["ok"] = true;
    o["source"] = r.source;
    o["fetched_at"] = r.fetchedAt.isValid()
        ? r.fetchedAt.toUTC().toString(Qt::ISODate)
        : QString();
    int freshCount = 0;
    QJsonArray arr;
    for (const SatListEntry& e : r.sats) {
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
    if (arr.isEmpty())
        o["note"] = QString::fromUtf8("缓存存在但无卫星条目");
    return compact(o);
}

// Default capture length for a calibration measurement (complex samples).
// 32768 splits into 4 independent 8192-point FFT blocks; at the common 2.048e6
// rate that gives ~250 Hz bins refined by parabolic interpolation to well under
// 1 ppm at VHF/UHF, and it also holds up on narrower offline rates. Named so
// the call site never invents a magic count.
constexpr int kCalibrationDefaultSamples = 32768;
// Minimum useful capture: below this even one 512-point FFT cannot be split.
constexpr int kCalibrationMinSamples = 4096;

// READ-ONLY measurement. Tunes the source to the reference, pulls one capture,
// and estimates the crystal ppm against the expected baseband position. It does
// NOT persist anything; the gated apply_frequency_correction does that. On a
// test / offline source the data is honestly-labelled generated IQ (never
// presented as a real antenna capture).
QString execCalibrateFrequency(const QJsonObject& args, dsp::SpectrumEngine* engine,
                               const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const double refFreq = args["reference_freq_hz"].toDouble();
    const QString type = args["reference_type"].toString();
    int sampleCount = static_cast<int>(args["sample_count"].toDouble(kCalibrationDefaultSamples));
    if (sampleCount < kCalibrationMinSamples) sampleCount = kCalibrationDefaultSamples;

    // Map the reference kind to the expected baseband position. handheld/manual
    // expect the reference at baseband DC (0); GSM FCCH sits exactly one symbol-
    // rate/4 tone above the tuned ARFCN centre.
    dsp::CalibrationReference ref;
    double expectedBaseband = 0.0;
    if (type == QStringLiteral("gsm_fcch")) {
        ref = dsp::CalibrationReference::GsmFcch;
        expectedBaseband = dsp::kFcchToneHz;
    } else if (type == QStringLiteral("handheld")) {
        ref = dsp::CalibrationReference::HandheldGuided;
        expectedBaseband = 0.0;
    } else {
        ref = dsp::CalibrationReference::Manual;
        expectedBaseband = 0.0;
    }

    std::vector<std::complex<float>> iq;
    double sr = 0.0, centre = 0.0;
    const std::size_t got = engine->captureForCalibration(
        refFreq, sampleCount, iq, sr, centre);

    QJsonObject o;
    o["ok"] = true;
    o["reference_type"] = type;
    o["reference_freq_hz"] = refFreq;
    o["applied"] = false;
    addSourceFields(o, src);

    if (got < static_cast<std::size_t>(kCalibrationMinSamples) || iq.empty()) {
        // Honest empty state: the source delivered too little to measure.
        o["detected"] = false;
        o["error"] = QString::fromUtf8("采集样本不足，无法测量（源无数据）");
        o["hint"] = QString::fromUtf8("请确认已连接设备或打开离线文件，并让参考信号处于该频点");
        return compact(o);
    }

    dsp::CalibratorConfig cfg;
    cfg.centreFreqHz = refFreq;
    cfg.expectedBasebandHz = expectedBaseband;
    const dsp::CalibrationResult res =
        dsp::calibrateFromCapture(iq, sr, ref, cfg, /*segments=*/4);

    o["detected"] = res.detected;
    if (res.detected) {
        o["measured_ppm"] = res.ppm;
        o["confidence"] = res.confidence;
        o["delta_hz"] = res.meanOffsetHz;
        o["spread_ppm"] = res.spreadPpm;
        o["snr_db"] = res.worstSnrDb;
        o["measurements_used"] = res.measurementsUsed;
        o["hint"] = QString::fromUtf8("如需应用，请调用 apply_frequency_correction");
    } else {
        // Never fabricate a ppm: carry the honest "no reference carrier" status.
        o["hint"] = res.status;
    }
    return compact(o);
}

// WRITE action: persist + apply a measured ppm correction. Gated in manual
// mode (see llm_worker::dispatchToolCall). Reads the previous stored value so
// the before/after comparison is real.
QString execApplyFrequencyCorrection(const QJsonObject& args,
                                     dsp::SpectrumEngine* engine,
                                     const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const double ppm = args["ppm"].toDouble();
    const double previous = dsp::currentPpmSetting();
    dsp::savePpmSetting(ppm);
    engine->setPpm(ppm);

    QJsonObject o;
    o["ok"] = true;
    o["applied"] = true;
    o["previous_ppm"] = previous;
    o["applied_ppm"] = ppm;
    // After applying the measured correction the residual baseband offset
    // should collapse to ~0; with no live re-measurement here we report the
    // design prediction (the next calibrate_frequency call would confirm).
    o["predicted_residual_hz"] = 0.0;
    o["message"] = QString::fromUtf8("已应用频率校正：%1 ppm（原 %2 ppm）")
                       .arg(ppm, 0, 'f', 3)
                       .arg(previous, 0, 'f', 3);
    addSourceFields(o, src);
    return compact(o);
}

// ---------------------------------------------------------------------------
// Wave2 read-only digital decode snapshot tools (POCSAG / m17 / VOR). They pull
// the accumulated decode output of a channel straight off the engine's read-only
// snapshot slots (which take sourceMutex_ so the copy is stable off the engine
// thread). They NEVER tune / gate / mutate the receiver, so they are registered
// write=false (predict_passes-style: never blocked by the manual gate). Unknown
// channel / non-matching mode / nothing decoded yet -> an HONEST empty state:
// an empty array, or locked=false for VOR. No fabricated message / call /
// bearing, and no pre-stored station.
//
// channel_id defaults to the currently selected VFO when omitted.
namespace {
int resolveChannelId(const QJsonObject& args, dsp::SpectrumEngine* engine) {
    const QJsonValue v = args.value(QStringLiteral("channel_id"));
    if (v.isDouble()) return static_cast<int>(v.toDouble());
    const QJsonValue c = args.value(QStringLiteral("channel"));
    if (c.isDouble()) return static_cast<int>(c.toDouble());
    return engine->selectedVfoId();
}

// Phase63 D1 bilateral alias: resolve a VFO write target from EITHER
//   - "index" (marker ordinal in vfoMarkers(), the Agent LLM schema key), or
//   - "id"    (direct VFO id, the ControlHub/HTTP key).
// On success fills outIndex/outId and returns true; on missing/bad input
// returns false with an honest error message. When `id` is given directly we
// do NOT bounds-check it against the marker list (the engine will reject an
// unknown id) -- but we DO fill outIndex by searching the markers so the
// response echo stays informative.
bool resolveVfoTarget(const QJsonObject& args, dsp::SpectrumEngine* engine,
                      int& outIndex, int& outId, QString& err) {
    const QJsonValue idxV = args.value(QStringLiteral("index"));
    const QJsonValue idV  = args.value(QStringLiteral("id"));
    const auto markers = engine->vfoMarkers();
    if (idxV.isDouble()) {
        const int i = static_cast<int>(idxV.toDouble());
        if (i < 0 || i >= markers.size()) {
            err = QString::fromUtf8("VFO index %1 越界（共 %2 个）").arg(i).arg(markers.size());
            return false;
        }
        outIndex = i;
        outId = markers[i].id;
        return true;
    }
    if (idV.isDouble()) {
        const int id = static_cast<int>(idV.toDouble());
        // Locate the marker ordinal for a useful echo; -1 if not found.
        outIndex = -1;
        for (int k = 0; k < markers.size(); ++k)
            if (markers[k].id == id) { outIndex = k; break; }
        outId = id;
        return true;
    }
    err = QString::fromUtf8("参数 index 或 id 必须提供一个（VFO 序号或 VFO id）");
    return false;
}
} // namespace

QString execGetPocsagMessages(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const int channelId = resolveChannelId(args, engine);
    const std::vector<dsp::PocsagMessage> msgs = engine->pocsagMessages(channelId);
    QJsonArray arr;
    for (const dsp::PocsagMessage& m : msgs) {
        QJsonObject o;
        o["address"] = static_cast<qint64>(m.address);   // RIC 0..2097151
        o["function"] = m.function;
        const char* typeStr = "unknown";
        switch (m.type) {
            case dsp::PocsagMessage::Type::Numeric: typeStr = "numeric"; break;
            case dsp::PocsagMessage::Type::Alpha:   typeStr = "alpha";   break;
            case dsp::PocsagMessage::Type::Unknown:
            default:                                typeStr = "unknown"; break;
        }
        o["type"] = QString::fromLatin1(typeStr);
        o["text"] = QString::fromStdString(m.text);
        arr.append(o);
    }
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["count"] = static_cast<int>(arr.size());
    out["messages"] = arr;   // empty array = honest empty state
    addSourceFields(out, src);
    return compact(out);
}

QString execGetM17Calls(const QJsonObject& args, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const int channelId = resolveChannelId(args, engine);
    const std::vector<dsp::M17Call> calls = engine->m17Calls(channelId);
    QJsonArray arr;
    for (const dsp::M17Call& c : calls) {
        QJsonObject o;
        o["src"] = QString::fromStdString(c.src);
        o["dst"] = QString::fromStdString(c.dst);
        o["type"] = static_cast<int>(c.type);          // raw LSF TYPE word
        o["is_stream"] = c.isStream;
        o["payload_class"] = c.payloadClass;
        o["frame_kind"] = c.frameKind;
        o["crc_ok"] = c.crcOk;
        o["voice_undecoded"] = c.voiceUndecoded;        // Codec2 not decoded: honest
        o["meta_size"] = static_cast<int>(c.meta.size());
        o["payload_size"] = static_cast<int>(c.payload.size());
        arr.append(o);
    }
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["count"] = static_cast<int>(arr.size());
    out["calls"] = arr;      // empty array = honest empty state
    addSourceFields(out, src);
    return compact(out);
}

QString execGetAcarsPackets(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const int channelId = resolveChannelId(args, engine);
    const std::vector<dsp::AcarsPacket> pkts = engine->acarsPackets(channelId);
    QJsonArray arr;
    for (const dsp::AcarsPacket& p : pkts) {
        QJsonObject o;
        const char* dir = "unknown";
        switch (p.direction) {
            case dsp::AcarsPacket::Direction::Air:    dir = "air";    break;
            case dsp::AcarsPacket::Direction::Ground: dir = "ground"; break;
            default:                                  dir = "unknown"; break;
        }
        o["direction"] = QString::fromLatin1(dir);
        o["mode"]     = QString::fromStdString(p.mode);
        o["label"]    = QString::fromStdString(p.label);
        o["block_id"] = QString::fromStdString(p.blockId);
        o["ack"]      = QString::fromStdString(p.ack);
        o["text"]     = QString::fromStdString(p.text);
        o["crc_ok"]   = p.crcOk;   // honest block-check result
        arr.append(o);
    }
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["count"] = static_cast<int>(arr.size());
    out["packets"] = arr;   // empty array = honest empty state
    addSourceFields(out, src);
    return compact(out);
}

QString execGetApRsMessages(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& /*src*/,
                              ui::BookmarkManager* /*bookmarks*/,
                              aigui::WorkspaceController* /*workspace*/) {
    int limit = 50;
    const QJsonValue lv = args.value(QStringLiteral("limit"));
    if (lv.isDouble() || lv.isBool()) {
        const double v = lv.toDouble(std::nan(""));
        if (std::isfinite(v)) limit = static_cast<int>(v);
    }
    if (limit < 1) limit = 1;
    if (limit > 50) limit = 50;
    const QList<dsp::SpectrumEngine::ApRsSnapshot> msgs = engine->aprsMessages(limit);
    QJsonArray arr;
    for (const auto& m : msgs) {
        QJsonObject o;
        o["time_ms"] = static_cast<double>(m.time_ms);
        o["src"]     = m.src;
        o["dst"]     = m.dst;
        o["path"]    = m.path;
        o["info"]    = m.info;
        arr.append(o);
    }
    QJsonObject res;
    res["ok"] = true;
    res["returned"] = arr.size();
    res["messages"] = arr;
    if (arr.isEmpty())
        res["note"] = QString::fromUtf8("空态：尚无 APRS 消息（未喂位流或无有效帧）");
    return QString::fromUtf8(QJsonDocument(res).toJson(QJsonDocument::Compact));
}

QString execGetNavtexMessages(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const int channelId = resolveChannelId(args, engine);
    const std::vector<dsp::NavtexMessage> msgs = engine->navtexMessages(channelId);
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
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["count"] = static_cast<int>(arr.size());
    out["messages"] = arr;
    addSourceFields(out, src);
    return compact(out);
}

QString execGetVorRadial(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const int channelId = resolveChannelId(args, engine);
    const dsp::VorResult v = engine->vorResult(channelId);
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["locked"] = v.locked;
    // Honest empty state: radial/quality are only meaningful when locked. When
    // unlocked we refuse to invent a bearing and say so explicitly.
    if (v.locked) {
        out["radial_deg"] = v.radialDeg;
        out["quality"] = v.quality;
    } else {
        out["radial_deg"] = QJsonValue(QJsonValue::Null);
        out["quality"] = QJsonValue(QJsonValue::Null);
        out["note"] = QString::fromUtf8("未锁定 VOR 台，方位不可信（不编造方位）");
    }
    out["morse_id"] = v.morseId;
    addSourceFields(out, src);
    return compact(out);
}

// ---------------------------------------------------------------------------
// Phase26: capability-everything-as-tools (21 tools, three-channel parity).
// Write tools are gated upstream by llm_worker::isWriteTool -- same semantics as
// the existing 14. Where the engine ALREADY exposes the backing (squelch / VFO /
// FFT / recDir_ / QSettings) we drive it for real; where the back-end lands on
// the parallel control/ A block (network-audio tap, ScanActivityLink, the
// BookmarkManager wiring, VFO rename) we return an honest routed/pending result
// rather than fabricate an effect. Bad/missing args are honest errors.
namespace {
QString errResult(const QString& msg) {
    QJsonObject o; o["ok"] = false; o["error"] = msg; return compact(o);
}
bool needNum(const QJsonObject& a, const char* key, double& out) {
    if (!a.contains(QLatin1String(key)) || !a.value(QLatin1String(key)).isDouble())
        return false;
    out = a.value(QLatin1String(key)).toDouble();
    return true;
}
bool needStr(const QJsonObject& a, const char* key, QString& out) {
    if (!a.contains(QLatin1String(key)) || !a.value(QLatin1String(key)).isString())
        return false;
    out = a.value(QLatin1String(key)).toString();
    return true;
}
// Accepted by the AI layer; the real effect is the ControlHub command of the
// same name on the three-channel side. Echo the args honestly + source fields.
// The note wording is unified HERE (single point) so every routed tool reports
// the identical contract: routed, not locally executed, must be confirmed on
// the UI / ControlHub channel.
QString routedOk(const char* command, const QJsonObject& echoed, const SourceInfo& src) {
    QJsonObject o = echoed;
    o["ok"] = true;
    o["routed_command"] = QString::fromLatin1(command);
    o["note"] = QString::fromUtf8("命令 %1 已路由，实际效果由 ControlHub 通道落地，需在 UI 或 ControlHub 确认执行")
                    .arg(QString::fromLatin1(command));
    addSourceFields(o, src);
    return compact(o);
}
} // namespace

// 1. set_network_audio_sink (write): enable/port/format. The engine network tap
//    is wired on the control side; here we validate + route by contract name.
//    Phase63 D5: echo host/stereo so the routed stub does not silently drop
//    them (CH cmdSetNetworkAudioSink already consumes them).
QString execSetNetworkAudioSink(const QJsonObject& args, dsp::SpectrumEngine*,
                                const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double port = 0.0;
    if (!args.contains("enable") || !args.value("enable").isBool())
        return errResult(QString::fromUtf8("参数 enable 缺失或不是布尔值"));
    if (!needNum(args, "port", port))
        return errResult(QString::fromUtf8("参数 port 缺失或不是数字"));
    QJsonObject echo;
    echo["enable"] = args.value("enable").toBool();
    echo["port"] = port;
    if (args.contains("format") && args.value("format").isString())
        echo["format"] = args.value("format").toString();
    if (args.contains("host") && args.value("host").isString())
        echo["host"] = args.value("host").toString();
    if (args.contains("stereo") && args.value("stereo").isBool())
        echo["stereo"] = args.value("stereo").toBool();
    return routedOk("set_network_audio_sink", echo, src);
}

// 2. get_network_audio_status (read): no engine status slot yet -> honest off.
QString execGetNetworkAudioStatus(const QJsonObject&, dsp::SpectrumEngine*,
                                  const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = false;
    o["note"] = QString::fromUtf8("无网络音频流状态读数（后端由 ControlHub 提供）");
    addSourceFields(o, src);
    return compact(o);
}

// 3. start_scan_link (write): target_freq_hz. ScanActivityLink lives on control.
QString execStartScanLink(const QJsonObject& args, dsp::SpectrumEngine*,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double tgt = 0.0;
    if (!needNum(args, "target_freq_hz", tgt))
        return errResult(QString::fromUtf8("参数 target_freq_hz 缺失或不是数字"));
    QJsonObject echo; echo["target_freq_hz"] = tgt;
    return routedOk("start_scan_link", echo, src);
}

// 4. stop_scan_link (write).
QString execStopScanLink(const QJsonObject&, dsp::SpectrumEngine*,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    return routedOk("stop_scan_link", QJsonObject{}, src);
}

// 5. get_scan_link_status (read): no link instance on the engine -> honest idle.
QString execGetScanLinkStatus(const QJsonObject&, dsp::SpectrumEngine*,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["scanning"] = false;
    o["dwelling"] = false;
    o["hit"] = QJsonValue(QJsonValue::Null);
    o["note"] = QString::fromUtf8("扫描活动链路未运行（后端由 ControlHub 提供）");
    addSourceFields(o, src);
    return compact(o);
}

// 6. set_squelch (write): the engine exposes setSquelchEnabled / Threshold AND
//    setSquelchAuto -- every field drives its real setter (parity with CH
//    cmdSetSquelch), so get_squelch_status reads the real value back.
QString execSetSquelch(const QJsonObject& args, dsp::SpectrumEngine* engine,
                       const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    if (args.contains("enabled") && args.value("enabled").isBool()) {
        engine->setSquelchEnabled(args.value("enabled").toBool());
        o["enabled"] = args.value("enabled").toBool();
    }
    if (args.contains("threshold_db") && args.value("threshold_db").isDouble()) {
        const double th = args.value("threshold_db").toDouble();
        // Honest domain guard (D2-4): the UI slider is [-100, -20] dBFS in the
        // 48 kHz audio-block RMS domain. Out-of-domain values are REJECTED here
        // too, never silently clamped -- parity with execSetCtcss's tone guard.
        if (th < tokens::kSquelchMinDb || th > tokens::kSquelchMaxDb)
            return errResult(QString::fromUtf8(
                "静噪门限越界：必须在 %1..%2 dBFS（48 kHz 音频块 RMS 域，越负越灵敏），收到 %3")
                .arg(tokens::kSquelchMinDb).arg(tokens::kSquelchMaxDb).arg(th));
        engine->setSquelchThreshold(static_cast<float>(th));
        o["threshold_db"] = th;
    }
    if (args.contains("auto") && args.value("auto").isBool()) {
        engine->setSquelchAuto(args.value("auto").toBool());
        o["auto"] = args.value("auto").toBool();
    }
    o["message"] = QString::fromUtf8("静噪参数已下发（门限/使能）");
    addSourceFields(o, src);
    return compact(o);
}

// 7. get_squelch_status (read): the engine DOES expose public readback
// (squelchEnabled/ThresholdDb/Auto/Open -- the same getters ControlHub's
// cmdGetSquelchStatus uses), so we return the REAL values rather than hardcoded
// nulls. On a fresh/no-device engine these read back honest defaults
// (off / -50 dB / auto off / closed), identical to the CH/HTTP wire.
QString execGetSquelchStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                             const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = engine->squelchEnabled();
    o["threshold_db"] = engine->squelchThresholdDb();
    o["auto"] = engine->squelchAuto();
    o["open"] = engine->squelchOpen();
    addSourceFields(o, src);
    return compact(o);
}

// 7b. set_noise_blanker (write): the engine exposes setNoiseBlanker(bool) and the
//     UI checkbox already drives it; wire the real setter. `on` is required and
//     must be a boolean -- missing/non-bool is an honest error, never a default.
QString execSetNoiseBlanker(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.contains("on") || !args.value("on").isBool())
        return errResult(QString::fromUtf8("参数 on 缺失或不是布尔值"));
    const bool on = args.value("on").toBool();
    engine->setNoiseBlanker(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = QString::fromUtf8("噪声抑制开关已下发");
    addSourceFields(o, src);
    return compact(o);
}

// 7c. get_noise_blanker_status (read): the engine DOES expose noiseBlankerEnabled()
//     (unlike squelch), so we return the real switch rather than a null.
QString execGetNoiseBlankerStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                                 const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = engine->noiseBlankerEnabled();
    addSourceFields(o, src);
    return compact(o);
}

// 7d. set_ctcss (write): CTCSS tone-squelch control, real engine backing.
//     `enabled` is REQUIRED and must be a boolean (missing/non-bool -> honest
//     error, never a silent toggle). `frequency_hz` is optional: when omitted
//     the current tuning (default 88.5 Hz) is kept; when supplied it must be a
//     finite number inside the legal PL domain [67.0, 254.1] Hz -- an out-of-
//     range value is REJECTED here with ok:false rather than letting the engine
//     silently clamp (the clamp would echo a fake success at a tone the caller
//     never asked for). On success the engine setters run for real.
QString execSetCtcss(const QJsonObject& args, dsp::SpectrumEngine* engine,
                     const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const QJsonValue en = args.value(QStringLiteral("enabled"));
    if (!en.isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = en.toBool();

    double fq = engine->ctcssFreqHz();   // default: keep current tuning
    if (args.contains(QStringLiteral("frequency_hz"))) {
        double v = 0.0;
        if (!needNum(args, "frequency_hz", v) || !std::isfinite(v))
            return errResult(QString::fromUtf8("参数 frequency_hz 缺失或不是有限数字"));
        if (v < tokens::kCtcssToneHzMin || v > tokens::kCtcssToneHzMax)
            return errResult(QString::fromUtf8(
                "CTCSS 亚音频率越界：必须在 %1–%2 Hz 之间（收到 %3）")
                .arg(tokens::kCtcssToneHzMin).arg(tokens::kCtcssToneHzMax).arg(v));
        fq = v;
    }

    // Optional speaker-only sub-audio gate. Omit -> leave the current gate
    // untouched; present-but-non-bool -> honest error (never a silent toggle).
    bool gateAudio = engine->ctcssGateAudio();
    if (args.contains(QStringLiteral("gate_audio"))) {
        const QJsonValue gv = args.value(QStringLiteral("gate_audio"));
        if (!gv.isBool())
            return errResult(QString::fromUtf8("参数 gate_audio 不是布尔值"));
        gateAudio = gv.toBool();
    }

    engine->setCtcssEnabled(on);
    engine->setCtcssFreqHz(fq);
    engine->setCtcssGateAudio(gateAudio);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["frequency_hz"] = fq;
    o["gate_audio"] = gateAudio;
    o["message"] = QString::fromUtf8("CTCSS 亚音参数已下发（开关/亚音频率/门控静音）");
    addSourceFields(o, src);
    return compact(o);
}

// 7e. get_ctcss_status (read): real engine getters -- ctcssEnabled /
//     ctcssFreqHz / ctcssPresent / ctcssGateAudio. `active` is the honest
//     detection latch: with no signal or detection disabled it reads false
//     (never a fabricated tone).
QString execGetCtcssStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = engine->ctcssEnabled();
    o["frequency_hz"] = engine->ctcssFreqHz();
    o["active"] = engine->ctcssPresent();
    o["gate_audio"] = engine->ctcssGateAudio();
    addSourceFields(o, src);
    return compact(o);
}

// 7f. set_cdcss (write): CDCSS/DCS digital coded squelch control. `enabled` is
//     REQUIRED and must be a boolean. `code` is an optional 3-digit octal string
//     ("023"); when supplied it must parse and be in the public 104-code table
//     (CdcssDecoder::isValidCode) -- an illegal code is REJECTED here with
//     ok:false rather than tuning to a nonsense address. `gate_audio` optional.
QString execSetCdcss(const QJsonObject& args, dsp::SpectrumEngine* engine,
                     const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const QJsonValue en = args.value(QStringLiteral("enabled"));
    if (!en.isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = en.toBool();

    int code = engine->cdcssCode();   // default: keep current
    if (args.contains(QStringLiteral("code"))) {
        const QString cs = args.value(QStringLiteral("code")).toString();
        bool ok = false;
        const int v = cs.toInt(&ok, 8);   // octal parse ("023" -> 19)
        if (!ok || !dsp::CdcssDecoder::isValidCode(v))
            return errResult(QString::fromUtf8(
                "DCS 码非法：须为三位八进制且在公开 104 码表内（收到 \"%1\"）").arg(cs));
        code = v;
    }

    bool gateAudio = engine->cdcssGateAudio();
    if (args.contains(QStringLiteral("gate_audio"))) {
        const QJsonValue gv = args.value(QStringLiteral("gate_audio"));
        if (!gv.isBool())
            return errResult(QString::fromUtf8("参数 gate_audio 不是布尔值"));
        gateAudio = gv.toBool();
    }

    engine->setCdcssEnabled(on);
    engine->setCdcssCode(code);
    engine->setCdcssGateAudio(gateAudio);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["code"] = QStringLiteral("%1").arg(code, 3, 8, QLatin1Char('0'));
    o["gate_audio"] = gateAudio;
    o["message"] = QString::fromUtf8("CDCSS/DCS 数字亚音参数已下发（开关/DCS码/门控静音）");
    addSourceFields(o, src);
    return compact(o);
}

// 7g. get_cdcss_status (read): real engine getters. `active` is the honest
//     detection latch (false with no signal / disabled).
QString execGetCdcssStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = engine->cdcssEnabled();
    o["code"] = QStringLiteral("%1").arg(engine->cdcssCode(), 3, 8, QLatin1Char('0'));
    o["active"] = engine->cdcssPresent();
    o["gate_audio"] = engine->cdcssGateAudio();
    addSourceFields(o, src);
    return compact(o);
}

// 7h. set_ft8 (write): FT8 detection-layer enable. `enabled` REQUIRED boolean.
//     Detection layer only; C++ BP decode deferred to step 4.
QString execSetFt8(const QJsonObject& args, dsp::SpectrumEngine* engine,
                   const SourceInfo& src,
                   ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const QJsonValue en = args.value(QStringLiteral("enabled"));
    if (!en.isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = en.toBool();
    engine->setFt8Enabled(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = QString::fromUtf8("FT8 检测层开关已下发（检测层，C++ BP 解码留后续）");
    addSourceFields(o, src);
    return compact(o);
}

// 7i. get_ft8_status (read): honest engine getters. `active` false with no signal.
QString execGetFt8Status(const QJsonObject&, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                         ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = engine->ft8Enabled();
    o["active"] = engine->ft8Present();
    o["freq_offset_hz"] = engine->ft8FreqOffsetHz();
    o["sync_quality"] = engine->ft8SyncQuality();
    o["candidate_count"] = engine->ft8CandidateCount();
    o["decoded_text"] = QString::fromStdString(engine->ft8DecodedText());
    o["decoded_frame_count"] = engine->ft8DecodedFrameCount();
    addSourceFields(o, src);
    return compact(o);
}

// 7i-bis. get_ft8_history (read): WSJT-X style rolling decoded-message window.
// Reads engine->ft8History(limit) honestly; empty list when no frames decoded.
// limit clamped to [1,200] by the engine accessor (default 50 if omitted/NaN).
QString execGetFt8History(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                          ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    int limit = 50;
    const QJsonValue lv = args.value(QStringLiteral("limit"));
    if (lv.isDouble() || lv.isBool()) {
        // Qt6: toDouble(default) returns the default when not a number.
        const double v = lv.toDouble(std::nan(""));
        if (std::isfinite(v)) {
            int iv = (int)std::lround(v);
            if (iv < 1) iv = 1;
            if (iv > dsp::SpectrumEngine::kFt8HistoryMax)
                iv = dsp::SpectrumEngine::kFt8HistoryMax;
            limit = iv;
        }
    }
    QJsonArray arr;
    const auto rows = engine->ft8History(limit);
    for (const auto& e : rows) {
        QJsonObject r;
        r["time_ms"] = (double)e.time_ms;
        r["freq_hz"] = e.freq_hz;
        r["demod"] = e.demod;
        r["text"] = e.text;
        arr.append(r);
    }
    QJsonObject o;
    o["ok"] = true;
    o["limit"] = limit;
    o["returned"] = (int)rows.size();
    o["history"] = arr;
    if (rows.isEmpty())
        o["note"] = QString::fromUtf8("无 FT8 解码历史（未开启/无帧/CRC 不过）：history=[] 诚实空态");
    addSourceFields(o, src);
    return compact(o);
}

// get_adsb_aircraft (read): dump1090 --aircraft shape snapshot.
// Reads engine->aircraftSnapshot(limit); empty array when no 1090 signal.
QString execGetAdsbAircraft(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                            ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    int limit = 50;
    const QJsonValue lv = args.value(QStringLiteral("limit"));
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
    QJsonArray arr;
    const auto rows = engine->aircraftSnapshot(limit);
    for (const auto& a : rows) {
        QJsonObject r;
        r["icao"] = a.icao;
        r["callsign"] = a.callsign;
        r["altitude_ft"] = a.altitude_ft;
        if (a.has_position) {
            r["lat"] = a.lat;
            r["lon"] = a.lon;
        } else {
            r["lat"] = QJsonValue(QJsonValue::Null);
            r["lon"] = QJsonValue(QJsonValue::Null);
        }
        r["has_position"] = a.has_position;
        if (a.has_velocity) {
            r["groundspeed_kt"] = a.groundspeed_kt;
            r["heading_deg"] = a.heading_deg;
            r["vertical_rate_fpm"] = a.vertical_rate_fpm;
        } else {
            r["groundspeed_kt"] = QJsonValue(QJsonValue::Null);
            r["heading_deg"] = QJsonValue(QJsonValue::Null);
            r["vertical_rate_fpm"] = QJsonValue(QJsonValue::Null);
        }
        r["has_velocity"] = a.has_velocity;
        r["df"] = a.df;
        r["crc_ok"] = a.crc_ok;
        r["last_seen_ms"] = (double)a.last_seen_ms;
        arr.append(r);
    }
    QJsonObject o;
    o["ok"] = true;
    o["limit"] = limit;
    o["returned"] = (int)rows.size();
    o["aircraft"] = arr;
    if (rows.isEmpty())
        o["note"] = QString::fromUtf8("1090 MHz 无信号或未调谐到 ADS-B：aircraft=[] 诚实空态");
    addSourceFields(o, src);
    return compact(o);
}

// 7j. set_lrpt (write): LRPT state-layer enable. `enabled` REQUIRED boolean.
//     State/control layer only; C++ demod/FEC port deferred to step 4.
QString execSetLrpt(const QJsonObject& args, dsp::SpectrumEngine* engine,
                    const SourceInfo& src,
                    ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const QJsonValue en = args.value(QStringLiteral("enabled"));
    if (!en.isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = en.toBool();
    engine->setLrptEnabled(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = QString::fromUtf8("LRPT 接收层开关已下发（状态层，C++ 解调/FEC 移植留后续第④轮）");
    addSourceFields(o, src);
    return compact(o);
}

// 7k. get_lrpt_status (read): honest engine getters. With no C++ decoder yet,
//     sync_locked stays false and decoded_frames 0 -- never fabricated.
QString execGetLrptStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                          ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = engine->lrptEnabled();
    o["sync_locked"] = engine->lrptSyncLocked();
    o["decoded_frames"] = engine->lrptDecodedFrames();
    o["note"] = QString::fromUtf8("C++ LRPT 解调/FEC 未移植（第④轮），当前为状态控制层");
    addSourceFields(o, src);
    return compact(o);
}

// ---- NanoVNA step-2 -------------------------------------------------------
// set_vna_sweep (write, gated): validate start/stop/points, then hand to the
// client. With no device attached the params are still validated (honest
// ok:false on bad input) but the command is not sent -- applied=false.
QString execSetVnaSweep(const QJsonObject& args, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const QJsonValue a = args.value(QStringLiteral("start_hz"));
    const QJsonValue b = args.value(QStringLiteral("stop_hz"));
    const QJsonValue p = args.value(QStringLiteral("points"));
    if (!a.isDouble() || !b.isDouble() || !p.isDouble())
        return errResult(QString::fromUtf8("参数 start_hz/stop_hz/points 缺失或不是数字"));
    const long start = (long)a.toDouble();
    const long stop  = (long)b.toDouble();
    const int points = (int)p.toDouble();
    if (stop <= start)
        return errResult(QString::fromUtf8("stop_hz 必须大于 start_hz"));
    if (points <= 0)
        return errResult(QString::fromUtf8("points 必须为正整数"));

    QJsonObject o;
    auto& vna = engine->vnaClient();
    if (!vna.isConnected()) {
        o["ok"] = true;
        o["connected"] = false;
        o["applied"] = false;
        o["start_hz"] = (double)start; o["stop_hz"] = (double)stop; o["points"] = points;
        o["message"] = QString::fromUtf8("未连接 NanoVNA：参数已校验但未下发");
        addSourceFields(o, src);
        return compact(o);
    }
    QString err;
    bool ok = vna.setSweep(start, stop, points, &err);
    o["ok"] = ok;
    o["connected"] = true;
    o["applied"] = ok;
    if (ok) { o["start_hz"] = (double)start; o["stop_hz"] = (double)stop; o["points"] = points; }
    else    o["error"] = err;
    addSourceFields(o, src);
    return compact(o);
}

// get_vna_data (read): frequencies + S11/S21 complex pairs + derived VSWR/RL/
// impedance. Empty arrays when no device / no data -- never fabricated.
QString execGetVnaData(const QJsonObject&, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    auto& vna = engine->vnaClient();
    o["connected"] = vna.isConnected();

    QJsonArray freqs, s11re, s11im, vswrArr, rlArr, zRe, zIm;
    QJsonArray s21re, s21im, s21gain, znormRe, znormIm;
    double coax = 0.0;
    if (vna.isConnected()) {
        auto f = vna.readFrequencies();
        auto s11 = vna.readData(0);
        auto s21 = vna.readData(1);
        for (size_t i = 0; i < s11.size(); ++i) {
            const std::complex<double>& g = s11[i];
            if (i < f.size()) freqs.append((double)f[i]);
            s11re.append(g.real()); s11im.append(g.imag());
            double rl = vna::returnLossDb(g);
            double sw = vna::vswr(g);
            auto z = vna::s11ToImpedance(g);
            auto zn = vna::smithNormalizedZ(g);
            auto finite = [](double x){ return std::isfinite(x) ? x : 0.0; };
            rlArr.append(finite(rl));
            vswrArr.append(finite(sw));
            zRe.append(finite(z.real())); zIm.append(finite(z.imag()));
            znormRe.append(finite(zn.real())); znormIm.append(finite(zn.imag()));
        }
        for (size_t i = 0; i < s21.size(); ++i) {
            s21re.append(s21[i].real()); s21im.append(s21[i].imag());
            double g = vna::s21GainDb(s21[i]);
            s21gain.append(std::isfinite(g) ? g : 0.0);
        }
        // Coax loss per metre: derive the cable length from TDR on the live S11
        // (vf=0.66 nominal), then divide the midpoint S21 insertion loss by that
        // length. Honest: no TDR peak -> 0 + note, never fabricated.
        if (!f.empty() && !s11.empty()) {
            std::vector<double> fv(f.begin(), f.end());
            vna::TdrResult t = vna::tdrCable(fv, s11, 0.66);
            if (t.valid && t.cable_length_m > 0.0 && !s21gain.isEmpty()) {
                double mid = s21gain.at(s21gain.size() / 2).toDouble(); // dB (<=0)
                if (std::isfinite(mid)) coax = -mid / t.cable_length_m;
            }
        }
    }
    o["frequencies"] = freqs;
    o["s11_re"] = s11re; o["s11_im"] = s11im;
    o["vswr"] = vswrArr; o["return_loss_db"] = rlArr;
    o["z_real"] = zRe; o["z_imag"] = zIm;
    o["s21_re"] = s21re; o["s21_im"] = s21im;
    o["s21_gain_db"] = s21gain;
    o["smith_z_real"] = znormRe; o["smith_z_imag"] = znormIm;
    o["coax_loss_db_per_m"] = coax;

    // #60 summary fields, all from the real arrays -- null when there is no data.
    // Min VSWR is the best (lowest) standing-wave point and the frequency at it.
    QJsonValue minVswr = QJsonValue::Null;
    QJsonValue minVswrAt = QJsonValue::Null;
    if (vswrArr.size() == freqs.size()) {
        double best = std::numeric_limits<double>::infinity();
        int bestIdx = -1;
        for (int i = 0; i < vswrArr.size(); ++i) {
            const double v = vswrArr.at(i).toDouble();
            if (std::isfinite(v) && v < best) { best = v; bestIdx = i; }
        }
        if (bestIdx >= 0) {
            minVswr = best;
            minVswrAt = freqs.at(bestIdx).toDouble();
        }
    }
    o["min_vswr"] = minVswr;
    o["min_vswr_at_hz"] = minVswrAt;

    // Calibration confidence + coverage-range drift (honest: unknown/uncalibrated
    // is never dressed up as calibrated).
    addCalInterpretation(o, vna);

    // Wiring fact: fixed protocol truth (S11=CH0 reflection, S21=CH0->CH1).
    QJsonObject wiring;
    wiring["s11_ch0"] = true;
    wiring["s21_ch0_ch1"] = true;
    wiring["note"] = QString::fromUtf8("S11 测量 CH0 反射，S21 测量 CH0→CH1 传输");
    o["wiring"] = wiring;

    // #60 note bugfix: stop lying "未连接" unconditionally. Three honest branches.
    QString note;
    if (!vna.isConnected()) {
        note = QString::fromUtf8(
                   "未连接 NanoVNA：排查 ①USB/CDC 接线（Linux 需 dialout/udev，"
                   "Windows 见设备管理器 COMx）②设备是否进入扫频模式 "
                   "③串口路径（/dev/ttyACM0 或 COMx）；连接后先 set_vna_sweep 再 get_vna_data");
    } else if (s11re.isEmpty()) {
        note = QString::fromUtf8(
                   "已连接但暂无测量数据：请先执行 set_vna_sweep（等待扫描完成）后再调用本工具");
    } else {
        note = QString::fromUtf8("coax_loss_db_per_m 需已知电缆长度（用 vna_tdr_cable 测长）");
    }
    o["note"] = note;
    addSourceFields(o, src);
    return compact(o);
}

// get_vna_status (read): connected / model / version / cal / current sweep.
// On connect we do a live readCalStatus() refresh, then interpret the cal_ set
// into structured step/compensation/complete fields (honest unknown-items list).
QString execGetVnaStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                          ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    auto& vna = engine->vnaClient();
    o["connected"] = vna.isConnected();
    o["model"] = vna.model();
    o["version"] = vna.version();
    if (vna.isConnected()) vna.readCalStatus();   // live refresh before interpreting
    o["cal"] = QJsonArray::fromStringList(vna.calStatus());
    // tcxo_hz: live query; emitted only when the firmware answers with a
    // parseable "current: <Hz>" (older firmware -> field absent, not 0).
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
    // Structured calibration interpretation (empty/honest when unconnected).
    fillCalSteps(o, vna);
    addCalInterpretation(o, vna);
    if (!vna.isConnected())
        o["note"] = QString::fromUtf8("未连接 NanoVNA：状态字段为空");
    addSourceFields(o, src);
    return compact(o);
}

// vna_calibration (write -- gated): single-tool calibration STATE MACHINE.
// step selects one instrument-protocol command word; the model (human-in-the-loop
// on every write) decides the ORDER and when to put standards on CH0 / THRU.
// step=status (or omitted) is read-only and returns the objective next step;
// write steps route through the manual gate. No fixed script is baked in -- the
// connection of standards to ports is a hardware fact the caller controls.
QString execVnaCalibration(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                          ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    auto& vna = engine->vnaClient();
    const QString step = args.value("step").toString(QStringLiteral("status"));

    // Honest unconnected envelope (before any command is issued).
    if (!vna.isConnected()) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("NanoVNA 未连接");
        o["suggestion"] = QString::fromUtf8(
                   "排查 ①USB/CDC 接线（Linux 需 dialout/udev，Windows 见设备管理器 COMx）"
                   "②设备是否进入扫频模式 ③串口路径（/dev/ttyACM0 或 COMx）；"
                   "连接后先 set_vna_sweep 再校准");
        addSourceFields(o, src);
        return compact(o);
    }

    // Read-only guidance: refresh + report objective next step in protocol order.
    if (step == QStringLiteral("status")) {
        vna.readCalStatus();
        QJsonObject o;
        o["ok"] = true;
        o["step"] = QStringLiteral("status");
        fillCalSteps(o, vna);
        addCalInterpretation(o, vna);
        const QStringList calNow = vna.calStatus();
        const QSet<QString> have(calNow.begin(), calNow.end());
        if (have.contains("done") && have.contains("open") &&
            have.contains("short") && have.contains("load")) {
            o["next_step"] = QString::fromUtf8("calibration complete（可用 step=save slot=1..4 保存校准集）");
        } else {
            // First missing step in canonical SOLT order, with the standard it needs.
            static const QList<QPair<QString, QString>> kGuide = {
                {QStringLiteral("open"),      QStringLiteral("接 OPEN 开路标准件到 CH0（PORT1）后 step=open")},
                {QStringLiteral("short"),     QStringLiteral("接 SHORT 短路标准件到 CH0（PORT1）后 step=short")},
                {QStringLiteral("load"),      QStringLiteral("接 LOAD 50Ω 负载标准件到 CH0（PORT1）后 step=load")},
                {QStringLiteral("isolation"), QStringLiteral("CH0 接 SOL 件、CH1 开路（测隔离）后 step=isolation")},
                {QStringLiteral("thru"),      QStringLiteral("CH0–CH1 用 through 直通（测传输）后 step=thru")},
                {QStringLiteral("done"),      QStringLiteral("以上就绪后 step=done 完成校准")},
            };
            for (const auto& g : kGuide)
                if (!have.contains(g.first)) { o["next_step"] = g.second; break; }
        }
        addSourceFields(o, src);
        return compact(o);
    }

    // write steps ------------------------------------------------------------
    const bool isSaveOrLoad = (step == QStringLiteral("save") || step == QStringLiteral("load"));
    if (isSaveOrLoad) {
        const QJsonValue sv = args.value("slot");
        if (!sv.isDouble()) {
            QJsonObject o; o["ok"] = false;
            o["error"] = QString::fromUtf8("save/load 需要 slot（1..4 整数）");
            addSourceFields(o, src); return compact(o);
        }
        const int slot = sv.toInt();
        if (slot < 1 || slot > 4) {
            QJsonObject o; o["ok"] = false;
            o["error"] = QString::fromUtf8("slot 必须在 1..4 之间");
            addSourceFields(o, src); return compact(o);
        }
    }

    QString err;
    bool ok = false;
    if (step == QStringLiteral("reset"))            ok = vna.calReset(&err);
    else if (step == QStringLiteral("open"))         ok = vna.calOpen(&err);
    else if (step == QStringLiteral("short"))       ok = vna.calShort(&err);
    else if (step == QStringLiteral("load"))        ok = vna.calLoad(&err);
    else if (step == QStringLiteral("isolation"))   ok = vna.calIsolation(&err);
    else if (step == QStringLiteral("thru"))        ok = vna.calThru(&err);
    else if (step == QStringLiteral("done"))        ok = vna.calDone(&err);
    else if (step == QStringLiteral("save"))         ok = vna.calSave(args.value("slot").toInt(), &err);
    else if (step == QStringLiteral("load"))        ok = vna.calLoadSlot(args.value("slot").toInt(), &err);
    else if (step == QStringLiteral("scan"))        ok = vna.scan(&err);
    else if (step == QStringLiteral("pause"))       ok = vna.pauseScan(&err);
    else if (step == QStringLiteral("resume"))      ok = vna.resumeScan(&err);
    else {
        QJsonObject o; o["ok"] = false;
        o["error"] = QString::fromUtf8("未知 step，合法值：status/reset/open/short/load/"
                                       "isolation/thru/done/save/load/scan/pause/resume");
        addSourceFields(o, src); return compact(o);
    }

    // Post-write readback: the cal step methods already refreshed cal_, but a
    // fresh read keeps the returned steps truthful for scan/pause/resume too.
    vna.readCalStatus();
    QJsonObject o;
    o["ok"] = ok;
    o["step"] = step;
    if (!ok) o["error"] = err;
    fillCalSteps(o, vna);
    addCalInterpretation(o, vna);
    o["coverage_recorded"] = vna.calCoverageKnown();
    addSourceFields(o, src);
    return compact(o);
}

// vna_device (write -- gated): single-tool device-level misc state machine.
// action selects one instrument-protocol command; read actions (status/vbat/
// freq_offset-read) return objective current values, write actions (freq_offset
// set / edelay / averaging) route through the manual gate. No fixed script --
// the caller decides when to change bias/delay/averaging.
QString execVnaDevice(const QJsonObject& args, dsp::SpectrumEngine* engine,
                      const SourceInfo& src,
                      ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    auto& vna = engine->vnaClient();
    const QString action = args.value("action").toString(QStringLiteral("status"));

    // Honest unconnected envelope (before any command is issued).
    if (!vna.isConnected()) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("NanoVNA 未连接");
        o["suggestion"] = QString::fromUtf8(
                   "排查 ①USB/CDC 接线（Linux 需 dialout/udev，Windows 见设备管理器 COMx）"
                   "②设备是否进入扫频模式 ③串口路径（/dev/ttyACM0 或 COMx）；"
                   "连接后先 set_vna_sweep 再操作设备");
        addSourceFields(o, src);
        return compact(o);
    }

    // status: read-only objective snapshot. edelay/averaging/freq_offset are NOT
    // kept as client readbacks in this session -> honest 0 + note, never guessed.
    if (action == QStringLiteral("status")) {
        QJsonObject o;
        o["ok"] = true;
        o["action"] = "status";
        o["has_sweep"] = vna.hasSweep();
        o["start_hz"] = (double)vna.sweepStartHz();
        o["stop_hz"] = (double)vna.sweepStopHz();
        o["points"] = vna.sweepPoints();
        fillCalSteps(o, vna);
        addCalInterpretation(o, vna);
        o["electrical_delay_ps"] = 0;
        o["averaging"] = 0;
        o["freq_offset_hz"] = 0;
        o["note"] = QString::fromUtf8(
                   "edelay/averaging/freq_offset 当前值本会话未读回，0 不代表设备真实设置；"
                   "读电压用 action=vbat，读频偏用 action=freq_offset（不带 value）");
        addSourceFields(o, src);
        // addSourceFields wrote the SDR-source connected flag; the VNA tool's
        // "connected" must reflect the NanoVNA link, not the SDR source.
        o["connected"] = vna.isConnected();
        return compact(o);
    }

    if (action == QStringLiteral("vbat")) {
        double volts = 0.0;
        QString err;
        const bool ok = vna.readVbat(volts, &err);
        QJsonObject o;
        o["ok"] = ok;
        o["action"] = "vbat";
        if (ok) o["volts"] = volts;
        else    o["error"] = err.isEmpty() ? QString::fromUtf8("读 vbat 失败") : err;
        addSourceFields(o, src);
        return compact(o);
    }

    if (action == QStringLiteral("freq_offset")) {
        // No value -> read current offset; with value -> write (gated at gate layer).
        if (!args.contains("value")) {
            long hz = 0;
            QString err;
            const bool ok = vna.readFreqOffset(hz, &err);
            QJsonObject o;
            o["ok"] = ok;
            o["action"] = "freq_offset";
            if (ok) o["freq_offset_hz"] = (double)hz;
            else    o["error"] = err.isEmpty() ? QString::fromUtf8("读 freqoffset 失败") : err;
            addSourceFields(o, src);
            return compact(o);
        }
        if (!args.value("value").isDouble()) {
            QJsonObject o; o["ok"] = false;
            o["error"] = QString::fromUtf8("freq_offset 写需要 value（Hz 整数）");
            addSourceFields(o, src); return compact(o);
        }
        const long hz = (long)args.value("value").toDouble();
        QString err;
        const bool ok = vna.setFreqOffset(hz, &err);
        QJsonObject o;
        o["ok"] = ok;
        o["action"] = "freq_offset";
        o["value"] = (double)hz;
        if (!ok) o["error"] = err.isEmpty() ? QString::fromUtf8("写 freqoffset 失败") : err;
        addSourceFields(o, src);
        return compact(o);
    }

    if (action == QStringLiteral("edelay")) {
        if (!args.value("value").isDouble()) {
            QJsonObject o; o["ok"] = false;
            o["error"] = QString::fromUtf8("edelay 需要 value（电延迟 ps 整数）");
            addSourceFields(o, src); return compact(o);
        }
        const int ps = args.value("value").toInt();
        QString err;
        const bool ok = vna.setElectricalDelay(ps, &err);
        QJsonObject o;
        o["ok"] = ok;
        o["action"] = "edelay";
        o["value"] = ps;
        if (!ok) o["error"] = err.isEmpty() ? QString::fromUtf8("写 edelay 失败") : err;
        addSourceFields(o, src);
        return compact(o);
    }

    if (action == QStringLiteral("averaging")) {
        // Executor-side range check too -- defends a direct call that bypasses
        // the manual gate.
        if (!args.value("value").isDouble()) {
            QJsonObject o; o["ok"] = false;
            o["error"] = QString::fromUtf8("averaging 需要 value（平均次数 1..64 整数）");
            addSourceFields(o, src); return compact(o);
        }
        const int n = args.value("value").toInt();
        if (n < 1 || n > 64) {
            QJsonObject o; o["ok"] = false;
            o["error"] = QString::fromUtf8("averaging 必须在 1..64 之间");
            addSourceFields(o, src); return compact(o);
        }
        QString err;
        const bool ok = vna.setAveraging(n, &err);
        QJsonObject o;
        o["ok"] = ok;
        o["action"] = "averaging";
        o["value"] = n;
        if (!ok) o["error"] = err.isEmpty() ? QString::fromUtf8("写 averages 失败") : err;
        addSourceFields(o, src);
        return compact(o);
    }

    QJsonObject o; o["ok"] = false;
    o["error"] = QString::fromUtf8("未知 action，合法值：status/vbat/freq_offset/edelay/averaging");
    addSourceFields(o, src);
    return compact(o);
}

// analyze_vna_resonance (read): run vna_rf::analyzeResonance on the live sweep.
QString execAnalyzeVnaResonance(const QJsonObject&, dsp::SpectrumEngine* engine,
                                const SourceInfo& src,
                                ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    auto& vna = engine->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) {
        o["valid"] = false;
        o["note"] = QString::fromUtf8("未连接 NanoVNA：无扫频数据可分析");
        addSourceFields(o, src);
        return compact(o);
    }
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
    if (!r.valid) o["note"] = QString::fromUtf8("点数不足或 span=0：谐振分析无效");
    addSourceFields(o, src);
    return compact(o);
}

// vna_tdr_cable (write, gated): TDR reflection run; velocity_factor required.
QString execVnaTdrCable(const QJsonObject& args, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const QJsonValue v = args.value(QStringLiteral("velocity_factor"));
    if (!v.isDouble())
        return errResult(QString::fromUtf8("参数 velocity_factor 缺失或不是数字"));
    const double vf = v.toDouble();
    if (vf <= 0.0 || vf > 1.0)
        return errResult(QString::fromUtf8("velocity_factor 必须在 (0,1] 之间"));

    QJsonObject o;
    o["ok"] = true;
    auto& vna = engine->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) {
        o["valid"] = false;
        o["note"] = QString::fromUtf8("未连接 NanoVNA：无扫频数据可做 TDR");
        addSourceFields(o, src);
        return compact(o);
    }
    auto f = vna.readFrequencies();
    auto s11 = vna.readData(0);
    vna::TdrResult t = vna::tdrCable(
        std::vector<double>(f.begin(), f.end()),
        std::vector<std::complex<double>>(s11.begin(), s11.end()), vf);
    o["valid"] = t.valid;
    o["distance_m"] = t.distance_m;
    o["cable_length_m"] = t.cable_length_m;
    if (!t.valid) o["note"] = QString::fromUtf8("未检出反射峰：TDR 无效");
    addSourceFields(o, src);
    return compact(o);
}

// analyze_vna_filter (read): run vna_rf::analyzeFilter on live S21 magnitude.
QString execAnalyzeVnaFilter(const QJsonObject&, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                            ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    auto& vna = engine->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) {
        o["valid"] = false;
        o["note"] = QString::fromUtf8("未连接 NanoVNA：无扫频数据可分析");
        addSourceFields(o, src);
        return compact(o);
    }
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
    if (!r.valid) o["note"] = QString::fromUtf8("幅度平坦或点数不足：非滤波器形态");
    addSourceFields(o, src);
    return compact(o);
}

// analyze_vna_equivalent (read): run vna::equivalentRlc on live S11 to extract a
// series-RLC equivalent (ESR / Q / L / C at the series resonance).
QString execAnalyzeVnaEquivalent(const QJsonObject&, dsp::SpectrumEngine* engine,
                                 const SourceInfo& src,
                                 ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o; o["ok"] = true;
    auto& vna = engine->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) {
        o["valid"] = false;
        o["note"] = QString::fromUtf8("未连接 NanoVNA：无扫频数据可分析");
        addSourceFields(o, src);
        return compact(o);
    }
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
    if (!r.valid) o["note"] = QString::fromUtf8(r.note.empty() ? "点数不足或谐振点病态" : r.note);
    addSourceFields(o, src);
    return compact(o);
}

// analyze_vna_touchstone (read): parse a Touchstone file's content or path and
// return the dataset (frequencies + S11/S21). Honest: unparseable -> ok:false.
QString execAnalyzeVnaTouchstone(const QJsonObject& a, dsp::SpectrumEngine*,
                                 const SourceInfo& src,
                                 ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o; o["ok"] = true;
    QString content = a.value("content").toString();
    const QString path = a.value("path").toString();
    if (content.isEmpty() && !path.isEmpty()) {
        QFile f(path);
        if (f.open(QIODevice::ReadOnly | QIODevice::Text)) content = QString::fromUtf8(f.readAll());
    }
    if (content.isEmpty()) {
        o["valid"] = false;
        o["note"] = QString::fromUtf8("需要 content 或可读的 path（Touchstone 文本）");
        addSourceFields(o, src); return compact(o);
    }
    vna::TouchstoneData d = vna::parseTouchstone(content.toStdString());
    o["valid"] = d.valid;
    o["n_ports"] = d.n_ports;
    QJsonArray fr, s11r, s11i, s21r, s21i;
    for (size_t i = 0; i < d.freq_hz.size(); ++i) {
        fr.append(d.freq_hz[i]);
        s11r.append(d.s11[i].real()); s11i.append(d.s11[i].imag());
        if (i < d.s21.size()) { s21r.append(d.s21[i].real()); s21i.append(d.s21[i].imag()); }
    }
    o["frequencies"] = fr;
    o["s11_re"] = s11r; o["s11_im"] = s11i;
    o["s21_re"] = s21r; o["s21_im"] = s21i;
    if (!d.valid) o["note"] = QString::fromStdString(d.note);
    addSourceFields(o, src); return compact(o);
}

// export_vna_snapshot (write, gated): offscreen-render the current sweep
// (Smith + VSWR) to a PNG at `path`. Honest: no device/data -> ok:false.
QString execExportVnaSnapshot(const QJsonObject& a, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                              ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    const QString path = a.value("path").toString();
    if (path.isEmpty()) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("需要 path（PNG 输出路径）");
        addSourceFields(o, src); return compact(o);
    }
    auto& vna = engine->vnaClient();
    if (!vna.isConnected()) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("未连接 NanoVNA：无扫频数据可截图");
        addSourceFields(o, src); return compact(o);
    }
    auto s11 = vna.readData(0);
    if (s11.empty()) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("无扫频数据");
        addSourceFields(o, src); return compact(o);
    }
    QImage img(640, 480, QImage::Format_ARGB32);
    img.fill(QColor(18, 18, 20));
    QPainter p(&img);
    p.setRenderHint(QPainter::Antialiasing, true);
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
    if (!img.save(path, "PNG")) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("写 PNG 失败（路径不可写）");
        addSourceFields(o, src); return compact(o);
    }
    o["ok"] = true;
    o["path"] = path;
    o["points"] = (int)s11.size();
    addSourceFields(o, src); return compact(o);
}

// export_spectrum_snapshot (write, gated): offscreen-render the current SDR
// spectrum panel (freq axis + center-marker) to a PNG at `path`. Honest.
QString execExportSpectrumSnapshot(const QJsonObject& a, dsp::SpectrumEngine* engine,
                                   const SourceInfo& src,
                                   ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    const QString path = a.value("path").toString();
    if (path.isEmpty()) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("需要 path（PNG 输出路径）");
        addSourceFields(o, src); return compact(o);
    }
    const double center = engine->centerFreq();
    const std::vector<float> bins = engine->fftDb();
    const double sr = engine->fftCenterHz() > 0 ? engine->centerFreq() : 0.0;
    QImage img(960, 480, QImage::Format_ARGB32);
    img.fill(QColor(18, 18, 20));
    QPainter p(&img);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setPen(QPen(QColor(120,120,120), 1.0));
    p.drawRect(40, 30, 900, 400);
    // Trace from real bins (dBFS mapped to y); honest flat baseline when empty.
    p.setPen(QPen(QColor(80,180,255), 1.6));
    if (!bins.empty()) {
        const int n = (int)bins.size();
        const double x0 = 40, w = 900, yTop = 30, h = 400;
        for (int i = 1; i < n; ++i) {
            double y1 = yTop + h * std::clamp((bins[i-1] + 60.0) / 80.0, 0.0, 1.0);
            double y2 = yTop + h * std::clamp((bins[i]   + 60.0) / 80.0, 0.0, 1.0);
            p.drawLine(QPointF(x0 + w * (i-1) / (n-1), y1),
                       QPointF(x0 + w * i / (n-1), y2));
        }
        // Peak marker
        int pk = (int)std::distance(bins.begin(),
                    std::max_element(bins.begin(), bins.end()));
        double px = x0 + w * pk / (n-1);
        double py = yTop + h * std::clamp((bins[pk] + 60.0) / 80.0, 0.0, 1.0);
        p.setPen(QPen(QColor(255,200,80), 1.4));
        p.drawLine(QPointF(px, py), QPointF(px, yTop + h));
        p.drawText(QPointF(px+4, py-4), QString::number(bins[pk], 'f', 1));
    }
    const double cx = 490;
    p.setPen(QPen(QColor(120,120,120), 1.0));
    p.drawLine(QPointF(cx, 30), QPointF(cx, 430));
    p.setPen(QPen(QColor(220,220,220), 1.0));
    p.drawText(QPointF(cx-40, 460), QString::number(center, 'f', 1));
    p.end();
    if (!img.save(path, "PNG")) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("写 PNG 失败（路径不可写）");
        addSourceFields(o, src); return compact(o);
    }
    o["ok"] = true;
    o["path"] = path;
    o["center_hz"] = center;
    addSourceFields(o, src); return compact(o);
}

// export_peaks_csv (write, gated): write the current peak list (freq,db)
// descending by strength to a CSV at `path`. Honest: no peak list -> empty.
QString execExportPeaksCsv(const QJsonObject& a, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                           ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    const QString path = a.value("path").toString();
    if (path.isEmpty()) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("需要 path（CSV 输出路径）");
        addSourceFields(o, src); return compact(o);
    }
    auto peaks = engine->peaks(10, 6.0);   // strength-desc, honest empty
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Text)) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("写 CSV 失败（路径不可写）");
        addSourceFields(o, src); return compact(o);
    }
    f.write("freq_hz,db\n");
    for (auto& p : peaks)
        f.write((QString::number(p.freq_hz, 'f', 1) + "," +
                 QString::number(p.dbfs, 'f', 1) + "\n").toUtf8());
    f.close();
    o["ok"] = true;
    o["path"] = path;
    o["rows"] = (int)peaks.size();
    if (peaks.empty()) o["note"] = QString::fromUtf8("无峰值（平坦/空 FFT）：仅写表头");
    addSourceFields(o, src); return compact(o);
}

// export_ft8_results (write, gated): dump current FT8 decode results to a file.
// Honest: C++ side has no FT8 result store (Python domain) -> header-only empty.
// With all_history=true, dumps the engine's decoded-message ring (WSJT-X style)
// with columns time_ms,freq_hz,demod,text; default false keeps the legacy
// candidate-list CSV (freq_hz,crc_ok,costas_score,rejected,reject_reason,iters).
QString execExportFt8Results(const QJsonObject& a, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                              ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    const QString path = a.value("path").toString();
    if (path.isEmpty()) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("需要 path（输出文件路径）");
        addSourceFields(o, src); return compact(o);
    }
    const bool allHistory = a.value("all_history").toBool(false);
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Text)) {
        o["ok"] = false;
        o["note"] = QString::fromUtf8("写文件失败（路径不可写）");
        addSourceFields(o, src); return compact(o);
    }
    int rows = 0;
    if (allHistory) {
        // Decoded-message history full dump (newest-first ring -> oldest-first
        // file for readability). Empty history = header-only (honest).
        f.write("time_ms,freq_hz,demod,text\n");
        if (engine) {
            const auto hist = engine->ft8History(dsp::SpectrumEngine::kFt8HistoryMax);
            // hist is newest-first; reverse for file output.
            for (int i = hist.size() - 1; i >= 0; --i) {
                const auto& e = hist.at(i);
                // CSV-quote text (may contain spaces; no commas in CALL grid
                // exchange by FT8 spec, but quote defensively).
                const QString t = QString(e.text).replace('"', "\"\"");
                f.write((QString::number(e.time_ms) + "," +
                         QString::number(e.freq_hz, 'f', 1) + "," +
                         e.demod + ",\"" + t + "\"\n").toUtf8());
                ++rows;
            }
        }
    } else {
        // Honest: dump the exported candidate list the C++ engine exposes. Columns
        // mirror Python collect_ft8_candidates(). Empty list = no candidates (no
        // fabricated callsigns/grid); crc_ok rows pending the decode bridge.
        f.write("freq_hz,crc_ok,costas_score,rejected,reject_reason,iters\n");
        if (engine) {
            auto cands = engine->ft8ExportCandidates();
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
    }
    f.close();
    o["ok"] = true;
    o["path"] = path;
    o["rows"] = rows;
    o["all_history"] = allHistory;
    if (rows == 0) {
        o["note"] = allHistory
            ? QString::fromUtf8("无 FT8 解码历史：仅写表头，不伪造呼号")
            : QString::fromUtf8("无 FT8 候选（无信号/未检出）：仅写表头，不伪造呼号");
    }
    addSourceFields(o, src); return compact(o);
}
// an EFHW / inverse-L matching network L/C suggestion.
QString execAnalyzeVnaMatch(const QJsonObject& a, dsp::SpectrumEngine*,
                            const SourceInfo& src,
                            ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o; o["ok"] = true;
    double r = a.value("load_r").toDouble(std::nan(""));
    double x = a.value("load_x").toDouble(0.0);
    double f = a.value("f_hz").toDouble(std::nan(""));
    if (!std::isfinite(r) || !std::isfinite(f) || r <= 0.0 || f <= 0.0) {
        o["valid"] = false;
        o["note"] = QString::fromUtf8("需要 load_r(>0) 与 f_hz(>0)");
        addSourceFields(o, src); return compact(o);
    }
    // Optional R-compensation (series loss resistance) folded into the load.
    double rComp = a.value("r_comp").toDouble(0.0);
    if (!std::isfinite(rComp) || rComp < 0.0) {
        o["valid"] = false;
        o["note"] = QString::fromUtf8("r_comp 需为 >=0 的电阻值");
        addSourceFields(o, src); return compact(o);
    }
    const double rEff = r + rComp;
    const QString topology = a.value("topology").toString(QStringLiteral("series_shunt"));

    // Default (omitted) topology keeps the legacy EFHW inverse-L output byte-for-
    // byte: same fields, same notes, same validity rule (R_load > 50).
    if (topology == QStringLiteral("series_shunt")) {
        vna::LMatchResult m = vna::efhwMatch({rEff, x}, f, 50.0);
        o["valid"] = m.valid;
        o["q"] = m.q;
        o["series_henry"] = m.series_henry;
        o["series_farad"] = m.series_farad;
        o["shunt_henry"] = m.shunt_henry;
        o["shunt_farad"] = m.shunt_farad;
        o["topology"] = QString::fromStdString(m.topology);
        if (!m.valid) o["note"] = QString::fromUtf8("负载电阻需 > 50Ω（EFHW 高阻馈电点）");
        addSourceFields(o, src); return compact(o);
    }

    // Multi-reactance topologies (xps / xs / xpl): closed-form pure function in
    // vna_rf. No closed-form / unknown topology -> honest valid:false.
    vna::MatchTopologyResult t =
        vna::analyzeMatchTopology(topology.toStdString(), {rEff, x}, f, 50.0);
    o["valid"] = t.valid;
    o["q"] = t.q;
    o["series_henry"] = t.series_henry;
    o["series_farad"] = t.series_farad;
    o["shunt_henry"] = t.shunt_henry;
    o["shunt_farad"] = t.shunt_farad;
    o["shunt2_henry"] = t.shunt2_henry;
    o["shunt2_farad"] = t.shunt2_farad;
    o["topology"] = QString::fromStdString(t.topology);
    if (!t.valid) o["note"] = QString::fromStdString(t.note);
    else if (!t.closed_form) o["note"] = QString::fromStdString(t.note);
    addSourceFields(o, src); return compact(o);
}
QString execListBookmarks(const QJsonObject&, dsp::SpectrumEngine*,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["bookmarks"] = QJsonArray{};
    o["count"] = 0;
    o["note"] = QString::fromUtf8("书签后端由 BookmarkManager（control 层）提供");
    addSourceFields(o, src);
    return compact(o);
}

// 9. add_bookmark (write): REAL execution against the injected BookmarkManager.
//    freq_hz required; mode / bandwidth_hz / group / name optional. A null store
//    is an honest error (never a routed stub); add() itself rejects freq<=0.
QString execAddBookmark(const QJsonObject& args, dsp::SpectrumEngine*,
                        const SourceInfo& src,
                        ui::BookmarkManager* bookmarks, aigui::WorkspaceController* /*workspace*/) {
    double f = 0.0;
    if (!needNum(args, "freq_hz", f))
        return errResult(QString::fromUtf8("参数 freq_hz 缺失或不是数字"));
    if (!bookmarks)
        return errResult(QString::fromUtf8("书签管理器未注入（Agent 层无书签存储，未执行）"));
    ui::Bookmark b;
    b.frequencyHz = f;
    if (args.contains("mode") && args.value("mode").isString())
        b.mode = args.value("mode").toString();
    if (args.contains("bandwidth_hz") && args.value("bandwidth_hz").isDouble())
        b.bandwidthHz = args.value("bandwidth_hz").toDouble();
    if (args.contains("group") && args.value("group").isString())
        b.group = args.value("group").toString();
    if (args.contains("name") && args.value("name").isString())
        b.name = args.value("name").toString();
    const int idx = bookmarks->add(b);   // auto-sorts by (group, freq) + saves
    QJsonObject o;
    if (idx < 0) {
        o["ok"] = false;
        o["error"] = QString::fromUtf8("频率非法：freq_hz 必须大于 0（书签已拒绝入册）");
        addSourceFields(o, src);
        return compact(o);
    }
    o["ok"] = true;
    o["index"] = idx;
    o["count"] = bookmarks->count();
    o["freq_hz"] = f;
    o["message"] = QString::fromUtf8("书签已写入（排序后落位 %1 / 共 %2 条）")
                       .arg(idx).arg(bookmarks->count());
    addSourceFields(o, src);
    return compact(o);
}

// 10. tune_to_bookmark (write): index required; out-of-range is an honest error.
//     On a hit the SELECTED VFO is retuned to the bookmark's frequency through
//     the same in-band IF-offset path the band-box drag uses (vfoSetOffset).
QString execTuneToBookmark(const QJsonObject& args, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                           ui::BookmarkManager* bookmarks, aigui::WorkspaceController* /*workspace*/) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    if (!bookmarks)
        return errResult(QString::fromUtf8("书签管理器未注入（Agent 层无书签存储，未执行）"));
    const int i = static_cast<int>(idx);
    if (i < 0 || i >= bookmarks->count())
        return errResult(QString::fromUtf8("书签 index %1 越界（共 %2 条）")
                         .arg(i).arg(bookmarks->count()));
    const ui::Bookmark b = bookmarks->list().at(i);
    const int vfoId = engine->selectedVfoId();
    engine->vfoSetOffset(vfoId, b.frequencyHz);
    QJsonObject o;
    o["ok"] = true;
    o["freq_hz"] = b.frequencyHz;
    o["vfo_index"] = vfoId;
    o["message"] = QString::fromUtf8("已按书签 %1 调谐至 %2 Hz").arg(i).arg(b.frequencyHz);
    addSourceFields(o, src);
    return compact(o);
}

// 11. delete_bookmark (write): index required; out-of-range is an honest error
//     (removeAt itself is silently out-of-bounds, so we check before calling).
QString execDeleteBookmark(const QJsonObject& args, dsp::SpectrumEngine*,
                           const SourceInfo& src,
                           ui::BookmarkManager* bookmarks, aigui::WorkspaceController* /*workspace*/) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    // Explicit confirm gate (D3-7): bookmark deletion has no undo and indices
    // shift when the list changes -- a bare index is never enough. Only a real
    // JSON true passes (missing / string "true" / 1 all refuse).
    if (!args.value(QStringLiteral("confirm")).toBool())
        return errResult(QString::fromUtf8(
            "删除书签需显式确认：传 confirm=true（书签删除无撤销，本次未执行）"));
    if (!bookmarks)
        return errResult(QString::fromUtf8("书签管理器未注入（Agent 层无书签存储，未执行）"));
    const int i = static_cast<int>(idx);
    if (i < 0 || i >= bookmarks->count())
        return errResult(QString::fromUtf8("书签 index %1 越界（共 %2 条）")
                         .arg(i).arg(bookmarks->count()));
    bookmarks->removeAt(i);
    QJsonObject o;
    o["ok"] = true;
    o["remaining"] = bookmarks->count();
    o["message"] = QString::fromUtf8("书签 %1 已删除（剩余 %2 条）").arg(i).arg(bookmarks->count());
    addSourceFields(o, src);
    return compact(o);
}

// 12. list_vfos (read): engine exposes vfoMarkers() for real.
QString execListVfos(const QJsonObject&, dsp::SpectrumEngine* engine,
                     const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonArray arr;
    for (const dsp::VfoMarker& m : engine->vfoMarkers()) {
        QJsonObject v;
        v["id"] = m.id;
        v["freq_hz"] = m.freqHz;
        v["bandwidth_hz"] = m.bandwidthHz;
        v["mode"] = m.mode;
        v["name"] = m.name;
        v["selected"] = m.selected;
        v["armed"] = m.armed;
        arr.append(v);
    }
    QJsonObject o;
    o["ok"] = true;
    o["count"] = static_cast<int>(arr.size());
    o["selected_vfo_id"] = engine->selectedVfoId();
    o["vfos"] = arr;
    addSourceFields(o, src);
    return compact(o);
}

// 13. add_vfo (write): engine vfoAdd() is real.
QString execAddVfo(const QJsonObject&, dsp::SpectrumEngine* engine,
                   const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    engine->vfoAdd();
    QJsonObject o;
    o["ok"] = true;
    o["selected_vfo_id"] = engine->selectedVfoId();
    o["message"] = QString::fromUtf8("已新增 VFO 信道");
    addSourceFields(o, src);
    return compact(o);
}

// 14. switch_vfo (write): index required; engine vfoSelect() is real.
QString execSwitchVfo(const QJsonObject& args, dsp::SpectrumEngine* engine,
                      const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    engine->vfoSelect(static_cast<int>(idx));
    QJsonObject o;
    o["ok"] = true;
    o["index"] = static_cast<int>(idx);
    o["selected_vfo_id"] = engine->selectedVfoId();
    o["message"] = QString::fromUtf8("已切换到 VFO %1").arg(static_cast<int>(idx));
    addSourceFields(o, src);
    return compact(o);
}

// 15. rename_vfo (write): index/name required; the vfo_manager rename interface
//     lands on the control side -> validated + routed.
QString execRenameVfo(const QJsonObject& args, dsp::SpectrumEngine*,
                      const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    QString name;
    if (!needStr(args, "name", name))
        return errResult(QString::fromUtf8("参数 name 缺失或不是字符串"));
    QJsonObject echo; echo["index"] = idx; echo["name"] = name;
    return routedOk("rename_vfo", echo, src);
}

// 16. set_vfo_armed (write): index + enabled required. Keeps a VFO demodulated in
//     the background (parallel monitoring) after another VFO is selected. Engine
//     vfoSetArmed() is real (forwarder to VfoManager::setArmed).
QString execSetVfoArmed(const QJsonObject& args, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    bool enabled = false;
    if (!args.contains("enabled"))
        return errResult(QString::fromUtf8("参数 enabled 缺失"));
    const QJsonValue v = args.value("enabled");
    if (v.isBool()) enabled = v.toBool();
    else if (v.isDouble()) enabled = v.toDouble() != 0.0;
    else return errResult(QString::fromUtf8("参数 enabled 必须是布尔值"));
    const auto markers = engine->vfoMarkers();
    const int i = static_cast<int>(idx);
    if (i < 0 || i >= markers.size())
        return errResult(QString::fromUtf8("VFO index %1 越界（共 %2 个）")
                         .arg(i).arg(markers.size()));
    const int id = markers[i].id;
    engine->vfoSetArmed(id, enabled);
    QJsonObject o;
    o["ok"] = true;
    o["index"] = i;
    o["vfo_id"] = id;
    o["armed"] = enabled;
    o["message"] = enabled
        ? QString::fromUtf8("VFO %1 将在后台并行监听").arg(i)
        : QString::fromUtf8("已取消 VFO %1 的后台并行监听").arg(i);
    addSourceFields(o, src);
    return compact(o);
}

// 17. set_vfo_frequency (write): index|id + freq_hz. Phase63 D1: accepts EITHER
//     "index" (marker ordinal, Agent schema) OR "id" (direct VFO id, CH/HTTP).
QString execSetVfoFrequency(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double hz = 0.0;
    if (!needNum(args, "freq_hz", hz))
        return errResult(QString::fromUtf8("参数 freq_hz 缺失或不是数字"));
    int i = -1, id = -1; QString err;
    if (!resolveVfoTarget(args, engine, i, id, err)) return errResult(err);
    engine->vfoSetFreq(id, hz);
    QJsonObject o;
    o["ok"] = true;
    o["index"] = i;
    o["vfo_id"] = id;
    o["freq_hz"] = hz;
    o["message"] = QString::fromUtf8("VFO %1 已调谐至 %2 Hz").arg(i).arg(hz);
    addSourceFields(o, src);
    return compact(o);
}

// 18. set_vfo_mode (write): index|id + mode, mode validated against the shared
//     ControlHub mode table. Phase63 D1: accepts "index" OR "id".
QString execSetVfoMode(const QJsonObject& args, dsp::SpectrumEngine* engine,
                       const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString mode;
    if (!needStr(args, "mode", mode))
        return errResult(QString::fromUtf8("参数 mode 缺失或不是字符串"));
    // Case-insensitive whitelist match; apply/echo the canonical token spelling
    // (engine decoder keys are exact-case: "m17" lowercase, "POCSAG" upper).
    QString canonical;
    for (int k = 0; k < tokens::kControlHubModesCount; ++k) {
        if (mode.compare(QLatin1String(tokens::kControlHubModes[k]),
                         Qt::CaseInsensitive) == 0) {
            canonical = QString::fromUtf8(tokens::kControlHubModes[k]);
            break;
        }
    }
    if (canonical.isEmpty())
        return errResult(QString::fromUtf8("未知解调模式: %1").arg(mode));
    int i = -1, id = -1; QString err;
    if (!resolveVfoTarget(args, engine, i, id, err)) return errResult(err);
    engine->vfoSetMode(id, canonical);
    QJsonObject o;
    o["ok"] = true;
    o["index"] = i;
    o["vfo_id"] = id;
    o["mode"] = canonical;
    o["message"] = QString::fromUtf8("VFO %1 解调模式已切换为 %2").arg(i).arg(canonical);
    addSourceFields(o, src);
    return compact(o);
}

// 19. set_vfo_bandwidth (write): index|id + bandwidth_hz. Phase63 D1: accepts
//     "index" OR "id"; positive-bandwidth guard preserved.
QString execSetVfoBandwidth(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double bw = 0.0;
    if (!needNum(args, "bandwidth_hz", bw))
        return errResult(QString::fromUtf8("参数 bandwidth_hz 缺失或不是数字"));
    // Honest hardware-domain guard (D2-6/D3-6): the same legal band as every UI
    // surface (tokens::kVfoMinBandwidthHz..kVfoMaxBandwidthHz, max raised to
    // 2 MHz so the ADS-B preset is actually reachable). Rejected, not clamped.
    if (bw < tokens::kVfoMinBandwidthHz || bw > tokens::kVfoMaxBandwidthHz)
        return errResult(QString::fromUtf8(
            "带宽越界：必须在 %1..%2 Hz（与 UI 同界，含 ADS-B 2 MHz 预设），收到 %3")
            .arg(tokens::kVfoMinBandwidthHz).arg(tokens::kVfoMaxBandwidthHz).arg(bw));
    int i = -1, id = -1; QString err;
    if (!resolveVfoTarget(args, engine, i, id, err)) return errResult(err);
    engine->vfoSetBandwidth(id, bw);
    QJsonObject o;
    o["ok"] = true;
    o["index"] = i;
    o["vfo_id"] = id;
    o["bandwidth_hz"] = bw;
    o["message"] = QString::fromUtf8("VFO %1 带宽已设为 %2 Hz").arg(i).arg(bw);
    addSourceFields(o, src);
    return compact(o);
}

// 20. list_recordings (read): scan the engine recDir_ honestly (empty if absent).
QString execListRecordings(const QJsonObject&, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const QString dir = engine->recordingDir();
    QDir d(dir);
    QJsonArray arr;
    if (d.exists()) {
        const QFileInfoList fis = d.entryInfoList(
            QDir::Files | QDir::NoSymLinks, QDir::Name);
        for (const QFileInfo& fi : fis) {
            QJsonObject f;
            f["name"] = fi.fileName();
            f["bytes"] = fi.size();
            f["modified"] = fi.lastModified().toString(Qt::ISODate);
            arr.append(f);
        }
    }
    QJsonObject o;
    o["ok"] = true;
    o["dir"] = dir;
    o["count"] = static_cast<int>(arr.size());   // honest empty state
    o["recordings"] = arr;
    addSourceFields(o, src);
    return compact(o);
}

// 17. delete_recording (write): name required; delete ONLY inside recDir_.
QString execDeleteRecording(const QJsonObject& args, dsp::SpectrumEngine* engine,
                             const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString name;
    if (!needStr(args, "name", name))
        return errResult(QString::fromUtf8("参数 name 缺失或不是字符串"));
    // Safety: only a bare filename inside recDir_ -- never a path traversal.
    if (name.contains('/') || name.contains('\\') || name == "." || name == "..")
        return errResult(QString::fromUtf8("name 必须是录制目录内的纯文件名"));
    const QString path = QDir(engine->recordingDir()).filePath(name);
    if (!QFileInfo::exists(path))
        return errResult(QString::fromUtf8("录制文件不存在：%1").arg(name));
    QJsonObject o;
    o["ok"] = QFile::remove(path);
    o["name"] = name;
    o["path"] = path;
    if (!o["ok"].toBool()) o["error"] = QString::fromUtf8("删除失败");
    addSourceFields(o, src);
    return compact(o);
}

// 18. export_recording (write): copy a recDir_ file out to out_path (honest).
QString execExportRecording(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString name, outPath;
    if (!needStr(args, "name", name))
        return errResult(QString::fromUtf8("参数 name 缺失或不是字符串"));
    if (!needStr(args, "out_path", outPath))
        return errResult(QString::fromUtf8("参数 out_path 缺失或不是字符串"));
    if (name.contains('/') || name.contains('\\'))
        return errResult(QString::fromUtf8("name 必须是录制目录内的纯文件名"));
    const QString srcPath = QDir(engine->recordingDir()).filePath(name);
    if (!QFileInfo::exists(srcPath))
        return errResult(QString::fromUtf8("录制文件不存在：%1").arg(name));
    QJsonObject o;
    o["ok"] = QFile::copy(srcPath, outPath);
    o["name"] = name;
    o["out_path"] = outPath;
    if (!o["ok"].toBool())
        o["error"] = QString::fromUtf8("导出失败（目标已存在或不可写）");
    addSourceFields(o, src);
    return compact(o);
}

// 19. set_fft_params (write): engine setFftSize/setWindowType/setAverageMode real.
//     Phase63 D3 bilateral dual-type: window/average accept BOTH the string
//     enum ("Hann"/"Flattop"/"Blackman", "Off"/"Slow"/"Fast") that the LLM
//     schema advertises AND the raw int (0/1/2) that the ControlHub/HTTP
//     channel has always used. Either type lands on the same engine setter.
namespace {
int mapWindowString(const QString& w) {
    // Case-insensitive (D2-3): the UI spells it "FlatTop" while older tool text
    // used "Flattop"; both -- and any case variant -- land on the same engine id
    // instead of silently falling back to Hann.
    if (w.compare(QLatin1String("FlatTop"), Qt::CaseInsensitive) == 0) return 1;
    if (w.compare(QLatin1String("Blackman"), Qt::CaseInsensitive) == 0) return 2;
    return 0;   // "Hann" (default / unrecognised falls back to Hann)
}
int mapAverageString(const QString& av) {
    if (av == QLatin1String("Slow")) return 1;
    if (av == QLatin1String("Fast")) return 2;
    return 0;   // "Off"
}
} // namespace
QString execSetFftParams(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double sz = 0.0;
    if (!needNum(args, "fft_size", sz))
        return errResult(QString::fromUtf8("参数 fft_size 缺失或不是数字"));
    // Honest reject (D2-2/D3-8): setFftSize() only accepts 1024/2048/4096 and
    // used to DROP anything else silently while we echoed the CURRENT value
    // back as if the request had applied (fake success). An out-of-set size is
    // now an explicit error naming the legal set -- nothing is silently ignored.
    const int n = static_cast<int>(sz);
    if (sz != static_cast<double>(n) || (n != 1024 && n != 2048 && n != 4096))
        return errResult(QString::fromUtf8(
            "fft_size 只接受 1024/2048/4096 三档（收到 %1），未生效——请按三档重试").arg(sz));
    engine->setFftSize(n);
    QJsonObject o;
    o["ok"] = true;
    o["fft_size"] = engine->fftSize();
    // window: accept string enum OR raw int (0=Hann 1=Flattop 2=Blackman).
    const QJsonValue wv = args.value(QStringLiteral("window"));
    if (wv.isString()) {
        const QString w = wv.toString();
        engine->setWindowType(mapWindowString(w));
        o["window"] = w;
    } else if (wv.isDouble()) {
        const int wi = wv.toInt();
        engine->setWindowType(wi);
        o["window"] = wi;
    }
    // average: accept string enum OR raw int (0=Off 1=Slow 2=Fast).
    const QJsonValue av = args.value(QStringLiteral("average"));
    if (av.isString()) {
        const QString avs = av.toString();
        engine->setAverageMode(mapAverageString(avs));
        o["average"] = avs;
    } else if (av.isDouble()) {
        const int ai = av.toInt();
        engine->setAverageMode(ai);
        o["average"] = ai;
    }
    o["message"] = QString::fromUtf8("FFT 参数已设置");
    addSourceFields(o, src);
    return compact(o);
}

// 20. set_color_map (write): persist the colormap file path to QSettings.
QString execSetColorMap(const QJsonObject& args, dsp::SpectrumEngine*,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString p;
    if (!needStr(args, "file_path", p))
        return errResult(QString::fromUtf8("参数 file_path 缺失或不是字符串"));
    ui::ParsedColormap parsed;
    QString err;
    if (!ui::loadColormapFileForTool(p, &parsed, &err))
        return errResult(err);
    mbdsdr::core::AppSettings().setValue(QStringLiteral("view/wfColormapFile"), p);
    QJsonObject o;
    o["ok"] = true;
    o["file_path"] = p;
    o["name"] = QString::fromUtf8(parsed.name.c_str());
    o["stops"] = static_cast<int>(parsed.stops.size());
    o["message"] = QString::fromUtf8(
        "色板文件已加载校验通过并持久化（UI 启动时重应用同一文件）");
    addSourceFields(o, src);
    return compact(o);
}

// get_color_maps (read): built-in palettes + live-validated external file.
QString execGetColorMaps(const QJsonObject& /*args*/, dsp::SpectrumEngine*,
                         const SourceInfo& src,
                         ui::BookmarkManager* /*bookmarks*/,
                         aigui::WorkspaceController* /*workspace*/) {
    QJsonArray builtin;
    QJsonObject b1; b1["name"] = QStringLiteral("经典");   b1["palette"] = 0; builtin.append(b1);
    QJsonObject b2; b2["name"] = QStringLiteral("单色");   b2["palette"] = 1; builtin.append(b2);
    QJsonObject b3; b3["name"] = QStringLiteral("Viridis"); b3["palette"] = 2; builtin.append(b3);

    QJsonObject o;
    o["ok"] = true;
    o["builtin"] = builtin;
    const QString path = mbdsdr::core::AppSettings().value(QStringLiteral("view/wfColormapFile")).toString();
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
    addSourceFields(o, src);
    return compact(o);
}

// 21. get_spectrum_status (read): engine fftSize()/windowType()/averageMode() real.
QString execGetSpectrumStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["fft_size"] = engine->fftSize();
    // Phase63 output-contract OF2: key names aligned to the declared schema
    // description ("fft_size/window/average"), the ControlHub readback
    // (cmdGetSpectrumStatus), and the set_fft_params write echo -- previously
    // emitted window_type/average_mode, which disagreed with both.
    o["window"] = engine->windowType();
    o["average"] = engine->averageMode();
    addSourceFields(o, src);
    return compact(o);
}

// Phase55 block3: toggle live satellite-pass Doppler auto-compensation through
// the abstract surface MainWindow registers on the engine. No surface
// (headless/test) -> honest "not available". The implementation re-checks the
// station/capture preconditions, so a toggle without them stays off.
QString execSetDopplerCompensation(const QJsonObject& args, dsp::SpectrumEngine* engine,
                                  const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    auto* surf = engine->dopplerControlSurface();
    if (!surf) {
        o["ok"] = false;
        o["available"] = false;
        o["error"] = QString::fromUtf8("多普勒补偿不可用（无 UI 控制面；需在桌面端设置本站并捕获过境）");
        addSourceFields(o, src);
        return compact(o);
    }
    const bool on = args.value("enable").toBool(false);
    surf->setDopplerCompensationEnabled(on);
    o["ok"] = true;
    o["available"] = surf->isDopplerCompensationAvailable();
    o["enabled"] = surf->isDopplerCompensationEnabled();
    o["requested_enable"] = on;
    addSourceFields(o, src);
    return compact(o);
}

// Phase58 block2: connect to an rtl_tcp network source. Real TCP handshake +
// RTL0 header; the engine reports the honest socket reason on failure.
QString execConnectNetworkSource(const QJsonObject& args, dsp::SpectrumEngine* engine,
                                 const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    const QString host = args.value("host").toString();
    if (host.isEmpty()) {
        o["ok"] = false;
        o["error"] = QString::fromUtf8("缺少 host 参数");
        addSourceFields(o, src);
        return compact(o);
    }
    const int port = static_cast<int>(args.value("port").toDouble(1234.0));
    const bool ok = engine->connectRtlTcp(host, static_cast<quint16>(port));
    o["ok"] = ok;
    o["host"] = host;
    o["port"] = port;
    o["source"] = ok ? QString("rtl_tcp %1:%2").arg(host).arg(port)
                     : QString::fromUtf8("连接失败（真实 socket 错误已回传）");
    addSourceFields(o, src);
    return compact(o);
}

// READ-ONLY: real source capability read-back (device name, tunable / sample-rate
// range, discrete gain steps) straight off the ACTIVE source. Honest empty state:
// an offline / test / unconnected source reports connected=false, an EMPTY gains
// array, and an honest provenance note -- we never fabricate a tunable range or a
// gain step table (the engine itself leaves those at 0 / empty for non-real sources).
QString execGetCapabilities(const QJsonObject&, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const dsp::DeviceCapabilities c = engine->sourceCapabilities();
    QJsonArray gains;
    for (double g : engine->availableGainsDb()) gains.append(g);   // empty when no real source
    // The struct carries the real source note, but a synthetic test source must
    // be explicitly labelled: the empty-state provenance does not distinguish
    // "synthetic" from "truly unplugged", so say so honestly. A fresh engine whose
    // caps snapshot has not been filled yet leaves provenance empty -> honest
    // "未连接" rather than a blank.
    QString provenance = c.provenance;
    if (src.testSignal)
        provenance = QString::fromUtf8("测试信号源（非硬件，能力表为空）");
    else if (!c.connected && provenance.isEmpty())
        provenance = QString::fromUtf8("未连接");
    QJsonObject o;
    o["ok"] = true;
    o["connected"] = c.connected;
    o["device_name"] = c.deviceName;
    o["tunable_min_hz"] = c.tunableMinHz;
    o["tunable_max_hz"] = c.tunableMaxHz;
    o["sample_rate_min_hz"] = c.sampleRateMinHz;
    o["sample_rate_max_hz"] = c.sampleRateMaxHz;
    o["gains_db"] = gains;
    o["provenance"] = provenance;
    addSourceFields(o, src);
    return compact(o);
}

// READ-ONLY: recording state straight off the engine. `recording` is derived from
// the live recording path (empty = not recording), which is deterministic and
// does not wait for a state-changed signal. watch/dir are real engine getters.
QString execGetRecordingState(const QJsonObject&, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const QString path = engine->recordingPath();
    QJsonObject o;
    o["ok"] = true;
    o["recording"] = !path.isEmpty();
    o["recording_path"] = path;
    o["watch_enabled"] = engine->watchEnabled();
    o["recording_dir"] = engine->recordingDir();
    addSourceFields(o, src);
    return compact(o);
}

// ---- P3 #73: local memory family (SQLite FTS5, ~/.mbdsdr/memory.db) --------
// Every call opens -> operates -> closes; no global handle. Writes go through
// the manual gate (write=true); search is read-only and never gated.
namespace {

QString memoryArgCheck(const QJsonObject& args, QString* scope, QString* scopeId,
                       QString* key, QString* content, QString* err) {
    *scope = args.value(QStringLiteral("scope")).toString();
    *scopeId = args.value(QStringLiteral("scope_id")).toString();
    *key = args.value(QStringLiteral("key")).toString();
    *content = args.value(QStringLiteral("content")).toString();
    static const QSet<QString> kScopes{
        QStringLiteral("user"), QStringLiteral("agent"), QStringLiteral("session")};
    if (!kScopes.contains(*scope))
        return QStringLiteral("scope 必填枚举 user/agent/session");
    if (scopeId->isEmpty()) return QStringLiteral("scope_id 必填（作用域内实例标识）");
    if (key->isEmpty()) return QStringLiteral("key 必填（scope 内唯一键）");
    if (content->isEmpty()) return QStringLiteral("content 必填（记忆正文）");
    return {};
}

} // namespace

QString execMemoryAdd(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                      const SourceInfo& src, ui::BookmarkManager*, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    QString scope, scopeId, key, content;
    if (const QString bad = memoryArgCheck(args, &scope, &scopeId, &key, &content, nullptr);
        !bad.isEmpty()) {
        o["ok"] = false; o["error"] = bad;
        addSourceFields(o, src);
        return compact(o);
    }
    // Async enqueue: the main agent never blocks on disk; the background
    // checkpoint writer persists rows (single-writer invariant).
    QString err;
    if (!memoryWriter().submit(MemoryWriteRequest::Add, scope, scopeId, key, content, &err)) {
        o["ok"] = false;
        o["error"] = err;
    } else {
        o["ok"] = true;
        o["queued"] = true;
        o["pending_count"] = memoryWriter().pendingCount();
        o["scope"] = scope; o["scope_id"] = scopeId; o["key"] = key;
        o["note"] = QStringLiteral("已入队，落库由后台 checkpoint writer 异步执行；立即落库用 memory_flush");
    }
    addSourceFields(o, src);
    return compact(o);
}

QString execMemorySearch(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                          const SourceInfo& src, ui::BookmarkManager*, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    const QString query = args.value(QStringLiteral("query")).toString().trimmed();
    if (query.isEmpty()) {
        o["ok"] = false; o["error"] = QStringLiteral("query 必填且不可为空");
        addSourceFields(o, src);
        return compact(o);
    }
    const QString scopeFilter = args.value(QStringLiteral("scope")).toString();
    const QString scopeIdFilter = args.value(QStringLiteral("scope_id")).toString();
    int limit = (int)args.value(QStringLiteral("limit")).toDouble(20.0);

    MemoryStore store(defaultMemoryDbPath());
    QString err;
    if (!store.open(&err)) {
        o["ok"] = false;
        o["error"] = QStringLiteral("记忆库不可用：%1").arg(err);
        o["note"] = QStringLiteral("检查 ~/.mbdsdr 目录权限");
        addSourceFields(o, src);
        return compact(o);
    }
    QList<MemoryHit> hits = store.search(query, scopeFilter, limit, &err);
    if (hits.isEmpty() && !err.isEmpty()) {
        o["ok"] = false;
        o["error"] = err;
        addSourceFields(o, src);
        return compact(o);
    }
    QJsonArray arr;
    for (const MemoryHit& h : hits) {
        QJsonObject hh;
        hh["scope"] = h.scope;
        hh["scope_id"] = h.scopeId;
        hh["key"] = h.key;
        hh["content"] = h.content;
        hh["snippet"] = h.snippet;
        if (!scopeIdFilter.isEmpty() && h.scopeId != scopeIdFilter) continue;  // honest client-side polish
        arr.append(hh);
    }
    o["ok"] = true;
    o["hits"] = arr;
    o["count"] = arr.size();
    if (arr.isEmpty())
        o["note"] = QStringLiteral("无命中（检索词：%1）；不编造记忆").arg(query);
    addSourceFields(o, src);
    return compact(o);
}

QString execMemoryUpdate(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                         const SourceInfo& src, ui::BookmarkManager*, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    QString scope, scopeId, key, content;
    if (const QString bad = memoryArgCheck(args, &scope, &scopeId, &key, &content, nullptr);
        !bad.isEmpty()) {
        o["ok"] = false; o["error"] = bad;
        addSourceFields(o, src);
        return compact(o);
    }
    QString err;
    if (!memoryWriter().submit(MemoryWriteRequest::Update, scope, scopeId, key, content, &err)) {
        o["ok"] = false;
        o["error"] = err;
    } else {
        o["ok"] = true;
        o["queued"] = true;
        o["pending_count"] = memoryWriter().pendingCount();
        o["scope"] = scope; o["scope_id"] = scopeId; o["key"] = key;
        o["note"] = QStringLiteral("已入队，upsert 由后台 checkpoint writer 异步落库；立即落库用 memory_flush");
    }
    addSourceFields(o, src);
    return compact(o);
}

// P3 #74: read-only writer introspection + synchronous checkpoint trigger.
QString execCheckpointStatus(const QJsonObject&, dsp::SpectrumEngine* /*engine*/,
                             const SourceInfo& src, ui::BookmarkManager*, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    o["pending_count"] = memoryWriter().pendingCount();
    o["queue_depth"] = memoryWriter().queueDepth();
    o["last_flush_ms"] = (double)memoryWriter().lastFlushMs();
    addSourceFields(o, src);
    return compact(o);
}

QString execMemoryFlush(const QJsonObject&, dsp::SpectrumEngine* /*engine*/,
                        const SourceInfo& src, ui::BookmarkManager*, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    QString err;
    const int written = memoryWriter().flush(&err);
    o["written"] = written;
    o["checkpoint_rewritten"] = true;
    if (!err.isEmpty()) {
        o["ok"] = false;
        o["error"] = err;
        o["note"] = QStringLiteral("部分条目落库失败；已处理 %1 条").arg(written);
    } else {
        o["ok"] = true;
    }
    addSourceFields(o, src);
    return compact(o);
}

// G1: read-only task-run history (TaskRunner completion records, ~/.mbdsdr/memory.db).
// Two modes: list (default, newest N) or detail (id present -> one run + steps[]).
QString execGetTaskHistory(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                           const SourceInfo& src, ui::BookmarkManager*, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    MemoryStore store(defaultMemoryDbPath());
    QString err;
    if (!store.open(&err)) {
        o["ok"] = false;
        o["error"] = QStringLiteral("任务历史库不可用：%1").arg(err);
        addSourceFields(o, src);
        return compact(o);
    }
    const QJsonValue idVal = args.value(QStringLiteral("id"));
    if (!idVal.isNull() && !idVal.isUndefined()) {
        // Detail mode: one run, with the parsed step array. Non-positive ids
        // honestly miss (autoincrement starts at 1).
        const qint64 id = (qint64)idVal.toDouble(-1.0);
        TaskRunRecord rec;
        if (!store.getTaskRun(id, &rec, &err)) {
            o["ok"] = false;
            o["error"] = err;
            o["note"] = QStringLiteral("没有该 id 的任务记录；用列表模式先查最近任务");
            addSourceFields(o, src);
            return compact(o);
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
        o["ok"] = true;
        o["run"] = run;
    } else {
        // List mode: newest-first snapshot (summary only).
        int limit = (int)args.value(QStringLiteral("limit")).toDouble(10.0);
        const QList<TaskRunRecord> runs = store.listTaskRuns(limit, &err);
        if (runs.isEmpty() && !err.isEmpty()) {
            o["ok"] = false;
            o["error"] = err;
            addSourceFields(o, src);
            return compact(o);
        }
        QJsonArray arr;
        for (const TaskRunRecord& r : runs) {
            QJsonObject rr;
            rr["id"] = (double)r.id;
            rr["name"] = r.name;
            rr["state"] = r.state;
            rr["finished_ms"] = (double)r.finishedMs;
            rr["step_count"] = r.stepCount;
            rr["summary"] = r.summary;
            arr.append(rr);
        }
        o["ok"] = true;
        o["runs"] = arr;
        o["count"] = arr.size();
        // 待执行计划槽快照（run_template 登记、执行后状态真实回写）：列表模式
        // 一并返回，模型可查自己登记的计划走到哪一步。空槽 plans=[] 诚实空态。
        const QJsonObject pending = PendingPlanStore::instance().toJson();
        o["pending_plans"] = pending.value("plans");
        o["pending_count"] = pending.value("count");
        if (arr.isEmpty())
            o["note"] = QStringLiteral("无任务历史（TaskRunner 尚未完成过任何任务；执行 list_templates/run_template 计划后此处可查）");
    }
    addSourceFields(o, src);
    return compact(o);
}

// G2: write-gated delete one memory by (scope, key). Synchronous short-lived
// connection: honest immediate found/not-found (dup-add surfaces failures at
// drain; delete must answer now).
QString execMemoryDelete(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                         const SourceInfo& src, ui::BookmarkManager*, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    QString scope, key;
    static const QSet<QString> kScopes{
        QStringLiteral("user"), QStringLiteral("agent"), QStringLiteral("session")};
    scope = args.value(QStringLiteral("scope")).toString();
    key = args.value(QStringLiteral("key")).toString();
    if (!kScopes.contains(scope)) {
        o["ok"] = false; o["error"] = QStringLiteral("scope 必填枚举 user/agent/session");
        addSourceFields(o, src);
        return compact(o);
    }
    if (key.isEmpty()) {
        o["ok"] = false; o["error"] = QStringLiteral("key 必填（scope 内唯一键）");
        addSourceFields(o, src);
        return compact(o);
    }
    MemoryStore store(defaultMemoryDbPath());
    QString err;
    if (!store.open(&err)) {
        o["ok"] = false;
        o["error"] = QStringLiteral("记忆库不可用：%1").arg(err);
        addSourceFields(o, src);
        return compact(o);
    }
    bool found = false;
    if (!store.deleteKey(scope, key, &found, &err)) {
        o["ok"] = false;
        o["error"] = err;
    } else if (!found) {
        // Honest: nothing was there -- not an error, but the caller must know
        // the delete was a no-op (silent success would lie about memory state).
        o["ok"] = true;
        o["deleted"] = false;
        o["scope"] = scope; o["key"] = key;
        o["note"] = QStringLiteral("该 (scope,key) 不存在，删除为空操作；不编造已删条目");
    } else {
        o["ok"] = true;
        o["deleted"] = true;
        o["scope"] = scope; o["key"] = key;
    }
    addSourceFields(o, src);
    return compact(o);
}

// G2: read-only memory statistics (honest empty on a fresh DB).
QString execMemoryStats(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                        const SourceInfo& src, ui::BookmarkManager*, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    MemoryStore store(defaultMemoryDbPath());
    QString err;
    if (!store.open(&err)) {
        o["ok"] = false;
        o["error"] = QStringLiteral("记忆库不可用：%1").arg(err);
        addSourceFields(o, src);
        return compact(o);
    }
    QJsonObject st = store.stats(&err);
    if (st.isEmpty() && !err.isEmpty()) {
        o["ok"] = false; o["error"] = err;
        addSourceFields(o, src);
        return compact(o);
    }
    o["ok"] = true;
    o["stats"] = st;
    addSourceFields(o, src);
    return compact(o);
}

// ---- 第 10 域「工作台编排」（AIGUI P0-b）---------------------------------
// These four tools drive the live WorkspaceController (three-column splitter +
// right-tab visibility). They do NOT touch DSP / audio / hardware. When the
// controller is not injected (headless tests / not-yet-wired MainWindow) they
// return an honest error envelope rather than a fabricated success -- same shape
// as the bookmark-not-injected path.

namespace {
QString workspaceNotInjected() {
    QJsonObject o;
    o["ok"] = false;
    // Distinct from CONTROLLER_NOT_CONNECTED (controller exists but widgets are
    // null): here the controller itself was never wired by MainWindow.
    o["code"] = "CONTROLLER_NOT_INJECTED";
    o["error"] = QString::fromUtf8("工作台控制器未注入（MainWindow 未创建 WorkspaceController）");
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}
}  // namespace

QString execArrangeWorkspace(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                             const SourceInfo& /*src*/,
                             ui::BookmarkManager* /*bookmarks*/,
                             aigui::WorkspaceController* workspace) {
    if (!workspace) return workspaceNotInjected();
    const QJsonObject res = workspace->applyOverlay(args);
    return QString::fromUtf8(QJsonDocument(res).toJson(QJsonDocument::Compact));
}

QString execGetWorkspaceLayout(const QJsonObject&, dsp::SpectrumEngine* /*engine*/,
                              const SourceInfo& /*src*/,
                              ui::BookmarkManager* /*bookmarks*/,
                              aigui::WorkspaceController* workspace) {
    if (!workspace) return workspaceNotInjected();
    const QJsonObject res = workspace->currentSpec();
    return QString::fromUtf8(QJsonDocument(res).toJson(QJsonDocument::Compact));
}

QString execListUiComponents(const QJsonObject&, dsp::SpectrumEngine* /*engine*/,
                             const SourceInfo& /*src*/,
                             ui::BookmarkManager* /*bookmarks*/,
                             aigui::WorkspaceController* workspace) {
    if (!workspace) return workspaceNotInjected();
    const QJsonObject res = workspace->componentCatalog();
    return QString::fromUtf8(QJsonDocument(res).toJson(QJsonDocument::Compact));
}

QString execResetWorkspace(const QJsonObject&, dsp::SpectrumEngine* /*engine*/,
                           const SourceInfo& /*src*/,
                           ui::BookmarkManager* /*bookmarks*/,
                           aigui::WorkspaceController* workspace) {
    if (!workspace) return workspaceNotInjected();
    const QJsonObject res = workspace->reset();
    return QString::fromUtf8(QJsonDocument(res).toJson(QJsonDocument::Compact));
}

// A2UI v1.0 updateDataModel minimal subset: upsert/clear one component's data
// slot. data may be an object (whole-replace) or null (clear the key). Unknown
// component ids are an honest error; not-injected controller -> honest error.
QString execUpdateComponentData(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                                const SourceInfo& /*src*/,
                                ui::BookmarkManager* /*bookmarks*/,
                                aigui::WorkspaceController* workspace) {
    if (!workspace) return workspaceNotInjected();
    const QString componentId = args.value(QStringLiteral("component_id")).toString();
    if (componentId.isEmpty()) {
        QJsonObject o;
        o["ok"] = false;
        o["code"] = "INVALID_PAYLOAD";
        o["error"] = QString::fromUtf8("缺少 component_id（白名单组件 id）");
        return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
    }
    // data is REQUIRED and may be null. QJsonObject::value() returns Null when
    // the key is absent, so we must distinguish "key present with null" from
    // "key missing" -- treat missing as an honest error (contract requires it).
    if (!args.contains(QStringLiteral("data"))) {
        QJsonObject o;
        o["ok"] = false;
        o["code"] = "INVALID_PAYLOAD";
        o["error"] = QString::fromUtf8("缺少 data（object=upsert，null=清空）");
        return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
    }
    const QJsonValue data = args.value(QStringLiteral("data"));
    const QJsonObject res = workspace->updateComponentData(componentId, data);
    return QString::fromUtf8(QJsonDocument(res).toJson(QJsonDocument::Compact));
}

// ---- Deterministic task templates (list_templates / run_template) ----------
// These wrap the internal plan* builders (task_orchestrator.{h,cpp}) -- we do
// NOT reimplement the step sequence here. list_templates is pure metadata;
// run_template BUILDS the plan and REGISTERS it into the real pending-plan slot
// (PendingPlanStore), returning its step sequence + the real pending_plan_id,
// honestly labeled executed=false (this call never synchronously tunes/records;
// the plan only runs after the user confirms, via the AI-panel 「执行待执行
// 计划」 button -> TaskRunner::runPendingPlan, and its execution state is
// written back into the slot). Both route through the normal engine guard, so a
// headless/no-engine caller gets the same honest "无引擎连接" envelope.
namespace {

QString toolErr(const QString& error) {
    QJsonObject o;
    o["ok"] = false;
    o["error"] = error;
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

// Honest required-number extraction: missing / non-numeric / non-finite -> error.
bool needArgDbl(const QJsonObject& a, const char* key, double& out, QString& err) {
    const QJsonValue v = a.value(QString::fromUtf8(key));
    if (!v.isDouble()) { err = QString::fromUtf8("缺少数字参数: %1").arg(QString::fromUtf8(key)); return false; }
    out = v.toDouble();
    if (!std::isfinite(out)) { err = QString::fromUtf8("参数非有限数: %1").arg(QString::fromUtf8(key)); return false; }
    return true;
}

// Render a built TaskPlan as an honest registered-plan object (no execution).
// planId is the REAL pending-plan slot id the plan was registered under.
QJsonObject planToRegisteredJson(const TaskPlan& p, int planId) {
    QJsonObject o;
    o["ok"] = true;
    o["plan_name"] = p.name;
    o["abort_on_fail"] = p.abortOnFail;
    QJsonArray steps;
    for (const TaskStep& s : p.steps) {
        QJsonObject step;
        step["tool"] = s.tool;
        step["description"] = s.description;
        step["arg_keys"] = QJsonArray::fromStringList(s.args.keys());
        steps.append(step);
    }
    o["steps"] = steps;
    o["step_count"] = p.steps.size();
    o["executed"] = false;
    o["pending_plan_id"] = planId;
    o["plan_status"] = QString::fromUtf8("pending");
    o["consumed"] = false;
    o["note"] = QString::fromUtf8(
        "计划已登记为待执行计划 #%1（PendingPlanStore 真实登记表，状态 pending）。"
        "本调用不做同步调谐/录制（executed=false）；需用户确认后经 AI 面板「执行待执行计划」"
        "按钮或 TaskRunner::runPendingPlan(#%1) 异步执行，执行状态（running/succeeded/"
        "failed/…）真实回写登记表，可经 get_task_history 的 pending_plans 查询进度。")
        .arg(planId);
    return o;
}

} // namespace

QString execListTemplates(const QJsonObject&, dsp::SpectrumEngine* /*engine*/,
                          const SourceInfo& /*src*/,
                          ui::BookmarkManager* /*bookmarks*/,
                          aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    QJsonArray arr;
    for (const TaskTemplateInfo& t : taskTemplateCatalog()) {
        QJsonObject e;
        e["name"] = t.key;
        e["description"] = t.description;
        e["params"] = QJsonArray::fromStringList(t.params);
        e["steps"] = t.stepCount;
        arr.append(e);
    }
    o["templates"] = arr;
    o["count"] = arr.size();
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

QString execRunTemplate(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                        const SourceInfo& /*src*/,
                        ui::BookmarkManager* /*bookmarks*/,
                        aigui::WorkspaceController* /*workspace*/) {
    const QString name = args.value(QStringLiteral("template_name")).toString();
    if (name.isEmpty())
        return toolErr(QString::fromUtf8("缺少 template_name（用 list_templates 查看可用模板）"));

    QString err;
    TaskPlan plan;
    if (name == QLatin1String("sweep_find_record")) {
        double lowHz, highHz, stepHz;
        if (!needArgDbl(args, "low_hz", lowHz, err)) return toolErr(err);
        if (!needArgDbl(args, "high_hz", highHz, err)) return toolErr(err);
        if (!needArgDbl(args, "step_hz", stepHz, err)) return toolErr(err);
        const QString mode = args.value(QStringLiteral("mode")).toString();
        if (mode.isEmpty()) return toolErr(QString::fromUtf8("缺少模式参数: mode"));
        const QString book = args.value(QStringLiteral("book_name")).toString();
        if (book.isEmpty()) return toolErr(QString::fromUtf8("缺少书签名: book_name"));
        plan = planSweepFindAndRecord(lowHz, highHz, stepHz, mode, book);
    } else if (name == QLatin1String("target_capture")) {
        double targetHz;
        if (!needArgDbl(args, "target_hz", targetHz, err)) return toolErr(err);
        const QString mode = args.value(QStringLiteral("mode")).toString();
        if (mode.isEmpty()) return toolErr(QString::fromUtf8("缺少模式参数: mode"));
        plan = planTargetCapture(targetHz, mode);
    } else if (name == QLatin1String("fixed_freq_record")) {
        double freqHz, bwHz;
        if (!needArgDbl(args, "freq_hz", freqHz, err)) return toolErr(err);
        if (!needArgDbl(args, "bandwidth_hz", bwHz, err)) return toolErr(err);
        const QString mode = args.value(QStringLiteral("mode")).toString();
        if (mode.isEmpty()) return toolErr(QString::fromUtf8("缺少模式参数: mode"));
        plan = planFixedFrequencyRecord(freqHz, mode, bwHz);
    } else {
        return toolErr(QString::fromUtf8("未知 template_name: %1（合法: sweep_find_record/target_capture/fixed_freq_record）")
                           .arg(name));
    }

    // 真实登记：进待执行计划槽，拿到回执用的 pending_plan_id。
    const int planId = PendingPlanStore::instance().submit(plan, name);
    QJsonObject o = planToRegisteredJson(plan, planId);
    o["template_name"] = name;
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

// The built-in tool registry: name -> executor. Learned (mechanism only) from
// ===== D1 executors (2026-10-30 reverse audit docs/learn/cpp-tool-ui-reverse-audit.md) =====
// UI 有控件、工具面无对应的缺口照单补齐。每个执行器只接引擎真 setter /
// 真持久化，绝不回显假成功：越界/缺参一律 ok:false。

// D1-A1 set_gain: engine onSetGain (pending -> hardware, rounded to the nearest
// real tuner step; negative = RF attenuation, same semantics as the UI slider).
QString execSetGain(const QJsonObject& args, dsp::SpectrumEngine* engine,
                    const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double g = 0.0;
    if (!needNum(args, "gain_db", g))
        return errResult(QString::fromUtf8("参数 gain_db 缺失或不是数字"));
    if (g < tokens::kGainMinDb || g > tokens::kGainMaxDb)
        return errResult(QString::fromUtf8(
            "增益越界：必须在 %1..%2 dB（负值=RF 衰减），收到 %3")
            .arg(tokens::kGainMinDb).arg(tokens::kGainMaxDb).arg(g));
    engine->onSetGain(g);
    QJsonObject o;
    o["ok"] = true;
    o["gain_db"] = g;
    o["message"] = QString::fromUtf8("增益已下发（硬件就近取整到实际档位）");
    addSourceFields(o, src);
    return compact(o);
}

// D1-A2 set_sample_rate: engine onSetSampleRate (enum of the 4 token rates).
QString execSetSampleRate(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double r = 0.0;
    if (!needNum(args, "sample_rate_hz", r))
        return errResult(QString::fromUtf8("参数 sample_rate_hz 缺失或不是数字"));
    bool ok = false;
    for (double cand : tokens::kSampleRatesHz)
        if (r == cand) { ok = true; break; }
    if (!ok)
        return errResult(QString::fromUtf8(
            "采样率不在能力档内（1024000/2048000/2400000/3200000），收到 %1——未生效").arg(r));
    engine->onSetSampleRate(r);
    QJsonObject o;
    o["ok"] = true;
    o["sample_rate_hz"] = r;
    o["message"] = QString::fromUtf8("采样率已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-A3 set_decimation: engine setFrontendDecimation (1/2/4/8 = UI 关/x2/x4/x8).
QString execSetDecimation(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double d = 0.0;
    if (!needNum(args, "factor", d))
        return errResult(QString::fromUtf8("参数 factor 缺失或不是数字"));
    const int f = static_cast<int>(d);
    if (d != static_cast<double>(f) || (f != 1 && f != 2 && f != 4 && f != 8))
        return errResult(QString::fromUtf8(
            "抽取系数只接受 1/2/4/8（1=关），收到 %1——未生效").arg(d));
    engine->setFrontendDecimation(f);
    QJsonObject o;
    o["ok"] = true;
    o["factor"] = f;
    o["message"] = QString::fromUtf8("前端抽取已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-A4 set_direct_sampling: engine setDirectSampling (off=0, i=1, q=2).
QString execSetDirectSampling(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString m;
    if (!needStr(args, "mode", m))
        return errResult(QString::fromUtf8("参数 mode 缺失或不是字符串"));
    int code = -1;
    if (m == QLatin1String("off")) code = 0;
    else if (m == QLatin1String("i")) code = 1;
    else if (m == QLatin1String("q")) code = 2;
    else
        return errResult(QString::fromUtf8(
            "直采模式只接受 off/i/q，收到 %1——未生效").arg(m));
    engine->setDirectSampling(code);
    QJsonObject o;
    o["ok"] = true;
    o["mode"] = m;
    o["message"] = QString::fromUtf8("直采模式已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-A5 set_offset_tuning: engine setOffsetTuning.
QString execSetOffsetTuning(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("enabled")).isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = args.value(QStringLiteral("enabled")).toBool();
    engine->setOffsetTuning(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = QString::fromUtf8("偏移调谐已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-A6 set_agc: engine setRtlAgc / setTunerAgc (at least one field).
QString execSetAgc(const QJsonObject& args, dsp::SpectrumEngine* engine,
                   const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    if (args.value(QStringLiteral("rtl_agc")).isBool()) {
        const bool v = args.value(QStringLiteral("rtl_agc")).toBool();
        engine->setRtlAgc(v);
        o["rtl_agc"] = v;
    }
    if (args.value(QStringLiteral("tuner_agc")).isBool()) {
        const bool v = args.value(QStringLiteral("tuner_agc")).toBool();
        engine->setTunerAgc(v);
        o["tuner_agc"] = v;
    }
    if (!o.contains(QStringLiteral("rtl_agc")) && !o.contains(QStringLiteral("tuner_agc")))
        return errResult(QString::fromUtf8(
            "rtl_agc 与 tuner_agc 至少提供其一（全缺=无操作）"));
    o["message"] = QString::fromUtf8("AGC 开关已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-A7 set_bias_tee: engine setBiasTee (antenna power -- gated write; the
// schema warns about the 5 V feed).
QString execSetBiasTee(const QJsonObject& args, dsp::SpectrumEngine* engine,
                       const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("enabled")).isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = args.value(QStringLiteral("enabled")).toBool();
    engine->setBiasTee(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = on ? QString::fromUtf8("Bias-T 已开启（天线口馈电中，注意供电风险）")
                      : QString::fromUtf8("Bias-T 已关闭");
    addSourceFields(o, src);
    return compact(o);
}

// D1-A8 set_force_mono: engine setForceMono (WFM stereo mix -> 0).
QString execSetForceMono(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("enabled")).isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = args.value(QStringLiteral("enabled")).toBool();
    engine->setForceMono(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = QString::fromUtf8("强制单声道已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-B1 set_anr: engine setAnrEnabled + setAnrStrength (UI 0..100 -> engine 0..1).
QString execSetAnr(const QJsonObject& args, dsp::SpectrumEngine* engine,
                   const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("enabled")).isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = args.value(QStringLiteral("enabled")).toBool();
    engine->setAnrEnabled(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    if (args.contains(QStringLiteral("strength_pct")) &&
        args.value(QStringLiteral("strength_pct")).isDouble()) {
        const double sp = args.value(QStringLiteral("strength_pct")).toDouble();
        if (sp < 0.0 || sp > 100.0)
            return errResult(QString::fromUtf8(
                "strength_pct 越界：必须在 0..100，收到 %1——未生效").arg(sp));
        engine->setAnrStrength(static_cast<float>(sp / 100.0));
        o["strength_pct"] = sp;
    }
    o["message"] = QString::fromUtf8("ANR 降噪参数已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-C1 set_recording_target: engine setRecTarget (BasebandIQ / DemodAudio).
QString execSetRecordingTarget(const QJsonObject& args, dsp::SpectrumEngine* engine,
                               const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString t;
    if (!needStr(args, "target", t))
        return errResult(QString::fromUtf8("参数 target 缺失或不是字符串"));
    if (t == QLatin1String("iq_sigmf")) {
        engine->setRecTarget(dsp::RecTarget::BasebandIQ);
    } else if (t == QLatin1String("audio_wav")) {
        engine->setRecTarget(dsp::RecTarget::DemodAudio);
    } else {
        return errResult(QString::fromUtf8(
            "录制对象只接受 iq_sigmf/audio_wav，收到 %1——未生效").arg(t));
    }
    QJsonObject o;
    o["ok"] = true;
    o["target"] = t;
    o["message"] = QString::fromUtf8("录制对象已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-C2 set_recording_dir: engine setRecordingDir.
QString execSetRecordingDir(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString dir;
    if (!needStr(args, "dir", dir))
        return errResult(QString::fromUtf8("参数 dir 缺失或不是字符串"));
    if (dir.trimmed().isEmpty())
        return errResult(QString::fromUtf8("录制目录不能为空"));
    engine->setRecordingDir(dir);
    QJsonObject o;
    o["ok"] = true;
    o["dir"] = dir;
    o["message"] = QString::fromUtf8("录制目录已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-C3 set_recording_template: engine setRecFilenameTemplate.
QString execSetRecordingTemplate(const QJsonObject& args, dsp::SpectrumEngine* engine,
                                 const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString tpl;
    if (!needStr(args, "template", tpl))
        return errResult(QString::fromUtf8("参数 template 缺失或不是字符串"));
    if (tpl.trimmed().isEmpty())
        return errResult(QString::fromUtf8("文件名模板不能为空"));
    engine->setRecFilenameTemplate(tpl);
    QJsonObject o;
    o["ok"] = true;
    o["template"] = tpl;
    o["message"] = QString::fromUtf8("录制文件名模板已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-C4 set_recording_stereo: engine setRecStereo.
QString execSetRecordingStereo(const QJsonObject& args, dsp::SpectrumEngine* engine,
                               const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("enabled")).isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = args.value(QStringLiteral("enabled")).toBool();
    engine->setRecStereo(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = QString::fromUtf8("录制立体声开关已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-C5 set_recording_ignore_squelch: engine setRecIgnoreSquelch.
QString execSetRecordingIgnoreSquelch(const QJsonObject& args, dsp::SpectrumEngine* engine,
                                      const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("enabled")).isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = args.value(QStringLiteral("enabled")).toBool();
    engine->setRecIgnoreSquelch(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = QString::fromUtf8("录制忽略静噪开关已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-C6 set_recording_max_segment: engine setRecMaxSegmentSeconds.
QString execSetRecordingMaxSegment(const QJsonObject& args, dsp::SpectrumEngine* engine,
                                   const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double s = 0.0;
    if (!needNum(args, "seconds", s))
        return errResult(QString::fromUtf8("参数 seconds 缺失或不是数字"));
    if (s < tokens::kRecMaxSegmentSecondsMin || s > tokens::kRecMaxSegmentSecondsMax)
        return errResult(QString::fromUtf8(
            "分段上限越界：必须在 %1..%2 秒，收到 %3——未生效")
            .arg(tokens::kRecMaxSegmentSecondsMin).arg(tokens::kRecMaxSegmentSecondsMax).arg(s));
    engine->setRecMaxSegmentSeconds(s);
    QJsonObject o;
    o["ok"] = true;
    o["seconds"] = s;
    o["message"] = QString::fromUtf8("录制分段上限已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-C7 set_gated_recording: engine setGatedRecordingEnabled.
QString execSetGatedRecording(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("enabled")).isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = args.value(QStringLiteral("enabled")).toBool();
    engine->setGatedRecordingEnabled(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = QString::fromUtf8("触发式分段录制开关已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-C8 set_watch_recording: engine setWatchEnabled / setWatchThresholdDb /
// setWatchPrerollMs / setWatchHangMs (at least one field).
QString execSetWatchRecording(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QJsonObject o;
    o["ok"] = true;
    if (args.value(QStringLiteral("enabled")).isBool()) {
        const bool v = args.value(QStringLiteral("enabled")).toBool();
        engine->setWatchEnabled(v);
        o["enabled"] = v;
    }
    if (args.contains(QStringLiteral("threshold_db")) &&
        args.value(QStringLiteral("threshold_db")).isDouble()) {
        const double th = args.value(QStringLiteral("threshold_db")).toDouble();
        if (th < -100.0 || th > -20.0)
            return errResult(QString::fromUtf8(
                "触发门限越界：必须在 -100..-20 dBFS，收到 %1——未生效").arg(th));
        engine->setWatchThresholdDb(static_cast<float>(th));
        o["threshold_db"] = th;
    }
    if (args.contains(QStringLiteral("preroll_ms")) &&
        args.value(QStringLiteral("preroll_ms")).isDouble()) {
        const double pr = args.value(QStringLiteral("preroll_ms")).toDouble();
        if (pr < 100.0 || pr > 2000.0)
            return errResult(QString::fromUtf8(
                "前滚越界：必须在 100..2000 ms，收到 %1——未生效").arg(pr));
        engine->setWatchPrerollMs(pr);
        o["preroll_ms"] = pr;
    }
    if (args.contains(QStringLiteral("hang_ms")) &&
        args.value(QStringLiteral("hang_ms")).isDouble()) {
        const double hg = args.value(QStringLiteral("hang_ms")).toDouble();
        if (hg < 300.0 || hg > 10000.0)
            return errResult(QString::fromUtf8(
                "结束延时越界：必须在 300..10000 ms，收到 %1——未生效").arg(hg));
        engine->setWatchHangMs(hg);
        o["hang_ms"] = hg;
    }
    if (!o.contains(QStringLiteral("enabled")) && !o.contains(QStringLiteral("threshold_db"))
        && !o.contains(QStringLiteral("preroll_ms")) && !o.contains(QStringLiteral("hang_ms")))
        return errResult(QString::fromUtf8(
            "enabled/threshold_db/preroll_ms/hang_ms 至少提供其一（全缺=无操作）"));
    o["message"] = QString::fromUtf8("值守录制参数已下发");
    addSourceFields(o, src);
    return compact(o);
}

// D1-H1 set_station_position: persist to AiConfig (same store the settings
// dialog writes) + sync the ADS-B reference point. Doppler still needs a pass
// capture -- the schema says so honestly.
QString execSetStationPosition(const QJsonObject& args, dsp::SpectrumEngine* engine,
                               const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double lat = 0.0, lon = 0.0;
    if (!needNum(args, "lat_deg", lat))
        return errResult(QString::fromUtf8("参数 lat_deg 缺失或不是数字"));
    if (!needNum(args, "lon_deg", lon))
        return errResult(QString::fromUtf8("参数 lon_deg 缺失或不是数字"));
    if (lat < -90.0 || lat > 90.0)
        return errResult(QString::fromUtf8("纬度越界：必须在 -90..90，收到 %1").arg(lat));
    if (lon < -180.0 || lon > 180.0)
        return errResult(QString::fromUtf8("经度越界：必须在 -180..180，收到 %1").arg(lon));
    AiConfig cfg;
    cfg.load();   // keep whatever else is configured (apiKey etc.)
    cfg.stationLat = lat;
    cfg.stationLon = lon;
    cfg.stationSet = true;
    if (args.contains(QStringLiteral("alt_m")) &&
        args.value(QStringLiteral("alt_m")).isDouble()) {
        const double alt = args.value(QStringLiteral("alt_m")).toDouble();
        if (alt < -500.0 || alt > 9000.0)
            return errResult(QString::fromUtf8(
                "海拔越界：必须在 -500..9000 米，收到 %1——未生效").arg(alt));
        cfg.stationAlt = alt;
    }
    if (!cfg.save())
        return errResult(QString::fromUtf8("本站位置持久化失败（AiConfig 写入错误）"));
    engine->setAdsbReferencePosition(cfg.stationLat, cfg.stationLon);
    QJsonObject o;
    o["ok"] = true;
    o["lat_deg"] = cfg.stationLat;
    o["lon_deg"] = cfg.stationLon;
    o["alt_m"] = cfg.stationAlt;
    o["message"] = QString::fromUtf8("本站位置已保存（多普勒补偿仍需捕获过境后才 available）");
    addSourceFields(o, src);
    return compact(o);
}

// D1-I1 vna_connect: engine.vnaClient().connectSerial / close. Honest failure:
// NanoVnaClient exposes no error string (only its private transport does), so
// we list the possible failure modes without fabricating a specific cause.
QString execVnaConnect(const QJsonObject& args, dsp::SpectrumEngine* engine,
                       const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    auto& vna = engine->vnaClient();
    const QString action =
        args.value(QStringLiteral("action")).toString(QStringLiteral("connect"));
    if (action == QLatin1String("disconnect")) {
        vna.close();
        QJsonObject o;
        o["ok"] = true;
        o["action"] = "disconnect";
        o["connected"] = vna.isConnected();
        o["message"] = QString::fromUtf8("NanoVNA 已断开");
        addSourceFields(o, src);
        return compact(o);
    }
    QString port;
    if (!needStr(args, "port", port))
        return errResult(QString::fromUtf8("参数 port 缺失或不是字符串（connect 需要串口路径）"));
    if (port.trimmed().isEmpty())
        return errResult(QString::fromUtf8("串口路径不能为空"));
    vna.connectSerial(port);
    if (!vna.isConnected()) {
        QJsonObject o;
        o["ok"] = false;
        o["connected"] = false;
        o["error"] = QString::fromUtf8(
            "NanoVNA 连接失败（串口打不开或设备握手无响应——核对串口路径、"
            "115200 8N1、设备供电后重试）");
        addSourceFields(o, src);
        return compact(o);
    }
    QJsonObject o;
    o["ok"] = true;
    o["action"] = "connect";
    o["connected"] = true;
    o["port"] = port;
    o["message"] = QString::fromUtf8("NanoVNA 已连接（下一步 set_vna_sweep）");
    addSourceFields(o, src);
    return compact(o);
}

// ===== D1 tool-gap fill round 3 (2026-10-30): 26 executors B2/D/E/F/G/H/I2/J/K/L =====
// UiControlSurface-driven tools share the one honest headless guard: no surface
// -> ok:false ("无 UI 控制面"), never a fake "applied" echo. TX tools (J group)
// additionally require an EXPLICIT confirm=true before touching the transmit
// chain (radio regulations; the same gate the QMessageBox confirm covers on the
// UI path). Engine-direct tools (B2/H2/K) skip the surface entirely.

namespace {
// Single wording for the headless case of every surface tool.
QString noUiSurface() {
    return errResult(QString::fromUtf8("无 UI 控制面（该参数由桌面端控件持有，headless 不可用）"));
}
// Integer-valued params sit behind int spinboxes; a fractional value would be
// silently truncated by the control behind the surface -- reject instead of
// clamping (绝不静默夹值).
bool isIntValued(double d) {
    return std::isfinite(d) && d == std::floor(d)
        && d >= static_cast<double>(std::numeric_limits<int>::min())
        && d <= static_cast<double>(std::numeric_limits<int>::max());
}
// TX confirm gate shared by set_cat_control / send_cw / send_ax25_beacon /
// soapy_transmit: confirm must be explicitly true (missing/false/non-bool all
// land here) before anything reaches the transmit chain.
QString txGateError() {
    return errResult(QString::fromUtf8("发射链路操作需 confirm=true 显式确认（发射受无线电管理条例约束）"));
}
} // namespace

// D1-B2 set_audio_output: engine AudioOutput volume/device (engine-direct, not a
// surface tool). volume mirrors main_window.cpp:2824 (rx/volume 0..100 -> 0..1);
// device mirrors the settings hot-restart path (main_window.cpp:3417-3428) and
// the startup path (:3918): "default" = null QAudioDevice, otherwise an exact
// description match. Unmatched -> ok:false with the real device list; never a
// silent no-op.
QString execSetAudioOutput(const QJsonObject& args, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    const bool hasVol = args.contains(QStringLiteral("volume"));
    const bool hasDev = args.contains(QStringLiteral("device"));
    if (!hasVol && !hasDev)
        return errResult(QString::fromUtf8("volume/device 至少提供其一（全缺=无操作）"));
    dsp::AudioOutput* audio = engine->audioOutput();
    if (!audio)
        return errResult(QString::fromUtf8("无音频输出（headless 不可用）"));
    QJsonObject o;
    o["ok"] = true;
    if (hasVol) {
        double v = 0.0;
        if (!needNum(args, "volume", v))
            return errResult(QString::fromUtf8("参数 volume 不是数字——未生效"));
        if (!(v >= 0.0 && v <= 100.0))
            return errResult(QString::fromUtf8(
                "音量越界：必须在 0..100，收到 %1——未生效").arg(v));
        audio->setVolume(v / 100.0);
        mbdsdr::core::AppSettings().setValue(QStringLiteral("rx/volume"), v);
        o["volume"] = v;
    }
    if (hasDev) {
        QString dev;
        if (!needStr(args, "device", dev))
            return errResult(QString::fromUtf8("参数 device 不是字符串——未生效"));
        if (dev == QLatin1String("default")) {
            audio->setDevice(QAudioDevice());   // null device = system default
        } else {
            const auto devs = QMediaDevices::audioOutputs();
            bool matched = false;
            for (const auto& d : devs) {
                if (d.description() == dev) {
                    audio->setDevice(d);
                    matched = true;
                    break;
                }
            }
            if (!matched) {
                QJsonObject e;
                e["ok"] = false;
                e["error"] = QString::fromUtf8(
                    "找不到音频输出设备（description 需精确匹配）：%1").arg(dev);
                QJsonArray avail;
                for (const auto& d : devs) avail.append(d.description());
                e["available_devices"] = avail;
                return compact(e);
            }
        }
        AiConfig cfg;
        cfg.load();   // keep whatever else is configured (apiKey etc.)
        cfg.audioDevice = dev;
        if (!cfg.save())
            return errResult(QString::fromUtf8("音频设备持久化失败（AiConfig 写入错误）"));
        o["device"] = dev;
    }
    o["message"] = QString::fromUtf8("音频输出已更新（音量即时生效；设备已切换并持久化，重启沿用）");
    addSourceFields(o, src);
    return compact(o);
}

// D1-D1 set_scan_params: write the scan-panel controls. Bounds copied verbatim
// from the main_window scan widgets: scanDwellSpin_ 100..2000 ms, scanThrSpin_
// -120..0 dBFS, scanLingerSpin_/scanHoldMsSpin_ 0..60000 ms. The scanner reads
// these at scan start, so the effect is honest-but-deferred (message says so).
QString execSetScanParams(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QJsonObject o;
    o["ok"] = true;
    if (args.contains(QStringLiteral("dwell_ms"))) {
        double v = 0.0;
        if (!needNum(args, "dwell_ms", v))
            return errResult(QString::fromUtf8("参数 dwell_ms 不是数字——未生效"));
        if (!isIntValued(v) || !(v >= 100.0 && v <= 2000.0))
            return errResult(QString::fromUtf8(
                "驻留越界：必须在 100..2000 ms 整数，收到 %1——未生效").arg(v));
        ui->uiSetScanDwellMs(static_cast<int>(v));
        o["dwell_ms"] = v;
    }
    if (args.contains(QStringLiteral("threshold_db"))) {
        double v = 0.0;
        if (!needNum(args, "threshold_db", v))
            return errResult(QString::fromUtf8("参数 threshold_db 不是数字——未生效"));
        if (!(v >= -120.0 && v <= 0.0))
            return errResult(QString::fromUtf8(
                "门限越界：必须在 -120..0 dBFS，收到 %1——未生效").arg(v));
        ui->uiSetScanThresholdDb(v);
        o["threshold_db"] = v;
    }
    if (args.contains(QStringLiteral("direction"))) {
        QString s;
        if (!needStr(args, "direction", s))
            return errResult(QString::fromUtf8("参数 direction 不是字符串——未生效"));
        int idx = -1;
        if (s == QLatin1String("up")) idx = 0;
        else if (s == QLatin1String("down")) idx = 1;
        else if (s == QLatin1String("pingpong")) idx = 2;
        else return errResult(QString::fromUtf8(
            "direction 只接受 up/down/pingpong，收到 %1——未生效").arg(s));
        ui->uiSetScanDirection(idx);
        o["direction"] = s;
    }
    if (args.contains(QStringLiteral("hold_mode"))) {
        QString s;
        if (!needStr(args, "hold_mode", s))
            return errResult(QString::fromUtf8("参数 hold_mode 不是字符串——未生效"));
        int idx = -1;
        if (s == QLatin1String("until_signal_gone")) idx = 0;
        else if (s == QLatin1String("fixed_ms")) idx = 1;
        else return errResult(QString::fromUtf8(
            "hold_mode 只接受 until_signal_gone/fixed_ms，收到 %1——未生效").arg(s));
        ui->uiSetScanHoldMode(idx);
        o["hold_mode"] = s;
    }
    if (args.contains(QStringLiteral("linger_ms"))) {
        double v = 0.0;
        if (!needNum(args, "linger_ms", v))
            return errResult(QString::fromUtf8("参数 linger_ms 不是数字——未生效"));
        if (!isIntValued(v) || !(v >= 0.0 && v <= 60000.0))
            return errResult(QString::fromUtf8(
                "消失延时越界：必须在 0..60000 ms 整数，收到 %1——未生效").arg(v));
        ui->uiSetScanLingerMs(static_cast<int>(v));
        o["linger_ms"] = v;
    }
    if (args.contains(QStringLiteral("hold_ms"))) {
        double v = 0.0;
        if (!needNum(args, "hold_ms", v))
            return errResult(QString::fromUtf8("参数 hold_ms 不是数字——未生效"));
        if (!isIntValued(v) || !(v >= 0.0 && v <= 60000.0))
            return errResult(QString::fromUtf8(
                "固定停留越界：必须在 0..60000 ms 整数，收到 %1——未生效").arg(v));
        ui->uiSetScanHoldMs(static_cast<int>(v));
        o["hold_ms"] = v;
    }
    if (args.contains(QStringLiteral("bookmarks_only"))) {
        if (!args.value(QStringLiteral("bookmarks_only")).isBool())
            return errResult(QString::fromUtf8("参数 bookmarks_only 不是布尔值——未生效"));
        const bool v = args.value(QStringLiteral("bookmarks_only")).toBool();
        ui->uiSetScanBookmarksOnly(v);
        o["bookmarks_only"] = v;
    }
    if (!o.contains(QStringLiteral("dwell_ms")) && !o.contains(QStringLiteral("threshold_db"))
        && !o.contains(QStringLiteral("direction")) && !o.contains(QStringLiteral("hold_mode"))
        && !o.contains(QStringLiteral("linger_ms")) && !o.contains(QStringLiteral("hold_ms"))
        && !o.contains(QStringLiteral("bookmarks_only")))
        return errResult(QString::fromUtf8(
            "dwell_ms/threshold_db/direction/hold_mode/linger_ms/hold_ms/bookmarks_only "
            "至少提供其一（全缺=无操作）"));
    o["message"] = QString::fromUtf8("已写入扫描面板，下一次『开始扫描』生效（值在启动时读入扫描器）");
    addSourceFields(o, src);
    return compact(o);
}

// D1-E1 set_persistence: spectrum-widget persist mode (UI 词 关/低/高).
QString execSetPersistence(const QJsonObject& args, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QString mode;
    if (!needStr(args, "mode", mode))
        return errResult(QString::fromUtf8("参数 mode 缺失或不是字符串（off/low/high）"));
    int idx = -1;
    if (mode == QLatin1String("off")) idx = 0;
    else if (mode == QLatin1String("low")) idx = 1;
    else if (mode == QLatin1String("high")) idx = 2;
    else return errResult(QString::fromUtf8(
        "mode 只接受 off/low/high，收到 %1——未生效").arg(mode));
    ui->uiSetPersistence(idx);
    QJsonObject o;
    o["ok"] = true;
    o["mode"] = mode;
    o["message"] = QString::fromUtf8("余辉模式已写入频谱显示面");
    addSourceFields(o, src);
    return compact(o);
}

// D1-E2 set_waterfall_depth: ring depth, exact tiers only
// (tokens::kWaterfallDepthChoices 全档 = UI depthCombo_ 同一数组，能力对拍不缩水).
QString execSetWaterfallDepth(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    double rows = 0.0;
    if (!needNum(args, "rows", rows))
        return errResult(QString::fromUtf8(
            "参数 rows 缺失或不是数字（128/256/512/1024/2048/4096）"));
    const int r = static_cast<int>(rows);
    bool exactTier = (rows == static_cast<double>(r));
    if (exactTier) {
        exactTier = false;
        for (int d : tokens::kWaterfallDepthChoices)
            if (d == r) { exactTier = true; break; }
    }
    if (!exactTier)
        return errResult(QString::fromUtf8(
            "rows 只接受 kWaterfallDepthChoices 精确值（128/256/512/1024/2048/4096），"
            "收到 %1——未生效").arg(rows));
    ui->uiSetWaterfallDepth(r);
    QJsonObject o;
    o["ok"] = true;
    o["rows"] = r;
    o["message"] = QString::fromUtf8("瀑布深度已写入频谱显示面（%1 行）").arg(r);
    addSourceFields(o, src);
    return compact(o);
}

// D1-E3 set_waterfall_speed: waterfall scroll speed (1/2/4 lines per frame).
QString execSetWaterfallSpeed(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    double speed = 0.0;
    if (!needNum(args, "speed", speed))
        return errResult(QString::fromUtf8("参数 speed 缺失或不是数字（1/2/4）"));
    const int s = static_cast<int>(speed);
    if (speed != static_cast<double>(s) || (s != 1 && s != 2 && s != 4))
        return errResult(QString::fromUtf8(
            "speed 只接受 1/2/4 精确值，收到 %1——未生效").arg(speed));
    ui->uiSetWaterfallSpeed(s);
    QJsonObject o;
    o["ok"] = true;
    o["speed"] = s;
    o["message"] = QString::fromUtf8("瀑布滚速已写入频谱显示面（%1 行/帧）").arg(s);
    addSourceFields(o, src);
    return compact(o);
}

// D1-E4 set_builtin_palette: builtin colormap (UI 词 经典/单色/Viridis).
QString execSetBuiltinPalette(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QString pal;
    if (!needStr(args, "palette", pal))
        return errResult(QString::fromUtf8("参数 palette 缺失或不是字符串（classic/monochrome/viridis）"));
    int idx = -1;
    if (pal == QLatin1String("classic")) idx = 0;
    else if (pal == QLatin1String("monochrome")) idx = 1;
    else if (pal == QLatin1String("viridis")) idx = 2;
    else return errResult(QString::fromUtf8(
        "palette 只接受 classic/monochrome/viridis，收到 %1——未生效").arg(pal));
    ui->uiSetBuiltinPalette(idx);
    QJsonObject o;
    o["ok"] = true;
    o["palette"] = pal;
    o["message"] = QString::fromUtf8("内置色图已写入频谱显示面");
    addSourceFields(o, src);
    return compact(o);
}

// D1-E5 set_spectrum_db_range: dB display window. Bounds copied verbatim from
// dbMinSpin_/dbMaxSpin_ setRange (spectrum_widget.cpp:300-310): lower spin
// kDbSpinLowerMin..kDbSpinLowerMax, upper spin kDbSpinUpperMin..kDbSpinUpperMax;
// min_db < max_db enforced at runtime (no silent re-pairing like the UI applyDb).
QString execSetSpectrumDbRange(const QJsonObject& args, dsp::SpectrumEngine* engine,
                               const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    double lo = 0.0, hi = 0.0;
    if (!needNum(args, "min_db", lo))
        return errResult(QString::fromUtf8("参数 min_db 缺失或不是数字"));
    if (!needNum(args, "max_db", hi))
        return errResult(QString::fromUtf8("参数 max_db 缺失或不是数字"));
    if (!(lo >= tokens::kDbSpinLowerMin && lo <= tokens::kDbSpinLowerMax))
        return errResult(QString::fromUtf8(
            "min_db 越界：必须在 %1..%2 dB，收到 %3——未生效")
            .arg(tokens::kDbSpinLowerMin).arg(tokens::kDbSpinLowerMax).arg(lo));
    if (!(hi >= tokens::kDbSpinUpperMin && hi <= tokens::kDbSpinUpperMax))
        return errResult(QString::fromUtf8(
            "max_db 越界：必须在 %1..%2 dB，收到 %3——未生效")
            .arg(tokens::kDbSpinUpperMin).arg(tokens::kDbSpinUpperMax).arg(hi));
    if (!(lo < hi))
        return errResult(QString::fromUtf8(
            "min_db 必须小于 max_db（收到 %1 >= %2）——未生效").arg(lo).arg(hi));
    ui->uiSetDbRange(static_cast<float>(lo), static_cast<float>(hi));
    QJsonObject o;
    o["ok"] = true;
    o["min_db"] = lo;
    o["max_db"] = hi;
    o["message"] = QString::fromUtf8("频谱 dB 显示范围已写入（%1..%2 dB）").arg(lo).arg(hi);
    addSourceFields(o, src);
    return compact(o);
}

// D1-E6 set_spectrum_zoom: display-only horizontal zoom (canvas
// setZoomFactor). Honest bound = positive finite; the display window itself
// converges to tokens::kZoomMin..kZoomMax (1.0..16.0, spectrum_display.cpp:767),
// which the message reports instead of pretending an unbounded zoom.
QString execSetSpectrumZoom(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    double z = 0.0;
    if (!needNum(args, "zoom", z))
        return errResult(QString::fromUtf8("参数 zoom 缺失或不是数字"));
    if (!std::isfinite(z) || !(z > 0.0))
        return errResult(QString::fromUtf8(
            "zoom 必须为正的有限数，收到 %1——未生效").arg(z));
    ui->uiSetZoomFactor(z);
    QJsonObject o;
    o["ok"] = true;
    o["zoom"] = z;
    o["message"] = QString::fromUtf8(
        "显示缩放已写入（zoom=%1）；显示窗口按 1.0..16.0 收敛").arg(z);
    addSourceFields(o, src);
    return compact(o);
}

// D1-E7 set_db_grid_step: y-ruler density tiers (tokens::kDbGridStepChoices).
QString execSetDbGridStep(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    double step = 0.0;
    if (!needNum(args, "step_db", step))
        return errResult(QString::fromUtf8("参数 step_db 缺失或不是数字（10/20/40）"));
    const int s = static_cast<int>(step);
    if (step != static_cast<double>(s) || (s != 10 && s != 20 && s != 40))
        return errResult(QString::fromUtf8(
            "step_db 只接受 10/20/40 精确值，收到 %1——未生效").arg(step));
    ui->uiSetDbGridStep(s);
    QJsonObject o;
    o["ok"] = true;
    o["step_db"] = s;
    o["message"] = QString::fromUtf8("dB 栅格步进已写入（%1 dB）").arg(s);
    addSourceFields(o, src);
    return compact(o);
}

// D1-E8 set_peak_threshold: peak-detection threshold above the spectral median,
// integer kPeakThresholdMin..kPeakThresholdMax (5..40 dB).
QString execSetPeakThreshold(const QJsonObject& args, dsp::SpectrumEngine* engine,
                             const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    double db = 0.0;
    if (!needNum(args, "threshold_db", db))
        return errResult(QString::fromUtf8("参数 threshold_db 缺失或不是数字"));
    if (!isIntValued(db) || !(db >= tokens::kPeakThresholdMin && db <= tokens::kPeakThresholdMax))
        return errResult(QString::fromUtf8(
            "峰值门限越界：必须在 %1..%2 dB 整数，收到 %3——未生效")
            .arg(tokens::kPeakThresholdMin).arg(tokens::kPeakThresholdMax).arg(db));
    ui->uiSetPeakThresholdDb(static_cast<int>(db));
    QJsonObject o;
    o["ok"] = true;
    o["threshold_db"] = static_cast<int>(db);
    o["message"] = QString::fromUtf8("峰值检测门限已写入（%1 dB，相对谱中位数）").arg((int)db);
    addSourceFields(o, src);
    return compact(o);
}

// D1-E9 set_peak_hold: max/min hold toggles + decay tier (tokens::
// kMaxHoldDecaySlow/Medium/Fast = 0.5/1.5/3.0 dB per frame); at least one field.
QString execSetPeakHold(const QJsonObject& args, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QJsonObject o;
    o["ok"] = true;
    if (args.contains(QStringLiteral("max_hold"))) {
        if (!args.value(QStringLiteral("max_hold")).isBool())
            return errResult(QString::fromUtf8("参数 max_hold 不是布尔值——未生效"));
        const bool v = args.value(QStringLiteral("max_hold")).toBool();
        ui->uiSetMaxHold(v);
        o["max_hold"] = v;
    }
    if (args.contains(QStringLiteral("min_hold"))) {
        if (!args.value(QStringLiteral("min_hold")).isBool())
            return errResult(QString::fromUtf8("参数 min_hold 不是布尔值——未生效"));
        const bool v = args.value(QStringLiteral("min_hold")).toBool();
        ui->uiSetMinHold(v);
        o["min_hold"] = v;
    }
    if (args.contains(QStringLiteral("decay"))) {
        QString s;
        if (!needStr(args, "decay", s))
            return errResult(QString::fromUtf8("参数 decay 不是字符串——未生效"));
        double dbPerFrame = 0.0;
        if (s == QLatin1String("slow")) dbPerFrame = tokens::kMaxHoldDecaySlow;
        else if (s == QLatin1String("medium")) dbPerFrame = tokens::kMaxHoldDecayMedium;
        else if (s == QLatin1String("fast")) dbPerFrame = tokens::kMaxHoldDecayFast;
        else return errResult(QString::fromUtf8(
            "decay 只接受 slow/medium/fast，收到 %1——未生效").arg(s));
        ui->uiSetHoldDecayDb(dbPerFrame);
        o["decay"] = s;
        o["decay_db_per_frame"] = dbPerFrame;
    }
    if (!o.contains(QStringLiteral("max_hold")) && !o.contains(QStringLiteral("min_hold"))
        && !o.contains(QStringLiteral("decay")))
        return errResult(QString::fromUtf8(
            "max_hold/min_hold/decay 至少提供其一（全缺=无操作）"));
    o["message"] = QString::fromUtf8("峰值保持参数已写入频谱显示面");
    addSourceFields(o, src);
    return compact(o);
}

// D1-E10 set_edge_rail_visibility: right-edge rail visibility (the control path
// also persists kSettingsKeyEdgeRailVisible -- see SpectrumWidget::
// setEdgeRailVisible, spectrum_widget.cpp:886-892).
QString execSetEdgeRailVisibility(const QJsonObject& args, dsp::SpectrumEngine* engine,
                                  const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    if (!args.value(QStringLiteral("visible")).isBool())
        return errResult(QString::fromUtf8("参数 visible 缺失或不是布尔值"));
    const bool v = args.value(QStringLiteral("visible")).toBool();
    ui->uiSetEdgeRailVisible(v);
    QJsonObject o;
    o["ok"] = true;
    o["visible"] = v;
    o["message"] = QString::fromUtf8("右缘快捷轨显隐已生效并持久化");
    addSourceFields(o, src);
    return compact(o);
}

// D1-F1 set_source_type: source selector (srcTypeCombo_ index order).
// Honest semantics: test_signal = 合成 IQ 非真实接收; rtl_tcp only switches to
// remote mode (the real link is connect_network_source); switching back to a
// real source tears the synthetic source down.
QString execSetSourceType(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QString s;
    if (!needStr(args, "source_type", s))
        return errResult(QString::fromUtf8("参数 source_type 缺失或不是字符串（local_rtlsdr/rtl_tcp/test_signal）"));
    int idx = -1;
    if (s == QLatin1String("local_rtlsdr")) idx = 0;
    else if (s == QLatin1String("rtl_tcp")) idx = 1;
    else if (s == QLatin1String("test_signal")) idx = 2;
    else return errResult(QString::fromUtf8(
        "source_type 只接受 local_rtlsdr/rtl_tcp/test_signal，收到 %1——未生效").arg(s));
    ui->uiSetSourceType(idx);
    QJsonObject o;
    o["ok"] = true;
    o["source_type"] = s;
    o["message"] = QString::fromUtf8(
        "源类型已切换（%1）：test_signal=合成 IQ 非真实接收；rtl_tcp 只是切到远程模式，"
        "真正建链用 connect_network_source；切回真实源会拆除合成源").arg(s);
    addSourceFields(o, src);
    return compact(o);
}

// D1-F2 set_spyserver: SpyServer listener toggle + port. The port spin is
// persisted under net/spyPort (main_window.cpp:498/5613), so a missing port
// honestly means "keep the current one". uiSetSpyServer returns the real end
// state; false (bind failed) -> ok:false, never "listening" over a dead socket.
QString execSetSpyServer(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    if (!args.value(QStringLiteral("enabled")).isBool())
        return errResult(QString::fromUtf8("参数 enabled 缺失或不是布尔值"));
    const bool on = args.value(QStringLiteral("enabled")).toBool();
    int port = mbdsdr::core::AppSettings().value(QStringLiteral("net/spyPort"), 5555).toInt();
    if (args.contains(QStringLiteral("port"))) {
        double p = 0.0;
        if (!needNum(args, "port", p))
            return errResult(QString::fromUtf8("参数 port 不是数字——未生效"));
        if (!isIntValued(p) || !(p >= 1024.0 && p <= 65535.0))
            return errResult(QString::fromUtf8(
                "port 越界：必须在 1024..65535 整数，收到 %1——未生效").arg(p));
        port = static_cast<int>(p);
    }
    if (!ui->uiSetSpyServer(on, port)) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("监听失败（端口被占？）");
        o["enabled"] = on;
        o["port"] = port;
        addSourceFields(o, src);
        return compact(o);
    }
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["port"] = port;
    o["message"] = on ? QString::fromUtf8("SpyServer 已开始监听（端口 %1）").arg(port)
                      : QString::fromUtf8("SpyServer 已停止监听");
    addSourceFields(o, src);
    return compact(o);
}

// D1-G1 connect_gnss: GNSS serial connect/disconnect via the GNSS panel path.
// Honest wording: the underlying start() is asynchronous -- "已发起连接" is NOT
// "已定位"; the readback only reports the running flag (uiGnssRunning()).
QString execConnectGnss(const QJsonObject& args, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    const QString action =
        args.value(QStringLiteral("action")).toString(QStringLiteral("connect"));
    if (action == QLatin1String("disconnect")) {
        ui->uiDisconnectGnss();
        QJsonObject o;
        o["ok"] = true;
        o["action"] = "disconnect";
        o["gnss_running"] = ui->uiGnssRunning();
        o["message"] = QString::fromUtf8("GNSS 已断开");
        addSourceFields(o, src);
        return compact(o);
    }
    if (action != QLatin1String("connect"))
        return errResult(QString::fromUtf8(
            "action 只接受 connect/disconnect，收到 %1——未生效").arg(action));
    QString device;
    if (!needStr(args, "device", device))
        return errResult(QString::fromUtf8("参数 device 缺失或不是字符串（connect 需要串口设备）"));
    if (device.trimmed().isEmpty())
        return errResult(QString::fromUtf8("串口设备不能为空"));
    double baud = 9600.0;
    if (args.contains(QStringLiteral("baud"))) {
        if (!needNum(args, "baud", baud))
            return errResult(QString::fromUtf8("参数 baud 不是数字——未生效"));
        const int b = static_cast<int>(baud);
        if (baud != static_cast<double>(b) || (b != 9600 && b != 38400 && b != 115200))
            return errResult(QString::fromUtf8(
                "baud 只接受 9600/38400/115200，收到 %1——未生效").arg(baud));
        baud = b;
    }
    if (!ui->uiConnectGnss(device, static_cast<int>(baud))) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("GNSS 串口打开失败：%1").arg(device);
        o["gnss_running"] = ui->uiGnssRunning();
        addSourceFields(o, src);
        return compact(o);
    }
    QJsonObject o;
    o["ok"] = true;
    o["action"] = "connect";
    o["device"] = device;
    o["baud"] = static_cast<int>(baud);
    o["gnss_running"] = ui->uiGnssRunning();
    o["message"] = QString::fromUtf8(
        "已发起 GNSS 连接（start() 异步：已发起连接≠已定位，需等待解算）");
    addSourceFields(o, src);
    return compact(o);
}

// D1-G2 set_map_layers: world-map layer visibility (gnss/adsb/sat), at least one.
QString execSetMapLayers(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QJsonObject o;
    o["ok"] = true;
    if (args.contains(QStringLiteral("gnss"))) {
        if (!args.value(QStringLiteral("gnss")).isBool())
            return errResult(QString::fromUtf8("参数 gnss 不是布尔值——未生效"));
        const bool v = args.value(QStringLiteral("gnss")).toBool();
        ui->uiSetMapLayerGnss(v);
        o["gnss"] = v;
    }
    if (args.contains(QStringLiteral("adsb"))) {
        if (!args.value(QStringLiteral("adsb")).isBool())
            return errResult(QString::fromUtf8("参数 adsb 不是布尔值——未生效"));
        const bool v = args.value(QStringLiteral("adsb")).toBool();
        ui->uiSetMapLayerAdsb(v);
        o["adsb"] = v;
    }
    if (args.contains(QStringLiteral("sat"))) {
        if (!args.value(QStringLiteral("sat")).isBool())
            return errResult(QString::fromUtf8("参数 sat 不是布尔值——未生效"));
        const bool v = args.value(QStringLiteral("sat")).toBool();
        ui->uiSetMapLayerSat(v);
        o["sat"] = v;
    }
    if (!o.contains(QStringLiteral("gnss")) && !o.contains(QStringLiteral("adsb"))
        && !o.contains(QStringLiteral("sat")))
        return errResult(QString::fromUtf8("gnss/adsb/sat 至少提供其一（全缺=无操作）"));
    o["message"] = QString::fromUtf8("地图图层显隐已写入");
    addSourceFields(o, src);
    return compact(o);
}

// D1-H2 set_sat_pass_template (engine-direct): plan a satellite capture via the
// pure ai::planSatelliteCapture and REGISTER it as a pending plan -- never
// execute. lat/lon fall back to the persisted station (AiConfig, only when
// stationSet). Demo/stale-TLE results are rejected outright: a demo plan must
// never drive a real capture (mirror main_window.cpp:8103-8119 honest branch).
QString execSetSatPassTemplate(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                               const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString satName;
    if (!needStr(args, "sat_name", satName))
        return errResult(QString::fromUtf8("参数 sat_name 缺失或不是字符串"));
    if (satName.trimmed().isEmpty())
        return errResult(QString::fromUtf8("sat_name 不能为空"));
    double lat = 0.0, lon = 0.0;
    bool haveLat = false, haveLon = false;
    if (args.contains(QStringLiteral("lat_deg"))) {
        if (!needNum(args, "lat_deg", lat))
            return errResult(QString::fromUtf8("参数 lat_deg 不是数字"));
        if (!(lat >= -90.0 && lat <= 90.0))
            return errResult(QString::fromUtf8("纬度越界：必须在 -90..90，收到 %1").arg(lat));
        haveLat = true;
    }
    if (args.contains(QStringLiteral("lon_deg"))) {
        if (!needNum(args, "lon_deg", lon))
            return errResult(QString::fromUtf8("参数 lon_deg 不是数字"));
        if (!(lon >= -180.0 && lon <= 180.0))
            return errResult(QString::fromUtf8("经度越界：必须在 -180..180，收到 %1").arg(lon));
        haveLon = true;
    }
    double hours = 12.0;
    if (args.contains(QStringLiteral("hours_ahead"))) {
        if (!needNum(args, "hours_ahead", hours))
            return errResult(QString::fromUtf8("参数 hours_ahead 不是数字"));
        if (!isIntValued(hours) || !(hours >= 1.0 && hours <= 24.0))
            return errResult(QString::fromUtf8(
                "hours_ahead 越界：必须在 1..24 整数，收到 %1——未生效").arg(hours));
    }
    // Missing coordinate -> the persisted station, only when explicitly set.
    AiConfig cfg;
    cfg.load();
    if (!haveLat && cfg.stationSet && std::isfinite(cfg.stationLat)) {
        lat = cfg.stationLat;
        haveLat = true;
    }
    if (!haveLon && cfg.stationSet && std::isfinite(cfg.stationLon)) {
        lon = cfg.stationLon;
        haveLon = true;
    }
    if (!haveLat || !haveLon)
        return errResult(QString::fromUtf8(
            "未提供 lat_deg/lon_deg 且未设置本站位置（先 set_station_position）"));
    const SatTaskResult sr = planSatelliteCapture(
        satName.trimmed(), lat, lon, QDateTime::currentDateTimeUtc(),
        static_cast<int>(hours));
    if (!sr.ok || sr.demo) {
        // demo==true means the numbers came from the stale builtin TLE (epoch
        // 2006): surface WHY (sr.warning) and refuse to register the plan.
        const QString reason = (sr.demo && !sr.warning.isEmpty()) ? sr.warning : sr.error;
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("卫星选频失败：%1").arg(reason);
        o["demo"] = sr.demo;
        addSourceFields(o, src);
        return compact(o);
    }
    const int planId =
        PendingPlanStore::instance().submit(sr.plan, QStringLiteral("sat_pass_capture"));
    QJsonObject o = planToRegisteredJson(sr.plan, planId);
    o["template_name"] = QStringLiteral("sat_pass_capture");
    o["sat_name"] = sr.satName;
    o["aos_utc"] = sr.aosUtc.toUTC().toString(Qt::ISODate);
    o["max_el_deg"] = sr.maxElDeg;
    o["capture_freq_hz"] = sr.captureFreqHz;
    o["message"] = QString::fromUtf8(
        "已登记待执行计划（executed=false），需用户确认后经『执行待执行计划』"
        "或 TaskRunner::runPendingPlan 执行");
    addSourceFields(o, src);
    return compact(o);
}

// D1-H3 set_tune_step: tuning-step combo (main_window.cpp:150 kStepValuesHz).
QString execSetTuneStep(const QJsonObject& args, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    double v = 0.0;
    if (!needNum(args, "step_hz", v))
        return errResult(QString::fromUtf8("参数 step_hz 缺失或不是数字"));
    constexpr int kSteps[] = {1, 10, 100, 1000, 10000, 100000, 1000000};
    const int st = static_cast<int>(v);
    bool ok = isIntValued(v);
    if (ok) {
        ok = false;
        for (int cand : kSteps)
            if (st == cand) { ok = true; break; }
    }
    if (!ok)
        return errResult(QString::fromUtf8(
            "step_hz 只接受 1/10/100/1000/10000/100000/1000000，收到 %1——未生效").arg(v));
    ui->uiSetTuneStepHz(st);
    QJsonObject o;
    o["ok"] = true;
    o["step_hz"] = st;
    o["message"] = QString::fromUtf8("调谐步进已写入（%1 Hz）").arg(st);
    addSourceFields(o, src);
    return compact(o);
}

// D1-I2 analyze_vna_permeability (READ, s.write=false): NRW permeability from
// the live sweep. thickness unit = mm -- the SAME unit as the VnaPanel 夹具厚
// field (vna_panel.cpp:232 "mm（磁导率夹具）"), converted to metres for
// vna::permeabilityNRW exactly like vna_panel.cpp:364. Sweep fetch mirrors
// vna_panel.cpp:383-385. Unconnected / no sweep -> ok:false, never invented μr.
QString execAnalyzeVnaPermeability(const QJsonObject& args, dsp::SpectrumEngine* engine,
                                   const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double thickMm = 0.0;
    if (!needNum(args, "thickness_mm", thickMm))
        return errResult(QString::fromUtf8("参数 thickness_mm 缺失或不是数字（单位 mm，同面板「夹具厚」）"));
    if (!std::isfinite(thickMm) || !(thickMm > 0.0))
        return errResult(QString::fromUtf8(
            "thickness_mm 必须为正的有限数（mm），收到 %1").arg(thickMm));
    QJsonObject o;
    auto& vna = engine->vnaClient();
    o["connected"] = vna.isConnected();
    if (!vna.isConnected()) {
        o["ok"] = false;
        o["error"] = QString::fromUtf8("未连接 NanoVNA：无扫频数据可分析");
        addSourceFields(o, src);
        return compact(o);
    }
    auto f = vna.readFrequencies();
    auto s11 = vna.readData(0);
    auto s21 = vna.readData(1);
    if (f.size() < 3 || s11.empty() || s21.empty()) {
        o["ok"] = false;
        o["error"] = QString::fromUtf8("无扫频数据（点数不足 3 或缺 S11/S21）：先 set_vna_sweep 并测量");
        addSourceFields(o, src);
        return compact(o);
    }
    const std::vector<double> fHz(f.begin(), f.end());
    const vna::PermeabilityResult pr = vna::permeabilityNRW(fHz, s11, s21, thickMm * 1e-3);
    o["ok"] = true;
    o["thickness_mm"] = thickMm;
    o["valid"] = pr.valid;
    QJsonArray muRe, muIm;
    for (double v : pr.mu_real)
        muRe.append(std::isfinite(v) ? QJsonValue(v) : QJsonValue(QJsonValue::Null));
    for (double v : pr.mu_imag)
        muIm.append(std::isfinite(v) ? QJsonValue(v) : QJsonValue(QJsonValue::Null));
    o["mu_real"] = muRe;
    o["mu_imag"] = muIm;
    // Midpoint readback, same spot VnaPanel reports (μr'/μr'' at the middle bin).
    if (pr.valid && !pr.mu_real.empty()) {
        const size_t mid = pr.mu_real.size() / 2;
        if (std::isfinite(pr.mu_real[mid])) o["mu_real_mid"] = pr.mu_real[mid];
        if (mid < pr.mu_imag.size() && std::isfinite(pr.mu_imag[mid]))
            o["mu_imag_mid"] = pr.mu_imag[mid];
    }
    if (!pr.valid)
        o["note"] = QString::fromUtf8("数据不足/病态，无法计算（%1）")
                        .arg(QString::fromStdString(pr.note));
    addSourceFields(o, src);
    return compact(o);
}

// D1-J1 set_cat_control: CAT radio connect (TX chain; confirm gate). Failure
// echoes uiRadioStatus() verbatim -- never "connected" over a dead link.
QString execSetCatControl(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("confirm")).toBool())
        return txGateError();
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QString device;
    if (!needStr(args, "device", device))
        return errResult(QString::fromUtf8("参数 device 缺失或不是字符串（串口设备）"));
    if (device.trimmed().isEmpty())
        return errResult(QString::fromUtf8("串口设备不能为空"));
    double baud = 19200.0;
    if (args.contains(QStringLiteral("baud"))) {
        if (!needNum(args, "baud", baud))
            return errResult(QString::fromUtf8("参数 baud 不是数字——未生效"));
        const int b = static_cast<int>(baud);
        if (baud != static_cast<double>(b) || (b != 9600 && b != 19200 && b != 57600 && b != 115200))
            return errResult(QString::fromUtf8(
                "baud 只接受 9600/19200/57600/115200，收到 %1——未生效").arg(baud));
        baud = b;
    }
    QString proto = QStringLiteral("civ");
    if (args.contains(QStringLiteral("protocol"))) {
        if (!needStr(args, "protocol", proto))
            return errResult(QString::fromUtf8("参数 protocol 不是字符串——未生效"));
        if (proto != QLatin1String("civ") && proto != QLatin1String("kenwood"))
            return errResult(QString::fromUtf8(
                "protocol 只接受 civ/kenwood，收到 %1——未生效").arg(proto));
    }
    if (!ui->uiCatConnect(device, static_cast<int>(baud), proto)) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("CAT 连接失败：%1").arg(ui->uiRadioStatus());
        o["radio_status"] = ui->uiRadioStatus();
        addSourceFields(o, src);
        return compact(o);
    }
    QJsonObject o;
    o["ok"] = true;
    o["device"] = device;
    o["baud"] = static_cast<int>(baud);
    o["protocol"] = proto;
    o["radio_status"] = ui->uiRadioStatus();
    o["message"] = QString::fromUtf8("CAT 电台已连接（%1，%2 baud）")
                       .arg(proto).arg((int)baud);
    addSourceFields(o, src);
    return compact(o);
}

// D1-J2 send_cw: key CW through the CAT radio (TX; confirm gate). Mirrors the
// radio_panel CW lambda bare path (empty text rejected; keyer schedule built).
QString execSendCw(const QJsonObject& args, dsp::SpectrumEngine* engine,
                   const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("confirm")).toBool())
        return txGateError();
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QString text;
    if (!needStr(args, "text", text))
        return errResult(QString::fromUtf8("参数 text 缺失或不是字符串"));
    if (text.trimmed().isEmpty())
        return errResult(QString::fromUtf8("CW 文本不能为空"));
    double wpm = 20.0;
    if (args.contains(QStringLiteral("wpm"))) {
        if (!needNum(args, "wpm", wpm))
            return errResult(QString::fromUtf8("参数 wpm 不是数字——未生效"));
        if (!isIntValued(wpm) || !(wpm >= 5.0 && wpm <= 60.0))
            return errResult(QString::fromUtf8(
                "wpm 越界：必须在 5..60 整数，收到 %1——未生效").arg(wpm));
    }
    if (!ui->uiSendCw(static_cast<int>(wpm), text)) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("CW 发射未发起：%1").arg(ui->uiRadioStatus());
        o["radio_status"] = ui->uiRadioStatus();
        addSourceFields(o, src);
        return compact(o);
    }
    QJsonObject o;
    o["ok"] = true;
    o["text"] = text;
    o["wpm"] = static_cast<int>(wpm);
    o["radio_status"] = ui->uiRadioStatus();
    o["message"] = QString::fromUtf8(
        "CW 发射已发起（%1 WPM，%2 字符）；发射受无线电管理条例约束")
        .arg((int)wpm).arg(text.size());
    addSourceFields(o, src);
    return compact(o);
}

// D1-J3 send_ax25_beacon: KISS AX.25 beacon via the TNC (TX; confirm gate).
// Mirrors the radio_panel beacon lambda bare path (call validated by the TNC
// path; empty path = no digipeaters).
QString execSendAx25Beacon(const QJsonObject& args, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("confirm")).toBool())
        return txGateError();
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QString call, msg;
    if (!needStr(args, "call", call))
        return errResult(QString::fromUtf8("参数 call 缺失或不是字符串"));
    if (call.trimmed().isEmpty())
        return errResult(QString::fromUtf8("呼号不能为空"));
    if (!needStr(args, "message", msg))
        return errResult(QString::fromUtf8("参数 message 缺失或不是字符串"));
    QString path;
    if (args.contains(QStringLiteral("path"))) {
        if (!needStr(args, "path", path))
            return errResult(QString::fromUtf8("参数 path 不是字符串——未生效"));
    }
    if (!ui->uiSendAx25Beacon(call.trimmed(), path, msg)) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("AX.25 信标未发送：%1").arg(ui->uiRadioStatus());
        o["radio_status"] = ui->uiRadioStatus();
        addSourceFields(o, src);
        return compact(o);
    }
    QJsonObject o;
    o["ok"] = true;
    o["call"] = call.trimmed();
    o["path"] = path;
    o["message_len"] = msg.size();
    o["radio_status"] = ui->uiRadioStatus();
    o["message"] = QString::fromUtf8(
        "AX.25 信标已交 TNC 发送；发射受无线电管理条例约束");
    addSourceFields(o, src);
    return compact(o);
}

// D1-J4 soapy_transmit: OPEN the Soapy TX device only (D3-2 confirm point) --
// PTT/codeword stays press-and-hold MANUAL, this tool never keys the carrier.
QString execSoapyTransmit(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    if (!args.value(QStringLiteral("confirm")).toBool())
        return txGateError();
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QString driver;
    if (!needStr(args, "driver", driver))
        return errResult(QString::fromUtf8("参数 driver 缺失或不是字符串（Soapy 驱动参数，如 driver=plutosdr）"));
    if (driver.trimmed().isEmpty())
        return errResult(QString::fromUtf8("Soapy 驱动参数不能为空"));
    double freqMhz = 0.0;
    if (!needNum(args, "freq_mhz", freqMhz))
        return errResult(QString::fromUtf8("参数 freq_mhz 缺失或不是数字"));
    if (!(freqMhz > 0.0 && freqMhz <= 100000.0))
        return errResult(QString::fromUtf8(
            "freq_mhz 越界：必须在 (0..100000]，收到 %1——未生效").arg(freqMhz));
    if (!ui->uiOpenSoapy(driver.trimmed(), freqMhz)) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("Soapy 发射设备打开失败：%1").arg(ui->uiRadioStatus());
        o["radio_status"] = ui->uiRadioStatus();
        addSourceFields(o, src);
        return compact(o);
    }
    QJsonObject o;
    o["ok"] = true;
    o["driver"] = driver.trimmed();
    o["freq_mhz"] = freqMhz;
    o["radio_status"] = ui->uiRadioStatus();
    o["message"] = QString::fromUtf8(
        "Soapy 发射设备已打开（驱动 %1，%2 MHz）；仅打开设备一步，PTT/载波保持人工按住；"
        "发射受无线电管理条例约束").arg(driver.trimmed()).arg(freqMhz);
    addSourceFields(o, src);
    return compact(o);
}

// D1-K1 set_ui_scale (engine-direct): persist ui/scaleFactor and apply live via
// the exact settings-dialog path (main_window.cpp:3433-3435: re-tokenize + restyle).
// Tiers mirror scaleCombo_ (settings_dialog.cpp:119): 0.7/1.0/1.25/1.5 -- a value
// outside the tiers would silently snap in the dialog on next load, so reject.
QString execSetUiScale(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                       const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    double v = 0.0;
    if (!needNum(args, "scale", v))
        return errResult(QString::fromUtf8("参数 scale 缺失或不是数字"));
    if (v != 0.7 && v != 1.0 && v != 1.25 && v != 1.5)
        return errResult(QString::fromUtf8(
            "scale 只接受 0.7/1.0/1.25/1.5（与设置对话框同档位），收到 %1——未生效").arg(v));
    mbdsdr::core::AppSettings().setValue(QStringLiteral("ui/scaleFactor"), v);
    tokens::setUserScale(v);
    static_cast<QApplication*>(qApp)->setStyleSheet(tokens::buildDarkQss());
    QJsonObject o;
    o["ok"] = true;
    o["scale"] = v;
    o["message"] = QString::fromUtf8("UI 缩放已持久化并即时生效（与设置对话框同路径：重新标记 QSS）");
    addSourceFields(o, src);
    return compact(o);
}

// D1-K2 set_language (engine-direct): persist ui/language (full BCP-47 label,
// l10n.h 值域 zh_CN/en/ja). Honest: the translator is installed at startup only
// (main.cpp:96-101), so this takes effect on restart -- never a fake live switch.
QString execSetLanguage(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    QString lang;
    if (!needStr(args, "language", lang))
        return errResult(QString::fromUtf8("参数 language 缺失或不是字符串（zh_CN/en/ja）"));
    if (lang != QLatin1String("zh_CN") && lang != QLatin1String("en") && lang != QLatin1String("ja"))
        return errResult(QString::fromUtf8(
            "language 只接受 zh_CN/en/ja（存储写全量标签），收到 %1——未生效").arg(lang));
    mbdsdr::core::AppSettings().setValue(QStringLiteral("ui/language"), lang);
    QJsonObject o;
    o["ok"] = true;
    o["language"] = lang;
    o["message"] = QString::fromUtf8("已持久化，需重启生效（启动时装载翻译器）");
    addSourceFields(o, src);
    return compact(o);
}

// D1-L1 set_left_groups: single left-rail group visibility (atomic; batch =
// repeated calls). Ids are tokens::kLeftPanelIds; the control path persists the
// hidden set under kSettingsKeyHiddenLeftPanels (ui/hiddenLeftGroups).
QString execSetLeftGroups(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/, aigui::WorkspaceController* /*workspace*/) {
    dsp::UiControlSurface* ui = engine->uiControlSurface();
    if (!ui) return noUiSurface();
    QString group;
    if (!needStr(args, "group", group))
        return errResult(QString::fromUtf8("参数 group 缺失或不是字符串"));
    bool known = false;
    QStringList ids;
    for (int i = 0; i < tokens::kLeftPanelCount; ++i) {
        const QString id = QString::fromUtf8(tokens::kLeftPanelIds[i]);
        ids << id;
        if (group == id) known = true;
    }
    if (!known)
        return errResult(QString::fromUtf8(
            "group 不是左栏组 id（合法: %1）——未生效").arg(ids.join(QLatin1String("/"))));
    if (!args.value(QStringLiteral("visible")).isBool())
        return errResult(QString::fromUtf8("参数 visible 缺失或不是布尔值"));
    const bool vis = args.value(QStringLiteral("visible")).toBool();
    ui->uiSetLeftGroupVisible(group, vis);
    QJsonObject o;
    o["ok"] = true;
    o["group"] = group;
    o["visible"] = vis;
    o["message"] = QString::fromUtf8("面板组「%1」显隐已生效并持久化（%2）")
                       .arg(group, QString::fromUtf8(tokens::kSettingsKeyHiddenLeftPanels));
    addSourceFields(o, src);
    return compact(o);
}

// SDR++'s registerSource(name, handler) table pattern -- a name-keyed lookup
// instead of an if-else chain. Independent reimplementation; no GPL code copied.
// Every name here MUST equal a ToolSchemaSpec name (see test_tool_registry);
// adding a tool = add the declarative spec in tool_schema.cpp + one row here.
struct ToolDispatch {
    const char* name;
    // The 4th arg is the optional injected bookmark store; only the three
    // bookmark tools consume it, every other executor ignores it. The 5th arg is
    // the optional injected AIGUI WorkspaceController; only the four workspace
    // tools consume it, every other executor ignores it.
    QString (*exec)(const QJsonObject&, dsp::SpectrumEngine*, const SourceInfo&,
                    ui::BookmarkManager*, aigui::WorkspaceController*);
};

const QList<ToolDispatch>& dispatchTable() {
    static const QList<ToolDispatch> kTable = {
        {"tune_frequency", &execTuneFrequency},
        {"set_mode", &execSetMode},
        {"start_recording", &execStartRecording},
        {"stop_recording", &execStopRecording},
        {"export_iq_segment", &execExportIqSegment},
        {"scan_band", &execScanBand},
        {"set_bandwidth", &execSetBandwidth},
        {"get_status", &execGetStatus},
        {"predict_passes", &execPredictPasses},
        {"calibrate_frequency", &execCalibrateFrequency},
        {"apply_frequency_correction", &execApplyFrequencyCorrection},
        {"get_pocsag_messages", &execGetPocsagMessages},
        {"get_m17_calls", &execGetM17Calls},
        {"get_vor_radial", &execGetVorRadial},
        // Phase60 packet-text snapshot tools (read-only, append-only order).
        {"get_acars_packets", &execGetAcarsPackets},
        {"get_navtex_messages", &execGetNavtexMessages},
        {"get_aprs_messages", &execGetApRsMessages},
        // Phase26: 21 capability tools (append-only, on-wire order kept).
        {"set_network_audio_sink", &execSetNetworkAudioSink},
        {"get_network_audio_status", &execGetNetworkAudioStatus},
        {"start_scan_link", &execStartScanLink},
        {"stop_scan_link", &execStopScanLink},
        {"get_scan_link_status", &execGetScanLinkStatus},
        {"set_squelch", &execSetSquelch},
        {"get_squelch_status", &execGetSquelchStatus},
        {"set_ctcss", &execSetCtcss},
        {"get_ctcss_status", &execGetCtcssStatus},
        {"set_cdcss", &execSetCdcss},
        {"get_cdcss_status", &execGetCdcssStatus},
        {"set_ft8", &execSetFt8},
        {"get_ft8_status", &execGetFt8Status},
        {"get_ft8_history", &execGetFt8History},
        {"get_adsb_aircraft", &execGetAdsbAircraft},
        {"set_lrpt", &execSetLrpt},
        {"get_lrpt_status", &execGetLrptStatus},
        {"set_vna_sweep", &execSetVnaSweep},
        {"get_vna_data", &execGetVnaData},
        {"get_vna_status", &execGetVnaStatus},
        {"analyze_vna_resonance", &execAnalyzeVnaResonance},
        {"vna_tdr_cable", &execVnaTdrCable},
        {"analyze_vna_filter", &execAnalyzeVnaFilter},
        {"analyze_vna_equivalent", &execAnalyzeVnaEquivalent},
        {"analyze_vna_touchstone", &execAnalyzeVnaTouchstone},
        {"export_vna_snapshot", &execExportVnaSnapshot},
        {"export_spectrum_snapshot", &execExportSpectrumSnapshot},
        {"export_peaks_csv", &execExportPeaksCsv},
        {"export_ft8_results", &execExportFt8Results},
        {"analyze_vna_match", &execAnalyzeVnaMatch},
        {"set_noise_blanker", &execSetNoiseBlanker},
        {"get_noise_blanker_status", &execGetNoiseBlankerStatus},
        {"list_bookmarks", &execListBookmarks},
        {"add_bookmark", &execAddBookmark},
        {"tune_to_bookmark", &execTuneToBookmark},
        {"delete_bookmark", &execDeleteBookmark},
        {"list_vfos", &execListVfos},
        {"add_vfo", &execAddVfo},
        {"switch_vfo", &execSwitchVfo},
        {"rename_vfo", &execRenameVfo},
        {"set_vfo_armed", &execSetVfoArmed},
        {"set_vfo_frequency", &execSetVfoFrequency},
        {"set_vfo_mode", &execSetVfoMode},
        {"set_vfo_bandwidth", &execSetVfoBandwidth},
        {"list_recordings", &execListRecordings},
        {"delete_recording", &execDeleteRecording},
        {"export_recording", &execExportRecording},
        {"set_fft_params", &execSetFftParams},
        {"set_color_map", &execSetColorMap},
        {"get_color_maps", &execGetColorMaps},
        {"get_spectrum_status", &execGetSpectrumStatus},
        {"set_doppler_compensation", &execSetDopplerCompensation},
        {"connect_network_source", &execConnectNetworkSource},
        // Read-only source-capability + recording-state snapshots (honest empty).
        {"get_capabilities", &execGetCapabilities},
        {"get_recording_state", &execGetRecordingState},
        // P0 #59: single-tool NanoVNA calibration state machine (write, gated).
        {"vna_calibration", &execVnaCalibration},
        // P1 #61/#70: device-level misc facade state machine (write, gated).
        {"vna_device", &execVnaDevice},
        // P3 #73: local memory family (SQLite FTS5; add/update gated, search read-only).
        {"memory_add", &execMemoryAdd},
        {"memory_search", &execMemorySearch},
        {"memory_update", &execMemoryUpdate},
        // P3 #74: checkpoint writer introspection (read) + synchronous flush (write, gated).
        {"checkpoint_status", &execCheckpointStatus},
        {"memory_flush", &execMemoryFlush},
        // 第 10 域「工作台编排」(AIGUI P0-b): arrange/reset are gated writes,
        // get/list are read-only. Drive the live WorkspaceController only.
        {"arrange_workspace", &execArrangeWorkspace},
        {"get_workspace_layout", &execGetWorkspaceLayout},
        {"list_ui_components", &execListUiComponents},
        {"reset_workspace", &execResetWorkspace},
        // A2UI v1.0 updateDataModel minimal subset (gated write).
        {"update_component_data", &execUpdateComponentData},
        // Deterministic task templates: list (read) / run (gated write).
        {"list_templates", &execListTemplates},
        {"run_template", &execRunTemplate},
        // G1: task-run history (read-only; TaskRunner completion records).
        {"get_task_history", &execGetTaskHistory},
        // G2: memory delete (gated write) + statistics (read-only).
        {"memory_delete", &execMemoryDelete},
        {"memory_stats", &execMemoryStats},
        // Read-only TLE-cache catalog: prerequisite for predict_passes.
        {"list_satellites", &execListSatellites},
        // D1 tool-gap fill (2026-10-30 reverse audit): A hardware params,
        // B ANR, C recording params, H station position, I VNA connect.
        {"set_gain", &execSetGain},
        {"set_sample_rate", &execSetSampleRate},
        {"set_decimation", &execSetDecimation},
        {"set_direct_sampling", &execSetDirectSampling},
        {"set_offset_tuning", &execSetOffsetTuning},
        {"set_agc", &execSetAgc},
        {"set_bias_tee", &execSetBiasTee},
        {"set_force_mono", &execSetForceMono},
        {"set_anr", &execSetAnr},
        {"set_recording_target", &execSetRecordingTarget},
        {"set_recording_dir", &execSetRecordingDir},
        {"set_recording_template", &execSetRecordingTemplate},
        {"set_recording_stereo", &execSetRecordingStereo},
        {"set_recording_ignore_squelch", &execSetRecordingIgnoreSquelch},
        {"set_recording_max_segment", &execSetRecordingMaxSegment},
        {"set_gated_recording", &execSetGatedRecording},
        {"set_watch_recording", &execSetWatchRecording},
        {"set_station_position", &execSetStationPosition},
        {"vna_connect", &execVnaConnect},
        // D1 tool-gap fill round 3 (2026-10-30 reverse audit): B2/D/E/F/G/H/I2/J/K/L
        {"set_audio_output", &execSetAudioOutput},
        {"set_scan_params", &execSetScanParams},
        {"set_persistence", &execSetPersistence},
        {"set_waterfall_depth", &execSetWaterfallDepth},
        {"set_waterfall_speed", &execSetWaterfallSpeed},
        {"set_builtin_palette", &execSetBuiltinPalette},
        {"set_spectrum_db_range", &execSetSpectrumDbRange},
        {"set_spectrum_zoom", &execSetSpectrumZoom},
        {"set_db_grid_step", &execSetDbGridStep},
        {"set_peak_threshold", &execSetPeakThreshold},
        {"set_peak_hold", &execSetPeakHold},
        {"set_edge_rail_visibility", &execSetEdgeRailVisibility},
        {"set_source_type", &execSetSourceType},
        {"set_spyserver", &execSetSpyServer},
        {"connect_gnss", &execConnectGnss},
        {"set_map_layers", &execSetMapLayers},
        {"set_sat_pass_template", &execSetSatPassTemplate},
        {"set_tune_step", &execSetTuneStep},
        {"analyze_vna_permeability", &execAnalyzeVnaPermeability},
        {"set_cat_control", &execSetCatControl},
        {"send_cw", &execSendCw},
        {"send_ax25_beacon", &execSendAx25Beacon},
        {"soapy_transmit", &execSoapyTransmit},
        {"set_ui_scale", &execSetUiScale},
        {"set_language", &execSetLanguage},
        {"set_left_groups", &execSetLeftGroups},
    };
    return kTable;
}
} // namespace

bool isWriteTool(const QString& name) {
    // The write/read gate is read straight from the declarative spec table
    // (ToolSchemaSpec::write) -- no parallel hardcoded write set to drift.
    for (const ToolSchemaSpec& s : registeredToolSpecs())
        if (s.name == name) return s.write;
    return false;   // unknown / read-only is never gated
}

QStringList executorToolNames() {
    QStringList out;
    out.reserve(dispatchTable().size());
    for (const ToolDispatch& d : dispatchTable())
        out.append(QString::fromUtf8(d.name));
    return out;
}

QList<ToolDef> toolDefs() {
    // Single source of truth for the SDR tools: the declarative registry
    // (M1) renders the JSON Schema WITH numeric bounds (min/max from tokens.h)
    // and enum members. Name/description pass through verbatim, so UI copy and
    // existing golden expectations never drift. Dispatch is now a name lookup
    // into dispatchTable() (see executeTool); arguments validation happens
    // upstream in llm_worker before dispatch.
    return toolDefsFromSpecs(registeredToolSpecs());
}

QString executeTool(const QString& name, const QJsonObject& args,
                    dsp::SpectrumEngine* engine, ui::BookmarkManager* bookmarks, aigui::WorkspaceController* workspace) {
    // Honest error envelope aligned with ControlHub::execute() (control_hub.cpp:354):
    // never a plain string, never {ok:true} with fabricated values. The executor
    // bodies below all dereference engine->... without their own null guard, so
    // this line is the single crash-seam for the null-engine state.
    if (!engine) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("无引擎连接（Agent 未 attach SpectrumEngine）");
        return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
    }

    const SourceInfo src = readSourceInfo(engine);

    for (const ToolDispatch& d : dispatchTable()) {
        if (name == QLatin1String(d.name))
            return d.exec(args, engine, src, bookmarks, workspace);
    }
    // Unknown-tool envelope aligned with the Python MCP server
    // (mbdsdr_ai/core/mcp_server.py call_tool): JSON object, never a bare
    // string, so content[0].text stays machine-parseable for third-party hosts.
    QJsonObject o;
    o["error"] = QStringLiteral("unknown_tool");
    o["tool"] = name;
    o["suggestion"] = QStringLiteral("用 tools/list 查看可用工具。");
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

} // namespace ai
} // namespace mbdsdr
