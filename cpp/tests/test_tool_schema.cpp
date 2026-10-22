// SPDX-License-Identifier: MIT
// M1 tool-schema generator test: fully deterministic and offline. No network,
// no API key, no hardware -- it only renders the declarative registry to JSON
// Schema and compares against the named constants in core/tokens.h and
// core/bandwidth_preset.h.
#include <QtTest/QtTest>

#include <QJsonObject>
#include <QJsonArray>
#include <QSet>
#include <algorithm>

#include "ai/tool_schema.h"
#include "ai/llm_client.h"
#include "core/tokens.h"
#include "core/bandwidth_preset.h"

using namespace mbdsdr;
using namespace mbdsdr::ai;

class TestToolSchema : public QObject {
    Q_OBJECT
private slots:
    void schemaShape();
    void tuneFrequencyBounds();
    void setModeEnum();
    void setBandwidthEnumIsPresetSet();
    void noArgToolsHaveEmptyProperties();
    void sevenToolsNameDescriptionMatch();
    void scanBandShape();
    void generateToolDocumentationCoversAllTools();
};

static const ToolSchemaSpec* findSpec(const QList<ToolSchemaSpec>& specs,
                                      const QString& name) {
    for (const ToolSchemaSpec& s : specs)
        if (s.name == name) return &s;
    return nullptr;
}

// Top-level shape: every rendered schema is an object with properties +
// required, and toolDefsFromSpecs() passes name/description verbatim.
void TestToolSchema::schemaShape() {
    QList<ToolSchemaSpec> specs = registeredToolSpecs();
    QCOMPARE(specs.size(), 90);
    for (const ToolSchemaSpec& s : specs) {
        QJsonObject j = buildToolSchema(s);
        QCOMPARE(j.value("type").toString(), QString("object"));
        QVERIFY(j.contains("properties"));
        QVERIFY(j.value("properties").isObject());
        QVERIFY(j.contains("required"));
        QVERIFY(j.value("required").isArray());
        QVERIFY(!s.description.trimmed().isEmpty());
    }
    QList<ToolDef> defs = toolDefsFromSpecs(specs);
    QCOMPARE(defs.size(), specs.size());
    for (int i = 0; i < defs.size(); ++i) {
        QCOMPARE(defs[i].name, specs[i].name);
        QCOMPARE(defs[i].description, specs[i].description);
        QCOMPARE(defs[i].parameters.value("type").toString(), QString("object"));
    }
}

// tune_frequency: numeric bounds must equal the hardware tokens, not literals.
void TestToolSchema::tuneFrequencyBounds() {
    QList<ToolSchemaSpec> specs = registeredToolSpecs();
    const ToolSchemaSpec* s = findSpec(specs, "tune_frequency");
    QVERIFY(s);
    QJsonObject f = buildToolSchema(*s)
                         .value("properties").toObject()
                         .value("freq_hz").toObject();
    QCOMPARE(f.value("type").toString(), QString("number"));
    QVERIFY(f.contains("minimum"));
    QVERIFY(f.contains("maximum"));
    QCOMPARE(f.value("minimum").toDouble(), tokens::kFreqMinHz);
    QCOMPARE(f.value("maximum").toDouble(), tokens::kFreqMaxHz);
    QJsonArray req = buildToolSchema(*s).value("required").toArray();
    QCOMPARE(req.size(), 1);
    QCOMPARE(req.at(0).toString(), QString("freq_hz"));
}

// set_mode: enum is the exact 6-mode set, mode required.
void TestToolSchema::setModeEnum() {
    QList<ToolSchemaSpec> specs = registeredToolSpecs();
    const ToolSchemaSpec* s = findSpec(specs, "set_mode");
    QVERIFY(s);
    QJsonObject m = buildToolSchema(*s)
                         .value("properties").toObject()
                         .value("mode").toObject();
    QCOMPARE(m.value("type").toString(), QString("string"));
    QSet<QString> got;
    for (const QJsonValue& v : m.value("enum").toArray()) got.insert(v.toString());
    QCOMPARE(got, QSet<QString>({"AM", "NFM", "WFM", "USB", "LSB", "CW"}));
    QCOMPARE(buildToolSchema(*s).value("required").toArray().at(0).toString(),
             QString("mode"));
}

// set_bandwidth: enum equals the full named-preset set (kBwFallbackHz excluded
// as it duplicates kBwNfmHz).
void TestToolSchema::setBandwidthEnumIsPresetSet() {
    QList<ToolSchemaSpec> specs = registeredToolSpecs();
    const ToolSchemaSpec* s = findSpec(specs, "set_bandwidth");
    QVERIFY(s);
    QJsonObject b = buildToolSchema(*s)
                        .value("properties").toObject()
                        .value("bandwidth_hz").toObject();
    QList<double> got;
    for (const QJsonValue& v : b.value("enum").toArray()) got.append(v.toDouble());
    std::sort(got.begin(), got.end());
    QList<double> want{
        core::kBwCwHz, core::kBwSsbHz, core::kBwAmHz, core::kBwDigitalHz,
        core::kBwNfmHz, core::kBwWfmHz, core::kBwAdsbHz
    };
    std::sort(want.begin(), want.end());
    QCOMPARE(got, want);
    QCOMPARE(buildToolSchema(*s).value("required").toArray().at(0).toString(),
             QString("bandwidth_hz"));
}

// No-argument tools: properties is an empty object and required is empty.
void TestToolSchema::noArgToolsHaveEmptyProperties() {
    QList<ToolSchemaSpec> specs = registeredToolSpecs();
    const char* names[] = {"start_recording", "stop_recording", "get_status"};
    for (const char* n : names) {
        const ToolSchemaSpec* s = findSpec(specs, QString::fromLatin1(n));
        QVERIFY2(s, n);
        QJsonObject j = buildToolSchema(*s);
        QVERIFY2(j.value("properties").toObject().isEmpty(), n);
        QVERIFY2(j.value("required").toArray().isEmpty(), n);
    }
}

