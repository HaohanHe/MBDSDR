// SPDX-License-Identifier: MIT
#include "agent_tools.h"
#include "dsp/spectrum_engine.h"

namespace mbdsdr {
namespace ai {

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
    return "未知工具: " + name;
}

} // namespace ai
} // namespace mbdsdr
