// SPDX-License-Identifier: MIT
#include "agent_tools.h"
#include "tool_schema.h"
#include "sat_task_planner.h"
#include "dsp/spectrum_engine.h"
#include "dsp/device_capabilities.h"

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
        {"scan_band", &execScanBand},
        {"set_bandwidth", &execSetBandwidth},
        {"get_status", &execGetStatus},
        {"predict_passes", &execPredictPasses},
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