// The tool names + descriptions are verbatim copies of src/ai/agent_tools.cpp
// toolDefs(); drifting them would break UI copy / golden expectations.
void TestToolSchema::sevenToolsNameDescriptionMatch() {
    QList<ToolDef> defs = toolDefsFromSpecs(registeredToolSpecs());
    QList<QPair<QString, QString>> expected = {
        {"tune_frequency", QString::fromUtf8(
            "写入：把接收机中心频率调谐到指定 Hz（单/主 VFO）。属于写动作，手动模式下被拦截。"
            "前置条件: freq_hz 必须提供且为正的有限数（Hz）；合法范围由 schema 边界"
            "（tokens::kFreqMinHz..kFreqMaxHz）在参数校验层强制。"
            "失败恢复: 非正/非有限频率被明确拒绝（不会假报成功也不会静默丢弃）——"
            "改正数值后重试; 多 VFO 场景请改用 set_vfo_frequency（index 或 id 定位目标 VFO）。"
            "副作用: 立即改变中心频率（硬件状态），音频与瀑布显示随之变化。"
            "能力边界: 只调主 VFO 中心频率; 不改模式/带宽，也不读回状态"
            "（读回用 get_status）。")},
        {"set_mode", "Set demodulation mode."},
        {"start_recording", "Start recording raw IQ to SigMF file."},
        {"stop_recording", "Stop recording."},
        {"scan_band", "Scan a frequency band and return the peak signal."},
        {"set_bandwidth", "Set channel filter bandwidth in Hz."},
        {"get_status",
         "Return current receiver state: frequency, mode, bandwidth, sample rate."},
        {"predict_passes",
         QString::fromUtf8(
            "只读：用本地新鲜 TLE 缓存预测指定卫星未来的过境（升/降时刻、最高仰角、起止方位）。"
            "无新鲜 TLE 时诚实返回空态，不使用陈旧内置数据。")},
        {"calibrate_frequency",
         QString::fromUtf8(
            "只读测量：用一段已知精确频率的参考信号估计本机晶振 ppm 误差。"
            "不修改任何设置。参考源：handheld=手台在已知频点按 PTT 发射；"
            "gsm_fcch=GSM FCCH 精确纯音；manual=任意已知精确频率。"
            "未检测到参考载波时诚实返回 detected=false，不编造 ppm。")},
        {"apply_frequency_correction",
         QString::fromUtf8(
            "写入并应用频率校正 ppm（通常取 calibrate_frequency 的 measured_ppm）。"
            "会保存到设置并下发给接收机；属于写动作，手动模式下被拦截。")},
        {"get_pocsag_messages",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 POCSAG 寻呼消息快照"
            "（地址 RIC/功能位/文本）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表，不编造消息。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 信道选择不改调谐状态。")},
        {"get_m17_calls",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 M17 呼叫/帧快照"
            "（源/目的呼号、类型、CRC 状态、语音帧诚实标注未解码）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 语音帧未解码时显式标注。")},
        {"get_vor_radial",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）VOR 信道最新径向读数"
            "（radialDeg 方位、质量、莫尔斯识别码、锁定态）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——未锁定时 locked=false，方位不可信并被显式标注，"
            "不编造方位。"
            "能力边界: 只读引擎最新读数; 方位仅锁定后可信。")},
        {"get_acars_packets",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 ACARS 航空报文快照"
            "（方向 air/ground、label、block id、ack、正文、CRC 结果）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表，不编造报文。"
            "能力边界: 只读引擎已解出的快照，不触发解调; CRC 失败帧显式标注。")},
        {"get_navtex_messages",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 NAVTEX 海上安全报文快照"
            "（发台 B1、类型 B2、编号、正文、时间分集/定相状态）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表，不编造报文。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 分集/定相失败在条目内显式标注。")},
        {"export_iq_segment",
         QString::fromUtf8(
            "写入（一次性）：立即抓取一段当前中心频率的基带 IQ 复样本并导出为 "
            "cf32_le SigMF 文件（.sigmf-data + .sigmf-meta），返回真实路径与样本数。"
            "区别于 start_recording 的连续录制：这是按需导出一个有界窗口后即返回。"
            "无 IQ 数据时诚实报错，不生成空文件。属于写动作，手动模式下被拦截。")},
        // Phase26: 21 new tools, verbatim description pairs (must match
        // tool_schema.cpp registeredToolSpecs() exactly, in on-wire order).
        {"set_network_audio_sink",
         QString::fromUtf8(
            "写入：配置网络音频流输出（UDP/TCP 镜像当前解调音频）。"
            "enable 开关、port 端口、format 协议；可选 host 与 stereo。"
            "属于写动作，手动模式下被拦截。")},
        {"get_network_audio_status",
         QString::fromUtf8(
            "只读：返回网络音频流状态（是否使能、端口、格式）。"
            "无状态时诚实返回 enabled=false，不编造端口。"
            "前置条件: 无。"
            "能力边界: 当前 Agent 层无网络音频流读数（后端由 ControlHub 提供），"
            "enabled=false 是诚实空态而非故障——不要据此声称网络音频损坏。")},
        {"start_scan_link",
         QString::fromUtf8(
            "写入：启动扫描活动链路（扫描→命中→驻留→解码→录制），目标频率 target_freq_hz。"
            "属于写动作，手动模式下被拦截。")},
        {"stop_scan_link",
         QString::fromUtf8(
            "写入：停止扫描活动链路。属于写动作，手动模式下被拦截。"
            "前置条件: 无（无参数，任意时刻可调）。"
            "失败恢复: 指令经 ControlHub 路由执行; 链路未运行时停止为空操作，"
            "返回成功而非报错——不要反复重试。"
            "副作用: 停止当前进行中的扫描活动链路（若有）。"
            "能力边界: 只发停止指令; 扫描链路真实运行态在 ControlHub 侧，"
            "用 get_scan_link_status 读取（Agent 层无链路实例时为空态读数）。")},
        {"get_scan_link_status",
         QString::fromUtf8(
            "只读：返回扫描活动链路状态（scanning/dwelling/hit）。"
            "未运行时诚实返回 scanning=false、无命中，不编造。")},
        {"set_squelch",
         QString::fromUtf8(
            "写入：设置静噪（enabled 开关、threshold_db 门限、auto 自动链路）。"
            "属于写动作，手动模式下被拦截。")},
        {"get_squelch_status",
         QString::fromUtf8(
            "只读：返回静噪状态（enabled/threshold_db/auto/当前是否 open）。"
            "无实时门限读数时诚实标注，不编造。")},
        {"set_ctcss",
         QString::fromUtf8(
            "写入：设置 CTCSS 亚音（enabled 开关、frequency_hz 亚音频率 67.0–254.1 Hz、"
            "可选 gate_audio 亚音门控静音开关）。"
            "属于写动作，手动模式下被拦截；越界频率诚实拒绝，不静默钳位。")},
        {"get_ctcss_status",
         QString::fromUtf8(
            "只读：返回 CTCSS 亚音状态（enabled 是否使能、frequency_hz 调谐频率、"
            "active 是否真实检测到亚音、gate_audio 是否开启亚音门控静音）。"
            "无信号/未使能时 active 诚实为 false，不编造。")},
        {"set_cdcss",
         QString::fromUtf8(
            "写入：设置 CDCSS/DCS 数字亚音（enabled 开关、code 三位八进制 DCS 码 "
            "\"023\"–\"754\"、可选 gate_audio 数字亚音门控静音开关）。"
            "属于写动作，手动模式下被拦截；非表内 DCS 码诚实拒绝，不静默接受。")},
        {"get_cdcss_status",
         QString::fromUtf8(
            "只读：返回 CDCSS/DCS 数字亚音状态（enabled 是否使能、code 调谐 DCS 码、"
            "active 是否真实检测到匹配码、gate_audio 是否开启数字亚音门控静音）。"
            "无信号/未使能时 active 诚实为 false，不编造。")},
        // Phase63 step3: FT8 detection-layer tools (must match tool_schema.cpp order).
        {"set_ft8",
         QString::fromUtf8(
            "写入：开关 FT8 数字模式检测层（enabled 布尔，必填）。属于写动作，"
            "手动模式下被拦截。本轮为检测层（Costas 同步 + 候选帧统计），"
            "C++ BP 解码留后续，不编造解码消息。")},
        {"get_ft8_status",
         QString::fromUtf8(
            "只读：返回 FT8 检测层状态（enabled 是否使能、active 是否真实检出候选帧、"
            "freq_offset_hz 估计频偏、sync_quality 相关峰/次峰比、candidate_count 候选数）。"
            "无信号/未使能时 active 诚实为 false，不编造。")},
        {"set_lrpt",
         QString::fromUtf8(
            "写入：开关 LRPT 卫星云图接收层（enabled 布尔，必填）。属于写动作，"
            "手动模式下被拦截。本轮为状态控制层（C++ 解调/FEC 移植留后续第④轮），"
            "不编造图像或帧。")},
        {"get_lrpt_status",
         QString::fromUtf8(
            "只读：返回 LRPT 接收层状态（enabled 是否使能、sync_locked 是否已锁定位同步、"
            "decoded_frames 已解码行数）。C++ 解码器未移植前 sync_locked 诚实为 false、"
            "decoded_frames 为 0，不编造。")},
        {"set_vna_sweep",
         QString::fromUtf8(
            "写入：设置 NanoVNA 扫频范围（start_hz/stop_hz/points，均必填；stop 必须大于 start，"
            "points 为正整数）。宽扫建议 ±5~10%，窄扫 ±1%（经验范围）。"
            "属于写动作，手动模式下被拦截。无设备时参数仍校验，但不下发（applied=false），"
            "不伪造连接。")},
        {"get_vna_data",
         QString::fromUtf8(
            "只读：返回当前扫频测量数据（frequencies、S11/S21 复数对及派生 VSWR/回波损耗/阻抗）。"
            "区别：本工具返回测量曲线数据；get_vna_status 仅返回连接/校准/扫描参数状态。"
            "无设备/未取数时各数组诚实为空，不编造曲线。")},
        {"get_vna_status",
         QString::fromUtf8(
            "只读：返回 NanoVNA 状态（connected 是否已连接、model 型号、version 固件、cal 已置位校准项、"
            "当前 sweep 范围与点数）。未连接时 connected 诚实为 false，字段为空。")},
        {"analyze_vna_resonance",
         QString::fromUtf8(
            "只读：对当前扫频 S11 做谐振分析（串联谐振 fr、并联谐振 fp、ESR、-3dB 带宽、有载 Q）。"
            "无设备/点数不足/span=0 时 valid 诚实为 false，不编造。")},
        {"vna_tdr_cable",
         QString::fromUtf8(
            "写入：对当前扫频 S11 做 TDR 时域反射（velocity_factor 速度因子，0<vf<=1，必填），"
            "返回首反射峰距离与电缆长度（馈线好坏/电缆故障/进水/接头虚接）。"
            "属于写动作，手动模式下被拦截。无设备/无峰时 valid 诚实为 false。")},
        {"analyze_vna_filter",
         QString::fromUtf8(
            "只读：对当前扫频 S21 幅度做滤波器分析（自动识别 bandpass/bandstop/highpass/lowpass，"
            "输出 -3dB 截止边、带宽、通带插损、阻带衰减）。无设备/幅度平坦非滤波器形态时 valid 诚实为 false。")},
        {"analyze_vna_equivalent",
         QString::fromUtf8(
            "只读：由当前扫频 S11 提取串联 RLC 等效电路（ESR=串联谐振点 |Z|，Q=fr/BW，L/C 由谐振关系推出）。"
            "无设备/点数不足时 valid 诚实为 false。")},
        {"analyze_vna_touchstone",
         QString::fromUtf8(
            "只读：解析 Touchstone s1p/s2p 文件（content 文本 或 path 路径），返回频率表与 S11/S21 实虚部数据集，"
            "可复用谐振/滤波器分析。仅 R/I 格式；不可解析时 ok:false 诚实拒绝。")},
        {"export_vna_snapshot",
         QString::fromUtf8(
            "写入：把当前 NanoVNA 扫频（Smith 轨迹）离屏渲染为 PNG 存到 path。属于写动作，手动模式下被拦截。"
            "未连接/无数据时 ok:false 诚实拒绝。")},
        {"export_spectrum_snapshot",
         QString::fromUtf8(
            "写入：把当前 SDR 频谱面板（频率轴 + 中心频点标记）离屏渲染为 PNG 存到 path。属于写动作，手动模式下被拦截。"
            "未初始化/路径不可写时 ok:false 诚实拒绝。")},
        {"export_peaks_csv",
         QString::fromUtf8(
            "写入：把当前频谱峰值列表（freq_hz,db）按强度降序写成 CSV 存到 path。属于写动作，手动模式下被拦截。"
            "无峰值/路径不可写时 ok:false 诚实拒绝。")},
        {"export_ft8_results",
         QString::fromUtf8(
            "写入：把当前 FT8 解码结果（含 SIC 分离）导出为文本/CSV 存到 path。属于写动作，手动模式下被拦截。"
            "默认（all_history=false）导出引擎候选列表（freq_hz,crc_ok,costas_score,rejected,reject_reason,iters）；"
            "all_history=true 时导出解码历史全量（表头 time_ms,freq_hz,demod,text），无历史 rows=0 诚实空态。"
            "无解码结果时 rows=0 诚实空态，不伪造呼号/经纬度。")},
        {"analyze_vna_match",
         QString::fromUtf8(
            "只读：给定实测负载阻抗 load_r(+load_x) 与设计频率 f_hz，返回匹配网络的 L/C 建议"
            "（Q、串联/并联臂值）（驻波下不来/做匹配/加感）。"
            "topology 可选：series_shunt（默认，既有 EFHW/反 L 语义）/xps（shunt 并联后 series 串联）"
            "/xs（series 串联后 shunt 并联）/xpl（shunt-series-shunt 三电抗 Pi）；"
            "r_comp 可选，串联 R 补偿（≥0，并入负载电阻）。"
            "无闭式解的拓扑诚实 valid:false，不伪造数值。")},
        {"set_noise_blanker",
         QString::fromUtf8(
            "写入：开关噪声抑制器（on 布尔）。属于写动作，手动模式下被拦截。"
            "前置条件: on 必须提供且为布尔值。"
            "失败恢复: on 缺失或非布尔时返回明确错误（绝不静默取默认值）——"
            "补齐布尔值后重试。"
            "副作用: 立即改变解调链路的噪声抑制状态。"
            "能力边界: 仅开关，不含门限/强度参数; 状态回读用 get_noise_blanker_status。")},
        {"get_noise_blanker_status",
         QString::fromUtf8(
            "只读：返回噪声抑制器状态（enabled 是否使能）。"
            "读取引擎真实开关，不编造。"
            "前置条件: 无（未连接设备也可读当前开关配置）。"
            "能力边界: 只读，不改变任何状态; 开关操作用 set_noise_blanker。")},
        {"list_bookmarks",
         QString::fromUtf8(
            "只读：列出书签（频率/名称/模式）。无书签时诚实返回空列表，不编造。"
            "前置条件: 无。"
            "能力边界: Agent 层未注入书签管理器时返回空列表+note（诚实空态），"
            "真实书签数据在 control 层——空列表不等于没有书签，不要虚构条目。")},
        {"add_bookmark",
         QString::fromUtf8(
            "写入：添加书签（freq_hz/name/mode/bandwidth_hz/group）。属于写动作，手动模式下被拦截。")},
        {"tune_to_bookmark",
         QString::fromUtf8(
            "写入：调谐到指定下标书签的频率。属于写动作，手动模式下被拦截。"
            "前置条件: index 必须是 0 起整数下标，且书签管理器已注入。"
            "失败恢复: index 越界时错误信息含真实书签总数（如「书签 index 5 越界"
            "（共 3 条）」）——按总数改下标重试; 管理器未注入时返回明确错误而非假装调谐。"
            "副作用: 改变当前选中 VFO 的调谐频率（硬件状态）。"
            "能力边界: 只调谐到书签频率，不改书签本身; 书签列表用 list_bookmarks 读取。")},
        {"delete_bookmark",
         QString::fromUtf8(
            "写入：删除指定下标书签。属于写动作，手动模式下被拦截。"
            "前置条件: index 必须是 0 起整数下标，且书签管理器已注入。"
            "失败恢复: index 越界时错误信息含真实书签总数——按总数改下标重试。"
            "副作用: 真删除书签（无回收站/无撤销接口），返回值 remaining 为删除后剩余数。"
            "能力边界: 只能删已注入管理器的书签; 不确定下标时先用 tune_to_bookmark "
            "的越界错误探测总数，或让用户确认。")},
        {"list_vfos",
         QString::fromUtf8(
            "只读：列出全部 VFO 信道（id/频率/带宽/模式/选中态）。"
            "前置条件: 无。"
            "能力边界: 返回引擎真实 VFO 标记，空列表即当前无 VFO; "
            "新增/切换/改参分别用 add_vfo / switch_vfo / set_vfo_* 系列。")},
        {"add_vfo",
         QString::fromUtf8(
            "写入：新增一个 VFO 信道。属于写动作，手动模式下被拦截。"
            "前置条件: 无（无参数，任意时刻可调）。"
            "失败恢复: 无失败路径——新增必定成功并回显新信道 id。"
            "副作用: 在引擎新增一个 VFO 信道（不自动切换选中，不改变当前调谐）。"
            "能力边界: 只增不选; 返回 selected_vfo_id 供后续 switch_vfo 参考。")},
        {"switch_vfo",
         QString::fromUtf8(
            "写入：切换选中的 VFO 信道（index）。属于写动作，手动模式下被拦截。"
            "前置条件: index 必须提供且为数字; 有效下标范围用 list_vfos 读取。"
            "失败恢复: index 缺失或非数字时返回明确错误——先 list_vfos 拿真实 id 再重试。"
            "副作用: 切换选中 VFO，解调输出与调谐目标随之改变。"
            "能力边界: 只切换不新增; 选错信道不会丢数据，再次 switch_vfo 即可。")},
        {"rename_vfo",
         QString::fromUtf8(
            "写入：重命名指定 VFO 信道（index/name）。属于写动作，手动模式下被拦截。"
            "前置条件: index（数字）与 name（字符串）都必须提供。"
            "失败恢复: 任一缺失或类型不符时返回明确错误——补齐后重试。"
            "副作用: 重命名经 control 层路由生效; 不影响频率/带宽/模式。"
            "能力边界: 只改显示名; 频率/模式/带宽用 set_vfo_* 系列。")},
        // Phase59: armed parallel VFO monitoring, in registeredToolSpecs order.
        {"set_vfo_armed",
         QString::fromUtf8(
            "写入：开启/关闭某 VFO 的后台并行解调（index/enabled）。开启后即使该 VFO "
            "未被选中，仍会在每个数据块被解调，便于并行监听/录制；关闭则停止以节省 CPU。"
            "属于写动作，手动模式下被拦截。")},
        // Phase61: VFO fine-grained edit tools (must match tool_schema.cpp order).
        {"set_vfo_frequency",
         QString::fromUtf8(
            "写入：把指定 VFO 调谐到新频率（index 或 id 二选一 + freq_hz）。"
            "前置条件: index（list_vfos 返回的序号）或 id（VFO 直传 id）必须提供其一；"
            "freq_hz 为目标频率（Hz，必填）。"
            "失败恢复: 两者都缺或类型不符时返回明确参数错误；index 越界时错误附当前"
            "VFO 总数——用 list_vfos 重新取序号/id 后重试。"
            "副作用: 该 VFO 的解调频率立即改变，音频与瀑布显示随之变化；不影响其他 "
            "VFO，也不改变模式/带宽。"
            "能力边界: 只调谐已存在的 VFO（不新增信道）；频率合法性由引擎与硬件决定。"
            "属于写动作，手动模式下被拦截。")},
        {"set_vfo_mode",
         QString::fromUtf8(
            "写入：切换指定 VFO 的解调模式（index 或 id 二选一 + mode）。"
            "前置条件: index 或 id 必须提供其一；mode 取值见 ControlHub 模式表："
            "AM/NFM/WFM/USB/LSB/CW/POCSAG/m17/VOR/ACARS/NAVTEX（大小写不敏感）。"
            "失败恢复: 目标缺失/越界或 mode 不在表中时返回明确错误（越界附 VFO 总数）——"
            "用 list_vfos 核对后重试。"
            "副作用: 该 VFO 的解调模式立即切换，音频输出随之改变。"
            "能力边界: 只改已存在 VFO 的模式; 不影响其他 VFO。"
            "属于写动作，手动模式下被拦截。")},
        {"set_vfo_bandwidth",
         QString::fromUtf8(
            "写入：设置指定 VFO 的信道带宽（index 或 id 二选一 + bandwidth_hz）。"
            "前置条件: index 或 id 必须提供其一；bandwidth_hz 必须为正数（Hz）。"
            "失败恢复: 目标缺失/越界或带宽非正时返回明确错误（越界附 VFO 总数）——"
            "补齐后重试。"
            "副作用: 该 VFO 的滤波器带宽立即改变，音频随之变化。"
            "能力边界: 只改已存在 VFO 的带宽; 可用范围由硬件/引擎决定。"
            "属于写动作，手动模式下被拦截。")},
        {"list_recordings",
         QString::fromUtf8(
            "只读：扫描录制目录并列出已有录制文件。"
            "前置条件: 无（任意时刻可调；目录路径由引擎配置决定，随结果 dir 字段透传）。"
            "失败恢复: 无失败路径——目录不存在或为空时诚实返回空列表（count=0），"
            "不编造文件。"
            "能力边界: 只列文件不读取内容; 每条含 name/bytes/modified; "
            "删除/导出分别用 delete_recording / export_recording。")},
        {"delete_recording",
         QString::fromUtf8(
            "写入：删除录制目录下指定名称的文件。属于写动作，手动模式下被拦截。"
            "前置条件: name 必须是录制目录内的纯文件名（不得含 / 或 \\，不得为 . / ..）——"
            "可用 list_recordings 先取真实文件名。"
            "失败恢复: name 含路径分隔符时明确拒绝（防目录穿越）；文件不存在时返回"
            "明确错误——核对 list_recordings 后重试; 删除本身失败也会明确报错。"
            "副作用: 真删除磁盘文件（无回收站/无撤销接口），不可恢复。"
            "能力边界: 只能删录制目录内的文件; 导出而非删除用 export_recording。")},
        {"export_recording",
         QString::fromUtf8(
            "写入：把录制目录下指定文件复制导出到 out_path。属于写动作，手动模式下被拦截。"
            "前置条件: name（录制目录内纯文件名，不得含路径）与 out_path（目标完整路径）"
            "都必须提供; 文件名可用 list_recordings 核对。"
            "失败恢复: name 含路径分隔符时明确拒绝; 源文件不存在时返回明确错误; "
            "目标已存在或不可写时返回「导出失败」——换目标路径后重试。"
            "副作用: 在 out_path 生成一份副本; 不改动/删除源文件。"
            "能力边界: 只导出录制目录内的文件; 复制失败不产生半成品承诺（QFile::copy 语义）。")},
        {"set_fft_params",
         QString::fromUtf8(
            "写入：设置频谱 FFT 参数（fft_size/window/average）。"
            "window/average 使用字符串枚举（Hann/Flattop/Blackman、Off/Slow/Fast）。"
            "属于写动作，手动模式下被拦截。"
            "能力边界: 模型侧只接受字符串枚举——原始整数 0/1/2 是 ControlHub/HTTP "
            "内部通道键，经 LLM/MCP 校验路径传入会被类型校验拒绝。")},
        {"set_color_map",
         QString::fromUtf8(
            "写入：保存瀑布图色板文件路径到设置（headless 仅持久化偏好，重绘由 UI 持有）。"
            "属于写动作，手动模式下被拦截。")},
        {"get_spectrum_status",
         QString::fromUtf8(
            "只读：返回频谱当前 FFT 参数真实值（fft_size/window/average）。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——值直接读引擎，不编造; 与预期不符时以本工具读回为准。"
            "能力边界: 只读不改; 修改 FFT 参数用 set_fft_params。")},
        // Phase55/58: two appended tools (must match tool_schema.cpp on-wire order).
        {"set_doppler_compensation",
         QString::fromUtf8(
            "写入：开关过境实时多普勒自动补偿（1Hz TLE 距离率重调 VFO）。"
            "需已设置本站位置并捕获一个过境，否则保持关闭（诚实拒绝）。"
            "属于写动作，手动模式下被拦截。")},
        {"connect_network_source",
         QString::fromUtf8(
            "写入：连接 rtl_tcp 网络接收机（host/port）。"
            "真实 TCP 握手+RTL0 设备头；失败返回真实 socket 原因并回到空态，不伪造 IQ。"
            "属于写动作，手动模式下被拦截。")},
        // Read-only capability/recording-state snapshot tools (must match
        // tool_schema.cpp on-wire order; appended last).
        {"get_capabilities",
         QString::fromUtf8(
            "只读：返回当前源的真实能力（设备名、可调谐频率范围、采样率范围、离散增益档 gains_db）。"
            "未连接或测试信号源时诚实返回 connected=false、空增益档数组与来源说明 provenance，"
            "不编造调谐范围或增益表。")},
        {"get_recording_state",
         QString::fromUtf8(
            "只读：返回录制状态（recording 是否在手动录制中、recording_path 当前路径、"
            "watch_enabled 值守录制是否使能、recording_dir 录制目录）。"
            "未录制时诚实返回 recording=false、空路径，不伪造。")},
        // P0 #59: single-tool NanoVNA calibration state machine (write, gated).
        {"vna_calibration",
         QString::fromUtf8(
            "写入：NanoVNA 校准单工具状态机。step 为校准/测量命令字（必填枚举）："
            "status=只读引导（返回客观下一步与已完成/缺失步骤）；"
            "reset/open/short/load/isolation/thru/done=校准写步骤；"
            "save/load=保存/加载校准集（需 slot 1..4）；scan/pause/resume=扫频控制。"
            "写动作走手动 gate、人在环逐步确认。接标准件到 CH0 / CH0-CH1 THRU 是仪器协议客观事实，"
            "编排权在调用者（模型），非固定剧本。未连接 ok:false 并给排查清单。")},
        // P1 #70: device-level misc facade state machine (write, gated).
        {"vna_device",
         QString::fromUtf8(
            "写入：NanoVNA 设备级杂项单工具状态机。action 为命令字（必填枚举）："
            "status=只读（连接/校准摘要/扫频范围/edelay/averaging/freq_offset 当前值，未读回的诚实给 0+note）；"
            "vbat=读电池电压；freq_offset 无 value=读本振频偏(Hz)、有 value=写频偏；"
            "edelay=写电延迟(ps)；averaging=写平均次数(1..64)。"
            "写动作走手动 gate、人在环确认。读动作客观返回当前值；编排权在调用者，非固定剧本。"
            "未连接 ok:false 并给排查清单。")},
        // P2 #71: FT8 decoded-message rolling history (read-only).
        {"get_ft8_history",
         QString::fromUtf8(
            "只读：返回最近 N 条 FT8 解码历史（WSJT-X 风格滚动窗口，按时间倒序），"
            "每条字段 time_ms（墙钟毫秒）/freq_hz（绝对 RF Hz）/demod（解调，如 FT8）/"
            "text（解码文本 FROM TO EXCHANGE）。无历史时 history=[] 诚实空态，不伪造。"
            "区别于 get_ft8_status（当前帧即时状态）与 export_ft8_results（导出文件）。")},
        // P2 #72: ADS-B aircraft snapshot (read-only).
        {"get_adsb_aircraft",
         QString::fromUtf8(
            "只读：返回最近 N 架已解码 ADS-B 飞机（按最后看到时间倒序），"
            "每条字段 icao（24bit hex）/callsign（呼号）/altitude_ft（高度 ft）/"
            "lat/lon（经纬度，has_position=false 时 NaN）/groundspeed_kt（地速 kt）/"
            "heading_deg（航向）/vertical_rate_fpm（垂直速率 fpm，has_velocity 标记）/"
            "df（下行格式）/crc_ok（CRC 通过）/last_seen（墙钟毫秒）。"
            "1090 MHz 无信号或未调谐时 aircraft=[] 诚实空态，不编造飞机。"
            "区别于 get_ft8_status / get_ft8_history（解码器结果面）。")},
        // P3 #73/#74: local memory family + checkpoint writer control plane.
        {"memory_add",
         QString::fromUtf8(
            "写入：向本地记忆库（SQLite FTS5，~/.mbdsdr/memory.db）新增一条记忆——"
            "实际落库由后台 checkpoint writer 异步执行（返回 queued=true，不阻塞主 agent）。"
            "scope 必填枚举 user（用户事实/偏好）/agent（模型经验/踩坑）/session（本次会话现场）；"
            "scope_id 为作用域内实例标识（如台站号、过境 id）；key 在 scope 内唯一。"
            "submit 不做 DB 级查重；立即落库用 memory_flush。属于写动作，手动模式下被拦截。")},
        {"memory_search",
         QString::fromUtf8(
            "只读：全文检索本地记忆库（中英文均可）。query 为自然语言词串，"
            "词元经安全转义后走 FTS5 MATCH；可选 scope/scope_id 过滤；limit 1..100 默认 20。"
            "无命中返回 hits=[] 空数组并附 note，不编造记忆。")},
        {"memory_update",
         QString::fromUtf8(
            "写入：按 (scope,key) upsert 记忆——存在则覆盖 content/scope_id 与时间戳，"
            "不存在则等价于新增；经后台 checkpoint writer 异步落库（返回 queued=true）。"
            "scope 必填枚举 user/agent/session。立即落库用 memory_flush。"
            "属于写动作，手动模式下被拦截。")},
        {"checkpoint_status",
         QString::fromUtf8(
            "只读：返回后台 checkpoint writer 状态——pending_count/queue_depth（待落库条目数）、"
            "last_flush_ms（最近一次落库完成的墙钟毫秒，0=从未落库）。"
            "主 agent 只读不写；写路径由 memory_add/update 入队后异步落库。")},
        {"memory_flush",
         QString::fromUtf8(
            "写入：同步排空记忆队列并落库（memory_store.add/update），重写 ~/.mbdsdr/checkpoint.md "
            "人类可读快照（时间戳+本次落库条数+key 列表），返回 written 实际落库条数。"
            "属于写动作，手动模式下被拦截。")},
        // 第 10 域「工作台编排」(AIGUI P0-b): descriptions pinned verbatim.
        {"arrange_workspace",
         QString::fromUtf8(
            "写入：把一条 A2UI v1.0 子集布局消息应用到主界面三栏工作台——"
            "op 枚举 createSurface（整面重建）/updateComponents（在当前布局上叠加，默认）/"
            "deleteSurface（回默认）。components 为扁平邻接节点表，每节点 "
            "{id,type,props:{sizes:[L,M,R],stretch:[a,b,c],current,visible},children:[...]}；"
            "id 仅认编译期白名单（splitter/left_rail/center_spectrum/right_tabs/status_bar/"
            "tab:XXX），未知 id 进 placeholders 诚实空态、绝不渲染。"
            "sizes 软下限 120px clamp，默认 280/800/280 stretch 1/3/1。"
            "只动 splitter 与右侧 tab 可见性，不碰 DSP/音频/硬件。属于写动作，手动模式下被拦截。")},
        {"get_workspace_layout",
         QString::fromUtf8(
            "只读：返回当前工作台布局模型——splitter_sizes[L,M,R]、splitter_stretch[a,b,c]、"
            "visible_tabs（tab:XXX 列表）、current_tab、hidden_panels、placeholders。"
            "默认三栏 280/800/280 stretch 1/3/1，14 个右侧 tab 全部可见。")},
        {"list_ui_components",
         QString::fromUtf8(
            "只读：返回编译期白名单组件目录——每项 {id,category,capabilities[],min_size}。"
            "共 23 项：splitter/left_rail/center_spectrum/right_tabs/status_bar + 14 个 tab:XXX "
            "+ 4 个 center:XXX。agent 编排工作台前先查此目录确认可引用的 id。")},
        {"reset_workspace",
         QString::fromUtf8(
            "写入：把工作台重置为默认三栏布局（280/800/280 stretch 1/3/1，14 个右侧 tab 全部可见），"
            "等价于 deleteSurface。只动 splitter 与 tab 可见性，不碰 DSP/音频/硬件。"
            "属于写动作，手动模式下被拦截。")},
        {"update_component_data",
         QString::fromUtf8(
            "写入：A2UI v1.0 updateDataModel 最小子集——给一个编译期白名单组件 id 写入/清空"
            "内存数据槽。data 为 object 时整体替换该组件的数据（upsert）；data 为 null 时清空"
            "（删键）。未知组件 id 诚实报错、不写入。布局层不解释 payload、不做 @path 绑定、"
            "不碰 DSP/音频/硬件；数据可经 get_workspace_layout 的 component_data 字段读回。"
            "属于写动作，手动模式下被拦截。")},
        {"get_aprs_messages",
         QString::fromUtf8(
            "读取：最近解出的 APRS/AX.25 消息（最多 limit 条，1..50，默认 50）。"
            "每条含 time_ms/src/dst/path/info。源为 1200/2400 baud FSK 解调后的 NRZI "
            "位流经 HDLC 拆帧（CRC-16/X.25 校验）与 AX.25 解码；无效帧丢弃，无解码"
            "则诚实空态。只读工具。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径; limit 越界诚实 clamp 到 1..50，不报错。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 只返回最近 limit 条。")},
        {"get_color_maps",
         QString::fromUtf8(
            "读取：列出瀑布图可用色板——内置 3 个（经典/单色/Viridis，palette 0/1/2）"
            "与当前配置的外部 JSON 色板文件（如有：path/name/stops）。外部文件缺失或"
            "已损坏时 external 为 null 并附诚实 note，不编造。只读工具。")},
        {"list_templates",
         QString::fromUtf8(
            "读取：列出内置确定性任务模板目录——每项 {name, description, params[], steps}。"
            "共 3 个：sweep_find_record（扫频找信号并存档，参数 low_hz/high_hz/step_hz/mode/book_name，6 步）、"
            "target_capture（目标频率捕获录制，参数 target_hz/mode，4 步）、"
            "fixed_freq_record（固定频率录制解码，参数 freq_hz/mode/bandwidth_hz，5 步）。"
            "模板机制来自内部既有实现，不预置场景/呼号/TLE。只读工具，手动模式不拦截。")},
        {"run_template",
         QString::fromUtf8(
            "写入：按 template_name 构建一个确定性任务计划并登记返回其步骤序列。"
            "template_name 枚举 sweep_find_record/target_capture/fixed_freq_record（先用 list_templates 查看）。"
            "参数槽：sweep_find_record 需 low_hz/high_hz/step_hz/mode/book_name；"
            "target_capture 需 target_hz/mode；fixed_freq_record 需 freq_hz/mode/bandwidth_hz。"
            "未知 template_name 或缺参数诚实报错；本调用只构建/登记计划（executed=false），"
            "不在此同步调谐或录制——真实异步执行在生产端经 TaskRunner。"
            "属于写动作，手动模式下被拦截。")},
        // G1: task-run history (read-only).
        {"get_task_history",
         QString::fromUtf8(
            "只读：查询 TaskRunner 已完成任务的历史（落于本地记忆库 task_runs 表，跨重启保留）。"
            "列表模式（默认）按完成时间倒序返回最近 limit 条（1..50 默认 10），"
            "每条含 id/name/state（succeeded/failed/aborted/gated/partial）/finished_ms/step_count/summary；"
            "传 id 进入详情模式，额外返回逐步 steps 数组（tool/state/summary/elapsed_ms/error）。"
            "无记录时 runs=[] 并附诚实 note，不编造历史；id 不存在时 ok:false 并给提示。")},
        // G2: memory_delete (gated write) + memory_stats (read-only).
        {"memory_delete",
         QString::fromUtf8(
            "写入：按 (scope,key) 删除一条本地记忆——同时删 mem_meta 行与其 FTS 索引行。"
            "scope 必填枚举 user/agent/session；key 必填。同步短连接执行："
            "条目存在返回 deleted=true；不存在返回 deleted=false 并附 note（空操作，不编造已删）。"
            "属于写动作，手动模式下被拦截。")},
        {"memory_stats",
         QString::fromUtf8(
            "只读：本地记忆库统计——total_entries（条目总数）、by_scope（user/agent/session 分布）、"
            "last_updated_ms（最近写入墙钟毫秒，0=空库）、task_runs（已完成任务数，G1 历史）。"
            "空库全 0 诚实返回，不编造数据。")},
        // Read-only TLE-cache catalog (prerequisite for predict_passes).
        {"list_satellites",
         QString::fromUtf8(
            "只读：列出本地 TLE 缓存中的卫星目录（名、NORAD 编号、TLE 历元、龄期天数、是否新鲜、标称下行频率）。"
            "这是 predict_passes 的前置——先列出可用卫星再选名预测过境。"
            "无缓存时诚实返回空态（source=none），不使用陈旧内置演示数据，不编造卫星。")},


    };
    QCOMPARE(defs.size(), expected.size());
    for (int i = 0; i < expected.size(); ++i) {
        QVERIFY2(!defs[i].description.trimmed().isEmpty(),
                 qPrintable(defs[i].name));
        QCOMPARE(defs[i].name, expected[i].first);
        QCOMPARE(defs[i].description, expected[i].second);
    }
}

