// SPDX-License-Identifier: MIT
#include "ai/tool_schema.h"

#include "ai/llm_client.h"          // ToolDef full definition
#include "core/tokens.h"            // kFreqMinHz / kFreqMaxHz
#include "core/bandwidth_preset.h"  // kBw*Hz preset set

#include <QJsonArray>

namespace mbdsdr {
namespace ai {

// scan_band step lower bound (Hz). The runtime already defaults step to 200k
// (see agent_tools.cpp); a 1 Hz floor just prevents a meaningless zero/negative
// sweep step. There is no hardware token for it, so it is named locally next
// to the only place it is used.
constexpr double kScanStepMinHz = 1.0;

QJsonObject buildToolSchema(const ToolSchemaSpec& spec) {
    QJsonObject properties;
    QJsonArray required;
    for (const ToolParamSpec& p : spec.params) {
        QJsonObject node;
        node["type"] = p.type;
        if (!p.description.isEmpty()) node["description"] = p.description;
        if (p.hasMin) node["minimum"] = p.min;
        if (p.hasMax) node["maximum"] = p.max;
        if (!p.enumValues.isEmpty()) {
            QJsonArray en;
            for (const QVariant& v : p.enumValues) en.append(QJsonValue::fromVariant(v));
            node["enum"] = en;
        }
        properties[p.name] = node;
        if (p.required) required.append(p.name);
    }
    QJsonObject out;
    out["type"] = "object";
    out["properties"] = properties;
    out["required"] = required;
    return out;
}

QList<ToolDef> toolDefsFromSpecs(const QList<ToolSchemaSpec>& specs) {
    QList<ToolDef> out;
    out.reserve(specs.size());
    for (const ToolSchemaSpec& s : specs) {
        ToolDef d;
        d.name = s.name;
        d.description = s.description;
        d.parameters = buildToolSchema(s);
        out.append(d);
    }
    return out;
}

QList<ToolSchemaSpec> registeredToolSpecs() {
    QList<ToolSchemaSpec> out;

    // tune_frequency ------------------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "tune_frequency";
        s.description = "Tune the receiver to a center frequency in Hz.";
        s.write = true;   // changes center frequency
        ToolParamSpec p;
        p.name = "freq_hz";
        p.type = "number";
        p.description = "Center frequency in Hz, e.g. 98500000 for 98.5 MHz";
        p.hasMin = true; p.min = tokens::kFreqMinHz;
        p.hasMax = true; p.max = tokens::kFreqMaxHz;
        p.required = true;
        s.params.append(p);
        out.append(s);
    }

    // set_mode ------------------------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "set_mode";
        s.description = "Set demodulation mode.";
        s.write = true;   // changes demodulation mode
        ToolParamSpec p;
        p.name = "mode";
        p.type = "string";
        p.enumValues = QVariantList{"AM", "NFM", "WFM", "USB", "LSB", "CW"};
        p.required = true;
        s.params.append(p);
        out.append(s);
    }

