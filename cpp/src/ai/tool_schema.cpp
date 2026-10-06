// SPDX-License-Identifier: MIT
#include "ai/tool_schema.h"

#include "ai/llm_client.h"          // ToolDef full definition
#include "core/tokens.h"            // kFreqMinHz / kFreqMaxHz
#include "core/bandwidth_preset.h"  // kBw*Hz preset set

#include <QJsonArray>
#include <QJsonDocument>

namespace mbdsdr {
namespace ai {

// scan_band step lower bound (Hz). The runtime already defaults step to 200k
// (see agent_tools.cpp); a 1 Hz floor just prevents a meaningless zero/negative
// sweep step. There is no hardware token for it, so it is named locally next
// to the only place it is used.
constexpr double kScanStepMinHz = 1.0;

// FFT size advisory bounds for set_fft_params. The engine already validates the
// real sizes it accepts; these just keep the JSON schema from advertising an
// absurd range. Not a hardware token -- named locally next to the only use.
constexpr double kFftSizeMin = 256.0;
constexpr double kFftSizeMax = 65536.0;

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

    // ---- Wave2: POCSAG / m17 / VOR digital decode snapshots (read-only) ----
    // These pull the ACCUMULATED decode output of a channel straight off the
    // engine's read-only snapshot slots (SpectrumEngine::pocsagMessages /
    // m17Calls / vorResult). They NEVER tune, gate, or mutate anything, so
    // write=false (not gated in manual mode). Unknown channel / non-matching
    // mode / no decoded frames yet -> an HONEST empty state (empty array, or
    // locked=false for VOR); we never fabricate a message / call / bearing.