// scan_band: low/high share the hardware freq bounds; step_hz is optional with
// a 1 Hz floor and must NOT appear in "required".
void TestToolSchema::scanBandShape() {
    QList<ToolSchemaSpec> specs = registeredToolSpecs();
    const ToolSchemaSpec* s = findSpec(specs, "scan_band");
    QVERIFY(s);
    QJsonObject props = buildToolSchema(*s).value("properties").toObject();
    QVERIFY(props.contains("low_hz"));
    QVERIFY(props.contains("high_hz"));
    QVERIFY(props.contains("step_hz"));
    for (const char* edge : {"low_hz", "high_hz"}) {
        QJsonObject e = props.value(QString::fromLatin1(edge)).toObject();
        QCOMPARE(e.value("type").toString(), QString("number"));
        QCOMPARE(e.value("minimum").toDouble(), tokens::kFreqMinHz);
        QCOMPARE(e.value("maximum").toDouble(), tokens::kFreqMaxHz);
    }
    QJsonObject step = props.value("step_hz").toObject();
    QCOMPARE(step.value("minimum").toDouble(), 1.0);
    QSet<QString> required;
    for (const QJsonValue& v : buildToolSchema(*s).value("required").toArray())
        required.insert(v.toString());
    QCOMPARE(required, QSet<QString>({"low_hz", "high_hz"}));
}

