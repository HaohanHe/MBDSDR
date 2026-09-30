// SPDX-License-Identifier: MIT
#include "agent_tools.h"
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
    QList<ToolDef> tools;

    ToolDef tune;
    tune.name = "tune_frequency";
    tune.description = "Tune the receiver to a center frequency in Hz.";
    tune.parameters = QJsonObject{
        {"type", "object"},
        {"properties", QJsonObject{
            {"freq_hz", QJsonObject{
                {"type", "number"},
                {"description", "Center frequency in Hz, e.g. 98500000 for 98.5 MHz"}
            }}
        }},
        {"required", QJsonArray{"freq_hz"}}
    };
    tools.append(tune);

    ToolDef mode;
    mode.name = "set_mode";
    mode.description = "Set demodulation mode.";
    mode.parameters = QJsonObject{
        {"type", "object"},
        {"properties", QJsonObject{
            {"mode", QJsonObject{
                {"type", "string"},
                {"enum", QJsonArray{"AM", "NFM", "WFM", "USB", "LSB", "CW"}}
            }}
        }},
        {"required", QJsonArray{"mode"}}
    };
    tools.append(mode);

    ToolDef rec;
    rec.name = "start_recording";
    rec.description = "Start recording raw IQ to SigMF file.";
    rec.parameters = QJsonObject{{"type", "object"}, {"properties", QJsonObject{}}};
    tools.append(rec);

    ToolDef stop;
    stop.name = "stop_recording";
    stop.description = "Stop recording.";
    stop.parameters = QJsonObject{{"type", "object"}, {"properties", QJsonObject{}}};
    tools.append(stop);

    ToolDef scan;
    scan.name = "scan_band";
    scan.description = "Scan a frequency band and return the peak signal.";
    scan.parameters = QJsonObject{
        {"type", "object"},
        {"properties", QJsonObject{
            {"low_hz", QJsonObject{{"type", "number"}, {"description", "Start frequency Hz"}}},
            {"high_hz", QJsonObject{{"type", "number"}, {"description", "End frequency Hz"}}},
            {"step_hz", QJsonObject{{"type", "number"}, {"description", "Step size Hz (default 200k)"}}}
        }},
        {"required", QJsonArray{"low_hz", "high_hz"}}
    };
    tools.append(scan);

    ToolDef bw;
    bw.name = "set_bandwidth";
    bw.description = "Set channel filter bandwidth in Hz.";
    bw.parameters = QJsonObject{
        {"type", "object"},
        {"properties", QJsonObject{
            {"bandwidth_hz", QJsonObject{
                {"type", "number"},
                {"description", "Filter bandwidth in Hz, e.g. 8000 for AM, 12500 for NFM, 200000 for WFM"}
            }}
        }},
        {"required", QJsonArray{"bandwidth_hz"}}
    };
    tools.append(bw);

    ToolDef st;
    st.name = "get_status";
    st.description = "Return current receiver state: frequency, mode, bandwidth, sample rate.";
    st.parameters = QJsonObject{{"type", "object"}, {"properties", QJsonObject{}}};
    tools.append(st);

    return tools;
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
        double peak = engine->scanBand(low, high, step);
        return QString("扫描 %1-%2 MHz，峰值 %3 dBFS")
            .arg(low/1e6, 0, 'f', 1).arg(high/1e6, 0, 'f', 1).arg(peak, 0, 'f', 1);
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