    // get_pocsag_messages (read-only) --------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_pocsag_messages";
        s.description = QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 POCSAG 寻呼消息快照"
            "（地址 RIC/功能位/文本）。无解码结果时诚实返回空列表，不编造消息。");
        s.write = false;
        ToolParamSpec ch;
        ch.name = "channel_id";
        ch.type = "number";
        ch.description = QString::fromUtf8("可选：信道 VFO id；缺省为当前选中信道");
        ch.required = false;
        s.params.append(ch);
        out.append(s);
    }

    // get_m17_calls (read-only) --------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_m17_calls";
        s.description = QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 M17 呼叫/帧快照"
            "（源/目的呼号、类型、CRC 状态、语音帧诚实标注未解码）。"
            "无解码结果时诚实返回空列表。");
        s.write = false;
        ToolParamSpec ch;
        ch.name = "channel_id";
        ch.type = "number";
        ch.description = QString::fromUtf8("可选：信道 VFO id；缺省为当前选中信道");
        ch.required = false;
        s.params.append(ch);
        out.append(s);
    }

    // get_vor_radial (read-only) -------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_vor_radial";
        s.description = QString::fromUtf8(
            "只读：读取指定（默认当前选中）VOR 信道最新径向读数"
            "（radialDeg 方位、质量、莫尔斯识别码、锁定态）。"
            "未锁定时 locked=false，方位不可信并被显式标注，不编造方位。");
        s.write = false;
        ToolParamSpec ch;
        ch.name = "channel_id";
        ch.type = "number";
        ch.description = QString::fromUtf8("可选：信道 VFO id；缺省为当前选中信道");
        ch.required = false;
        s.params.append(ch);
        out.append(s);
    }

    // export_iq_segment (write -- gated in manual mode) ---------------------
    // One-shot on-demand IQ dump: capture a bounded baseband IQ window NOW and
    // write it to a cf32_le SigMF file, then return -- distinct from the
    // continuous start_recording/stop_recording pair. It WRITES a file to disk,
    // so it is a write tool (gated in manual mode). No source data -> honest
    // ok:false, never a fabricated empty file.
    {
        ToolSchemaSpec s;
        s.name = "export_iq_segment";
        s.description = QString::fromUtf8(
            "写入（一次性）：立即抓取一段当前中心频率的基带 IQ 复样本并导出为 "
            "cf32_le SigMF 文件（.sigmf-data + .sigmf-meta），返回真实路径与样本数。"
            "区别于 start_recording 的连续录制：这是按需导出一个有界窗口后即返回。"
            "无 IQ 数据时诚实报错，不生成空文件。属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec n;
        n.name = "sample_count";
        n.type = "number";
        n.description = QString::fromUtf8("导出复样本数，默认 65536");
        n.hasMin = true; n.min = 1024.0;
        n.required = false;
        ToolParamSpec f;
        f.name = "tune_hz";
        f.type = "number";
        f.description = QString::fromUtf8("可选：先调谐到该 Hz 再导出；缺省/负数=保持当前中心频率");
        f.hasMin = true; f.min = tokens::kFreqMinHz;
        f.hasMax = true; f.max = tokens::kFreqMaxHz;
        f.required = false;
        s.params << n << f;
        out.append(s);
    }

    // ---- Phase26: capability-everything-as-tools (three-channel parity) ----
    // The 21 frozen tools/commands below. Each Agent tool == a ControlHub command
    // == HTTP POST /command on the SAME gate. write=true => gated in manual mode;
    // write=false => two modes really execute. Order follows the frozen SPEC table.
    // Backed by the engine methods / QSettings / recDir_ that already exist; the
    // ScanActivityLink / network-audio / bookmark back-ends land on the control/
    // side (parallel A block) -- these specs are the AI registration layer only.

    // set_network_audio_sink (write -- gated) -----------------------------
    {
        ToolSchemaSpec s;
        s.name = "set_network_audio_sink";
        s.description = QString::fromUtf8(
            "写入：配置网络音频流输出（UDP/TCP 镜像当前解调音频）。"
            "enable 开关、port 端口、format 采样格式。属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec en;
        en.name = "enable";
        en.type = "boolean";
        en.description = QString::fromUtf8("是否开启网络音频流");
        en.required = true;
        ToolParamSpec port;
        port.name = "port";
        port.type = "number";
        port.description = QString::fromUtf8("网络音频端口号");
        port.required = true;
        ToolParamSpec fmt;
        fmt.name = "format";
        fmt.type = "string";
        fmt.description = QString::fromUtf8("采样格式，如 s16le");
        fmt.required = false;
        s.params << en << port << fmt;
        out.append(s);
    }

    // get_network_audio_status (read-only) --------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_network_audio_status";
        s.description = QString::fromUtf8(
            "只读：返回网络音频流状态（是否使能、端口、格式）。"
            "无状态时诚实返回 enabled=false，不编造端口。");
        out.append(s);
    }

    // start_scan_link (write -- gated) ------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "start_scan_link";
        s.description = QString::fromUtf8(
            "写入：启动扫描活动链路（扫描→命中→驻留→解码→录制），目标频率 target_freq_hz。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec t;
        t.name = "target_freq_hz";
        t.type = "number";
        t.description = QString::fromUtf8("扫描目标中心频率 Hz");
        t.hasMin = true; t.min = tokens::kFreqMinHz;
        t.hasMax = true; t.max = tokens::kFreqMaxHz;
        t.required = true;
        s.params.append(t);
        out.append(s);
    }

    // stop_scan_link (write -- gated) -------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "stop_scan_link";
        s.description = QString::fromUtf8(
            "写入：停止扫描活动链路。属于写动作，手动模式下被拦截。");
        s.write = true;
        out.append(s);
    }

    // get_scan_link_status (read-only) ------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_scan_link_status";
        s.description = QString::fromUtf8(
            "只读：返回扫描活动链路状态（scanning/dwelling/hit）。"
            "未运行时诚实返回 scanning=false、无命中，不编造。");
        out.append(s);
    }

    // set_squelch (write -- gated) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "set_squelch";
        s.description = QString::fromUtf8(
            "写入：设置静噪（enabled 开关、threshold_db 门限、auto 自动链路）。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec en;
        en.name = "enabled";
        en.type = "boolean";
        en.description = QString::fromUtf8("是否开启静噪");
        en.required = false;
        ToolParamSpec th;
        th.name = "threshold_db";
        th.type = "number";
        th.description = QString::fromUtf8("静噪门限 dB");
        th.required = false;
        ToolParamSpec au;
        au.name = "auto";
        au.type = "boolean";
        au.description = QString::fromUtf8("是否启用自动静噪");
        au.required = false;
        s.params << en << th << au;
        out.append(s);
    }

    // get_squelch_status (read-only) --------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_squelch_status";
        s.description = QString::fromUtf8(
            "只读：返回静噪状态（enabled/threshold_db/auto/当前是否 open）。"
            "无实时门限读数时诚实标注，不编造。");
        out.append(s);
    }

    // list_bookmarks (read-only) -----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "list_bookmarks";
        s.description = QString::fromUtf8(
            "只读：列出书签（频率/名称/模式）。无书签时诚实返回空列表，不编造。");
        out.append(s);
    }

    // add_bookmark (write -- gated) --------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "add_bookmark";
        s.description = QString::fromUtf8(
            "写入：添加书签（freq_hz/name/mode）。属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec f;
        f.name = "freq_hz";
        f.type = "number";
        f.description = QString::fromUtf8("书签频率 Hz");
        f.hasMin = true; f.min = tokens::kFreqMinHz;
        f.hasMax = true; f.max = tokens::kFreqMaxHz;
        f.required = true;
        ToolParamSpec nm;
        nm.name = "name";
        nm.type = "string";
        nm.description = QString::fromUtf8("书签名称");
        nm.required = false;
        ToolParamSpec md;
        md.name = "mode";
        md.type = "string";
        md.description = QString::fromUtf8("解调模式，如 NFM/AM");
        md.required = false;
        s.params << f << nm << md;
        out.append(s);
    }

    // tune_to_bookmark (write -- gated) ----------------------------------
    {
        ToolSchemaSpec s;
        s.name = "tune_to_bookmark";
        s.description = QString::fromUtf8(
            "写入：调谐到指定下标书签的频率。属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("书签下标（从 0 开始）");
        idx.required = true;
        s.params.append(idx);
        out.append(s);
    }

    // delete_bookmark (write -- gated) -----------------------------------
    {
        ToolSchemaSpec s;
        s.name = "delete_bookmark";
        s.description = QString::fromUtf8(
            "写入：删除指定下标书签。属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("书签下标（从 0 开始）");
        idx.required = true;
        s.params.append(idx);
        out.append(s);
    }

    // list_vfos (read-only) ----------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "list_vfos";
        s.description = QString::fromUtf8(
            "只读：列出全部 VFO 信道（id/频率/带宽/模式/选中态）。");
        out.append(s);
    }

    // add_vfo (write -- gated) -------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "add_vfo";
        s.description = QString::fromUtf8(
            "写入：新增一个 VFO 信道。属于写动作，手动模式下被拦截。");
        s.write = true;
        out.append(s);
    }

    // switch_vfo (write -- gated) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "switch_vfo";
        s.description = QString::fromUtf8(
            "写入：切换选中的 VFO 信道（index）。属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("VFO id");
        idx.required = true;
        s.params.append(idx);
        out.append(s);
    }

    // rename_vfo (write -- gated) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "rename_vfo";
        s.description = QString::fromUtf8(
            "写入：重命名指定 VFO 信道（index/name）。属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("VFO id");
        idx.required = true;
        ToolParamSpec nm;
        nm.name = "name";
        nm.type = "string";
        nm.description = QString::fromUtf8("新名称");
        nm.required = true;
        s.params << idx << nm;
        out.append(s);
    }

    // list_recordings (read-only) ---------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "list_recordings";
        s.description = QString::fromUtf8(
            "只读：扫描录制目录并列出已有录制文件。目录不存在或为空时诚实返回空列表。");
        out.append(s);
    }

    // delete_recording (write -- gated) ----------------------------------
    {
        ToolSchemaSpec s;
        s.name = "delete_recording";
        s.description = QString::fromUtf8(
            "写入：删除录制目录下指定名称的文件（仅限录制目录内）。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec nm;
        nm.name = "name";
        nm.type = "string";
        nm.description = QString::fromUtf8("录制文件名（仅文件名，不得含路径）");
        nm.required = true;
        s.params.append(nm);
        out.append(s);
    }

    // export_recording (write -- gated) ----------------------------------
    {
        ToolSchemaSpec s;
        s.name = "export_recording";
        s.description = QString::fromUtf8(
            "写入：把录制目录下指定文件复制导出到 out_path。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec nm;
        nm.name = "name";
        nm.type = "string";
        nm.description = QString::fromUtf8("录制文件名（仅文件名）");
        nm.required = true;
        ToolParamSpec outPath;
        outPath.name = "out_path";
        outPath.type = "string";
        outPath.description = QString::fromUtf8("导出目标完整路径");
        outPath.required = true;
        s.params << nm << outPath;
        out.append(s);
    }

    // set_fft_params (write -- gated) ------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "set_fft_params";
        s.description = QString::fromUtf8(
            "写入：设置频谱 FFT 参数（fft_size/window/average）。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec sz;
        sz.name = "fft_size";
        sz.type = "number";
        sz.description = QString::fromUtf8("FFT 点数，如 1024/2048/4096/8192");
        sz.hasMin = true; sz.min = kFftSizeMin;
        sz.hasMax = true; sz.max = kFftSizeMax;
        sz.required = true;
        ToolParamSpec w;
        w.name = "window";
        w.type = "string";
        w.description = QString::fromUtf8("窗函数");
        w.enumValues = QVariantList{"Hann", "Flattop", "Blackman"};
        w.required = false;
        ToolParamSpec av;
        av.name = "average";
        av.type = "string";
        av.description = QString::fromUtf8("平均模式");
        av.enumValues = QVariantList{"Off", "Slow", "Fast"};
        av.required = false;
        s.params << sz << w << av;
        out.append(s);
    }

    // set_color_map (write -- gated) -------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "set_color_map";
        s.description = QString::fromUtf8(
            "写入：保存瀑布图色板文件路径到设置（headless 仅持久化偏好，重绘由 UI 持有）。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec p;
        p.name = "file_path";
        p.type = "string";
        p.description = QString::fromUtf8("色板文件路径");
        p.required = true;
        s.params.append(p);
        out.append(s);
    }

    // get_spectrum_status (read-only) ------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_spectrum_status";
        s.description = QString::fromUtf8(
            "只读：返回频谱当前参数（fft_size/window/average）真实值。");
        out.append(s);
    }

    // set_doppler_compensation (write) -- Phase55 block3 -------------------
    // Live satellite-pass Doppler auto-compensation (the 1 Hz TLE range-rate
    // retune). This is the programmatic mirror of the sky-tab checkbox; it
    // refuses honestly (stays off) unless a station is configured AND a pass is
    // captured. Without a UI surface (headless/test) it reports unavailable.
    {
        ToolSchemaSpec s;
        s.name = "set_doppler_compensation";
        s.description = QString::fromUtf8(
            "写入：开关过境实时多普勒自动补偿（1Hz TLE 距离率重调 VFO）。"
            "需已设置本站位置并捕获一个过境，否则保持关闭（诚实拒绝）。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec en;
        en.name = "enable";
        en.type = "boolean";
        en.description = QString::fromUtf8("是否开启多普勒自动补偿");
        en.required = true;
        s.params << en;
        out.append(s);
    }

    return out;
}

QString generateToolDocumentation() {
    const QList<ToolSchemaSpec> specs = registeredToolSpecs();
    QStringList doc;
    doc << QString::fromUtf8("# Agent 工具能力清单（自动生成，%1 个工具）")
               .arg(specs.size());
    doc << QString::fromUtf8(
        "固定字段：name / description / JSON Schema / read-write 标记 / 错误示例。"
        "参数可能是非法 JSON 或幻觉字段，调用前由 runtime 校验器拒绝并以 role=tool 回注自纠。");
    for (const ToolSchemaSpec& s : specs) {
        const QJsonObject schema = buildToolSchema(s);
        const QString schemaJson =
            QString::fromUtf8(QJsonDocument(schema).toJson(QJsonDocument::Compact));
        doc << QString::fromUtf8("\n## %1  [%2]")
                   .arg(s.name,
                        s.write ? QString::fromUtf8("write 写(手动模式拦截)")
                                : QString::fromUtf8("read 只读"));
        doc << QString::fromUtf8("description: %1").arg(s.description);
        doc << QString::fromUtf8("schema: %1").arg(schemaJson);
        // Error examples: write tools additionally fail shut at the manual gate;
        // every tool rejects illegal args and feeds the error back for self-correction.
        QString errs =
            QString::fromUtf8("错误示例: 参数非法/缺失/幻觉字段 -> "
                              "{\"ok\":false,\"reasons\":[...]}，以 role=tool 回注自纠");
        if (s.write) {
            errs += QString::fromUtf8("；手动模式写门拒绝 -> "
                                      "{\"ok\":false,\"gated\":true,"
                                      "\"error\":\"手动模式：未执行 %1\"}（不自动重试写动作）")
                        .arg(s.name);
        }
        doc << errs;
    }
    return doc.join(QLatin1Char('\n'));
}

} // namespace ai
} // namespace mbdsdr
