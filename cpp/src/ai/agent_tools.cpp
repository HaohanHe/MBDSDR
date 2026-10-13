// SPDX-License-Identifier: MIT
#include "agent_tools.h"
#include "tool_schema.h"
#include "sat_task_planner.h"
#include "core/tokens.h"
#include "ui/spectrum_render.h"
#include "dsp/spectrum_engine.h"
#include "vna/vna_rf.h"
#include "dsp/device_capabilities.h"
#include "dsp/frequency_calibrator.h"   // calibrateFromCapture / savePpmSetting
#include "dsp/fcch_detector.h"          // kFcchToneHz
#include "ui/bookmark_manager.h"   // ui::BookmarkManager / ui::Bookmark (real Agent-layer store)
#include "ui/workspace_controller.h"  // aigui::WorkspaceController (P0-b workspace seam)
#include "memory_store.h"         // SQLite FTS5 local memory store
#include "checkpoint_writer.h"     // background single-writer drain for memory writes

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
    // it honestly. Echo the canonical upper-case name like the other channels.
    const QString up = args["mode"].toString().toUpper();
    bool known = false;
    for (int k = 0; k < tokens::kControlHubModesCount; ++k)
        if (up == QLatin1String(tokens::kControlHubModes[k])) { known = true; break; }
    if (!known)
        return errResult(QString::fromUtf8("未知解调模式: %1")
                             .arg(args["mode"].toString()));
    engine->setDemodMode(up);
    QJsonObject o;
    o["ok"] = true;
    o["mode"] = up;
    o["message"] = QString("解调模式切换为 %1").arg(up);
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
    int sampleCount = static_cast<int>(args["sample_count"].toDouble(65536.0));
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
        engine->setSquelchThreshold(static_cast<float>(args.value("threshold_db").toDouble()));
        o["threshold_db"] = args.value("threshold_db").toDouble();
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
    const QString up = mode.toUpper();
    bool known = false;
    for (int k = 0; k < tokens::kControlHubModesCount; ++k)
        if (up == QLatin1String(tokens::kControlHubModes[k])) { known = true; break; }
    if (!known)
        return errResult(QString::fromUtf8("未知解调模式: %1").arg(mode));
    int i = -1, id = -1; QString err;
    if (!resolveVfoTarget(args, engine, i, id, err)) return errResult(err);
    engine->vfoSetMode(id, up);
    QJsonObject o;
    o["ok"] = true;
    o["index"] = i;
    o["vfo_id"] = id;
    o["mode"] = up;
    o["message"] = QString::fromUtf8("VFO %1 解调模式已切换为 %2").arg(i).arg(up);
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
    if (bw <= 0.0)
        return errResult(QString::fromUtf8("参数 bandwidth_hz 必须为正数"));
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
    if (w == QLatin1String("Flattop")) return 1;
    if (w == QLatin1String("Blackman")) return 2;
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
    engine->setFftSize(static_cast<int>(sz));
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
    QSettings().setValue(QStringLiteral("view/wfColormapFile"), p);
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
    const QString path = QSettings().value(QStringLiteral("view/wfColormapFile")).toString();
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
// run_template BUILDS + REGISTERS a plan and returns its step sequence, honestly
// labeled executed=false (this call never synchronously tunes/records; production
// runs the plan on the async TaskRunner). Both route through the normal engine
// guard, so a headless/no-engine caller gets the same honest "无引擎连接" envelope.
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
QJsonObject planToRegisteredJson(const TaskPlan& p) {
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
    o["note"] = QString::fromUtf8(
        "计划已构建并登记，未在本调用执行（executed=false）。本工具不做同步调谐/录制；"
        "生产环境经异步 TaskRunner 逐步执行，每步经同一 executeTool 通道驱动引擎。");
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

    QJsonObject o = planToRegisteredJson(plan);
    o["template_name"] = name;
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

// The built-in tool registry: name -> executor. Learned (mechanism only) from
// SDR++'s registerSource(name, handler) table pattern -- a name-keyed lookup
// instead of an if-else chain. Clean-room reimplementation; no GPL code copied.
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
