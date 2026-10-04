// SPDX-License-Identifier: MIT
#include "agent_tools.h"
#include "tool_schema.h"
#include "sat_task_planner.h"
#include "dsp/spectrum_engine.h"
#include "dsp/device_capabilities.h"
#include "dsp/frequency_calibrator.h"   // calibrateFromCapture / savePpmSetting
#include "dsp/fcch_detector.h"          // kFcchToneHz

#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QStringList>

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

// ---- Built-in tool executors ------------------------------------------------
// Each was one branch of the old if-else chain in executeTool(). They are now
// plain functions bound into the registry table below; the bodies are verbatim
// (same JSON keys, same order, same messages) so on-wire output never drifts.
// `src` is the honest source snapshot read once per call and injected here.

QString execTuneFrequency(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src) {
    double f = args["freq_hz"].toDouble();
    engine->onSetCenterFreq(f);
    QJsonObject o;
    o["ok"] = true;
    o["frequency_hz"] = f;
    o["message"] = QString("已调谐到 %1 MHz").arg(f / 1e6, 0, 'f', 3);
    addSourceFields(o, src);
    return compact(o);
}

QString execSetMode(const QJsonObject& args, dsp::SpectrumEngine* engine,
                    const SourceInfo& src) {
    QString m = args["mode"].toString();
    engine->setDemodMode(m);
    QJsonObject o;
    o["ok"] = true;
    o["mode"] = m;
    o["message"] = QString("解调模式切换为 %1").arg(m);
    addSourceFields(o, src);
    return compact(o);
}

QString execStartRecording(const QJsonObject& /*args*/, dsp::SpectrumEngine* engine,
                           const SourceInfo& src) {
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
                          const SourceInfo& src) {
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
                            const SourceInfo& src) {
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
                     const SourceInfo& src) {
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
                         const SourceInfo& src) {
    double bw = args["bandwidth_hz"].toDouble();
    engine->setBandwidth(bw);
    QJsonObject o;
    o["ok"] = true;
    o["bandwidth_hz"] = bw;
    o["message"] = QString("带宽设为 %1 Hz").arg(bw, 0, 'f', 0);
    addSourceFields(o, src);
    return compact(o);
}

QString execGetStatus(const QJsonObject& /*args*/, dsp::SpectrumEngine* engine,
                      const SourceInfo& src) {
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
    o["summary"] = QString("频率=%1MHz 模式=%2 带宽=%3Hz 连接=%4")
        .arg(engine->centerFreq() / 1e6, 0, 'f', 3)
        .arg(engine->demodMode())
        .arg(engine->bandwidth(), 0, 'f', 0)
        .arg(src.connected ? QString::fromUtf8("已连接")
                           : QString::fromUtf8("未连接"));
    return compact(o);
}

QString execPredictPasses(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                           const SourceInfo& /*src*/) {
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
                               const SourceInfo& src) {
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
                                     const SourceInfo& src) {
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
    if (args.contains("channel_id") && args["channel_id"].isDouble())
        return static_cast<int>(args["channel_id"].toDouble());
    return engine->selectedVfoId();
}
} // namespace

QString execGetPocsagMessages(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src) {
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
                        const SourceInfo& src) {
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

QString execGetVorRadial(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src) {
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

// The built-in tool registry: name -> executor. Learned (mechanism only) from
// SDR++'s registerSource(name, handler) table pattern -- a name-keyed lookup
// instead of an if-else chain. Clean-room reimplementation; no GPL code copied.
// Every name here MUST equal a ToolSchemaSpec name (see test_tool_registry);
// adding a tool = add the declarative spec in tool_schema.cpp + one row here.
struct ToolDispatch {
    const char* name;
    QString (*exec)(const QJsonObject&, dsp::SpectrumEngine*, const SourceInfo&);
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
                    dsp::SpectrumEngine* engine) {
    if (!engine) return "error: no engine";

    const SourceInfo src = readSourceInfo(engine);

    for (const ToolDispatch& d : dispatchTable()) {
        if (name == QLatin1String(d.name))
            return d.exec(args, engine, src);
    }
    return "未知工具: " + name;
}

} // namespace ai
} // namespace mbdsdr