// Phase31 A4: generateToolDocumentation() renders EVERY registered tool with the
// SAME fixed fields (name/description/schema/read-write marker/error example).
void TestToolSchema::generateToolDocumentationCoversAllTools() {
    const QList<ToolSchemaSpec> specs = registeredToolSpecs();
    const QString doc = generateToolDocumentation();
    QVERIFY(!doc.isEmpty());

    // Every one of the 38 tools appears as its own "## <name>" block.
    int headerCount = 0;
    for (const ToolSchemaSpec& s : specs) {
        QVERIFY2(doc.contains(QString("## %1 ").arg(s.name)), qPrintable(s.name));
        // Fixed fields: description line, schema JSON, read-write marker, err ex.
        QVERIFY2(doc.contains(s.description), qPrintable(s.name));
        if (s.write) {
            QVERIFY2(doc.contains(QStringLiteral("[write")), qPrintable(s.name));
            // write tools additionally fail shut at the manual gate
            QVERIFY2(doc.contains(QStringLiteral("gated")), qPrintable(s.name));
        } else {
            QVERIFY2(doc.contains(QStringLiteral("[read")), qPrintable(s.name));
        }
        ++headerCount;
    }
    QCOMPARE(headerCount, 90);
    // Fixed schema + error-example fields present on every block.
    QVERIFY(doc.contains(QStringLiteral("schema:")));
    QVERIFY(doc.contains(QStringLiteral("错误示例")));
    QVERIFY(doc.contains(QStringLiteral("description:")));
}

QTEST_MAIN(TestToolSchema)
#include "test_tool_schema.moc"
