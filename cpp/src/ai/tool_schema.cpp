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
        s.description = QString::fromUtf8(
            "写入：把接收机中心频率调谐到指定 Hz（单/主 VFO）。属于写动作，手动模式下被拦截。"
            "前置条件: freq_hz 必须提供且为正的有限数（Hz）；合法范围由 schema 边界"
            "（tokens::kFreqMinHz..kFreqMaxHz）在参数校验层强制。"
            "失败恢复: 非正/非有限频率被明确拒绝（不会假报成功也不会静默丢弃）——"
            "改正数值后重试; 多 VFO 场景请改用 set_vfo_frequency（index 或 id 定位目标 VFO）。"
            "副作用: 立即改变中心频率（硬件状态），音频与瀑布显示随之变化。"
            "能力边界: 只调主 VFO 中心频率; 不改模式/带宽，也不读回状态"
            "（读回用 get_status）。");
        s.write = true;   // changes center frequency
        ToolParamSpec p;
        p.name = "freq_hz";
        p.type = "number";
        p.description = QString::fromUtf8("中心频率（Hz），如 98500000 表示 98.5 MHz");
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
        s.description = QString::fromUtf8(
            "写入：把解调模式切换为指定模式。属于写动作，手动模式下被拦截。"
            "前置条件: mode 必须提供; schema 枚举列出 6 种常用模式（AM/NFM/WFM/USB/LSB/CW），"
            "大小写不敏感，内部统一转规范大写名。"
            "失败恢复: 白名单外的模式名明确拒绝为「未知解调模式」——未识别的模式不会假报生效; "
            "按 schema 枚举重试; 引擎控制hub白名单另含 POCSAG/m17/VOR/ACARS/NAVTEX/RAW，"
            "传入这些也会被接受并以规范名回显，但需确认对应解调链路已配置; "
            "多 VFO 场景改用 set_vfo_mode 定位具体信道。"
            "副作用: 立即改变解调模式（DSP 状态），音频输出与瀑布显示随之变化。"
            "能力边界: 只改当前选中信道的解调模式; 不改频率/带宽，也不读回状态（读回用 get_status）。");
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
        s.description = QString::fromUtf8(
            "写入：开始连续录制（原始 IQ 写 SigMF，或音频写 WAV+sidecar，由录制目标决定），"
            "直到 stop_recording。属于写动作，手动模式下被拦截。"
            "前置条件: 无需参数; 信号源必须已有数据（离线测试源同样可录，sidecar 标注测试信号）。"
            "失败恢复: 启动失败返回 ok:false 与「录制启动失败」——不得当作已开始; "
            "常见原因是已在录制中或信号源无数据，先 get_recording_state 确认状态、"
            "确认信号源连接后再重试。"
            "副作用: 持续写盘直到显式停止; 成功时返回体带 path 可直接读回。"
            "能力边界: 只启动连续录制; 一次性导出有界窗口用 export_iq_segment; "
            "本工具不返回录制内容本身。");
        s.write = true;   // starts IQ recording
        out.append(s);
    }

    // stop_recording (no parameters) --------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "stop_recording";
        s.description = QString::fromUtf8(
            "写入：停止进行中的录制并封存文件（SigMF 或 WAV+sidecar）。属于写动作，手动模式下被拦截。"
            "前置条件: 无（未在录制时调用也返回成功，属幂等停止）。"
            "失败恢复: 本工具不报失败; 要确认是否真在录制，用 get_recording_state 读真实状态。"
            "副作用: 结束连续写盘，已录内容落盘可读。"
            "能力边界: 只停止不删除; 录制文件的管理（列举/导出/删除）用 "
            "list_recordings/export_recording/delete_recording。");
        s.write = true;   // stops IQ recording
        out.append(s);
    }

    // scan_band -----------------------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "scan_band";
        s.description = QString::fromUtf8(
            "写入：在 [low_hz, high_hz] 频段内按 step_hz 步进扫频，返回最强峰（频率 + dBFS）。"
            "属于写动作（扫频过程逐点移动调谐频率），手动模式下被拦截。"
            "前置条件: low_hz/high_hz 必须提供且在合法频率范围内; step_hz 可缺省"
            "（默认 200 kHz，下限 1 Hz）; 注意 low_hz > high_hz 时一个点都不扫，"
            "峰值保持 -200 dBFS 初值。"
            "失败恢复: 本工具不因无信号报错——hits[0].dbfs 很低（≈-200）即该段无强信号或"
            "频段方向给反了; 检查频段方向与信号源连接后重扫。"
            "副作用: 每步约 20 ms 驻留，细步进大频段会很慢; 扫描结束后调谐停在峰值点。"
            "能力边界: 只报最强单峰; 合成测试信号上峰值为固定基带偏音（返回体 synthetic=true "
            "已标注），非真实电台命中; 要全段峰清单用 export_peaks_csv。");
        s.write = true;   // sweeps the receiver across a band (mutates freq)
        ToolParamSpec low;
        low.name = "low_hz";
        low.type = "number";
        low.description = QString::fromUtf8("起始频率（Hz）");
        low.hasMin = true; low.min = tokens::kFreqMinHz;
        low.hasMax = true; low.max = tokens::kFreqMaxHz;
        low.required = true;
        ToolParamSpec high;
        high.name = "high_hz";
        high.type = "number";
        high.description = QString::fromUtf8("结束频率（Hz）");
        high.hasMin = true; high.min = tokens::kFreqMinHz;
        high.hasMax = true; high.max = tokens::kFreqMaxHz;
        high.required = true;
        ToolParamSpec step;
        step.name = "step_hz";
        step.type = "number";
        step.description = QString::fromUtf8("步进（Hz，缺省 200000，下限 1）");
        step.hasMin = true; step.min = kScanStepMinHz;
        step.required = false;
        s.params << low << high << step;
        out.append(s);
    }

    // set_bandwidth -------------------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "set_bandwidth";
        s.description = QString::fromUtf8(
            "写入：把信道滤波器带宽设为指定 Hz。属于写动作，手动模式下被拦截。"
            "前置条件: bandwidth_hz 必须提供且取 schema 枚举内的具名预设"
            "（AM 9000 / NFM 12500 / SSB 2400 / CW 500 / DIG 12000 / WFM 200000 / ADS-B 2000000）; "
            "当前解调模式不能是 RAW。"
            "失败恢复: RAW 直通模式下明确拒绝（RAW 是信道化窄带 IQ 的 L=I/R=Q 直通，"
            "无解调滤波器可设）——先 set_mode 切到解调模式再设带宽; "
            "枚举外的值由参数校验层拒绝。"
            "副作用: 立即改变信道滤波（DSP 状态），音频带宽与瀑布显示随之变化。"
            "能力边界: 只设当前选中信道带宽; 其他 VFO 用 set_vfo_bandwidth; "
            "不读回（读回用 get_status）。");
        s.write = true;   // changes channel filter bandwidth
        ToolParamSpec p;
        p.name = "bandwidth_hz";
        p.type = "number";
        p.description = QString::fromUtf8("信道带宽（Hz），取 schema 枚举预设：AM 9000 / NFM 12500 / SSB 2400 / CW 500 / DIG 12000 / WFM 200000 / ADS-B 2000000");
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
        s.description = QString::fromUtf8(
            "只读：返回接收机当前完整状态——连接状态与信号源名、中心频率、解调模式、带宽、"
            "Costas 载波/符号锁定与 EVM、多普勒补偿的可用/启用状态。"
            "前置条件: 无（未连接也可查询，connected=false 如实返回）。"
            "失败恢复: 本工具不报失败; 未连接时其余字段为引擎当前值，不编造连接。"
            "副作用: 无（纯读）。"
            "能力边界: 只读当前状态; 引擎未暴露的字段（如增益/采样率）缺失即不返回，不虚构; "
            "改状态用对应的 set_* 写入工具。");
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
            "写入：应用频率校正 ppm（通常取 calibrate_frequency 的 measured_ppm），"
            "保存到设置并下发给接收机。属于写动作，手动模式下被拦截。"
            "前置条件: ppm 必须显式提供且落在参数边界内——执行层对缺失 ppm 按 0 处理"
            "（会把你已有的校正清零），所以绝不省略; reference_freq_hz 可选，仅记录出处。"
            "失败恢复: ppm 缺失或越界时参数校验层明确拒绝，补合法数值后重试; "
            "想确认校正效果请用 calibrate_frequency 重新实测，不要把本工具的预测值当实测。"
            "副作用: 立即写 QSettings 持久化 ppm 并改变接收机频综校正（硬件状态）。"
            "能力边界: 只保存并下发 ppm，返回 previous_ppm/applied_ppm 前后对比与 "
            "predicted_residual_hz=0.0 的设计预测（非实测残差）；读回当前配置用 get_status。");
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
            "（地址 RIC/功能位/文本）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表，不编造消息。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 信道选择不改调谐状态。");
        s.write = false;
        ToolParamSpec ch;
        ch.name = "channel_id";
        ch.type = "number";
        ch.description = QString::fromUtf8("可选：信道 VFO id；与 channel 二选一；缺省为当前选中信道");
        ch.required = false;
        ToolParamSpec chAlias;
        chAlias.name = "channel";
        chAlias.type = "number";
        chAlias.description = QString::fromUtf8("可选：channel_id 的别名（ControlHub/HTTP 键名）；二选一");
        chAlias.required = false;
        s.params << ch << chAlias;
        out.append(s);
    }

    // get_m17_calls (read-only) --------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_m17_calls";
        s.description = QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 M17 呼叫/帧快照"
            "（源/目的呼号、类型、CRC 状态、语音帧诚实标注未解码）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 语音帧未解码时显式标注。");
        s.write = false;
        ToolParamSpec ch;
        ch.name = "channel_id";
        ch.type = "number";
        ch.description = QString::fromUtf8("可选：信道 VFO id；与 channel 二选一；缺省为当前选中信道");
        ch.required = false;
        ToolParamSpec chAlias;
        chAlias.name = "channel";
        chAlias.type = "number";
        chAlias.description = QString::fromUtf8("可选：channel_id 的别名（ControlHub/HTTP 键名）；二选一");
        chAlias.required = false;
        s.params << ch << chAlias;
        out.append(s);
    }

    // get_vor_radial (read-only) -------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_vor_radial";
        s.description = QString::fromUtf8(
            "只读：读取指定（默认当前选中）VOR 信道最新径向读数"
            "（radialDeg 方位、质量、莫尔斯识别码、锁定态）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——未锁定时 locked=false，方位不可信并被显式标注，"
            "不编造方位。"
            "能力边界: 只读引擎最新读数; 方位仅锁定后可信。");
        s.write = false;
        ToolParamSpec ch;
        ch.name = "channel_id";
        ch.type = "number";
        ch.description = QString::fromUtf8("可选：信道 VFO id；与 channel 二选一；缺省为当前选中信道");
        ch.required = false;
        ToolParamSpec chAlias;
        chAlias.name = "channel";
        chAlias.type = "number";
        chAlias.description = QString::fromUtf8("可选：channel_id 的别名（ControlHub/HTTP 键名）；二选一");
        chAlias.required = false;
        s.params << ch << chAlias;
        out.append(s);
    }

    // get_acars_packets (read-only) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_acars_packets";
        s.description = QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 ACARS 航空报文快照"
            "（方向 air/ground、label、block id、ack、正文、CRC 结果）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表，不编造报文。"
            "能力边界: 只读引擎已解出的快照，不触发解调; CRC 失败帧显式标注。");
        s.write = false;
        ToolParamSpec ch;
        ch.name = "channel_id";
        ch.type = "number";
        ch.description = QString::fromUtf8("可选：信道 VFO id；与 channel 二选一；缺省为当前选中信道");
        ch.required = false;
        ToolParamSpec chAlias;
        chAlias.name = "channel";
        chAlias.type = "number";
        chAlias.description = QString::fromUtf8("可选：channel_id 的别名（ControlHub/HTTP 键名）；二选一");
        chAlias.required = false;
        s.params << ch << chAlias;
        out.append(s);
    }

    // get_navtex_messages (read-only) --------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_navtex_messages";
        s.description = QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 NAVTEX 海上安全报文快照"
            "（发台 B1、类型 B2、编号、正文、时间分集/定相状态）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表，不编造报文。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 分集/定相失败在条目内显式标注。");
        s.write = false;
        ToolParamSpec ch;
        ch.name = "channel_id";
        ch.type = "number";
        ch.description = QString::fromUtf8("可选：信道 VFO id；与 channel 二选一；缺省为当前选中信道");
        ch.required = false;
        ToolParamSpec chAlias;
        chAlias.name = "channel";
        chAlias.type = "number";
        chAlias.description = QString::fromUtf8("可选：channel_id 的别名（ControlHub/HTTP 键名）；二选一");
        chAlias.required = false;
        s.params << ch << chAlias;
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
            "写入（一次性）：立即抓取一段基带 IQ 复样本，导出为 cf32_le SigMF 文件"
            "（.sigmf-data + .sigmf-meta）后即返回。区别于 start_recording 的连续录制："
            "这是按需导出有界窗口。属于写动作，手动模式下被拦截。"
            "前置条件: 接收机已有可用的 IQ 数据流（源在运行、有硬件或已开离线文件）；"
            "sample_count 缺省 65536，引擎按 [1024, 16777216] 诚实钳位; "
            "tune_hz 缺省/负值=保持当前中心频率。"
            "失败恢复: 无 IQ 数据或源 EOF 时诚实 ok:false 并说明原因，绝不生成空文件; "
            "tune_hz 非数字时明确拒绝。"
            "副作用: 在录制目录写 SigMF 文件（路径自动生成 export_<时间戳>_<中心Hz>，"
            "按秒级消歧不会覆盖既有文件）; tune_hz>=0 时会先把中心频率调过去（硬件状态）; "
            "不影响进行中的连续录制。"
            "能力边界: 只导出不连续录制; 源提前 EOF 时返回真实样本数（可能少于请求值）; "
            "文件位置见返回 path 字段。");
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
    // Phase63 D5: advertise host/stereo so the routed stub echoes them instead
    // of silently dropping (CH cmdSetNetworkAudioSink already accepts them).
    {
        ToolSchemaSpec s;
        s.name = "set_network_audio_sink";
        s.description = QString::fromUtf8(
            "写入：配置网络音频流输出（UDP/TCP 镜像当前解调音频）。属于写动作，手动模式下被拦截。"
            "前置条件: enable（布尔）与 port（数字，1..65535）均必填; format 可选 udp/tcp"
            "（默认 udp）、host 可选（默认 127.0.0.1）、stereo 可选（默认 false 单声道）。"
            "失败恢复: enable/port 缺失或类型不符时明确报错（不静默取默认）——补齐后重试; "
            "指令经 ControlHub 路由执行，链路真实状态用 get_network_audio_status 读取。"
            "副作用: 改变网络音频流的目标端口/协议/单双声道配置（桌面端持续生效）。"
            "能力边界: 只下发配置不直接推流; Agent 层不持有音频流实例，回显值经控制层确认。");
        s.write = true;
        ToolParamSpec en;
        en.name = "enable";
        en.type = "boolean";
        en.description = QString::fromUtf8("是否开启网络音频流");
        en.required = true;
        ToolParamSpec port;
        port.name = "port";
        port.type = "number";
        port.description = QString::fromUtf8("网络音频端口号 (1..65535)");
        port.required = true;
        ToolParamSpec fmt;
        fmt.name = "format";
        fmt.type = "string";
        fmt.description = QString::fromUtf8("协议：udp 或 tcp（默认 udp）");
        fmt.required = false;
        ToolParamSpec host;
        host.name = "host";
        host.type = "string";
        host.description = QString::fromUtf8("绑定/对端主机（默认 127.0.0.1）");
        host.required = false;
        ToolParamSpec st;
        st.name = "stereo";
        st.type = "boolean";
        st.description = QString::fromUtf8("立体声（默认 false=单声道）");
        st.required = false;
        s.params << en << port << fmt << host << st;
        out.append(s);
    }

    // get_network_audio_status (read-only) --------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_network_audio_status";
        s.description = QString::fromUtf8(
            "只读：返回网络音频流状态（是否使能、端口、格式）。"
            "无状态时诚实返回 enabled=false，不编造端口。"
            "前置条件: 无。"
            "能力边界: 当前 Agent 层无网络音频流读数（后端由 ControlHub 提供），"
            "enabled=false 是诚实空态而非故障——不要据此声称网络音频损坏。");
        out.append(s);
    }

    // start_scan_link (write -- gated) ------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "start_scan_link";
        s.description = QString::fromUtf8(
            "写入：启动扫描活动链路（扫描→命中→驻留→解码→录制），目标频率 target_freq_hz。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: target_freq_hz 必填且为数字（Hz），范围由参数边界强制; "
            "接收机有可用数据流（链路要真实扫描）。"
            "失败恢复: 参数缺失或非数字时明确报错——补齐合法频率后重试; "
            "指令经 ControlHub 路由，链路已在运行时重复启动由控制层语义处理。"
            "副作用: 控制层开始扫描→命中→驻留→解码→录制活动链路（持续消耗接收资源，"
            "可能自动录制到磁盘）。"
            "能力边界: 只发启动指令; 链路真实运行态在 ControlHub 侧，用 get_scan_link_status "
            "读取，停止用 stop_scan_link。");
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
            "写入：停止扫描活动链路。属于写动作，手动模式下被拦截。"
            "前置条件: 无（无参数，任意时刻可调）。"
            "失败恢复: 指令经 ControlHub 路由执行; 链路未运行时停止为空操作，"
            "返回成功而非报错——不要反复重试。"
            "副作用: 停止当前进行中的扫描活动链路（若有）。"
            "能力边界: 只发停止指令; 扫描链路真实运行态在 ControlHub 侧，"
            "用 get_scan_link_status 读取（Agent 层无链路实例时为空态读数）。");
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
            "属于写动作，手动模式下被拦截。"
            "前置条件: 三个字段全部可选，至少提供其一才有意义（全缺则不下发任何变更）; "
            "threshold_db 为 dB 门限数值。"
            "失败恢复: 类型不符的字段被静默跳过而非报错（如 threshold_db 传了字符串）——"
            "发现设置未生效时用 get_squelch_status 读回真实值核对，不要反复重试同一入参。"
            "副作用: 立即改变引擎静噪状态，音频输出（静音/放音）随之变化。"
            "能力边界: 只改静噪参数，不影响 CTCSS/CDCSS 亚音门控（分别用 set_ctcss/set_cdcss）; "
            "读回用 get_squelch_status。");
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

    // set_ctcss (write -- gated) ------------------------------------------
    // Phase63 CTCSS tone-squelch tool. `enabled` is REQUIRED (a missing /
    // non-bool is an honest error, never a silent toggle). `frequency_hz` is
    // optional (omit to keep the current / default 88.5 Hz); an out-of-domain
    // value (<67.0 or >254.1 Hz) is REJECTED here with ok:false -- the engine
    // clamps, so the tool layer is the honest gate that refuses rather than
    // silently retuning to a clamped tone. `gate_audio` (optional) arms the
    // speaker-only sub-audio gate: while on, the speaker stays silent unless a
    // matching tone is detected (the recorder is NOT muted). Omit to leave the
    // gate untouched.
    {
        ToolSchemaSpec s;
        s.name = "set_ctcss";
        s.description = QString::fromUtf8(
            "写入：设置 CTCSS 亚音（enabled 开关、frequency_hz 亚音频率 67.0–254.1 Hz、"
            "可选 gate_audio 亚音门控静音开关）。属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔; frequency_hz 可选（省略沿用当前值/默认 88.5 Hz），"
            "取值须落在 [67.0, 254.1]; gate_audio 可选布尔。"
            "失败恢复: enabled 缺失或非布尔时明确报错（绝不静默切换）; 频率越界时诚实拒绝"
            "（不静默钳位）——改合法频率后重试。"
            "副作用: 立即改变 CTCSS 检测状态; gate_audio 开启后仅扬声器被门控（静音至匹配），"
            "录制不受影响。"
            "能力边界: 只设亚音参数; 数字亚音用 set_cdcss; 读回（含 active 是否真实检出）"
            "用 get_ctcss_status。");
        s.write = true;
        ToolParamSpec en;
        en.name = "enabled";
        en.type = "boolean";
        en.description = QString::fromUtf8("是否开启 CTCSS 亚音检测");
        en.required = true;
        ToolParamSpec fq;
        fq.name = "frequency_hz";
        fq.type = "number";
        fq.description = QString::fromUtf8("CTCSS 亚音频率 Hz（67.0–254.1，缺省沿用当前/默认 88.5）");
        fq.hasMin = true; fq.min = tokens::kCtcssToneHzMin;
        fq.hasMax = true; fq.max = tokens::kCtcssToneHzMax;
        fq.required = false;
        ToolParamSpec gate;
        gate.name = "gate_audio";
        gate.type = "boolean";
        gate.description = QString::fromUtf8(
            "可选：是否开启亚音门控静音（开启后仅在检测到匹配亚音时才放音，录制不受影响；缺省不改动当前门控）");
        gate.required = false;
        s.params << en << fq << gate;
        out.append(s);
    }

    // get_ctcss_status (read-only) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_ctcss_status";
        s.description = QString::fromUtf8(
            "只读：返回 CTCSS 亚音状态（enabled 是否使能、frequency_hz 调谐频率、"
            "active 是否真实检测到亚音、gate_audio 是否开启亚音门控静音）。"
            "无信号/未使能时 active 诚实为 false，不编造。");
        out.append(s);
    }

    // set_cdcss (write -- gated) ------------------------------------------
    // Phase63 CDCSS/DCS digital coded squelch tool. `enabled` is REQUIRED
    // (missing/non-bool -> honest error). `code` is an optional 3-digit octal
    // DCS address string ("023".."754"); when supplied it MUST be in the public
    // 104-code table (dsp/cdcss.cpp) -- an illegal code is REJECTED here with
    // ok:false rather than silently tuning to a nonsense address. Omit to keep
    // the current code. `gate_audio` (optional) arms the speaker-only digital
    // sub-audio gate: while on, the speaker stays silent unless a matching DCS
    // codeword is detected (the recorder is NOT muted).
    {
        ToolSchemaSpec s;
        s.name = "set_cdcss";
        s.description = QString::fromUtf8(
            "写入：设置 CDCSS/DCS 数字亚音（enabled 开关、code 三位八进制 DCS 码 "
            "\"023\"–\"754\"、可选 gate_audio 数字亚音门控静音开关）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔; code 可选三位八进制字符串（省略沿用当前码），"
            "且必须落在公开 104 码表内（dsp/cdcss.cpp）; gate_audio 可选布尔。"
            "失败恢复: enabled 缺失或非布尔时明确报错; code 不在码表内时诚实拒绝"
            "（不静默调到非法地址）——按码表取值重试。"
            "副作用: 立即改变 CDCSS 检测状态; gate_audio 开启后仅扬声器被门控（静音至匹配码），"
            "录制不受影响。"
            "能力边界: 只设数字亚音参数; 模拟亚音用 set_ctcss; 读回（含 active 是否真实检出）"
            "用 get_cdcss_status。");
        s.write = true;
        ToolParamSpec en;
        en.name = "enabled";
        en.type = "boolean";
        en.description = QString::fromUtf8("是否开启 CDCSS/DCS 数字亚音检测");
        en.required = true;
        ToolParamSpec code;
        code.name = "code";
        code.type = "string";
        code.description = QString::fromUtf8(
            "三位八进制 DCS 码字符串（如 \"023\"，须在公开 104 码表内；缺省沿用当前码）");
        code.required = false;
        ToolParamSpec gate;
        gate.name = "gate_audio";
        gate.type = "boolean";
        gate.description = QString::fromUtf8(
            "可选：是否开启数字亚音门控静音（开启后仅在检测到匹配 DCS 码时才放音，录制不受影响；缺省不改动当前门控）");
        gate.required = false;
        s.params << en << code << gate;
        out.append(s);
    }

    // get_cdcss_status (read-only) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_cdcss_status";
        s.description = QString::fromUtf8(
            "只读：返回 CDCSS/DCS 数字亚音状态（enabled 是否使能、code 调谐 DCS 码、"
            "active 是否真实检测到匹配码、gate_audio 是否开启数字亚音门控静音）。"
            "无信号/未使能时 active 诚实为 false，不编造。");
        out.append(s);
    }

    // set_ft8 (write -- gated) --------------------------------------------
    // Phase63 step3 FT8 detection-layer tool. `enabled` is REQUIRED (missing/
    // non-bool -> honest error). Detection layer only; C++ BP decode deferred.
    {
        ToolSchemaSpec s;
        s.name = "set_ft8";
        s.description = QString::fromUtf8(
            "写入：开关 FT8 数字模式检测层（enabled 布尔，必填）。属于写动作，手动模式下被拦截。"
            "本轮为检测层（Costas 同步 + 候选帧统计），C++ BP 解码留后续，不编造解码消息。"
            "前置条件: enabled 必须提供且为布尔值（缺失/非布尔明确报错，绝不静默切换）。"
            "失败恢复: 入参类型不符时补齐布尔值重试; 开启后无候选帧不代表故障，"
            "先确认频率与模式已对准 FT8 子带。"
            "副作用: 立即改变引擎 FT8 检测层状态（持续占用解调资源）。"
            "能力边界: 只开关检测层，不出解码消息; 检测层读数用 get_ft8_status，"
            "解码历史用 get_ft8_history，导出用 export_ft8_results。");
        s.write = true;
        ToolParamSpec en;
        en.name = "enabled";
        en.type = "boolean";
        en.description = QString::fromUtf8("是否开启 FT8 检测层");
        en.required = true;
        s.params << en;
        out.append(s);
    }

    // get_ft8_status (read-only) ------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_ft8_status";
        s.description = QString::fromUtf8(
            "只读：返回 FT8 检测层状态（enabled 是否使能、active 是否真实检出候选帧、"
            "freq_offset_hz 估计频偏、sync_quality 相关峰/次峰比、candidate_count 候选数）。"
            "无信号/未使能时 active 诚实为 false，不编造。");
        out.append(s);
    }

    // set_lrpt (write -- gated) --------------------------------------------
    // P2 LRPT step-3 satellite-imaging tool. `enabled` is REQUIRED (missing/
    // non-bool -> honest error). State/control layer only: the C++ LRPT demod/FEC
    // port lands in step 4; this arms the desired flag so the control plane can
    // gate on it. It does NOT fabricate imagery or frames.
    {
        ToolSchemaSpec s;
        s.name = "set_lrpt";
        s.description = QString::fromUtf8(
            "写入：开关 LRPT 卫星云图接收层（enabled 布尔，必填）。属于写动作，手动模式下被拦截。"
            "本轮为状态控制层（C++ 解调/FEC 移植留后续第④轮），不编造图像或帧。"
            "前置条件: enabled 必须提供且为布尔值（缺失/非布尔明确报错）; 卫星在轨且频率对准。"
            "失败恢复: 入参类型不符时补齐布尔值重试; 开启后 sync_locked 仍为 false 是"
            "C++ 解码器未移植的诚实空态，不代表接收失败。"
            "副作用: 立即改变引擎 LRPT 接收层开关状态。"
            "能力边界: 只置位状态标志，不出图像/帧数据; 读回用 get_lrpt_status"
            "（sync_locked/decoded_frames 为真实引擎值）。");
        s.write = true;
        ToolParamSpec en;
        en.name = "enabled";
        en.type = "boolean";
        en.description = QString::fromUtf8("是否开启 LRPT 卫星云图接收");
        en.required = true;
        s.params << en;
        out.append(s);
    }

    // get_lrpt_status (read-only) ------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_lrpt_status";
        s.description = QString::fromUtf8(
            "只读：返回 LRPT 接收层状态（enabled 是否使能、sync_locked 是否已锁定位同步、"
            "decoded_frames 已解码行数）。C++ 解码器未移植前 sync_locked 诚实为 false、"
            "decoded_frames 为 0，不编造。");
        out.append(s);
    }

    // set_vna_sweep (write -- gated) ---------------------------------------
    // NanoVNA step-2: program a sweep on the connected instrument. start_hz /
    // stop_hz / points are REQUIRED; stop>start and points>0 validated both in
    // the tool layer (honest ok:false) and the client. With no device attached
    // parameters are validated but the command is not sent (applied=false).
    {
        ToolSchemaSpec s;
        s.name = "set_vna_sweep";
        s.description = QString::fromUtf8(
            "写入：设置 NanoVNA 扫频范围（start_hz/stop_hz/points，均必填；stop 必须大于 start，"
            "points 为正整数）。宽扫建议 ±5~10%，窄扫 ±1%（经验范围）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: 三个参数都必须提供且为数字; points 为正整数; stop_hz 大于 start_hz。"
            "失败恢复: 任一缺失/非数字、stop<=start 或 points 非正时明确 ok:false——"
            "改正后重试; 无设备时参数仍被校验，合法但不下发（applied=false），"
            "不伪造连接——先接好设备再调。"
            "副作用: 设备已连接时立即向 NanoVNA 下发新扫频参数（硬件状态），"
            "当前测量数据随之失效，需重扫后再读。"
            "能力边界: 只编程扫频参数，不读数据（用 get_vna_data）; 连接/校准状态用 "
            "get_vna_status。");
        s.write = true;
        ToolParamSpec a;
        a.name = "start_hz"; a.type = "number"; a.required = true;
        a.description = QString::fromUtf8("扫频起始频率 (Hz)");
        ToolParamSpec b;
        b.name = "stop_hz"; b.type = "number"; b.required = true;
        b.description = QString::fromUtf8("扫频终止频率 (Hz，必须大于 start_hz)");
        ToolParamSpec c;
        c.name = "points"; c.type = "number"; c.required = true;
        c.description = QString::fromUtf8("扫描点数 (正整数)");
        s.params << a << b << c;
        out.append(s);
    }

    // get_vna_data (read-only) ----------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_vna_data";
        s.description = QString::fromUtf8(
            "只读：返回当前扫频测量数据（frequencies、S11/S21 复数对及派生 VSWR/回波损耗/阻抗）。"
            "区别：本工具返回测量曲线数据；get_vna_status 仅返回连接/校准/扫描参数状态。"
            "无设备/未取数时各数组诚实为空，不编造曲线。");
        out.append(s);
    }

    // get_vna_status (read-only) --------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_vna_status";
        s.description = QString::fromUtf8(
            "只读：返回 NanoVNA 状态（connected 是否已连接、model 型号、version 固件、cal 已置位校准项、"
            "当前 sweep 范围与点数）。未连接时 connected 诚实为 false，字段为空。");
        out.append(s);
    }

    // analyze_vna_resonance (read-only) -------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "analyze_vna_resonance";
        s.description = QString::fromUtf8(
            "只读：对当前扫频 S11 做谐振分析（串联谐振 fr、并联谐振 fp、ESR、-3dB 带宽、有载 Q）。"
            "无设备/点数不足/span=0 时 valid 诚实为 false，不编造。");
        out.append(s);
    }

    // vna_tdr_cable (write -- gated): runs a TDR reflection run on the current
    // sweep. velocity_factor (0..1) REQUIRED.
    {
        ToolSchemaSpec s;
        s.name = "vna_tdr_cable";
        s.description = QString::fromUtf8(
            "写入：对当前扫频 S11 做 TDR 时域反射（velocity_factor 速度因子，0<vf<=1，必填），"
            "返回首反射峰距离与电缆长度（馈线好坏/电缆故障/进水/接头虚接）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: velocity_factor 必填数字且落在 (0,1]（同轴常用 0.66）; "
            "NanoVNA 已连接且已完成一次扫频（先 set_vna_sweep）。"
            "失败恢复: vf 缺失/非数字或越界时明确 ok:false——补合法值重试; "
            "无设备或未检出反射峰时 valid=false 诚实返回，不编造距离。"
            "副作用: 只读当前扫频缓存做变换，不向设备发新命令（门禁仍按写动作把守）。"
            "能力边界: 只用当前 S11 数据; 实测 S 参数曲线用 get_vna_data; "
            "coax_loss_db_per_m 需先用本工具测得电缆长度。");
        s.write = true;
        ToolParamSpec vf;
        vf.name = "velocity_factor"; vf.type = "number"; vf.required = true;
        vf.description = QString::fromUtf8("电缆速度因子 (0<vf<=1，如同轴 0.66)");
        s.params << vf;
        out.append(s);
    }

    // analyze_vna_filter (read-only) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "analyze_vna_filter";
        s.description = QString::fromUtf8(
            "只读：对当前扫频 S21 幅度做滤波器分析（自动识别 bandpass/bandstop/highpass/lowpass，"
            "输出 -3dB 截止边、带宽、通带插损、阻带衰减）。无设备/幅度平坦非滤波器形态时 valid 诚实为 false。");
        out.append(s);
    }

    // analyze_vna_equivalent (read-only) ------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "analyze_vna_equivalent";
        s.description = QString::fromUtf8(
            "只读：由当前扫频 S11 提取串联 RLC 等效电路（ESR=串联谐振点 |Z|，Q=fr/BW，L/C 由谐振关系推出）。"
            "无设备/点数不足时 valid 诚实为 false。");
        out.append(s);
    }

    // analyze_vna_touchstone (read-only) ------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "analyze_vna_touchstone";
        s.description = QString::fromUtf8(
            "只读：解析 Touchstone s1p/s2p 文件（content 文本 或 path 路径），返回频率表与 S11/S21 实虚部数据集，"
            "可复用谐振/滤波器分析。仅 R/I 格式；不可解析时 ok:false 诚实拒绝。");
        ToolParamSpec content;
        content.name = "content"; content.type = "string";
        content.description = QString::fromUtf8("Touchstone 文件全文（与 path 二选一）");
        s.params << content;
        ToolParamSpec path;
        path.name = "path"; path.type = "string";
        path.description = QString::fromUtf8("Touchstone 文件路径（与 content 二选一）");
        s.params << path;
        out.append(s);
    }

    // export_vna_snapshot (write, gated) -------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "export_vna_snapshot";
        s.description = QString::fromUtf8(
            "写入：把当前 NanoVNA 扫频的 Smith 圆图轨迹离屏渲染为 PNG 存到 path。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: path 必填（PNG 输出完整路径）; NanoVNA 已连接且已有扫频数据"
            "（先 set_vna_sweep 并等待扫描完成）。"
            "失败恢复: path 缺失、未连接或无扫频数据时诚实 ok:false——补齐前置条件后重试; "
            "路径不可写时 ok:false 并提示。"
            "副作用: 在 path 写一个 PNG 文件（覆盖同名文件）; 不影响进行中的测量。"
            "能力边界: 只渲染 Smith 轨迹; 频谱/峰值另有 export_spectrum_snapshot / "
            "export_peaks_csv; 原始数据用 get_vna_data。");
        s.write = true;
        ToolParamSpec path;
        path.name = "path"; path.type = "string";
        path.description = QString::fromUtf8("PNG 输出路径");
        path.required = true;
        s.params << path;
        out.append(s);
    }

    // export_spectrum_snapshot (write, gated) -------------------------------
    {
        ToolSchemaSpec s;
        s.name = "export_spectrum_snapshot";
        s.description = QString::fromUtf8(
            "写入：把当前 SDR 频谱面板（频率轴 + 中心频点标记 + 峰值标注）离屏渲染为 PNG "
            "存到 path。属于写动作，手动模式下被拦截。"
            "前置条件: path 必填（PNG 输出完整路径）; 引擎已初始化（FFT 已运行）。"
            "失败恢复: path 缺失时诚实 ok:false; 路径不可写时 ok:false 并提示——换可写路径重试; "
            "FFT 为空时画平坦基线（诚实空态）而非伪造曲线。"
            "副作用: 在 path 写一个 PNG 文件（覆盖同名文件）; 不改动调谐/解调状态。"
            "能力边界: 只截当前频谱画面; 原始 FFT 数据用 get_spectrum_status; "
            "数值化峰值导出用 export_peaks_csv。");
        s.write = true;
        ToolParamSpec path;
        path.name = "path"; path.type = "string";
        path.description = QString::fromUtf8("PNG 输出路径");
        path.required = true;
        s.params << path;
        out.append(s);
    }

    // export_peaks_csv (write, gated) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "export_peaks_csv";
        s.description = QString::fromUtf8(
            "写入：把当前频谱峰值列表（freq_hz,db，按强度降序）写成 CSV 存到 path。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: path 必填（CSV 输出完整路径）; 引擎有运行中的 FFT 数据。"
            "失败恢复: path 缺失或不可写时诚实 ok:false——换可写路径重试; "
            "平坦/空 FFT 时 ok:true 但 rows=0 仅写表头（诚实空态，不是错误，不要重试）。"
            "副作用: 在 path 写 CSV 文件（覆盖同名文件）; 不改动调谐/解调状态。"
            "能力边界: 固定导出前 10 个峰值、门限 6 dB（引擎 peaks(10,6.0) 口径）; "
            "整段频谱图用 export_spectrum_snapshot。");
        s.write = true;
        ToolParamSpec path;
        path.name = "path"; path.type = "string";
        path.description = QString::fromUtf8("CSV 输出路径");
        path.required = true;
        s.params << path;
        out.append(s);
    }

    // export_ft8_results (write, gated) --------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "export_ft8_results";
        s.description = QString::fromUtf8(
            "写入：把当前 FT8 解码结果导出为 CSV 文件存到 path。属于写动作，手动模式下被拦截。"
            "前置条件: path 必填（输出文件完整路径）; all_history 可选布尔（默认 false）。"
            "失败恢复: path 缺失或不可写时诚实 ok:false——换可写路径重试; "
            "无结果时 ok:true 但 rows=0 仅写表头（诚实空态，不伪造呼号/网格）。"
            "副作用: 在 path 写文件（覆盖同名文件）; 不改动解码器状态。"
            "能力边界: all_history=false 导出候选列表（freq_hz,crc_ok,costas_score,rejected,"
            "reject_reason,iters）；true 导出解码历史全量（time_ms,freq_hz,demod,text）; "
            "在线读取分别用 get_ft8_status / get_ft8_history。");
        s.write = true;
        ToolParamSpec path;
        path.name = "path"; path.type = "string";
        path.description = QString::fromUtf8("输出文件路径（.csv/.txt）");
        path.required = true;
        s.params << path;
        ToolParamSpec allHist;
        allHist.name = "all_history"; allHist.type = "boolean";
        allHist.description = QString::fromUtf8("true=导出解码历史全量（time_ms,freq_hz,demod,text）；默认 false=候选列表");
        allHist.required = false;
        s.params << allHist;
        out.append(s);
    }

    // analyze_vna_match (read-only) ------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "analyze_vna_match";
        s.description = QString::fromUtf8(
            "只读：给定实测负载阻抗 load_r(+load_x) 与设计频率 f_hz，返回匹配网络的 L/C 建议"
            "（Q、串联/并联臂值）（驻波下不来/做匹配/加感）。"
            "topology 可选：series_shunt（默认，既有 EFHW/反 L 语义）/xps（shunt 并联后 series 串联）"
            "/xs（series 串联后 shunt 并联）/xpl（shunt-series-shunt 三电抗 Pi）；"
            "r_comp 可选，串联 R 补偿（≥0，并入负载电阻）。"
            "无闭式解的拓扑诚实 valid:false，不伪造数值。");
        ToolParamSpec r; r.name = "load_r"; r.type = "number";
        r.description = QString::fromUtf8("负载电阻 Ω（高阻馈电点用 >50；xs 用 <50）"); r.required = true;
        ToolParamSpec x; x.name = "load_x"; x.type = "number";
        x.description = QString::fromUtf8("负载电抗 Ω（默认 0）");
        ToolParamSpec f; f.name = "f_hz"; f.type = "number";
        f.description = QString::fromUtf8("设计频率 Hz"); f.required = true;
        ToolParamSpec topo; topo.name = "topology"; topo.type = "string";
        topo.description = QString::fromUtf8("匹配拓扑：series_shunt（默认既有 EFHW 语义）/xps/xs/xpl");
        topo.enumValues = QVariantList{"series_shunt", "xps", "xs", "xpl"};
        ToolParamSpec rc; rc.name = "r_comp"; rc.type = "number";
        rc.description = QString::fromUtf8("串联 R 补偿 Ω（≥0，并入负载电阻，默认 0）");
        s.params << r << x << f << topo << rc;
        out.append(s);
    }

    {
        ToolSchemaSpec s;
        s.name = "set_noise_blanker";
        s.description = QString::fromUtf8(
            "写入：开关噪声抑制器（on 布尔）。属于写动作，手动模式下被拦截。"
            "前置条件: on 必须提供且为布尔值。"
            "失败恢复: on 缺失或非布尔时返回明确错误（绝不静默取默认值）——"
            "补齐布尔值后重试。"
            "副作用: 立即改变解调链路的噪声抑制状态。"
            "能力边界: 仅开关，不含门限/强度参数; 状态回读用 get_noise_blanker_status。");
        s.write = true;
        ToolParamSpec on;
        on.name = "on";
        on.type = "boolean";
        on.description = QString::fromUtf8("是否开启噪声抑制");
        on.required = true;
        s.params << on;
        out.append(s);
    }

    // get_noise_blanker_status (read-only) ---------------------------------
    {
        ToolSchemaSpec s;
        s.name = "get_noise_blanker_status";
        s.description = QString::fromUtf8(
            "只读：返回噪声抑制器状态（enabled 是否使能）。"
            "读取引擎真实开关，不编造。"
            "前置条件: 无（未连接设备也可读当前开关配置）。"
            "能力边界: 只读，不改变任何状态; 开关操作用 set_noise_blanker。");
        out.append(s);
    }

    // list_bookmarks (read-only) -----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "list_bookmarks";
        s.description = QString::fromUtf8(
            "只读：列出书签（频率/名称/模式）。无书签时诚实返回空列表，不编造。"
            "前置条件: 无。"
            "能力边界: Agent 层未注入书签管理器时返回空列表+note（诚实空态），"
            "真实书签数据在 control 层——空列表不等于没有书签，不要虚构条目。");
        out.append(s);
    }

    // add_bookmark (write -- gated) --------------------------------------
    // Phase63 D4: schema now declares bandwidth_hz and group (which the executor
    // already accepted) so the LLM sees them; CH cmdAddBookmark also consumes
    // group on the control side.
    {
        ToolSchemaSpec s;
        s.name = "add_bookmark";
        s.description = QString::fromUtf8(
            "写入：添加书签（freq_hz/name/mode/bandwidth_hz/group）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: freq_hz 必填且为正的有限数（Hz）; name/mode/bandwidth_hz/group 全可选"
            "（缺省时对应字段留空）; 书签管理器已由桌面端注入。"
            "失败恢复: freq_hz 缺失/非数字或 <=0 时诚实拒绝（书签不入册）——改正后重试; "
            "管理器未注入时返回明确错误而非假装写入。"
            "副作用: 真写入书签存储并按 (group, freq) 自动排序落位（无撤销接口），"
            "返回 index/count 真实落位。"
            "能力边界: 只增书签不调谐; 用书签频率调谐是 tune_to_bookmark; "
            "列书签用 list_bookmarks。");
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
        ToolParamSpec bw;
        bw.name = "bandwidth_hz";
        bw.type = "number";
        bw.description = QString::fromUtf8("书签带宽 Hz（可选）");
        bw.required = false;
        ToolParamSpec grp;
        grp.name = "group";
        grp.type = "string";
        grp.description = QString::fromUtf8("书签分组名（可选）");
        grp.required = false;
        s.params << f << nm << md << bw << grp;
        out.append(s);
    }

    // tune_to_bookmark (write -- gated) ----------------------------------
    {
        ToolSchemaSpec s;
        s.name = "tune_to_bookmark";
        s.description = QString::fromUtf8(
            "写入：调谐到指定下标书签的频率。属于写动作，手动模式下被拦截。"
            "前置条件: index 必须是 0 起整数下标，且书签管理器已注入。"
            "失败恢复: index 越界时错误信息含真实书签总数（如「书签 index 5 越界"
            "（共 3 条）」）——按总数改下标重试; 管理器未注入时返回明确错误而非假装调谐。"
            "副作用: 改变当前选中 VFO 的调谐频率（硬件状态）。"
            "能力边界: 只调谐到书签频率，不改书签本身; 书签列表用 list_bookmarks 读取。");
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
            "写入：删除指定下标书签。属于写动作，手动模式下被拦截。"
            "前置条件: index 必须是 0 起整数下标，且书签管理器已注入。"
            "失败恢复: index 越界时错误信息含真实书签总数——按总数改下标重试。"
            "副作用: 真删除书签（无回收站/无撤销接口），返回值 remaining 为删除后剩余数。"
            "能力边界: 只能删已注入管理器的书签; 不确定下标时先用 tune_to_bookmark "
            "的越界错误探测总数，或让用户确认。");
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
            "只读：列出全部 VFO 信道（id/频率/带宽/模式/选中态）。"
            "前置条件: 无。"
            "能力边界: 返回引擎真实 VFO 标记，空列表即当前无 VFO; "
            "新增/切换/改参分别用 add_vfo / switch_vfo / set_vfo_* 系列。");
        out.append(s);
    }

    // add_vfo (write -- gated) -------------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "add_vfo";
        s.description = QString::fromUtf8(
            "写入：新增一个 VFO 信道。属于写动作，手动模式下被拦截。"
            "前置条件: 无（无参数，任意时刻可调）。"
            "失败恢复: 无失败路径——新增必定成功并回显新信道 id。"
            "副作用: 在引擎新增一个 VFO 信道（不自动切换选中，不改变当前调谐）。"
            "能力边界: 只增不选; 返回 selected_vfo_id 供后续 switch_vfo 参考。");
        s.write = true;
        out.append(s);
    }

    // switch_vfo (write -- gated) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "switch_vfo";
        s.description = QString::fromUtf8(
            "写入：切换选中的 VFO 信道（index）。属于写动作，手动模式下被拦截。"
            "前置条件: index 必须提供且为数字; 有效下标范围用 list_vfos 读取。"
            "失败恢复: index 缺失或非数字时返回明确错误——先 list_vfos 拿真实 id 再重试。"
            "副作用: 切换选中 VFO，解调输出与调谐目标随之改变。"
            "能力边界: 只切换不新增; 选错信道不会丢数据，再次 switch_vfo 即可。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("VFO 下标（用 list_vfos 读取真实 id）");
        idx.required = true;
        s.params.append(idx);
        out.append(s);
    }

    // rename_vfo (write -- gated) ----------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "rename_vfo";
        s.description = QString::fromUtf8(
            "写入：重命名指定 VFO 信道（index/name）。属于写动作，手动模式下被拦截。"
            "前置条件: index（数字）与 name（字符串）都必须提供。"
            "失败恢复: 任一缺失或类型不符时返回明确错误——补齐后重试。"
            "副作用: 重命名经 control 层路由生效; 不影响频率/带宽/模式。"
            "能力边界: 只改显示名; 频率/模式/带宽用 set_vfo_* 系列。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("VFO 下标（用 list_vfos 读取真实 id）");
        idx.required = true;
        ToolParamSpec nm;
        nm.name = "name";
        nm.type = "string";
        nm.description = QString::fromUtf8("新名称");
        nm.required = true;
        s.params << idx << nm;
        out.append(s);
    }

    // set_vfo_armed (write -- gated) ------------------------------------
    {
        ToolSchemaSpec s;
        s.name = "set_vfo_armed";
        s.description = QString::fromUtf8(
            "写入：开启/关闭某 VFO 的后台并行解调（index/enabled）。开启后即使该 VFO "
            "未被选中，仍会在每个数据块被解调，便于并行监听/录制；关闭则停止以节省 CPU。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: index（list_vfos 返回的标记序号）与 enabled 均必填; "
            "enabled 接受布尔或数字（非 0 即真）。"
            "失败恢复: index 缺失/非数字、enabled 缺失或类型不符时明确报错; "
            "index 越界时错误附当前 VFO 总数——用 list_vfos 重新取序号后重试。"
            "副作用: 立即改变该 VFO 的后台解调使能（真实 CPU 开销随之增减）。"
            "能力边界: 只改并行监听开关; 频率/模式/带宽用 set_vfo_* 系列; "
            "当前各 VFO 状态用 list_vfos 读回。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("VFO 序号（list_vfos 返回顺序）");
        idx.required = true;
        ToolParamSpec en;
        en.name = "enabled";
        en.type = "boolean";
        en.description = QString::fromUtf8("true=后台并行监听，false=停止");
        en.required = true;
        s.params << idx << en;
        out.append(s);
    }

    // set_vfo_frequency (write -- gated) ---------------------------------
    // Phase63 D1: accept EITHER index (marker ordinal) OR id (direct VFO id).
    // Phase67.2 honesty: BOTH keys are declared and NEITHER is required.
    // The strict validator (arguments_validator.cpp) rejects unknown keys AND
    // missing required ones, so an undeclared alias is unreachable from the
    // model even though execSetVfoFrequency resolves it -- declaring both is
    // the only way the D1 bilateral contract survives the validated path.
    // The handler itself errors honestly when neither is supplied.
    {
        ToolSchemaSpec s;
        s.name = "set_vfo_frequency";
        s.description = QString::fromUtf8(
            "写入：把指定 VFO 调谐到新频率（index 或 id 二选一 + freq_hz）。"
            "前置条件: index（list_vfos 返回的序号）或 id（VFO 直传 id）必须提供其一；"
            "freq_hz 为目标频率（Hz，必填）。"
            "失败恢复: 两者都缺或类型不符时返回明确参数错误；index 越界时错误附当前"
            "VFO 总数——用 list_vfos 重新取序号/id 后重试。"
            "副作用: 该 VFO 的解调频率立即改变，音频与瀑布显示随之变化；不影响其他 "
            "VFO，也不改变模式/带宽。"
            "能力边界: 只调谐已存在的 VFO（不新增信道）；频率合法性由引擎与硬件决定。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("VFO 序号（list_vfos 返回顺序；与 id 二选一）");
        idx.required = false;
        ToolParamSpec idp;
        idp.name = "id";
        idp.type = "number";
        idp.description = QString::fromUtf8("VFO id（直传；与 index 二选一）");
        idp.required = false;
        ToolParamSpec fq;
        fq.name = "freq_hz";
        fq.type = "number";
        fq.description = QString::fromUtf8("目标频率（Hz）");
        fq.required = true;
        s.params << idx << idp << fq;
        out.append(s);
    }

    // set_vfo_mode (write -- gated) --------------------------------------
    // Phase67.2 honesty: same bilateral declaration as set_vfo_frequency --
    // index AND id both declared, neither required (validator whitelist).
    {
        ToolSchemaSpec s;
        s.name = "set_vfo_mode";
        s.description = QString::fromUtf8(
            "写入：切换指定 VFO 的解调模式（index 或 id 二选一 + mode）。"
            "前置条件: index 或 id 必须提供其一；mode 取值见 ControlHub 模式表："
            "AM/NFM/WFM/USB/LSB/CW/POCSAG/m17/VOR/ACARS/NAVTEX（大小写不敏感）。"
            "失败恢复: 目标缺失/越界或 mode 不在表中时返回明确错误（越界附 VFO 总数）——"
            "用 list_vfos 核对后重试。"
            "副作用: 该 VFO 的解调模式立即切换，音频输出随之改变。"
            "能力边界: 只改已存在 VFO 的模式; 不影响其他 VFO。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("VFO 序号（list_vfos 返回顺序；与 id 二选一）");
        idx.required = false;
        ToolParamSpec idp;
        idp.name = "id";
        idp.type = "number";
        idp.description = QString::fromUtf8("VFO id（直传；与 index 二选一）");
        idp.required = false;
        ToolParamSpec md;
        md.name = "mode";
        md.type = "string";
        md.description = QString::fromUtf8("解调模式（大小写不敏感）");
        md.required = true;
        s.params << idx << idp << md;
        out.append(s);
    }

    // set_vfo_bandwidth (write -- gated) ---------------------------------
    // Phase67.2 honesty: same bilateral declaration as set_vfo_frequency.
    {
        ToolSchemaSpec s;
        s.name = "set_vfo_bandwidth";
        s.description = QString::fromUtf8(
            "写入：设置指定 VFO 的信道带宽（index 或 id 二选一 + bandwidth_hz）。"
            "前置条件: index 或 id 必须提供其一；bandwidth_hz 必须为正数（Hz）。"
            "失败恢复: 目标缺失/越界或带宽非正时返回明确错误（越界附 VFO 总数）——"
            "补齐后重试。"
            "副作用: 该 VFO 的滤波器带宽立即改变，音频随之变化。"
            "能力边界: 只改已存在 VFO 的带宽; 可用范围由硬件/引擎决定。"
            "属于写动作，手动模式下被拦截。");
        s.write = true;
        ToolParamSpec idx;
        idx.name = "index";
        idx.type = "number";
        idx.description = QString::fromUtf8("VFO 序号（list_vfos 返回顺序；与 id 二选一）");
        idx.required = false;
        ToolParamSpec idp;
        idp.name = "id";
        idp.type = "number";
        idp.description = QString::fromUtf8("VFO id（直传；与 index 二选一）");
        idp.required = false;
        ToolParamSpec bw;
        bw.name = "bandwidth_hz";
        bw.type = "number";
        bw.description = QString::fromUtf8("信道带宽（Hz，必须为正数）");
        bw.required = true;
        s.params << idx << idp << bw;
        out.append(s);
    }

    // list_recordings (read-only) ---------------------------------------
    // Phase67.2: four-dimension contract against execListRecordings --
    // a missing dir is an HONEST empty list (ok:true, count 0), not an error.
    {
        ToolSchemaSpec s;
        s.name = "list_recordings";
        s.description = QString::fromUtf8(
            "只读：扫描录制目录并列出已有录制文件。"
            "前置条件: 无（任意时刻可调；目录路径由引擎配置决定，随结果 dir 字段透传）。"
            "失败恢复: 无失败路径——目录不存在或为空时诚实返回空列表（count=0），"
            "不编造文件。"
            "能力边界: 只列文件不读取内容; 每条含 name/bytes/modified; "
            "删除/导出分别用 delete_recording / export_recording。");
        out.append(s);
    }

    // delete_recording (write -- gated) ----------------------------------
    // Phase67.2: four-dimension contract against execDeleteRecording --
    // bare filename only (traversal refused), missing file is an explicit
    // error, removal is real and irreversible.
    {
        ToolSchemaSpec s;
        s.name = "delete_recording";
        s.description = QString::fromUtf8(
            "写入：删除录制目录下指定名称的文件。属于写动作，手动模式下被拦截。"
            "前置条件: name 必须是录制目录内的纯文件名（不得含 / 或 \\，不得为 . / ..）——"
            "可用 list_recordings 先取真实文件名。"
            "失败恢复: name 含路径分隔符时明确拒绝（防目录穿越）；文件不存在时返回"
            "明确错误——核对 list_recordings 后重试; 删除本身失败也会明确报错。"
            "副作用: 真删除磁盘文件（无回收站/无撤销接口），不可恢复。"
            "能力边界: 只能删录制目录内的文件; 导出而非删除用 export_recording。");
        s.write = true;
        ToolParamSpec nm;
        nm.name = "name";
        nm.type = "string";
        nm.description = QString::fromUtf8("录制文件名（仅纯文件名，不得含路径）");
        nm.required = true;
        s.params.append(nm);
        out.append(s);
    }

    // export_recording (write -- gated) ----------------------------------
    // Phase67.2: four-dimension contract against execExportRecording --
    // bare filename only, missing source is explicit, QFile::copy fails
    // honestly when the target exists or is unwritable, source untouched.
    {
        ToolSchemaSpec s;
        s.name = "export_recording";
        s.description = QString::fromUtf8(
            "写入：把录制目录下指定文件复制导出到 out_path。属于写动作，手动模式下被拦截。"
            "前置条件: name（录制目录内纯文件名，不得含路径）与 out_path（目标完整路径）"
            "都必须提供; 文件名可用 list_recordings 核对。"
            "失败恢复: name 含路径分隔符时明确拒绝; 源文件不存在时返回明确错误; "
            "目标已存在或不可写时返回「导出失败」——换目标路径后重试。"
            "副作用: 在 out_path 生成一份副本; 不改动/删除源文件。"
            "能力边界: 只导出录制目录内的文件; 复制失败不产生半成品承诺（QFile::copy 语义）。");
        s.write = true;
        ToolParamSpec nm;
        nm.name = "name";
        nm.type = "string";
        nm.description = QString::fromUtf8("录制文件名（仅纯文件名）");
        nm.required = true;
        ToolParamSpec outPath;
        outPath.name = "out_path";
        outPath.type = "string";
        outPath.description = QString::fromUtf8("导出目标完整路径（已存在则导出失败）");
        outPath.required = true;
        s.params << nm << outPath;
        out.append(s);
    }

    // set_fft_params (write -- gated) ------------------------------------
    // Phase63 D3: window/average accept BOTH the string enum below AND the raw
    // int (0/1/2) used by the ControlHub/HTTP channel. The schema advertises
    // the human-readable enum for the LLM; the executor accepts either type.
    {
        ToolSchemaSpec s;
        s.name = "set_fft_params";
        s.description = QString::fromUtf8(
            "写入：设置频谱 FFT 参数（fft_size/window/average）。"
            "window/average 使用字符串枚举（Hann/Flattop/Blackman、Off/Slow/Fast）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: fft_size 必填数字; window/average 可选（省略保持当前值）; "
            "LLM/MCP 校验路径只接受字符串枚举。"
            "失败恢复: fft_size 缺失或非数字时明确报错; 注意引擎实收只有 1024/2048/4096 三档——"
            "传入其他值（如 8192）会被静默忽略且回显当前值而非请求值，发现未生效时以 "
            "get_spectrum_status 读回为准; window/average 传无法识别的字符串时回落 Hann/Off。"
            "副作用: 立即改变 FFT 点数/窗函数/平均模式，频谱与瀑布显示随之变化。"
            "能力边界: 模型侧只接受字符串枚举——原始整数 0/1/2 是 ControlHub/HTTP "
            "内部通道键，经 LLM/MCP 校验路径传入会被类型校验拒绝; 读回用 get_spectrum_status。");
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
        w.description = QString::fromUtf8("窗函数（Hann/Flattop/Blackman）");
        w.enumValues = QVariantList{"Hann", "Flattop", "Blackman"};
        w.required = false;
        ToolParamSpec av;
        av.name = "average";
        av.type = "string";
        av.description = QString::fromUtf8("平均模式（Off/Slow/Fast）");
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
            "写入：加载并校验瀑布图色板 JSON 文件，把路径持久化到设置"
            "（view/wfColormapFile，UI 启动时重应用同一文件）。属于写动作，手动模式下被拦截。"
            "前置条件: file_path 必填且指向可读的 JSON 色板文件。"
            "失败恢复: 路径缺失/不可读/解析失败时诚实 ok:false 并说明原因（name/stops 无效"
            "不落盘）——修正文件后重试; 可用 get_color_maps 先看当前配置与内置色板。"
            "副作用: 写 QSettings 持久化色板文件路径（headless 仅持久化偏好，"
            "立即重绘由桌面端 UI 持有）。"
            "能力边界: 只加载外部文件色板; 内置 3 个色板（经典/单色/Viridis）经 get_color_maps "
            "读取，不经本工具切换。");
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
    // Phase67.2: four-dimension contract against execGetSpectrumStatus --
    // real engine fftSize()/windowType()/averageMode() readback; output keys
    // (fft_size/window/average) match the schema and set_fft_params echo.
    {
        ToolSchemaSpec s;
        s.name = "get_spectrum_status";
        s.description = QString::fromUtf8(
            "只读：返回频谱当前 FFT 参数真实值（fft_size/window/average）。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——值直接读引擎，不编造; 与预期不符时以本工具读回为准。"
            "能力边界: 只读不改; 修改 FFT 参数用 set_fft_params。");
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
            "属于写动作，手动模式下被拦截。"
            "前置条件: enable 布尔必填; 桌面端 UI 控制面存在（headless/测试环境不可用，"
            "返回 available=false）; 已配置本站位置且 TLE 缓存里有目标卫星。"
            "失败恢复: 无 UI 控制面或未设本站/未捕获过境时诚实 ok:false 并说明原因——"
            "先在桌面端天空页完成设置; enable 缺省按 false 处理（保持关闭）。"
            "副作用: 开启后过境期间按 TLE 距离率持续重调 VFO 频率（硬件状态持续变化）。"
            "能力边界: 只置补偿开关; 过境预测用 predict_passes，卫星目录用 list_satellites。");
        s.write = true;
        ToolParamSpec en;
        en.name = "enable";
        en.type = "boolean";
        en.description = QString::fromUtf8("是否开启多普勒自动补偿");
        en.required = true;
        s.params << en;
        out.append(s);
    }

    // connect_network_source (write) -- Phase58 block2 ---------------------
    // Connect to an rtl_tcp network SDR source. Real TCP handshake + RTL0
    // dongle-info header; on failure it reports the honest socket reason and
    // installs the idle source (no fake IQ). 
    {
        ToolSchemaSpec s;
        s.name = "connect_network_source";
        s.description = QString::fromUtf8(
            "写入：连接 rtl_tcp 网络接收机（host/port）。"
            "真实 TCP 握手+RTL0 设备头；失败返回真实 socket 原因并回到空态，不伪造 IQ。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: host 必填（服务端主机/IP）; port 可选，缺省 1234; "
            "对端已运行 rtl_tcp 且网络可达。"
            "失败恢复: host 缺失时明确报错; 握手/协议失败时 ok:false 并附真实 socket 原因，"
            "回到空态源（无假 IQ）——核对地址端口与服务端状态后重试。"
            "副作用: 成功时切换当前 IQ 源到该 rtl_tcp 服务（调谐/录制目标随之改变）; "
            "失败时保持既有源不变。"
            "能力边界: 只发起连接; 连接后的源能力（调谐范围/增益档）用 get_capabilities 读取。");
        s.write = true;
        ToolParamSpec host;
        host.name = "host";
        host.type = "string";
        host.description = QString::fromUtf8("rtl_tcp 服务端主机/IP");
        host.required = true;
        ToolParamSpec port;
        port.name = "port";
        port.type = "number";
        port.description = QString::fromUtf8("rtl_tcp 服务端端口（默认 1234）");
        port.required = false;
        s.params << host << port;
        out.append(s);
    }

    // get_capabilities (read-only) ----------------------------------------
    // Real source capability read-back straight off the ACTIVE source: device
    // name, tunable / sample-rate range, the discrete gain-step table. It NEVER
    // fabricates a tunable range or a gain table: an offline/test/unconnected
    // source reports connected=false, an EMPTY gains array, and an honest
    // provenance note. write=false (predict_passes-style: never gated).
    {
        ToolSchemaSpec s;
        s.name = "get_capabilities";
        s.description = QString::fromUtf8(
            "只读：返回当前源的真实能力（设备名、可调谐频率范围、采样率范围、离散增益档 gains_db）。"
            "未连接或测试信号源时诚实返回 connected=false、空增益档数组与来源说明 provenance，"
            "不编造调谐范围或增益表。");
        s.write = false;
        out.append(s);
    }

    // get_recording_state (read-only) --------------------------------------
    // Recording state straight off the engine (live recording path, watch enable,
    // recording dir). recording is derived from the live path (empty = not
    // recording) -- never fabricated. write=false.
    {
        ToolSchemaSpec s;
        s.name = "get_recording_state";
        s.description = QString::fromUtf8(
            "只读：返回录制状态（recording 是否在手动录制中、recording_path 当前路径、"
            "watch_enabled 值守录制是否使能、recording_dir 录制目录）。"
            "未录制时诚实返回 recording=false、空路径，不伪造。");
        s.write = false;
        out.append(s);
    }

    // vna_calibration (write -- gated): single-tool NanoVNA calibration state
    // machine. `step` is the instrument-protocol command word; `slot` is only
    // consumed by save/load. status (or omitted) is read-only guidance that
    // returns the objective next step. Write steps go through the manual gate so
    // a human confirms each standard connection. The ordering / when to attach
    // standards (OPEN/SHORT/LOAD to CH0, isolation, THRU CH0-CH1) is a hardware
    // fact under the caller's control -- no fixed script is baked in.
    {
        ToolSchemaSpec s;
        s.name = "vna_calibration";
        s.description = QString::fromUtf8(
            "写入：NanoVNA 校准单工具状态机。step 为校准/测量命令字（必填枚举）："
            "status=只读引导（返回客观下一步与已完成/缺失步骤）；"
            "reset/open/short/load/isolation/thru/done=校准写步骤；"
            "save/load=保存/加载校准集（需 slot 1..4）；scan/pause/resume=扫频控制。"
            "写动作走手动 gate、人在环逐步确认。接标准件到 CH0 / CH0-CH1 THRU 是仪器协议客观事实，"
            "编排权在调用者（模型），非固定剧本。未连接 ok:false 并给排查清单。"
            "前置条件: step 必填且属 13 个合法值之一; save/load 另需 slot（1..4 整数）; "
            "NanoVNA 已连接。"
            "失败恢复: step 未知或缺 slot 时明确 ok:false 并列合法值——改正后重试; "
            "未连接时给接线/串口排查清单，不伪造连接; 每步写完真实读回校准位（steps）。"
            "副作用: 写步骤真正改变设备校准状态（reset 清空、save 落槽位）; "
            "scan/pause/resume 控制扫频运行。"
            "能力边界: 单工具状态机不预置剧本; 校准质量解读（coverage/置信度）随返回 "
            "cal_interpretation 字段给出; 连接/型号信息用 get_vna_status。");
        s.write = true;
        ToolParamSpec step;
        step.name = "step"; step.type = "string"; step.required = true;
        step.description = QString::fromUtf8(
            "校准命令字：status/reset/open/short/load/isolation/thru/done/save/load/scan/pause/resume");
        step.enumValues = QVariantList{
            "status", "reset", "open", "short", "load", "isolation",
            "thru", "done", "save", "load", "scan", "pause", "resume"};
        ToolParamSpec slot;
        slot.name = "slot"; slot.type = "number"; slot.required = false;
        slot.description = QString::fromUtf8("校准集槽位 1..4（仅 save/load 用）");
        slot.hasMin = true; slot.min = 1.0;
        slot.hasMax = true; slot.max = 4.0;
        s.params << step << slot;
        out.append(s);
    }

    // vna_device (write -- gated): single-tool device-level misc state machine.
    // Read actions (status/vbat/freq_offset read) return objective current
    // values; write actions (freq_offset set/edelay/averaging) go through the
    // manual gate. The whole tool is write=true because it CAN write the
    // device -- defense in depth, never half-open.
    {
        ToolSchemaSpec s;
        s.name = "vna_device";
        s.description = QString::fromUtf8(
            "写入：NanoVNA 设备级杂项单工具状态机。action 为命令字（必填枚举）："
            "status=只读（连接/校准摘要/扫频范围/edelay/averaging/freq_offset 当前值，未读回的诚实给 0+note）；"
            "vbat=读电池电压；freq_offset 无 value=读本振频偏(Hz)、有 value=写频偏；"
            "edelay=写电延迟(ps)；averaging=写平均次数(1..64)。"
            "写动作走手动 gate、人在环确认。读动作客观返回当前值；编排权在调用者，非固定剧本。"
            "未连接 ok:false 并给排查清单。"
            "前置条件: action 必填且属 5 个合法值之一; 写动作（freq_offset/edelay/averaging）"
            "另需 value 数字（averaging 限定 1..64）; NanoVNA 已连接。"
            "失败恢复: action 未知或缺 value 时明确 ok:false——补齐后重试; "
            "averaging 越界 1..64 明确拒绝; 未连接时给接线/串口排查清单。"
            "副作用: 写动作真正改变设备硬件设置（频偏/电延迟/平均次数）。"
            "能力边界: status 中 edelay/averaging/freq_offset 三项本会话未读回时诚实给 0+note"
            "（0 不代表设备真实设置）; 扫频参数用 set_vna_sweep，数据曲线用 get_vna_data。");
        s.write = true;
        ToolParamSpec act;
        act.name = "action"; act.type = "string"; act.required = true;
        act.description = QString::fromUtf8("设备命令字：status/vbat/freq_offset/edelay/averaging");
        act.enumValues = QVariantList{
            "status", "vbat", "freq_offset", "edelay", "averaging"};
        ToolParamSpec val;
        val.name = "value"; val.type = "number"; val.required = false;
        val.description = QString::fromUtf8(
            "写动作参数：freq_offset=Hz（整数）、edelay=ps（整数）、averaging=1..64（整数）；读动作不用");
        s.params << act << val;
        out.append(s);
    }

    // get_ft8_history (read-only): rolling decoded-message window (WSJT-X style).
    // Distinct from get_ft8_status (current-frame instant state) and
    // export_ft8_results (file dump). Empty list when no frames decoded.
    {
        ToolSchemaSpec s;
        s.name = "get_ft8_history";
        s.description = QString::fromUtf8(
            "只读：返回最近 N 条 FT8 解码历史（WSJT-X 风格滚动窗口，按时间倒序），"
            "每条字段 time_ms（墙钟毫秒）/freq_hz（绝对 RF Hz）/demod（解调，如 FT8）/"
            "text（解码文本 FROM TO EXCHANGE）。无历史时 history=[] 诚实空态，不伪造。"
            "区别于 get_ft8_status（当前帧即时状态）与 export_ft8_results（导出文件）。");
        s.write = false;
        ToolParamSpec lim;
        lim.name = "limit"; lim.type = "number";
        lim.description = QString::fromUtf8("返回条数上限 1..200，默认 50；超界诚实 clamp");
        lim.required = false;
        lim.hasMin = true; lim.min = 1.0;
        lim.hasMax = true; lim.max = 200.0;
        s.params << lim;
        out.append(s);
    }

    // get_adsb_aircraft (read-only): dump1090 --aircraft shape snapshot.
    // Distinct from get_ft8_status / get_ft8_history (decoder result surface).
    {
        ToolSchemaSpec s;
        s.name = "get_adsb_aircraft";
        s.description = QString::fromUtf8(
            "只读：返回最近 N 架已解码 ADS-B 飞机（按最后看到时间倒序），"
            "每条字段 icao（24bit hex）/callsign（呼号）/altitude_ft（高度 ft）/"
            "lat/lon（经纬度，has_position=false 时 NaN）/groundspeed_kt（地速 kt）/"
            "heading_deg（航向）/vertical_rate_fpm（垂直速率 fpm，has_velocity 标记）/"
            "df（下行格式）/crc_ok（CRC 通过）/last_seen（墙钟毫秒）。"
            "1090 MHz 无信号或未调谐时 aircraft=[] 诚实空态，不编造飞机。"
            "区别于 get_ft8_status / get_ft8_history（解码器结果面）。");
        s.write = false;
        ToolParamSpec lim;
        lim.name = "limit"; lim.type = "number";
        lim.description = QString::fromUtf8("返回架数上限 1..50，默认 50；超界诚实 clamp");
        lim.required = false;
        lim.hasMin = true; lim.min = 1.0;
        lim.hasMax = true; lim.max = 50.0;
        s.params << lim;
        out.append(s);
    }

    // P3 #73/#74: persistent local memory family (SQLite FTS5 on ~/.mbdsdr).
    // memory_add/update enqueue; the background checkpoint writer persists rows.
    {
        ToolSchemaSpec s;
        s.name = "memory_add";
        s.description = QString::fromUtf8(
            "写入：向本地记忆库（SQLite FTS5，~/.mbdsdr/memory.db）新增一条记忆——"
            "实际落库由后台 checkpoint writer 异步执行（返回 queued=true，不阻塞主 agent）。"
            "scope 必填枚举 user（用户事实/偏好）/agent（模型经验/踩坑）/session（本次会话现场）；"
            "scope_id 为作用域内实例标识（如台站号、过境 id）；key 在 scope 内唯一。"
            "submit 不做 DB 级查重；立即落库用 memory_flush。属于写动作，手动模式下被拦截。"
            "前置条件: scope/scope_id/key/content 四项全部必填（任一缺失明确报错）; "
            "~/.mbdsdr 目录可写。"
            "失败恢复: 缺参时按项点名报错——补齐后重试; 提交失败（writer 异常）时返回 "
            "ok:false 与真实错误; 队列积压数量见返回 pending_count。"
            "副作用: 记忆进入后台写队列（异步落库，不保证立即可检索——需要立即生效先 "
            "memory_flush 再用 memory_search 验证）。"
            "能力边界: 只入队不查重（重复 (scope,key) 以 memory_update 的 upsert 语义为准）; "
            "读用 memory_search，删用 memory_delete，统计用 memory_stats。");
        s.write = true;
        auto en = [](const char* n) {
            ToolParamSpec p; p.name = QString::fromUtf8(n); p.type = "string"; p.required = true; return p;
        };
        s.params << en("scope") << en("scope_id") << en("key") << en("content");
        out.append(s);
    }
    {
        ToolSchemaSpec s;
        s.name = "memory_search";
        s.description = QString::fromUtf8(
            "只读：全文检索本地记忆库（中英文均可）。query 为自然语言词串，"
            "词元经安全转义后走 FTS5 MATCH；可选 scope/scope_id 过滤；limit 1..100 默认 20。"
            "无命中返回 hits=[] 空数组并附 note，不编造记忆。");
        s.write = false;
        ToolParamSpec q;
        q.name = "query"; q.type = "string";
        q.description = QString::fromUtf8("检索词（自然语言，自动词元化）");
        q.required = true;
        ToolParamSpec sc;
        sc.name = "scope"; sc.type = "string";
        sc.description = QString::fromUtf8("可选过滤：user/agent/session");
        sc.required = false;
        ToolParamSpec sid;
        sid.name = "scope_id"; sid.type = "string";
        sid.description = QString::fromUtf8("可选过滤：作用域内实例标识");
        sid.required = false;
        ToolParamSpec lim;
        lim.name = "limit"; lim.type = "number";
        lim.description = QString::fromUtf8("返回上限 1..100，默认 20；越界诚实 clamp");
        lim.required = false;
        lim.hasMin = true; lim.min = 1.0;
        lim.hasMax = true; lim.max = 100.0;
        s.params << q << sc << sid << lim;
        out.append(s);
    }
    {
        ToolSchemaSpec s;
        s.name = "memory_update";
        s.description = QString::fromUtf8(
            "写入：按 (scope,key) upsert 记忆——存在则覆盖 content/scope_id 与时间戳，"
            "不存在则等价于新增；经后台 checkpoint writer 异步落库（返回 queued=true）。"
            "scope 必填枚举 user/agent/session。立即落库用 memory_flush。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: scope/scope_id/key/content 四项全部必填（任一缺失按项点名报错）; "
            "~/.mbdsdr 目录可写。"
            "失败恢复: 缺参时按项点名——补齐后重试; 提交失败时返回 ok:false 与真实错误; "
            "落库后检索不到先 memory_flush 再 memory_search 验证。"
            "副作用: 记忆 upsert 进入后台写队列（异步落库）; 同 (scope,key) 旧值被覆盖，"
            "不可恢复。"
            "能力边界: 只改记忆内容不碰任务历史; 新增语义用 memory_add，删除用 memory_delete，"
            "统计用 memory_stats。");
        s.write = true;
        auto en = [](const char* n) {
            ToolParamSpec p; p.name = QString::fromUtf8(n); p.type = "string"; p.required = true; return p;
        };
        s.params << en("scope") << en("scope_id") << en("key") << en("content");
        out.append(s);
    }
    // P3 #74: checkpoint-writer control plane. memory_add/update now enqueue;
    // these two introspect / explicitly drain.
    {
        ToolSchemaSpec s;
        s.name = "checkpoint_status";
        s.description = QString::fromUtf8(
            "只读：返回后台 checkpoint writer 状态——pending_count/queue_depth（待落库条目数）、"
            "last_flush_ms（最近一次落库完成的墙钟毫秒，0=从未落库）。"
            "主 agent 只读不写；写路径由 memory_add/update 入队后异步落库。");
        s.write = false;
        out.append(s);
    }
    {
        ToolSchemaSpec s;
        s.name = "memory_flush";
        s.description = QString::fromUtf8(
            "写入：同步排空记忆队列并落库（memory_store.add/update），重写 ~/.mbdsdr/checkpoint.md "
            "人类可读快照（时间戳+本次落库条数+key 列表），返回 written 实际落库条数。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: 无参数（任意时刻可调）; ~/.mbdsdr 目录可写; 队列里有待落库条目时才有意义。"
            "失败恢复: 部分条目落库失败时 ok:false 并附已处理条数与真实错误——修好磁盘/权限"
            "后重试; 队列为空时 written=0 正常返回（不是错误）。"
            "副作用: 真正写 SQLite 与 checkpoint.md 快照文件; 落库后 memory_search 立即可检索。"
            "能力边界: 只 flush 不删除; 队列深度用 checkpoint_status 读取; "
            "memory_add/update 的异步语义靠本工具兜底立即落库。");
        s.write = true;
        out.append(s);
    }

    // ---- 第 10 域「工作台编排」（AIGUI P0-b）-------------------------------
    // The live WorkspaceController seam: resize the three-column splitter and
    // show/hide right-panel tabs through the compile-time whitelist. Unknown
    // component ids become honest placeholders (never rendered). These tools do
    // NOT touch DSP / audio / hardware.

    // arrange_workspace (write -- gated): apply an A2UI v1.0 subset message.
    {
        ToolSchemaSpec s;
        s.name = "arrange_workspace";
        s.description = QString::fromUtf8(
            "写入：把一条 A2UI v1.0 子集布局消息应用到主界面三栏工作台——"
            "op 枚举 createSurface（整面重建）/updateComponents（在当前布局上叠加，默认）/"
            "deleteSurface（回默认）。components 为扁平邻接节点表，每节点 "
            "{id,type,props:{sizes:[L,M,R],stretch:[a,b,c],current,visible},children:[...]}；"
            "id 仅认编译期白名单（splitter/left_rail/center_spectrum/right_tabs/status_bar/"
            "tab:XXX），未知 id 进 placeholders 诚实空态、绝不渲染。"
            "sizes 软下限 120px clamp，默认 280/800/280 stretch 1/3/1。"
            "只动 splitter 与右侧 tab 可见性，不碰 DSP/音频/硬件。属于写动作，手动模式下被拦截。"
            "前置条件: WorkspaceController 已由 MainWindow 注入（headless 返回 "
            "CONTROLLER_NOT_INJECTED 诚实错误）; op 可选枚举三项; components 为 A2UI 节点数组。"
            "失败恢复: 控制器未注入时明确报错（不假装应用）; 未知组件 id 进 placeholders "
            "空态而非报错——用 list_ui_components 核对合法 id 后重发; 当前布局用 "
            "get_workspace_layout 读回。"
            "副作用: 实时改变主界面三栏尺寸与右侧 tab 可见性（用户可见）。"
            "能力边界: 只改布局不改数据（数据槽用 update_component_data）; "
            "回默认布局用 reset_workspace; 不碰 DSP/音频/硬件。");
        s.write = true;
        ToolParamSpec op;
        op.name = "op"; op.type = "string";
        op.description = QString::fromUtf8("布局语义：createSurface/updateComponents(默认)/deleteSurface");
        op.required = false;
        ToolParamSpec comps;
        comps.name = "components"; comps.type = "object";
        comps.description = QString::fromUtf8("A2UI 节点数组（untrusted：未知 id→placeholder，sizes clamp≥120）");
        comps.required = false;
        s.params << op << comps;
        out.append(s);
    }
    // get_workspace_layout (read): current model snapshot.
    {
        ToolSchemaSpec s;
        s.name = "get_workspace_layout";
        s.description = QString::fromUtf8(
            "只读：返回当前工作台布局模型——splitter_sizes[L,M,R]、splitter_stretch[a,b,c]、"
            "visible_tabs（tab:XXX 列表）、current_tab、hidden_panels、placeholders。"
            "默认三栏 280/800/280 stretch 1/3/1，14 个右侧 tab 全部可见。");
        s.write = false;
        out.append(s);
    }
    // list_ui_components (read): whitelist catalog.
    {
        ToolSchemaSpec s;
        s.name = "list_ui_components";
        s.description = QString::fromUtf8(
            "只读：返回编译期白名单组件目录——每项 {id,category,capabilities[],min_size}。"
            "共 23 项：splitter/left_rail/center_spectrum/right_tabs/status_bar + 14 个 tab:XXX "
            "+ 4 个 center:XXX。agent 编排工作台前先查此目录确认可引用的 id。");
        s.write = false;
        out.append(s);
    }
    // reset_workspace (write -- gated): back to the default three-column spec.
    {
        ToolSchemaSpec s;
        s.name = "reset_workspace";
        s.description = QString::fromUtf8(
            "写入：把工作台重置为默认三栏布局（280/800/280 stretch 1/3/1，14 个右侧 tab 全部可见），"
            "等价于 deleteSurface。只动 splitter 与 tab 可见性，不碰 DSP/音频/硬件。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: 无参数（任意时刻可调）; WorkspaceController 已由 MainWindow 注入。"
            "失败恢复: 控制器未注入时返回 CONTROLLER_NOT_INJECTED 诚实错误（不假装重置）——"
            "在桌面端主界面就绪后重试。"
            "副作用: 丢弃当前自定义布局（用户可见，无撤销接口），回到默认三栏。"
            "能力边界: 只重置布局; 自定义布局用 arrange_workspace; 当前布局读回用 "
            "get_workspace_layout; 不碰 DSP/音频/硬件。");
        s.write = true;
        out.append(s);
    }
    // update_component_data (write -- gated): A2UI updateDataModel minimal subset.
    {
        ToolSchemaSpec s;
        s.name = "update_component_data";
        s.description = QString::fromUtf8(
            "写入：A2UI v1.0 updateDataModel 最小子集——给一个编译期白名单组件 id 写入/清空"
            "内存数据槽。data 为 object 时整体替换该组件的数据（upsert）；data 为 null 时清空"
            "（删键）。未知组件 id 诚实报错、不写入。布局层不解释 payload、不做 @path 绑定、"
            "不碰 DSP/音频/硬件；数据可经 get_workspace_layout 的 component_data 字段读回。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: component_id 必填（编译期白名单 id，用 list_ui_components 核对）; "
            "data 必填且仅接受 object 或 null（键缺失也按缺失报错）; 控制器已注入。"
            "失败恢复: component_id/data 缺失时明确 ok:false——补齐后重试; "
            "未知组件 id 诚实报错不写入——用 list_ui_components 取合法 id。"
            "副作用: 整体替换该组件内存数据槽（旧值不可恢复）; null 清空删键。"
            "能力边界: 只写数据不改布局; 布局操作用 arrange_workspace/reset_workspace; "
            "读回用 get_workspace_layout。");
        s.write = true;
        ToolParamSpec cid;
        cid.name = "component_id"; cid.type = "string";
        cid.description = QString::fromUtf8("白名单组件 id（splitter/left_rail/center_spectrum/right_tabs/status_bar/tab:XXX/center:XXX）");
        cid.required = true;
        ToolParamSpec data;
        data.name = "data"; data.type = "object";
        data.description = QString::fromUtf8("object=整体替换数据槽；null=清空（删键）；其他类型拒绝");
        data.required = true;
        s.params << cid << data;
        out.append(s);
    }
    // get_aprs_messages (read): APRS/AX.25 messages decoded from a recovered
    // FSK bit stream (NRZI -> HDLC deframe + CRC-16/X.25 -> AX.25 decode).
    // Honest empty when nothing decoded / no feed.
    // Phase67.2 honesty: the description has always promised `limit` (1..50,
    // default 50) but the param was never declared -- the strict validator
    // rejects it as an unknown key. Declared with min/max like get_task_history.
    {
        ToolSchemaSpec s;
        s.name = "get_aprs_messages";
        s.description = QString::fromUtf8(
            "读取：最近解出的 APRS/AX.25 消息（最多 limit 条，1..50，默认 50）。"
            "每条含 time_ms/src/dst/path/info。源为 1200/2400 baud FSK 解调后的 NRZI "
            "位流经 HDLC 拆帧（CRC-16/X.25 校验）与 AX.25 解码；无效帧丢弃，无解码"
            "则诚实空态。只读工具。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径; limit 越界诚实 clamp 到 1..50，不报错。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 只返回最近 limit 条。");
        s.write = false;
        ToolParamSpec lim;
        lim.name = "limit"; lim.type = "number";
        lim.description = QString::fromUtf8("返回条数上限，取值 1..50，默认 50（越界由参数校验拒绝）");
        lim.required = false;
        lim.hasMin = true; lim.min = 1.0;
        lim.hasMax = true; lim.max = 50.0;
        s.params << lim;
        out.append(s);
    }

    // get_color_maps (read): built-in palettes + currently configured external
    // colormap file (validated live). Honest when none / file gone / file bad.
    {
        ToolSchemaSpec s;
        s.name = "get_color_maps";
        s.description = QString::fromUtf8(
            "读取：列出瀑布图可用色板——内置 3 个（经典/单色/Viridis，palette 0/1/2）"
            "与当前配置的外部 JSON 色板文件（如有：path/name/stops）。外部文件缺失或"
            "已损坏时 external 为 null 并附诚实 note，不编造。只读工具。");
        s.write = false;
        out.append(s);
    }

    // list_templates (read): enumerate the three deterministic task templates.
    // Pure metadata -- no engine, no station data. Always the same three.
    {
        ToolSchemaSpec s;
        s.name = "list_templates";
        s.description = QString::fromUtf8(
            "读取：列出内置确定性任务模板目录——每项 {name, description, params[], steps}。"
            "共 3 个：sweep_find_record（扫频找信号并存档，参数 low_hz/high_hz/step_hz/mode/book_name，6 步）、"
            "target_capture（目标频率捕获录制，参数 target_hz/mode，4 步）、"
            "fixed_freq_record（固定频率录制解码，参数 freq_hz/mode/bandwidth_hz，5 步）。"
            "模板机制来自内部既有实现，不预置场景/呼号/TLE。只读工具，手动模式不拦截。");
        s.write = false;
        out.append(s);
    }
    // run_template (write -- gated): build a deterministic plan from one of the
    // three templates and return the registered step sequence. Reuses the internal
    // plan* builders (no reimplementation); does NOT synchronously tune/record in
    // this call -- production execution goes through the async TaskRunner. Classified
    // as a write because the plan WOULD mutate the receiver, so manual mode gates it.
    {
        ToolSchemaSpec s;
        s.name = "run_template";
        s.description = QString::fromUtf8(
            "写入：按 template_name 构建一个确定性任务计划并登记返回其步骤序列。"
            "template_name 枚举 sweep_find_record/target_capture/fixed_freq_record（先用 list_templates 查看）。"
            "参数槽：sweep_find_record 需 low_hz/high_hz/step_hz/mode/book_name；"
            "target_capture 需 target_hz/mode；fixed_freq_record 需 freq_hz/mode/bandwidth_hz。"
            "未知 template_name 或缺参数诚实报错；本调用只构建/登记计划（executed=false），"
            "不在此同步调谐或录制——真实异步执行在生产端经 TaskRunner。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: template_name 必填枚举三项之一; 按所选模板提供其参数槽"
            "（三类模板参数键不同，缺任一即明确报错点名）。"
            "失败恢复: 模板名未知时错误附合法清单; 缺参数时按项点名——补齐后重试; "
            "plan 只登记不执行，取消/重来直接重新提交。"
            "副作用: 向 PendingPlanStore 真实登记一个待执行计划（返回 pending_plan_id），"
            "后续经 AI 面板「执行待执行计划」按钮或 TaskRunner 异步执行后才真正调谐/录制。"
            "能力边界: 不同步执行任何硬件动作（executed=false 是诚实标注）; "
            "进度用 get_task_history 的 pending_plans 查询; 模板目录用 list_templates。");
        s.write = true;
        ToolParamSpec tname;
        tname.name = "template_name";
        tname.type = "string";
        tname.description = QString::fromUtf8("模板名枚举：sweep_find_record/target_capture/fixed_freq_record");
        tname.enumValues = QVariantList{"sweep_find_record", "target_capture", "fixed_freq_record"};
        tname.required = true;
        ToolParamSpec lowHz;
        lowHz.name = "low_hz"; lowHz.type = "number";
        lowHz.description = QString::fromUtf8("sweep_find_record：扫描下限 Hz");
        ToolParamSpec highHz;
        highHz.name = "high_hz"; highHz.type = "number";
        highHz.description = QString::fromUtf8("sweep_find_record：扫描上限 Hz");
        ToolParamSpec stepHz;
        stepHz.name = "step_hz"; stepHz.type = "number";
        stepHz.description = QString::fromUtf8("sweep_find_record：扫描步进 Hz");
        ToolParamSpec mode;
        mode.name = "mode"; mode.type = "string";
        mode.enumValues = QVariantList{"AM", "NFM", "WFM", "USB", "LSB", "CW"};
        mode.description = QString::fromUtf8("解调模式");
        ToolParamSpec bookName;
        bookName.name = "book_name"; bookName.type = "string";
        bookName.description = QString::fromUtf8("sweep_find_record：命中书签名");
        ToolParamSpec targetHz;
        targetHz.name = "target_hz"; targetHz.type = "number";
        targetHz.description = QString::fromUtf8("target_capture：目标频率 Hz（必填）");
        ToolParamSpec freqHz;
        freqHz.name = "freq_hz"; freqHz.type = "number";
        freqHz.description = QString::fromUtf8("fixed_freq_record：固定频率 Hz（必填）");
        ToolParamSpec bwHz;
        bwHz.name = "bandwidth_hz"; bwHz.type = "number";
        bwHz.description = QString::fromUtf8("fixed_freq_record：信道带宽 Hz");
        s.params << tname << lowHz << highHz << stepHz << mode << bookName
                 << targetHz << freqHz << bwHz;
        out.append(s);
    }

    // G1: task-run history (read-only). TaskRunner 完成计划后落一行到
    // ~/.mbdsdr/memory.db 的 task_runs 表；本工具是它唯一的只读读面。
    {
        ToolSchemaSpec s;
        s.name = "get_task_history";
        s.description = QString::fromUtf8(
            "只读：查询 TaskRunner 已完成任务的历史（落于本地记忆库 task_runs 表，跨重启保留）。"
            "列表模式（默认）按完成时间倒序返回最近 limit 条（1..50 默认 10），"
            "每条含 id/name/state（succeeded/failed/aborted/gated/partial）/finished_ms/step_count/summary；"
            "传 id 进入详情模式，额外返回逐步 steps 数组（tool/state/summary/elapsed_ms/error）。"
            "无记录时 runs=[] 并附诚实 note，不编造历史；id 不存在时 ok:false 并给提示。");
        s.write = false;
        ToolParamSpec lim;
        lim.name = "limit"; lim.type = "number";
        lim.description = QString::fromUtf8("列表模式返回条数上限，取值 1..50，默认 10（越界由参数校验拒绝）");
        lim.required = false;
        lim.hasMin = true; lim.min = 1.0;
        lim.hasMax = true; lim.max = 50.0;
        ToolParamSpec idp;
        idp.name = "id"; idp.type = "number";
        idp.description = QString::fromUtf8("可选：任务记录 id（来自列表模式）；传入则返回该条详情（含 steps 数组）");
        idp.required = false;
        s.params << lim << idp;
        out.append(s);
    }
    // G2: memory_delete (gated write) + memory_stats (read-only).
    {
        ToolSchemaSpec s;
        s.name = "memory_delete";
        s.description = QString::fromUtf8(
            "写入：按 (scope,key) 删除一条本地记忆——同时删 mem_meta 行与其 FTS 索引行。"
            "scope 必填枚举 user/agent/session；key 必填。同步短连接执行："
            "条目存在返回 deleted=true；不存在返回 deleted=false 并附 note（空操作，不编造已删）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: scope（枚举三项）与 key 均必填; ~/.mbdsdr 记忆库可打开。"
            "失败恢复: scope/key 缺失时明确 ok:false——补齐后重试; 库不可用（权限/损坏）时"
            "ok:false 并附真实错误，提示检查 ~/.mbdsdr; 条目不存在时 ok:true 但 "
            "deleted=false（空操作，不要当成功删除复述）。"
            "副作用: 真删 SQLite 行与 FTS 索引（不可恢复）; 同步执行无需 flush。"
            "能力边界: 只删 (scope,key) 精确匹配的一条; 检索用 memory_search，"
            "清空全库无此接口（按 key 逐条删）。");
        s.write = true;
        ToolParamSpec sc;
        sc.name = "scope"; sc.type = "string";
        sc.description = QString::fromUtf8("作用域枚举：user/agent/session");
        sc.enumValues = QVariantList{"user", "agent", "session"};
        sc.required = true;
        ToolParamSpec key;
        key.name = "key"; key.type = "string";
        key.description = QString::fromUtf8("scope 内唯一键");
        key.required = true;
        s.params << sc << key;
        out.append(s);
    }
    {
        ToolSchemaSpec s;
        s.name = "memory_stats";
        s.description = QString::fromUtf8(
            "只读：本地记忆库统计——total_entries（条目总数）、by_scope（user/agent/session 分布）、"
            "last_updated_ms（最近写入墙钟毫秒，0=空库）、task_runs（已完成任务数，G1 历史）。"
            "空库全 0 诚实返回，不编造数据。");
        s.write = false;
        out.append(s);
    }

    // list_satellites (read-only) ------------------------------------------
    // Read-only catalog of satellites present in the on-disk TLE cache. This is
    // the prerequisite for predict_passes: the model needs the available
    // satellite names before it can predict a pass. Uses ONLY the on-disk cache;
    // the stale offline builtinTle() (epoch 2006) is deliberately NOT listed.
    // Honest empty state when there is no cache. Kept out of the manual write gate.
    {
        ToolSchemaSpec s;
        s.name = "list_satellites";
        s.description = QString::fromUtf8(
            "只读：列出本地 TLE 缓存中的卫星目录（名、NORAD 编号、TLE 历元、龄期天数、是否新鲜、标称下行频率）。"
            "这是 predict_passes 的前置——先列出可用卫星再选名预测过境。"
            "无缓存时诚实返回空态（source=none），不使用陈旧内置演示数据，不编造卫星。");
        s.write = false;   // pure read of the on-disk TLE cache
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