    // start_recording (no parameters) -------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "start_recording";
        s.description = "Start recording raw IQ to SigMF file.";
        s.write = true;   // starts IQ recording
        out.append(s);
    }

    // stop_recording (no parameters) --------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "stop_recording";
        s.description = "Stop recording.";
        s.write = true;   // stops IQ recording
        out.append(s);
    }

    // scan_band -----------------------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "scan_band";
        s.description = "Scan a frequency band and return the peak signal.";
        s.write = true;   // sweeps the receiver across a band (mutates freq)
        ToolParamSpec low;
        low.name = "low_hz";
        low.type = "number";
        low.description = "Start frequency Hz";
        low.hasMin = true; low.min = tokens::kFreqMinHz;
        low.hasMax = true; low.max = tokens::kFreqMaxHz;
        low.required = true;
        ToolParamSpec high;
        high.name = "high_hz";
        high.type = "number";
        high.description = "End frequency Hz";
        high.hasMin = true; high.min = tokens::kFreqMinHz;
        high.hasMax = true; high.max = tokens::kFreqMaxHz;
        high.required = true;
        ToolParamSpec step;
        step.name = "step_hz";
        step.type = "number";
        step.description = "Step size Hz (default 200k)";
        step.hasMin = true; step.min = kScanStepMinHz;
        step.required = false;
        s.params << low << high << step;
        out.append(s);
    }

    // set_bandwidth -------------------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "set_bandwidth";
        s.description = "Set channel filter bandwidth in Hz.";
        s.write = true;   // changes channel filter bandwidth
        ToolParamSpec p;
        p.name = "bandwidth_hz";
        p.type = "number";
        p.description = "Filter bandwidth in Hz, e.g. 8000 for AM, 12500 for NFM, 200000 for WFM";
        // The full set of named channel presets. kBwFallbackHz is deliberately
        // excluded -- it is just an alias of kBwNfmHz, not a distinct preset.
        p.enumValues = QVariantList{
            core::kBwAmHz, core::kBwNfmHz, core::kBwSsbHz, core::kBwCwHz,
            core::kBwDigitalHz, core::kBwWfmHz, core::kBwAdsbHz
        };
        p.required = true;
        s.params.append(p);
        out.append(s);
    }

    // get_status (no parameters) ------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_status";
        s.description = "Return current receiver state: frequency, mode, bandwidth, sample rate.";
        out.append(s);
    }

    // predict_passes (read-only) ------------------------------------------
    // Read-only satellite pass predictor. Uses the FRESH on-disk TLE cache only;
    // no builtin/demo TLE is reported as a real pass. Honest empty state when
    // there is no fresh cache. Kept out of the manual-mode write gate.
    {
        ToolSchemaSpec s;
        s.name = "predict_passes";
        s.description = QString::fromUtf8(
            "只读：用本地新鲜 TLE 缓存预测指定卫星未来的过境（升/降时刻、最高仰角、起止方位）。"
            "无新鲜 TLE 时诚实返回空态，不使用陈旧内置数据。");
        ToolParamSpec sat;
        sat.name = "satellite_name";
        sat.type = "string";
        sat.description = QString::fromUtf8("卫星名，大小写不敏感子串匹配，如 NOAA / ISS");
        sat.required = true;
        ToolParamSpec hrs;
        hrs.name = "hours_ahead";
        hrs.type = "number";
        hrs.description = QString::fromUtf8("预测窗口小时数，默认 24");
        hrs.hasMin = true; hrs.min = 1.0;
        hrs.hasMax = true; hrs.max = 168.0;
        hrs.required = false;
        ToolParamSpec lat;
        lat.name = "station_lat_deg";
        lat.type = "number";
        lat.description = QString::fromUtf8("本站纬度（度，-90..90）");
        lat.required = false;
        ToolParamSpec lon;
        lon.name = "station_lon_deg";
        lon.type = "number";
        lon.description = QString::fromUtf8("本站经度（度，-180..180）");
        lon.required = false;
        s.params << sat << hrs << lat << lon;
        out.append(s);
    }

    // calibrate_frequency (read-only measurement) -------------------------
    // Measures the local clock / crystal ppm error against a signal of EXACTLY
    // known frequency. This is a generic capability -- any SDR, any known
    // reference tone works (handheld keyed on an exact frequency, GSM FCCH
    // pure tone, or any user-supplied exact frequency). It is deliberately
    // write=FALSE: taking a measurement must never be blocked by the manual
    // gate, and it does NOT persist or apply any correction. Applying the
    // measured ppm is the separate, gated apply_frequency_correction tool.
    {
        ToolSchemaSpec s;
        s.name = "calibrate_frequency";
        s.description = QString::fromUtf8(
            "只读测量：用一段已知精确频率的参考信号估计本机晶振 ppm 误差。"
            "不修改任何设置。参考源：handheld=手台在已知频点按 PTT 发射；"
            "gsm_fcch=GSM FCCH 精确纯音；manual=任意已知精确频率。"
            "未检测到参考载波时诚实返回 detected=false，不编造 ppm。");
        s.write = false;
        ToolParamSpec ref;
        ref.name = "reference_freq_hz";
        ref.type = "number";
        ref.description = QString::fromUtf8(
            "参考频率 Hz：handheld/manual 为已知精确频率；gsm_fcch 为 ARFCN 下行中心频率");
        ref.hasMin = true; ref.min = tokens::kFreqMinHz;
        ref.hasMax = true; ref.max = tokens::kFreqMaxHz;
        ref.required = true;
        ToolParamSpec type;
        type.name = "reference_type";
        type.type = "string";
        type.description = QString::fromUtf8("参考源类型");
        type.enumValues = QVariantList{"handheld", "gsm_fcch", "manual"};
        type.required = true;
        ToolParamSpec n;
        n.name = "sample_count";
        n.type = "number";
        n.description = QString::fromUtf8("采集复样本数，默认 32768（4 段独立测量）");
        n.hasMin = true; n.min = 4096.0;
        n.required = false;
        s.params << ref << type << n;
        out.append(s);
    }

    // apply_frequency_correction (write -- gated in manual mode) -----------
    // Persists and applies a measured ppm correction to the source. This is the
    // ONLY write side of calibration: it writes QSettings("rtl/ppm") and calls
    // source->setPpm, so it IS gated by the manual-mode write gate. It never
    // invents a ppm of its own -- the caller is expected to have just measured
    // one with calibrate_frequency.
    {
        ToolSchemaSpec s;
        s.name = "apply_frequency_correction";
        s.description = QString::fromUtf8(
            "写入并应用频率校正 ppm（通常取 calibrate_frequency 的 measured_ppm）。"
            "会保存到设置并下发给接收机；属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec ppm;
        ppm.name = "ppm";
        ppm.type = "number";
        ppm.description = QString::fromUtf8("要应用的 ppm 校正值（如 32.0）");
        ppm.hasMin = true; ppm.min = tokens::kPpmMin;
        ppm.hasMax = true; ppm.max = tokens::kPpmMax;
        ppm.required = true;
        ToolParamSpec ref;
        ref.name = "reference_freq_hz";
        ref.type = "number";
        ref.description = QString::fromUtf8("可选：测量时所用参考频率，仅用于出处/前后对比");
        ref.hasMin = true; ref.min = tokens::kFreqMinHz;
        ref.hasMax = true; ref.max = tokens::kFreqMaxHz;
        ref.required = false;
        s.params << ppm << ref;
        out.append(s);
    }

    return out;
}

} // namespace ai
} // namespace mbdsdr
