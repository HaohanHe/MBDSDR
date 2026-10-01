// SPDX-License-Identifier: MIT
#include "agent_tools.h"
#include "tool_schema.h"
#include "dsp/spectrum_engine.h"

#include <QSet>
#include <QJsonDocument>

namespace mbdsdr {
namespace ai {

// Write/mutating tools -- the ONLY tools gated in manual mode. Kept in one place
// next to toolDefs()/executeTool() so the gate list never drifts from the actual
// registered tools. Everything not listed here (notably get_status) is treated as
// read-only and always executes.
static const QSet<QString>& writeTools() {
    static const QSet<QString> kSet = {
        "tune_frequency",   // changes center frequency
        "set_mode",         // changes demodulation mode
        "set_bandwidth",    // changes channel filter bandwidth
        "start_recording",  // starts IQ recording
        "stop_recording",   // stops IQ recording
        "scan_band",        // sweeps the receiver across a band (mutates freq)
    };
    return kSet;
}

bool isWriteTool(const QString& name) {
    return writeTools().contains(name);
}

QString gatedToolResult(const QString& toolName) {
    QJsonObject o;
    o["ok"] = false;
    o["gated"] = true;
    o["error"] = QString("手动模式：未执行 %1").arg(toolName);
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

QList<ToolDef> toolDefs() {
    // Single source of truth for the 7 SDR tools: the declarative registry
    // (M1) renders the JSON Schema WITH numeric bounds (min/max from tokens.h)
    // and enum members. Name/description pass through verbatim, so UI copy and
    // existing golden expectations never drift. executeTool() below is unchanged;
    // arguments validation now happens upstream in llm_worker before dispatch.
    return toolDefsFromSpecs(registeredToolSpecs());
}

QString executeTool(const QString& name, const QJsonObject& args,
                    dsp::SpectrumEngine* engine) {
    if (!engine) return "error: no engine";

    if (name == "tune_frequency") {
        double f = args["freq_hz"].toDouble();
        engine->onSetCenterFreq(f);
        return QString("已调谐到 %1 MHz").arg(f / 1e6, 0, 'f', 3);
    }
    if (name == "set_mode") {
        QString m = args["mode"].toString();
        engine->setDemodMode(m);
        return QString("解调模式切换为 %1").arg(m);
    }
    if (name == "start_recording") {
        engine->startRecording();
        return "开始录制";
    }
    if (name == "stop_recording") {
        engine->stopRecording();
        return "停止录制";
    }
    if (name == "scan_band") {
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
        return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
    }
    if (name == "set_bandwidth") {
        double bw = args["bandwidth_hz"].toDouble();
        engine->setBandwidth(bw);
        return QString("带宽设为 %1 Hz").arg(bw, 0, 'f', 0);
    }
    if (name == "get_status") {
        return QString("频率=%1MHz 模式=%2 带宽=%3Hz")
            .arg(engine->centerFreq()/1e6, 0, 'f', 3)
            .arg(engine->demodMode())
            .arg(engine->bandwidth(), 0, 'f', 0);
    }
    return "未知工具: " + name;
}

} // namespace ai
} // namespace mbdsdr
