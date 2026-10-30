// SPDX-License-Identifier: MIT
// M1 tool-schema generator test: fully deterministic and offline. No network,
// no API key, no hardware -- it only renders the declarative registry to JSON
// Schema and compares against the named constants in core/tokens.h and
// core/bandwidth_preset.h.
//
// phase72 起兼负「描述质量门禁」（模型体验轴，DSH 四维标准，与 Python 侧
// mbdsdr_ai/tests/test_tool_description_quality.py 对位）：
//   - 全工具面语言地板：每个工具/参数描述必须含中文（英文描述模型读不懂，
//     phase72 前 set_mode 等 6 个核心工具曾整句英文）；
//   - 无占位符残语（TODO/TBD/待补充/待实现/FIXME）；
//   - 核心 7 工具（tune_frequency/set_mode/start_recording/stop_recording/
//     scan_band/set_bandwidth/get_status）四维齐全；
//   - 四维覆盖率棘轮（只升不降，见 fourDimensionCoverageRatchet）。
// 描述金样（逐字钉死防漂移）在 sevenToolsNameDescriptionMatch。
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
    // phase72 描述质量门禁（模型体验轴，DSH 四维标准）
    void descriptionsAreChineseWithoutPlaceholders();
    void coreToolsHaveFourDimensions();
    void fourDimensionCoverageRatchet();
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
    QCOMPARE(specs.size(), 109);
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

// set_mode: enum is the exact 12-mode ControlHub token set (single source,
// D2-1), mode required.
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
    QCOMPARE(got, QSet<QString>({"AM", "NFM", "WFM", "USB", "LSB", "CW",
                                 "POCSAG", "m17", "VOR", "ACARS", "NAVTEX", "RAW"}));
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
        {"set_mode", QString::fromUtf8(
            "写入：把解调模式切换为指定模式。属于写动作，手动模式下被拦截。"
            "前置条件: mode 必须提供; schema 枚举与 ControlHub 白名单同源（12 种："
            "AM/NFM/WFM/USB/LSB/CW/POCSAG/m17/VOR/ACARS/NAVTEX/RAW），"
            "按枚举原样取值（注意 m17 是小写）——参数校验层按枚举精确匹配。"
            "失败恢复: 枚举外的模式名被参数校验层拒绝; 执行层另有大小写不敏感匹配并"
            "回显规范名，但不要依赖它绕过枚举——未识别的模式不会假报生效; "
            "UI 另有 BPSK/QPSK/ADS-B 本地模式，任何工具面均不可达（引擎白名单不含），"
            "需要时只能在桌面端点选; 多 VFO 场景改用 set_vfo_mode 定位具体信道。"
            "副作用: 立即改变解调模式（DSP 状态），音频输出与瀑布显示随之变化。"
            "能力边界: 只改当前选中信道的解调模式; 不改频率/带宽，也不读回状态（读回用 get_status）。")},
        {"start_recording", QString::fromUtf8(
            "写入：开始连续录制（原始 IQ 写 SigMF，或音频写 WAV+sidecar，由录制目标决定），"
            "直到 stop_recording。属于写动作，手动模式下被拦截。"
            "前置条件: 无需参数; 信号源必须已有数据（离线测试源同样可录，sidecar 标注测试信号）。"
            "失败恢复: 启动失败返回 ok:false 与「录制启动失败」——不得当作已开始; "
            "常见原因是已在录制中或信号源无数据，先 get_recording_state 确认状态、"
            "确认信号源连接后再重试。"
            "副作用: 持续写盘直到显式停止; 成功时返回体带 path 可直接读回。"
            "能力边界: 只启动连续录制; 一次性导出有界窗口用 export_iq_segment; "
            "本工具不返回录制内容本身。")},
        {"stop_recording", QString::fromUtf8(
            "写入：停止进行中的录制并封存文件（SigMF 或 WAV+sidecar）。属于写动作，手动模式下被拦截。"
            "前置条件: 无（未在录制时调用也返回成功，属幂等停止）。"
            "失败恢复: 本工具不报失败; 要确认是否真在录制，用 get_recording_state 读真实状态。"
            "副作用: 结束连续写盘，已录内容落盘可读。"
            "能力边界: 只停止不删除; 录制文件的管理（列举/导出/删除）用 "
            "list_recordings/export_recording/delete_recording。")},
        {"scan_band", QString::fromUtf8(
            "写入：在 [low_hz, high_hz] 频段内按 step_hz 步进扫频，返回最强峰（频率 + dBFS）。"
            "属于写动作（扫频过程逐点移动调谐频率），手动模式下被拦截。"
            "前置条件: low_hz/high_hz 必须提供且在合法频率范围内; step_hz 可缺省"
            "（默认 200 kHz，下限 1 Hz）; 注意 low_hz > high_hz 时一个点都不扫，"
            "峰值保持 -200 dBFS 初值。"
            "失败恢复: 本工具不因无信号报错——hits[0].dbfs 很低（≈-200）即该段无强信号或"
            "频段方向给反了; 检查频段方向与信号源连接后重扫。"
            "副作用: 每步约 20 ms 固定驻留（不同于扫描面板的可调驻留 100..2000 ms，"
            "两个「扫频」口径不同）; 细步进大频段会很慢; 扫描结束后调谐停在峰值点。"
            "能力边界: 只报最强单峰; 合成测试信号上峰值为固定基带偏音（返回体 synthetic=true "
            "已标注），非真实电台命中; 要全段峰清单用 export_peaks_csv。")},
        {"set_bandwidth", QString::fromUtf8(
            "写入：把信道滤波器带宽设为指定 Hz。属于写动作，手动模式下被拦截。"
            "前置条件: bandwidth_hz 必须提供且取 schema 枚举内的具名预设"
            "（AM 9000 / NFM 12500 / SSB 2400 / CW 500 / DIG 12000 / WFM 200000 / ADS-B 2000000）; "
            "当前解调模式不能是 RAW。"
            "失败恢复: RAW 直通模式下明确拒绝（RAW 是信道化窄带 IQ 的 L=I/R=Q 直通，"
            "无解调滤波器可设）——先 set_mode 切到解调模式再设带宽; "
            "枚举外的值由参数校验层拒绝。"
            "副作用: 立即改变信道滤波（DSP 状态），音频带宽与瀑布显示随之变化。"
            "能力边界: 只设当前选中信道带宽; 其他 VFO 用 set_vfo_bandwidth; "
            "不读回（读回用 get_status）。")},
        {"get_status", QString::fromUtf8(
            "只读：返回接收机当前完整状态——连接状态与信号源名、中心频率、解调模式、带宽、"
            "Costas 载波/符号锁定与 EVM、多普勒补偿的可用/启用状态。"
            "前置条件: 无（未连接也可查询，connected=false 如实返回）。"
            "失败恢复: 本工具不报失败; 未连接时其余字段为引擎当前值，不编造连接。"
            "副作用: 无（纯读）。"
            "能力边界: 只读当前状态; 引擎未暴露的字段（如增益/采样率）缺失即不返回，不虚构; "
            "改状态用对应的 set_* 写入工具。")},
        {"predict_passes",
         QString::fromUtf8(
            "只读：用本地新鲜 TLE 缓存预测指定卫星未来的过境（升/降时刻、最高仰角、起止方位）。"
            "无新鲜 TLE 时诚实返回空态，不使用陈旧内置数据。"
            "前置条件: satellite_name 必填（大小写不敏感子串匹配，可用 list_satellites "
            "核对缓存里的真实星名）; hours_ahead 可选 1..168（默认 24）; "
            "station_lat_deg/station_lon_deg 可选（缺省用设置里的本站位置）。"
            "失败恢复: 卫星名缺失或缓存无匹配/无新鲜 TLE 时 ok:false 并附 source 说明——"
            "先 list_satellites 确认缓存内容，或更新 TLE 后重试; 绝不把 2006 年内置陈旧"
            "TLE 当真过境返回。"
            "副作用: 无（只读纯计算，不碰电台/不改调谐状态）。"
            "能力边界: 只预测不过境控制; 返回 passes[] 每项 name/catalog_number/rise_time/"
            "rise_az/set_time/set_az/max_el（UTC）; 实际多普勒补偿用 set_doppler_compensation。")},
        {"calibrate_frequency",
         QString::fromUtf8(
            "只读测量：用一段已知精确频率的参考信号估计本机晶振 ppm 误差。"
            "不持久化、不应用——应用测量值是 apply_frequency_correction（写门控）的职责。"
            "前置条件: reference_freq_hz 必填（Hz，参考信号的已知精确频率）; "
            "reference_type 必填枚举 handheld/gsm_fcch/manual（gsm_fcch 的参考频率填 ARFCN "
            "下行中心频率，内部按 FCCH 音频位置折算）; sample_count 可选（默认 32768，"
            "4 段独立测量; 下限 4096，低于下限按默认值处理）。"
            "失败恢复: reference_type 无法识别时按 manual 处理（DC 参考）而非报错——"
            "需要 FCCH 折算时显式传 gsm_fcch; 采集样本不足（源无数据）时 detected=false "
            "并给 hint，不编造 ppm; 检测不到参考载波时 hint 为真实状态文本，measured_ppm 缺席。"
            "副作用: 测量期间会把源调谐到参考频率并抓一段 IQ（采集动作，不落盘不持久化）; "
            "applied 恒为 false——本工具永不修改 ppm 设置。"
            "能力边界: 只出测量值（measured_ppm/confidence/delta_hz/spread_ppm/snr_db/"
            "measurements_used）; 测试/离线源的数据被诚实标注为生成 IQ; 写入校正用 "
            "apply_frequency_correction。")},
        {"apply_frequency_correction",
         QString::fromUtf8(
            "写入：应用频率校正 ppm（通常取 calibrate_frequency 的 measured_ppm），"
            "保存到设置并下发给接收机。属于写动作，手动模式下被拦截。"
            "前置条件: ppm 必须显式提供且落在参数边界内——执行层对缺失 ppm 按 0 处理"
            "（会把你已有的校正清零），所以绝不省略; reference_freq_hz 可选，仅记录出处。"
            "失败恢复: ppm 缺失或越界时参数校验层明确拒绝，补合法数值后重试; "
            "想确认校正效果请用 calibrate_frequency 重新实测，不要把本工具的预测值当实测。"
            "副作用: 立即写 QSettings 持久化 ppm 并改变接收机频综校正（硬件状态）。"
            "能力边界: 只保存并下发 ppm，返回 previous_ppm/applied_ppm 前后对比与 "
            "predicted_residual_hz=0.0 的设计预测（非实测残差）；读回当前配置用 get_status。")},
        {"get_pocsag_messages",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 POCSAG 寻呼消息快照"
            "（地址 RIC/功能位/文本）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表，不编造消息。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 信道选择不改调谐状态。")},
        {"get_m17_calls",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 M17 呼叫/帧快照"
            "（源/目的呼号、类型、CRC 状态、语音帧诚实标注未解码）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 语音帧未解码时显式标注。")},
        {"get_vor_radial",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）VOR 信道最新径向读数"
            "（radialDeg 方位、质量、莫尔斯识别码、锁定态）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——未锁定时 locked=false，radial_deg/quality 为 null"
            "且方位不可信并被显式标注，不编造方位。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读引擎最新读数; 方位仅锁定后可信; morse_id 恒返回（未识别时为空串）。")},
        {"get_acars_packets",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 ACARS 航空报文快照"
            "（方向 air/ground、label、block id、ack、正文、CRC 结果）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表，不编造报文。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读引擎已解出的快照，不触发解调; CRC 失败帧显式标注。")},
        {"get_navtex_messages",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 NAVTEX 海上安全报文快照"
            "（发台 B1、类型 B2、编号、正文、时间分集/定相状态）。"
            "前置条件: 无（channel_id/channel 可选，缺省为当前选中信道）。"
            "失败恢复: 无失败路径——无解码结果时诚实返回空列表，不编造报文。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 分集/定相失败在条目内显式标注。")},
        {"export_iq_segment",
         QString::fromUtf8(
            "写入（一次性）：立即抓取一段基带 IQ 复样本，导出为 cf32_le SigMF 文件"
            "（.sigmf-data + .sigmf-meta）后即返回。区别于 start_recording 的连续录制："
            "这是按需导出有界窗口。属于写动作，手动模式下被拦截。"
            "前置条件: 接收机已有可用的 IQ 数据流（源在运行、有硬件或已开离线文件）；"
            "长度二选一：sample_count（复样本数，缺省 65536，引擎按 [1024, 16777216] 诚实钳位）"
            "或 seconds（时长秒，0.1..30，与 UI 导出对话框同一口径，按 当前采样率×秒 折算样本数，"
            "两者同给时 sample_count 优先）; "
            "tune_hz 缺省/负值=保持当前中心频率。"
            "失败恢复: 无 IQ 数据或源 EOF 时诚实 ok:false 并说明原因，绝不生成空文件; "
            "tune_hz 非数字时明确拒绝。"
            "副作用: 在录制目录写 SigMF 文件（路径自动生成 export_<时间戳>_<中心Hz>，"
            "按秒级消歧不会覆盖既有文件）; tune_hz>=0 时会先把中心频率调过去（硬件状态）; "
            "不影响进行中的连续录制。"
            "能力边界: 只导出不连续录制; 源提前 EOF 时返回真实样本数（可能少于请求值）; "
            "文件位置见返回 path 字段。")},
        // Phase26: 21 new tools, verbatim description pairs (must match
        // tool_schema.cpp registeredToolSpecs() exactly, in on-wire order).
        {"set_network_audio_sink",
         QString::fromUtf8(
            "写入：配置网络音频流输出（UDP/TCP 镜像当前解调音频）。属于写动作，手动模式下被拦截。"
            "前置条件: enable（布尔）与 port（数字，1..65535）均必填; format 可选 udp/tcp"
            "（默认 udp）、host 可选（默认 127.0.0.1）、stereo 可选（默认 false 单声道）。"
            "失败恢复: enable/port 缺失或类型不符时明确报错（不静默取默认）——补齐后重试; "
            "指令经 ControlHub 路由执行，链路真实状态用 get_network_audio_status 读取。"
            "副作用: 改变网络音频流的目标端口/协议/单双声道配置（桌面端持续生效）。"
            "能力边界: 只下发配置不直接推流; Agent 层不持有音频流实例，回显值经控制层确认。")},
        {"get_network_audio_status",
         QString::fromUtf8(
            "只读：返回网络音频流状态（是否使能、端口、格式）。"
            "无状态时诚实返回 enabled=false，不编造端口。"
            "前置条件: 无。"
            "失败恢复: 无失败路径——当前 Agent 层无网络音频流读数时 enabled=false 是诚实空态"
            "而非故障，不要据此声称网络音频损坏; 真实流状态以桌面端/ControlHub 为准。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: Agent 层不持有音频流实例（后端由 ControlHub 提供）; "
            "配置用 set_network_audio_sink。")},
        {"start_scan_link",
         QString::fromUtf8(
            "写入：启动扫描活动链路（扫描→命中→驻留→解码→录制），目标频率 target_freq_hz。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: target_freq_hz 必填且为数字（Hz），范围由参数边界强制; "
            "接收机有可用数据流（链路要真实扫描）。"
            "失败恢复: 参数缺失或非数字时明确报错——补齐合法频率后重试; "
            "指令经 ControlHub 路由，链路已在运行时重复启动由控制层语义处理。"
            "副作用: 控制层开始扫描→命中→驻留→解码→录制活动链路（持续消耗接收资源，"
            "可能自动录制到磁盘）。"
            "能力边界: 只发启动指令; 链路真实运行态在 ControlHub 侧，用 get_scan_link_status "
            "读取，停止用 stop_scan_link。")},
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
            "未运行时诚实返回 scanning=false、无命中，不编造。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——链路未启动时 scanning=false 是诚实空态; "
            "Agent 层无链路实例时读数为空态，不代表链路故障。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读状态不启动/停止链路; 启动用 start_scan_link，停止用 stop_scan_link; "
            "链路真实运行态在 ControlHub 侧。")},
        {"set_squelch",
         QString::fromUtf8(
            "写入：设置静噪（enabled 开关、threshold_db 门限、auto 自动链路）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: 三个字段全部可选，至少提供其一才有意义（全缺则不下发任何变更）; "
            "threshold_db 是 48 kHz 音频块 RMS dBFS 域门限，范围 [-100, -20]"
            "（与 UI 滑杆同域同界），越负越灵敏。"
            "失败恢复: threshold_db 超出 [-100, -20] 由参数校验层明确拒绝（不静默夹值）; "
            "其余类型不符的字段被静默跳过而非报错（如 enabled 传了字符串）——"
            "发现设置未生效时用 get_squelch_status 读回真实值核对，不要反复重试同一入参。"
            "副作用: 立即改变引擎静噪状态，音频输出（静音/放音）随之变化。"
            "能力边界: 只改静噪参数，不影响 CTCSS/CDCSS 亚音门控（分别用 set_ctcss/set_cdcss）; "
            "读回用 get_squelch_status。")},
        {"get_squelch_status",
         QString::fromUtf8(
            "只读：返回静噪状态（enabled/threshold_db/auto/当前是否 open）。"
            "无实时门限读数时诚实标注，不编造。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——值直接读引擎，门限读数缺失时显式标注而非填 0; "
            "与预期不符时以本工具读回为准（set_squelch 对类型不符字段是静默跳过的）。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读不改; 设置用 set_squelch; CTCSS/CDCSS 亚音状态另有 "
            "get_ctcss_status / get_cdcss_status。")},
        {"set_ctcss",
         QString::fromUtf8(
            "写入：设置 CTCSS 亚音（enabled 开关、frequency_hz 亚音频率 67.0–254.1 Hz、"
            "可选 gate_audio 亚音门控静音开关）。属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔; frequency_hz 可选（省略沿用当前值/默认 88.5 Hz），"
            "取值须落在 [67.0, 254.1]; gate_audio 可选布尔。"
            "失败恢复: enabled 缺失或非布尔时明确报错（绝不静默切换）; 频率越界时诚实拒绝"
            "（不静默钳位）——改合法频率后重试。"
            "副作用: 立即改变 CTCSS 检测状态; gate_audio 开启后仅扬声器被门控（静音至匹配），"
            "录制不受影响。"
            "能力边界: 只设亚音参数; 数字亚音用 set_cdcss; 读回（含 active 是否真实检出）"
            "用 get_ctcss_status。")},
        {"get_ctcss_status",
         QString::fromUtf8(
            "只读：返回 CTCSS 亚音状态（enabled 是否使能、frequency_hz 调谐频率、"
            "active 是否真实检测到亚音、gate_audio 是否开启亚音门控静音）。"
            "无信号/未使能时 active 诚实为 false，不编造。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——未使能或无信号时 active=false，不要把「没检测到」"
            "当成设备故障; 频率与开关以本读回为准。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读检测层状态; 设置用 set_ctcss; 数字亚音另有 get_cdcss_status。")},
        {"set_cdcss",
         QString::fromUtf8(
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
            "用 get_cdcss_status。")},
        {"get_cdcss_status",
         QString::fromUtf8(
            "只读：返回 CDCSS/DCS 数字亚音状态（enabled 是否使能、code 调谐 DCS 码、"
            "active 是否真实检测到匹配码、gate_audio 是否开启数字亚音门控静音）。"
            "无信号/未使能时 active 诚实为 false，不编造。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——未使能或无信号时 active=false; code 以本读回为准。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读检测层状态; 设置用 set_cdcss; 模拟亚音另有 get_ctcss_status。")},
        // Phase63 step3: FT8 detection-layer tools (must match tool_schema.cpp order).
        {"set_ft8",
         QString::fromUtf8(
            "写入：开关 FT8 数字模式检测层（enabled 布尔，必填）。属于写动作，手动模式下被拦截。"
            "本轮为检测层（Costas 同步 + 候选帧统计），C++ BP 解码留后续，不编造解码消息。"
            "前置条件: enabled 必须提供且为布尔值（缺失/非布尔明确报错，绝不静默切换）。"
            "失败恢复: 入参类型不符时补齐布尔值重试; 开启后无候选帧不代表故障，"
            "先确认频率与模式已对准 FT8 子带。"
            "副作用: 立即改变引擎 FT8 检测层状态（持续占用解调资源）。"
            "能力边界: 只开关检测层，不出解码消息; 检测层读数用 get_ft8_status，"
            "解码历史用 get_ft8_history，导出用 export_ft8_results。")},
        {"get_ft8_status",
         QString::fromUtf8(
            "只读：返回 FT8 检测层状态（enabled 是否使能、active 是否真实检出候选帧、"
            "freq_offset_hz 估计频偏、sync_quality 相关峰/次峰比、candidate_count 候选数）。"
            "无信号/未使能时 active 诚实为 false，不编造。"
            "前置条件: 无（任意时刻可调）; 频率已对准 FT8 子带才有候选。"
            "失败恢复: 无失败路径——开启后无候选不代表故障，先核对频率/模式; "
            "本工具只反映检测层，不代表解码结果。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读检测层即时状态; 历史用 get_ft8_history，导出用 export_ft8_results，"
            "开关检测层用 set_ft8。")},
        {"set_lrpt",
         QString::fromUtf8(
            "写入：开关 LRPT 卫星云图接收层（enabled 布尔，必填）。属于写动作，手动模式下被拦截。"
            "本轮为状态控制层（C++ 解调/FEC 移植留后续第④轮），不编造图像或帧。"
            "前置条件: enabled 必须提供且为布尔值（缺失/非布尔明确报错）; 卫星在轨且频率对准。"
            "失败恢复: 入参类型不符时补齐布尔值重试; 开启后 sync_locked 仍为 false 是"
            "C++ 解码器未移植的诚实空态，不代表接收失败。"
            "副作用: 立即改变引擎 LRPT 接收层开关状态。"
            "能力边界: 只置位状态标志，不出图像/帧数据; 读回用 get_lrpt_status"
            "（sync_locked/decoded_frames 为真实引擎值）。")},
        {"get_lrpt_status",
         QString::fromUtf8(
            "只读：返回 LRPT 接收层状态（enabled 是否使能、sync_locked 是否已锁定位同步、"
            "decoded_frames 已解码行数）。C++ 解码器未移植前 sync_locked 诚实为 false、"
            "decoded_frames 为 0，不编造。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——sync_locked=false/decoded_frames=0 是 C++ 解调/FEC "
            "未移植（第④轮）的诚实空态，不代表接收失败，不要据此声称卫星信号中断。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读状态控制层读数，不出图像/帧; 开关用 set_lrpt。")},
        {"set_vna_sweep",
         QString::fromUtf8(
            "写入：设置 NanoVNA 扫频范围（start_hz/stop_hz/points，均必填；stop 必须大于 start）。"
            "宽扫建议 ±5~10%，窄扫 ±1%（经验范围）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: 三个参数都必须提供且为数字; 频率范围 50 kHz..999.999999 MHz"
            "（仪器实际工作域，0 Hz 无物理意义）; points 为 1..401 整数（面板硬上限）; "
            "stop_hz 大于 start_hz。"
            "失败恢复: 任一缺失/非数字、越界、stop<=start 或 points 越界时明确 ok:false——"
            "改正后重试; 无设备时参数仍被校验，合法但不下发（applied=false），"
            "不伪造连接——先接好设备再调。"
            "副作用: 设备已连接时立即向 NanoVNA 下发新扫频参数（硬件状态），"
            "当前测量数据随之失效，需重扫后再读。"
            "能力边界: 只编程扫频参数，不读数据（用 get_vna_data）; 连接/校准状态用 "
            "get_vna_status。")},
        {"get_vna_data",
         QString::fromUtf8(
            "只读：返回当前扫频测量数据（frequencies、S11/S21 复数对及派生 VSWR/回波损耗/阻抗）。"
            "区别：本工具返回测量曲线数据；get_vna_status 仅返回连接/校准/扫描参数状态。"
            "前置条件: 无（任意时刻可调）; 已连接 NanoVNA 且执行过 set_vna_sweep 并等扫描完成。"
            "失败恢复: 无失败路径——未连接时各数组诚实为空并附排查 note（USB/CDC 接线、"
            "扫频模式、串口路径）；已连接但无数据时提示先 set_vna_sweep; 不编造曲线。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读当前测量窗口; coax_loss_db_per_m 需先用 vna_tdr_cable 测得电缆长度; "
            "S11=CH0 反射、S21=CH0→CH1 传输（wiring 字段透传）。")},
        {"get_vna_status",
         QString::fromUtf8(
            "只读：返回 NanoVNA 状态（connected 是否已连接、model 型号、version 固件、cal 已置位校准项、"
            "当前 sweep 范围与点数）。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——未连接时 connected 诚实为 false、字段为空，不伪造型号/固件; "
            "已连接时 live 刷新校准状态后才返回。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读连接/校准/扫描参数状态; 曲线数据用 get_vna_data; "
            "校准操作是 vna_calibration，设备级杂项是 vna_device。")},
        {"analyze_vna_resonance",
         QString::fromUtf8(
            "只读：对当前扫频 S11 做谐振分析（串联谐振 fr、并联谐振 fp、ESR、-3dB 带宽、有载 Q）。"
            "前置条件: 无（任意时刻可调）; 已连接 NanoVNA 且有扫频数据。"
            "失败恢复: 无失败路径——无设备/点数不足/span=0 时 valid 诚实为 false，不编造。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只分析当前 S11 窗口; 传输特性用 analyze_vna_filter，等效电路用 "
            "analyze_vna_equivalent; 取数用 get_vna_data。")},
        {"vna_tdr_cable",
         QString::fromUtf8(
            "写入：对当前扫频 S11 做 TDR 时域反射（velocity_factor 速度因子，0<vf<=1，必填），"
            "返回首反射峰距离与电缆长度（馈线好坏/电缆故障/进水/接头虚接）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: velocity_factor 必填数字且落在 (0,1]（同轴常用 0.66）; "
            "NanoVNA 已连接且已完成一次扫频（先 set_vna_sweep）。"
            "失败恢复: vf 缺失/非数字或越界时明确 ok:false——补合法值重试; "
            "无设备或未检出反射峰时 valid=false 诚实返回，不编造距离。"
            "副作用: 只读当前扫频缓存做变换，不向设备发新命令（门禁仍按写动作把守）。"
            "能力边界: 只用当前 S11 数据; 实测 S 参数曲线用 get_vna_data; "
            "coax_loss_db_per_m 需先用本工具测得电缆长度。")},
        {"analyze_vna_filter",
         QString::fromUtf8(
            "只读：对当前扫频 S21 幅度做滤波器分析（自动识别 bandpass/bandstop/highpass/lowpass，"
            "输出 -3dB 截止边、带宽、通带插损、阻带衰减）。"
            "前置条件: 无（任意时刻可调）; 已连接 NanoVNA 且有 S21 扫频数据（CH0→CH1）。"
            "失败恢复: 无失败路径——无设备/幅度平坦非滤波器形态时 valid 诚实为 false，"
            "不硬套滤波器模型。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只分析当前 S21 幅度; 反射/谐振用 analyze_vna_resonance; 取数用 get_vna_data。")},
        {"analyze_vna_equivalent",
         QString::fromUtf8(
            "只读：由当前扫频 S11 提取串联 RLC 等效电路（ESR=串联谐振点 |Z|，Q=fr/BW，L/C 由谐振关系推出）。"
            "前置条件: 无（任意时刻可调）; 已连接 NanoVNA 且有扫频数据。"
            "失败恢复: 无失败路径——无设备/点数不足/谐振点病态时 valid 诚实为 false 并附 note，"
            "不编造元件值。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只给串联 RLC 等效（不含并联支路）; 匹配网络 L/C 建议用 analyze_vna_match。")},
        {"analyze_vna_touchstone",
         QString::fromUtf8(
            "只读：解析 Touchstone s1p/s2p 文件（content 文本 或 path 路径），返回频率表与 S11/S21 实虚部数据集，"
            "可复用谐振/滤波器分析。"
            "前置条件: content 或 path 至少提供其一（path 须可读）。"
            "失败恢复: 两者皆空时 valid=false 并附 note; 不可解析时 ok:false 诚实拒绝——"
            "修正文件格式（R/I 实虚部对）后重试; 仅支持 R/I 格式。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只解析不分析; 解出的数据集可交给 analyze_vna_resonance / analyze_vna_filter "
            "做离线分析（无需连接设备）。")},
        {"export_vna_snapshot",
         QString::fromUtf8(
            "写入：把当前 NanoVNA 扫频的 Smith 圆图轨迹离屏渲染为 PNG 存到 path。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: path 必填（PNG 输出完整路径）; NanoVNA 已连接且已有扫频数据"
            "（先 set_vna_sweep 并等待扫描完成）。"
            "失败恢复: path 缺失、未连接或无扫频数据时诚实 ok:false——补齐前置条件后重试; "
            "路径不可写时 ok:false 并提示。"
            "副作用: 在 path 写一个 PNG 文件（覆盖同名文件）; 不影响进行中的测量。"
            "能力边界: 只渲染 Smith 轨迹; 频谱/峰值另有 export_spectrum_snapshot / "
            "export_peaks_csv; 原始数据用 get_vna_data。")},
        {"export_spectrum_snapshot",
         QString::fromUtf8(
            "写入：把当前 SDR 频谱面板（频率轴 + 中心频点标记 + 峰值标注）离屏渲染为 PNG "
            "存到 path。属于写动作，手动模式下被拦截。"
            "前置条件: path 必填（PNG 输出完整路径）; 引擎已初始化（FFT 已运行）。"
            "失败恢复: path 缺失时诚实 ok:false; 路径不可写时 ok:false 并提示——换可写路径重试; "
            "FFT 为空时画平坦基线（诚实空态）而非伪造曲线。"
            "副作用: 在 path 写一个 PNG 文件（覆盖同名文件）; 不改动调谐/解调状态。"
            "能力边界: 只截当前频谱画面; 原始 FFT 数据用 get_spectrum_status; "
            "数值化峰值导出用 export_peaks_csv。")},
        {"export_peaks_csv",
         QString::fromUtf8(
            "写入：把当前频谱峰值列表（freq_hz,db，按强度降序）写成 CSV 存到 path。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: path 必填（CSV 输出完整路径）; 引擎有运行中的 FFT 数据。"
            "失败恢复: path 缺失或不可写时诚实 ok:false——换可写路径重试; "
            "平坦/空 FFT 时 ok:true 但 rows=0 仅写表头（诚实空态，不是错误，不要重试）。"
            "副作用: 在 path 写 CSV 文件（覆盖同名文件）; 不改动调谐/解调状态。"
            "能力边界: 固定导出前 10 个峰值、门限 6 dB（引擎 peaks(10,6.0) 口径）; "
            "整段频谱图用 export_spectrum_snapshot。")},
        {"export_ft8_results",
         QString::fromUtf8(
            "写入：把当前 FT8 解码结果导出为 CSV 文件存到 path。属于写动作，手动模式下被拦截。"
            "前置条件: path 必填（输出文件完整路径）; all_history 可选布尔（默认 false）。"
            "失败恢复: path 缺失或不可写时诚实 ok:false——换可写路径重试; "
            "无结果时 ok:true 但 rows=0 仅写表头（诚实空态，不伪造呼号/网格）。"
            "副作用: 在 path 写文件（覆盖同名文件）; 不改动解码器状态。"
            "能力边界: all_history=false 导出候选列表（freq_hz,crc_ok,costas_score,rejected,"
            "reject_reason,iters）；true 导出解码历史全量（time_ms,freq_hz,demod,text）; "
            "在线读取分别用 get_ft8_status / get_ft8_history。")},
        {"analyze_vna_match",
         QString::fromUtf8(
            "只读：给定实测负载阻抗 load_r(+load_x) 与设计频率 f_hz，返回匹配网络的 L/C 建议"
            "（Q、串联/并联臂值）（驻波下不来/做匹配/加感）。"
            "前置条件: load_r>0 与 f_hz>0 均必填; load_x 可选（默认 0）; "
            "topology 可选 series_shunt（默认，既有 EFHW/反 L 语义）/xps/xs/xpl; "
            "r_comp 可选串联 R 补偿（≥0，并入负载电阻）。"
            "失败恢复: load_r/f_hz 缺失、非正或 r_comp<0 时 valid=false 并说明; "
            "无闭式解的拓扑诚实 valid:false，不伪造数值; series_shunt 要求 R_load>50Ω"
            "（EFHW 高阻馈电点）。"
            "副作用: 无（纯计算只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只给闭式解建议; 实测阻抗用 get_vna_data 的 z_real/z_imag。")},
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
            "失败恢复: 无失败路径——值直接读引擎，不编造。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读，不改变任何状态; 开关操作用 set_noise_blanker。")},
        {"list_bookmarks",
         QString::fromUtf8(
            "只读：列出书签（频率/名称/模式）。无书签时诚实返回空列表，不编造。"
            "前置条件: 无。"
            "失败恢复: 无失败路径——Agent 层未注入书签管理器时返回空列表+note（诚实空态），"
            "真实书签数据在 control 层——空列表不等于没有书签，不要虚构条目。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只列不增删; 增删分别用 add_bookmark / delete_bookmark，调谐用 tune_to_bookmark。")},
        {"add_bookmark",
         QString::fromUtf8(
            "写入：添加书签（freq_hz/name/mode/bandwidth_hz/group）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: freq_hz 必填且为正的有限数（Hz）; name/mode/bandwidth_hz/group 全可选"
            "（缺省时对应字段留空）; 书签管理器已由桌面端注入。"
            "失败恢复: freq_hz 缺失/非数字或 <=0 时诚实拒绝（书签不入册）——改正后重试; "
            "管理器未注入时返回明确错误而非假装写入。"
            "副作用: 真写入书签存储并按 (group, freq) 自动排序落位（无撤销接口），"
            "返回 index/count 真实落位。"
            "能力边界: 只增书签不调谐; 用书签频率调谐是 tune_to_bookmark; "
            "列书签用 list_bookmarks。")},
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
            "写入：删除指定下标书签（需显式确认）。属于写动作，手动模式下被拦截。"
            "前置条件: index 必须是 0 起整数下标，且书签管理器已注入; "
            "confirm 必须显式传 true——书签删除无撤销，插队移位也容易误删，"
            "所以不接受默许式删除。"
            "失败恢复: confirm 非 true 时明确拒绝（书签不动）; index 越界时错误信息含"
            "真实书签总数——按总数改下标重试。"
            "副作用: 真删除书签（无回收站/无撤销接口），返回值 remaining 为删除后剩余数。"
            "能力边界: 只能删已注入管理器的书签; 不确定下标时先用 list_bookmarks 核对。")},
        {"list_vfos",
         QString::fromUtf8(
            "只读：列出全部 VFO 信道（id/频率/带宽/模式/选中态）。"
            "前置条件: 无。"
            "失败恢复: 无失败路径——返回引擎真实 VFO 标记，空列表即当前无 VFO; "
            "越界下标类错误可用本工具先核对真实数量再重试。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读不改; 新增/切换/改参分别用 add_vfo / switch_vfo / set_vfo_* 系列。")},
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
            "属于写动作，手动模式下被拦截。"
            "前置条件: index（list_vfos 返回的标记序号）与 enabled 均必填; "
            "enabled 接受布尔或数字（非 0 即真）。"
            "失败恢复: index 缺失/非数字、enabled 缺失或类型不符时明确报错; "
            "index 越界时错误附当前 VFO 总数——用 list_vfos 重新取序号后重试。"
            "副作用: 立即改变该 VFO 的后台解调使能（真实 CPU 开销随之增减）。"
            "能力边界: 只改并行监听开关; 频率/模式/带宽用 set_vfo_* 系列; "
            "当前各 VFO 状态用 list_vfos 读回。")},
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
            "前置条件: index 或 id 必须提供其一；mode 取值与 set_mode 同源"
            "（12 种：AM/NFM/WFM/USB/LSB/CW/POCSAG/m17/VOR/ACARS/NAVTEX/RAW），"
            "按枚举原样取值（注意 m17 是小写）——参数校验层按枚举精确匹配。"
            "失败恢复: 目标缺失/越界或 mode 不在枚举中时返回明确错误（越界附 VFO 总数）——"
            "用 list_vfos 核对后重试。"
            "副作用: 该 VFO 的解调模式立即切换，音频输出随之改变。"
            "能力边界: 只改已存在 VFO 的模式; 不影响其他 VFO。"
            "属于写动作，手动模式下被拦截。")},
        {"set_vfo_bandwidth",
         QString::fromUtf8(
            "写入：设置指定 VFO 的信道带宽（index 或 id 二选一 + bandwidth_hz）。"
            "前置条件: index 或 id 必须提供其一；bandwidth_hz 在 100..2000000 Hz 之间"
            "（与 UI 拖边/微调/预设同一合法域，含 ADS-B 2 MHz 预设）。"
            "失败恢复: 目标缺失/越界或带宽越出 [100, 2000000] 时返回明确错误"
            "（越界附 VFO 总数）——补齐后重试。"
            "副作用: 该 VFO 的滤波器带宽立即改变，音频随之变化。"
            "能力边界: 只改已存在 VFO 的带宽; 具体滤波形态由引擎决定。"
            "属于写动作，手动模式下被拦截。")},
        {"list_recordings",
         QString::fromUtf8(
            "只读：扫描录制目录并列出已有录制文件。"
            "前置条件: 无（任意时刻可调；目录路径由引擎配置决定，随结果 dir 字段透传）。"
            "失败恢复: 无失败路径——目录不存在或为空时诚实返回空列表（count=0），"
            "不编造文件。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
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
            "window/average 使用字符串枚举（Hann/FlatTop/Blackman、Off/Slow/Fast）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: fft_size 必填，且只接受 1024/2048/4096 三档（引擎实收集合）; "
            "window/average 可选（省略保持当前值）; LLM/MCP 校验路径只接受字符串枚举。"
            "失败恢复: fft_size 缺失/非数字/不在三档内时明确报错（不静默忽略、"
            "不回显当前值冒充生效）——按 1024/2048/4096 重试; "
            "window/average 传无法识别的字符串时回落 Hann/Off"
            "（执行层对 window 大小写不敏感，旧拼写 Flattop 也识别）。"
            "副作用: 立即改变 FFT 点数/窗函数/平均模式，频谱与瀑布显示随之变化。"
            "能力边界: 模型侧只接受字符串枚举——原始整数 0/1/2 是 ControlHub/HTTP "
            "内部通道键，经 LLM/MCP 校验路径传入会被类型校验拒绝; 读回用 get_spectrum_status。")},
        {"set_color_map",
         QString::fromUtf8(
            "写入：加载并校验瀑布图色板 JSON 文件，把路径持久化到设置"
            "（view/wfColormapFile，UI 启动时重应用同一文件）。属于写动作，手动模式下被拦截。"
            "前置条件: file_path 必填且指向可读的 JSON 色板文件。"
            "失败恢复: 路径缺失/不可读/解析失败时诚实 ok:false 并说明原因（name/stops 无效"
            "不落盘）——修正文件后重试; 可用 get_color_maps 先看当前配置与内置色板。"
            "副作用: 写 QSettings 持久化色板文件路径（headless 仅持久化偏好，"
            "立即重绘由桌面端 UI 持有）。"
            "能力边界: 只加载外部文件色板; 内置 3 个色板（经典/单色/Viridis）经 get_color_maps "
            "读取，不经本工具切换。")},
        {"get_spectrum_status",
         QString::fromUtf8(
            "只读：返回频谱当前 FFT 参数真实值（fft_size/window/average）。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——值直接读引擎，不编造; 与预期不符时以本工具读回为准"
            "（set_fft_params 对非 1024/2048/4096 的 fft_size 现在明确报错拒绝，不再静默忽略）。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读不改; 修改 FFT 参数用 set_fft_params。")},
        // Phase55/58: two appended tools (must match tool_schema.cpp on-wire order).
        {"set_doppler_compensation",
         QString::fromUtf8(
            "写入：开关过境实时多普勒自动补偿（1Hz TLE 距离率重调 VFO）。"
            "需已设置本站位置并捕获一个过境，否则保持关闭（诚实拒绝）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: enable 布尔必填; 桌面端 UI 控制面存在（headless/测试环境不可用，"
            "返回 available=false）; 已配置本站位置且 TLE 缓存里有目标卫星。"
            "失败恢复: 无 UI 控制面或未设本站/未捕获过境时诚实 ok:false 并说明原因——"
            "先在桌面端天空页完成设置; enable 缺省按 false 处理（保持关闭）。"
            "副作用: 开启后过境期间按 TLE 距离率持续重调 VFO 频率（硬件状态持续变化）。"
            "能力边界: 只置补偿开关; 过境预测用 predict_passes，卫星目录用 list_satellites。")},
        {"connect_network_source",
         QString::fromUtf8(
            "写入：连接 rtl_tcp 网络接收机（host/port）。"
            "真实 TCP 握手+RTL0 设备头；失败返回真实 socket 原因并回到空态，不伪造 IQ。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: host 必填（服务端主机/IP）; port 可选，缺省 1234; "
            "对端已运行 rtl_tcp 且网络可达。"
            "失败恢复: host 缺失时明确报错; 握手/协议失败时 ok:false 并附真实 socket 原因，"
            "回到空态源（无假 IQ）——核对地址端口与服务端状态后重试。"
            "副作用: 成功时切换当前 IQ 源到该 rtl_tcp 服务（调谐/录制目标随之改变）; "
            "失败时保持既有源不变。"
            "能力边界: 只发起连接; 连接后的源能力（调谐范围/增益档）用 get_capabilities 读取。")},
        // Read-only capability/recording-state snapshot tools (must match
        // tool_schema.cpp on-wire order; appended last).
        {"get_capabilities",
         QString::fromUtf8(
            "只读：返回当前源的真实能力（设备名、可调谐频率范围、采样率范围、离散增益档 gains_db）。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——未连接或测试信号源时诚实返回 connected=false、"
            "空增益档数组与来源说明 provenance，不编造调谐范围或增益表; "
            "provenance 会明确标注「测试信号源（非硬件）」/「未连接」。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读当前活动源快照; 换源（含 rtl_tcp 网络源）用 connect_network_source。")},
        {"get_recording_state",
         QString::fromUtf8(
            "只读：返回录制状态（recording 是否在手动录制中、recording_path 当前路径、"
            "watch_enabled 值守录制是否使能、recording_dir 录制目录）。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——未录制时诚实返回 recording=false、空路径，不伪造。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读引擎状态; 开始/停止录制用 start_recording / stop_recording，"
            "文件操作用 list_recordings / export_recording / delete_recording。")},
        // P0 #59: single-tool NanoVNA calibration state machine (write, gated).
        {"vna_calibration",
         QString::fromUtf8(
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
            "cal_interpretation 字段给出; 连接/型号信息用 get_vna_status。")},
        // P1 #70: device-level misc facade state machine (write, gated).
        {"vna_device",
         QString::fromUtf8(
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
            "（0 不代表设备真实设置）; 扫频参数用 set_vna_sweep，数据曲线用 get_vna_data。")},
        // P2 #71: FT8 decoded-message rolling history (read-only).
        {"get_ft8_history",
         QString::fromUtf8(
            "只读：返回最近 N 条 FT8 解码历史（WSJT-X 风格滚动窗口，按时间倒序），"
            "每条字段 time_ms（墙钟毫秒）/freq_hz（绝对 RF Hz）/demod（解调，如 FT8）/"
            "text（解码文本 FROM TO EXCHANGE）。"
            "前置条件: 无（limit 可选 1..200，默认 50，越界由参数校验拒绝）。"
            "失败恢复: 无失败路径——无历史时 history=[] 诚实空态，不伪造。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读滚动窗口（上限 200 条，引擎 kFt8HistoryMax）; "
            "区别于 get_ft8_status（当前帧即时状态）与 export_ft8_results（导出文件）。")},
        // P2 #72: ADS-B aircraft snapshot (read-only).
        {"get_adsb_aircraft",
         QString::fromUtf8(
            "只读：返回最近 N 架已解码 ADS-B 飞机（按最后看到时间倒序），"
            "每条字段 icao（24bit hex）/callsign（呼号）/altitude_ft（高度 ft）/"
            "lat/lon（经纬度，has_position=false 时 null）/groundspeed_kt（地速 kt）/"
            "heading_deg（航向）/vertical_rate_fpm（垂直速率 fpm，has_velocity 标记）/"
            "df（下行格式）/crc_ok（CRC 通过）/last_seen（墙钟毫秒）。"
            "前置条件: 无（limit 可选 1..50，默认 50，越界由参数校验拒绝）; "
            "1090 MHz 有信号且已调谐到 ADS-B 载波。"
            "失败恢复: 无失败路径——1090 MHz 无信号或未调谐时 aircraft=[] 诚实空态，"
            "不编造飞机; 无位置/速度的字段返回 null 而非 0。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读引擎快照，不触发解调; 区别于 get_ft8_status / get_ft8_history（解码器结果面）。")},
        // P3 #73/#74: local memory family + checkpoint writer control plane.
        {"memory_add",
         QString::fromUtf8(
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
            "读用 memory_search，删用 memory_delete，统计用 memory_stats。")},
        {"memory_search",
         QString::fromUtf8(
            "只读：全文检索本地记忆库（中英文均可）。query 为自然语言词串，"
            "词元经安全转义后走 FTS5 MATCH；可选 scope/scope_id 过滤；limit 1..100 默认 20。"
            "前置条件: query 必填且不可为空（空白串会被 trim 后判空并报错）; "
            "~/.mbdsdr/memory.db 可打开。"
            "失败恢复: query 为空时明确报错; 库不可用（权限/损坏）时 ok:false 并附真实错误"
            "与排查提示; 无命中返回 hits=[] 空数组并附 note（附检索词），不编造记忆。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只检索已落库条目——刚 memory_add/update 的条目在后台 writer 落库前"
            "检索不到（先 memory_flush）; 写用 memory_add/memory_update，删用 memory_delete。")},
        {"memory_update",
         QString::fromUtf8(
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
            "统计用 memory_stats。")},
        {"checkpoint_status",
         QString::fromUtf8(
            "只读：返回后台 checkpoint writer 状态——pending_count/queue_depth（待落库条目数）、"
            "last_flush_ms（最近一次落库完成的墙钟毫秒，0=从未落库）。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——writer 未启动过时 last_flush_ms=0 是诚实初值，不编造时间。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读队列 introspection; 主 agent 只读不写; 排空落库用 memory_flush。")},
        {"memory_flush",
         QString::fromUtf8(
            "写入：同步排空记忆队列并落库（memory_store.add/update），重写 ~/.mbdsdr/checkpoint.md "
            "人类可读快照（时间戳+本次落库条数+key 列表），返回 written 实际落库条数。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: 无参数（任意时刻可调）; ~/.mbdsdr 目录可写; 队列里有待落库条目时才有意义。"
            "失败恢复: 部分条目落库失败时 ok:false 并附已处理条数与真实错误——修好磁盘/权限"
            "后重试; 队列为空时 written=0 正常返回（不是错误）。"
            "副作用: 真正写 SQLite 与 checkpoint.md 快照文件; 落库后 memory_search 立即可检索。"
            "能力边界: 只 flush 不删除; 队列深度用 checkpoint_status 读取; "
            "memory_add/update 的异步语义靠本工具兜底立即落库。")},
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
            "只动 splitter 与右侧 tab 可见性，不碰 DSP/音频/硬件。属于写动作，手动模式下被拦截。"
            "前置条件: WorkspaceController 已由 MainWindow 注入（headless 返回 "
            "CONTROLLER_NOT_INJECTED 诚实错误）; op 可选枚举三项; components 为 A2UI 节点数组。"
            "失败恢复: 控制器未注入时明确报错（不假装应用）; 未知组件 id 进 placeholders "
            "空态而非报错——用 list_ui_components 核对合法 id 后重发; 当前布局用 "
            "get_workspace_layout 读回。"
            "副作用: 实时改变主界面三栏尺寸与右侧 tab 可见性（用户可见）。"
            "能力边界: 只改布局不改数据（数据槽用 update_component_data）; "
            "回默认布局用 reset_workspace; 不碰 DSP/音频/硬件。")},
        {"get_workspace_layout",
         QString::fromUtf8(
            "只读：返回当前工作台布局模型——splitter_sizes[L,M,R]、splitter_stretch[a,b,c]、"
            "visible_tabs（tab:XXX 列表）、current_tab、hidden_panels、placeholders。"
            "默认三栏 280/800/280 stretch 1/3/1，14 个右侧 tab 全部可见。"
            "前置条件: 无（任意时刻可调）; WorkspaceController 已由 MainWindow 注入。"
            "失败恢复: 控制器未注入时返回 CONTROLLER_NOT_INJECTED 诚实错误，不返回假装布局。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读当前模型; 改布局用 arrange_workspace / reset_workspace; "
            "组件数据槽经 component_data 字段读回。")},
        {"list_ui_components",
         QString::fromUtf8(
            "只读：返回编译期白名单组件目录——每项 {id,category,capabilities[],min_size}。"
            "共 23 项：splitter/left_rail/center_spectrum/right_tabs/status_bar + 14 个 tab:XXX "
            "+ 4 个 center:XXX。agent 编排工作台前先查此目录确认可引用的 id。"
            "前置条件: 无（任意时刻可调）; WorkspaceController 已由 MainWindow 注入。"
            "失败恢复: 控制器未注入时返回 CONTROLLER_NOT_INJECTED 诚实错误。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读目录; 未知 id 在 arrange_workspace 里进 placeholders 空态而非报错——"
            "先用本工具核对再下发布局。")},
        {"reset_workspace",
         QString::fromUtf8(
            "写入：把工作台重置为默认三栏布局（280/800/280 stretch 1/3/1，14 个右侧 tab 全部可见），"
            "等价于 deleteSurface。只动 splitter 与 tab 可见性，不碰 DSP/音频/硬件。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: 无参数（任意时刻可调）; WorkspaceController 已由 MainWindow 注入。"
            "失败恢复: 控制器未注入时返回 CONTROLLER_NOT_INJECTED 诚实错误（不假装重置）——"
            "在桌面端主界面就绪后重试。"
            "副作用: 丢弃当前自定义布局（用户可见，无撤销接口），回到默认三栏。"
            "能力边界: 只重置布局; 自定义布局用 arrange_workspace; 当前布局读回用 "
            "get_workspace_layout; 不碰 DSP/音频/硬件。")},
        {"update_component_data",
         QString::fromUtf8(
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
            "读回用 get_workspace_layout。")},
        {"get_aprs_messages",
         QString::fromUtf8(
            "读取：最近解出的 APRS/AX.25 消息（最多 limit 条，1..50，默认 50）。"
            "每条含 time_ms/src/dst/path/info。源为 1200/2400 baud FSK 解调后的 NRZI "
            "位流经 HDLC 拆帧（CRC-16/X.25 校验）与 AX.25 解码；无效帧丢弃，无解码"
            "则诚实空态。只读工具。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径; limit 越界由参数校验拒绝（不静默 clamp）。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读引擎已解出的快照，不触发解调; 只返回最近 limit 条。")},
        {"get_color_maps",
         QString::fromUtf8(
            "读取：列出瀑布图可用色板——内置 3 个（经典/单色/Viridis，palette 0/1/2）"
            "与当前配置的外部 JSON 色板文件（如有：path/name/stops）。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——外部文件缺失或已损坏时 external 为 null 并附诚实 note，"
            "不编造; 内置 3 项恒定可用。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读色板目录与当前配置; 加载/持久化外部色板用 set_color_map。")},
        {"list_templates",
         QString::fromUtf8(
            "读取：列出内置确定性任务模板目录——每项 {name, description, params[], steps}。"
            "共 3 个：sweep_find_record（扫频找信号并存档，参数 low_hz/high_hz/step_hz/mode/book_name，6 步）、"
            "target_capture（目标频率捕获录制，参数 target_hz/mode，4 步）、"
            "fixed_freq_record（固定频率录制解码，参数 freq_hz/mode/bandwidth_hz，5 步）。"
            "模板机制来自内部既有实现，不预置场景/呼号/TLE。只读工具，手动模式不拦截。"
            "前置条件: 无。"
            "失败恢复: 无失败路径——目录恒定返回 3 项，空目录即内部实现异常（不应出现）。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读目录; 构建并登记计划用 run_template。")},
        {"run_template",
         QString::fromUtf8(
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
            "进度用 get_task_history 的 pending_plans 查询; 模板目录用 list_templates。")},
        // G1: task-run history (read-only).
        {"get_task_history",
         QString::fromUtf8(
            "只读：查询 TaskRunner 已完成任务的历史（落于本地记忆库 task_runs 表，跨重启保留）。"
            "前置条件: 无（任意时刻可调）; ~/.mbdsdr/memory.db 可打开。"
            "失败恢复: 库不可用时 ok:false 并附真实错误; 无记录时 runs=[] 并附诚实 note，"
            "不编造历史; 传 id 查详情但 id 不存在时 ok:false 并提示先用列表模式查真实 id。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 列表模式（默认）按完成时间倒序返回最近 limit 条（1..50 默认 10），"
            "每条含 id/name/state（succeeded/failed/aborted/gated/partial）/finished_ms/"
            "step_count/summary，并附 pending_plans/pending_count（run_template 登记的待执行计划快照）; "
            "传 id 进入详情模式，额外返回逐步 steps 数组（tool/state/summary/elapsed_ms/error）。")},
        // G2: memory_delete (gated write) + memory_stats (read-only).
        {"memory_delete",
         QString::fromUtf8(
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
            "清空全库无此接口（按 key 逐条删）。")},
        {"memory_stats",
         QString::fromUtf8(
            "只读：本地记忆库统计——total_entries（条目总数）、by_scope（user/agent/session 分布）、"
            "last_updated_ms（最近写入墙钟毫秒，0=空库）、task_runs（已完成任务数，G1 历史）。"
            "前置条件: 无（任意时刻可调）; ~/.mbdsdr/memory.db 可打开。"
            "失败恢复: 库不可用时 ok:false 并附真实错误; 空库全 0 诚实返回，不编造数据。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只读统计; 逐条内容检索用 memory_search; 队列状态另有 checkpoint_status。")},
        // Read-only TLE-cache catalog (prerequisite for predict_passes).
        {"list_satellites",
         QString::fromUtf8(
            "只读：列出本地 TLE 缓存中的卫星目录（名、NORAD 编号、TLE 历元、龄期天数、是否新鲜、标称下行频率）。"
            "这是 predict_passes 的前置——先列出可用卫星再选名预测过境。"
            "前置条件: 无（任意时刻可调）。"
            "失败恢复: 无失败路径——无缓存时诚实返回空态（source=none），"
            "不使用陈旧内置演示数据，不编造卫星; 缓存存在但无条目时附 note。"
            "副作用: 无（只读，不改写任何硬件/设置/文件状态）。"
            "能力边界: 只列缓存目录; 每条含 name/catalog_number/epoch_utc/age_days/fresh/downlink_hz; "
            "预测过境用 predict_passes。")},




        // D1 tool-gap fill (2026-10-30 reverse audit): verbatim pairs for the
        // 19 new gated writes (extracted from tool_schema.cpp to stay byte-identical).
        {"set_gain",
         QString::fromUtf8(
            "写入：设置接收链增益（dB）。属于写动作，手动模式下被拦截。"
            "前置条件: gain_db 必填，范围 -90.0..49.6（负值=RF 衰减，与 UI 滑杆同一语义）。"
            "失败恢复: 越界由参数校验层明确拒绝（不静默夹值）——改到范围内重试。"
            "副作用: 立即下发硬件增益（请求值会被硬件就近取整到实际档位，"
            "可用 get_capabilities 的 gains_db 看实测档表）。"
            "能力边界: 只设增益; AGC 开启时手动档可能被覆盖（用 set_agc 关 AGC）; "
            "读回用 get_status（未暴露增益字段时以硬件为准）。")},
        {"set_sample_rate",
         QString::fromUtf8(
            "写入：设置 IQ 采样率（Hz）。属于写动作，手动模式下被拦截。"
            "前置条件: sample_rate_hz 必填，只接受枚举四档"
            "（1024000 / 2048000 / 2400000 / 3200000，与 UI 下拉同一能力表）。"
            "失败恢复: 枚举外的采样率被参数校验层明确拒绝（不静默改写）——"
            "按枚举取值重试。"
            "副作用: 立即重配硬件采样率与抽取链，频谱/音频显示随之变化。"
            "能力边界: 只设采样率; 抽取系数另用 set_decimation; "
            "读回用 get_status（未暴露采样率字段时以硬件为准）。")},
        {"set_decimation",
         QString::fromUtf8(
            "写入：设置前端抽取系数（抗混叠抽取，关=1）。属于写动作，手动模式下被拦截。"
            "前置条件: factor 必填，取 1/2/4/8（与 UI 下拉 关/×2/×4/×8 一致）。"
            "失败恢复: 枚举外的系数被参数校验层明确拒绝——按枚举取值重试。"
            "副作用: 立即改变前端抽取链，等效基带带宽随系数收窄。"
            "能力边界: 只设抽取; 采样率另用 set_sample_rate。")},
        {"set_direct_sampling",
         QString::fromUtf8(
            "写入：设置 RTL-SDR 直采模式（绕过调谐器）。属于写动作，手动模式下被拦截。"
            "前置条件: mode 必填，取 off/i/q（与 UI 下拉 关/I/Q 一致）。"
            "失败恢复: 枚举外的模式被参数校验层明确拒绝——按枚举取值重试。"
            "副作用: 立即切换硬件采样路径，频率语义与常规调谐不同（直采无调谐器增益）。"
            "能力边界: 只设直采开关; 天线接 BNC/I/Q 输入由用户自理。")},
        {"set_offset_tuning",
         QString::fromUtf8(
            "写入：开启/关闭偏移调谐（避开本振泄漏）。属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔。"
            "失败恢复: 非布尔被参数校验层明确拒绝——改正后重试。"
            "副作用: 立即改变调谐策略，中心频率显示语义随之偏移（状态仍以 get_status 为准）。"
            "能力边界: 只设偏移调谐开关。")},
        {"set_agc",
         QString::fromUtf8(
            "写入：设置 RTL AGC / Tuner AGC 开关（与 UI 两个复选框同一状态）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: rtl_agc 与 tuner_agc 至少提供其一（全缺则不下发任何变更）。"
            "失败恢复: 非布尔字段被静默跳过——用 get_status/硬件读回核对; "
            "全缺入参视为无操作。"
            "副作用: 立即改变 AGC 状态; Tuner AGC 开启时手动增益档可能被覆盖。"
            "能力边界: 只设 AGC 开关; 增益档另用 set_gain。")},
        {"set_bias_tee",
         QString::fromUtf8(
            "写入：开启/关闭 Bias-T 天线馈电（硬件供电）。属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔。"
            "失败恢复: 非布尔被参数校验层明确拒绝——改正后重试。"
            "副作用: 立即向天线口输出/切断馈电（约 5 V）——为不接受馈电的设备"
            "（天线/滤波器/其他接收机）上电可能损坏，确认线路后再开。"
            "能力边界: 只设 Bias-T 开关; 发射类操作不在本工具面。")},
        {"set_force_mono",
         QString::fromUtf8(
            "写入：开启/关闭强制单声道（WFM 立体声混合置 0）。属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔。"
            "失败恢复: 非布尔被参数校验层明确拒绝——改正后重试。"
            "副作用: 立即改变音频混合（立体声→单声道），音频输出随之变化。"
            "能力边界: 只设单声道开关; 不改解调模式。")},
        {"set_anr",
         QString::fromUtf8(
            "写入：设置 ANR 音频降噪（开关+强度）。属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔; strength_pct 可选（0..100，与 UI 滑杆同刻度，"
            "引擎内部折算 0..1，100=最强抑制）。"
            "失败恢复: strength_pct 越界由参数校验层明确拒绝（不静默夹值）——"
            "改到 0..100 重试; enabled 非布尔同样被校验层拒绝。"
            "副作用: 立即改变音频降噪状态/强度，音频输出随之变化。"
            "能力边界: 只设 ANR; 不改静噪/亚音门控。")},
        {"set_recording_target",
         QString::fromUtf8(
            "写入：设置录制对象（IQ SigMF 或音频 WAV）。属于写动作，手动模式下被拦截。"
            "前置条件: target 必填，取 iq_sigmf/audio_wav。"
            "失败恢复: 枚举外的目标被参数校验层明确拒绝——按枚举取值重试。"
            "副作用: 立即改变 start_recording 的落盘形态（下一段录制生效）。"
            "能力边界: 只设录制对象; 目录/模板/立体声分别用 "
            "set_recording_dir/set_recording_template/set_recording_stereo。")},
        {"set_recording_dir",
         QString::fromUtf8(
            "写入：设置录制目录。属于写动作，手动模式下被拦截。"
            "前置条件: dir 必填非空字符串（绝对/相对路径均可，目录须可写）。"
            "失败恢复: 空串被明确拒绝——改正后重试; 写入失败在录制时诚实报错，"
            "不会假装落盘。"
            "副作用: 下一段录制写入新目录; 进行中的录制不受影响。"
            "能力边界: 只设目录; 读回用 get_recording_state。")},
        {"set_recording_template",
         QString::fromUtf8(
            "写入：设置录制文件名模板。属于写动作，手动模式下被拦截。"
            "前置条件: template 必填非空字符串。"
            "失败恢复: 空串被明确拒绝——改正后重试。"
            "副作用: 下一段录制按新模板命名; 进行中的录制不受影响。"
            "能力边界: 只设文件名模板; 目录另用 set_recording_dir。")},
        {"set_recording_stereo",
         QString::fromUtf8(
            "写入：开启/关闭录制立体声。属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔。"
            "失败恢复: 非布尔被参数校验层明确拒绝——改正后重试。"
            "副作用: 立即改变音频录制声道形态（下一段录制生效）。"
            "能力边界: 只设立体声开关; 录制对象另用 set_recording_target。")},
        {"set_recording_ignore_squelch",
         QString::fromUtf8(
            "写入：设置录制是否忽略静噪（静噪关断时也继续录）。属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔。"
            "失败恢复: 非布尔被参数校验层明确拒绝——改正后重试。"
            "副作用: 立即改变录制对静噪状态的响应。"
            "能力边界: 只设该开关; 静噪本身用 set_squelch。")},
        {"set_recording_max_segment",
         QString::fromUtf8(
            "写入：设置录制分段上限（秒）。属于写动作，手动模式下被拦截。"
            "前置条件: seconds 必填，范围 5..3600（引擎实界；UI 面板取 30..3600）。"
            "失败恢复: 越界由参数校验层明确拒绝（不静默夹值）——改到范围内重试。"
            "副作用: 录制到达上限自动分段换文件。"
            "能力边界: 只设分段上限; 触发式分段用 set_gated_recording。")},
        {"set_gated_recording",
         QString::fromUtf8(
            "写入：开启/关闭触发式分段录制（按信号门控）。属于写动作，手动模式下被拦截。"
            "前置条件: enabled 必填布尔。"
            "失败恢复: 非布尔被参数校验层明确拒绝——改正后重试。"
            "副作用: 立即改变录制分段策略（门控开=按信号分段）。"
            "能力边界: 只设触发式分段开关; 值守录制（信号触发起停）用 set_watch_recording。")},
        {"set_watch_recording",
         QString::fromUtf8(
            "写入：设置值守录制（信号触发录制）开关/触发门限/前滚/结束延时。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: enabled/threshold_db/preroll_ms/hang_ms 至少提供其一"
            "（全缺则不下发任何变更）; threshold_db 为 RSSI 触发门限（dBFS，-100..-20）; "
            "preroll_ms 前滚 100..2000; hang_ms 结束延时 300..10000。"
            "失败恢复: 任一越界由参数校验层明确拒绝（不静默夹值）——改到范围内重试; "
            "类型不符的字段被静默跳过。"
            "副作用: 立即改变值守录制行为（门控起停、带前滚落盘）。"
            "能力边界: 只设值守录制; 连续录制用 start_recording; 读回用 get_recording_state。")},
        {"set_station_position",
         QString::fromUtf8(
            "写入：设置本站位置（经纬度/海拔）并持久化（AiConfig）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: lat_deg（-90..90）与 lon_deg（-180..180）必填; "
            "alt_m 可选（-500..9000 米）。"
            "失败恢复: 越界由参数校验层明确拒绝（不静默夹值）; 持久化失败诚实 ok:false。"
            "副作用: 写入 AiConfig 并同步 ADS-B 参考点; predict_passes 的缺省站点随之更新"
            "（单次调用仍可用 per-call 参数覆盖）。"
            "能力边界: 只设本站位置; 卫星多普勒补偿还依赖过境捕获"
            "（set_doppler_compensation 的 available 回读为准），不是设了位置就自动可用。")},
        {"vna_connect",
         QString::fromUtf8(
            "写入：连接/断开 NanoVNA 串口（此前只能人在 UI 点「连接」，AI 够不着）。"
            "属于写动作，手动模式下被拦截。"
            "前置条件: action=connect（缺省）时 port 必填非空"
            "（如 /dev/ttyACM0 或 COM3）; action=disconnect 不需要 port。"
            "失败恢复: 连接失败（端口不存在/被占用/握手失败）时 ok:false 并带错误串，"
            "绝不假装已连; 用 get_vna_status 复核 connected。"
            "副作用: 打开/关闭串口并跑版本握手（硬件状态）。"
            "能力边界: 只管连接; 扫频参数用 set_vna_sweep，校准用 vna_calibration。")},
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

// ---- phase72 描述质量门禁（模型体验轴，DSH 四维标准） ------------------

static bool containsCjk(const QString& s) {
    for (const QChar& c : s) {
        const ushort u = c.unicode();
        if (u >= 0x4E00 && u <= 0x9FFF) return true;
    }
    return false;
}

static int dimensionHits(const QString& desc) {
    int n = 0;
    const QStringList dims = {
        QString::fromUtf8("前置条件"), QString::fromUtf8("失败恢复"),
        QString::fromUtf8("副作用"), QString::fromUtf8("能力边界")};
    for (const QString& d : dims)
        if (desc.contains(d)) ++n;
    return n;
}

// 全工具面语言地板 + 无占位符：模型读不懂英文描述——phase72 前 set_mode 等
// 6 个核心工具曾整句英文（"Set demodulation mode." 等），此槽锁死不再回退。
void TestToolSchema::descriptionsAreChineseWithoutPlaceholders() {
    const QList<ToolSchemaSpec> specs = registeredToolSpecs();
    const QStringList stubs = {
        QStringLiteral("TODO"), QStringLiteral("TBD"),
        QString::fromUtf8("待补充"), QString::fromUtf8("待实现"),
        QStringLiteral("FIXME"), QStringLiteral("coming soon")};
    for (const ToolSchemaSpec& s : specs) {
        QVERIFY2(containsCjk(s.description),
                 qPrintable(QStringLiteral("工具描述非中文: %1").arg(s.name)));
        QVERIFY2(s.description.trimmed().length() >= 50,
                 qPrintable(QStringLiteral("工具描述过短: %1").arg(s.name)));
        for (const QString& t : stubs)
            QVERIFY2(!s.description.contains(t, Qt::CaseInsensitive),
                     qPrintable(QStringLiteral("工具描述含占位残语 %1: %2")
                                    .arg(t, s.name)));
        for (const ToolParamSpec& p : s.params) {
            if (p.description.trimmed().isEmpty())
                continue;  // 空描述 => schema 省略该键，是合法设计
            QVERIFY2(containsCjk(p.description),
                     qPrintable(QStringLiteral("参数描述非中文: %1.%2")
                                    .arg(s.name, p.name)));
        }
    }
}

// 核心 7 工具四维齐全：模型对工具的第一屏认知全来自 description，缺维 = 盲飞。
void TestToolSchema::coreToolsHaveFourDimensions() {
    const QList<ToolSchemaSpec> specs = registeredToolSpecs();
    const QStringList core = {
        QStringLiteral("tune_frequency"), QStringLiteral("set_mode"),
        QStringLiteral("start_recording"), QStringLiteral("stop_recording"),
        QStringLiteral("scan_band"), QStringLiteral("set_bandwidth"),
        QStringLiteral("get_status")};
    for (const QString& name : core) {
        const ToolSchemaSpec* s = findSpec(specs, name);
        QVERIFY2(s != nullptr,
                 qPrintable(QStringLiteral("核心工具缺失: %1").arg(name)));
        const int hits = dimensionHits(s->description);
        QVERIFY2(hits == 4, qPrintable(QStringLiteral("核心工具四维不全(%1/4): %2")
                                           .arg(hits).arg(name)));
    }
}

// 四维覆盖率棘轮：phase74 起全工具面 90/90 四维齐全（phase73 为 50）——只允许升，
// 不允许降。抬阈值前先真把描述写实（门禁只查「有没有四维」，属实性靠读码实证，
// 见 phase67 教训：描述必须对着 handler 逐行核对后再写；phase74 把剩余 40 个
// 只读工具逐个对照 handler 实证后补齐，并修正 phase73 台账一处事实错误——
// predict_passes/calibrate_frequency 均为 write=false 只读工具，非写工具）。
void TestToolSchema::fourDimensionCoverageRatchet() {
    const QList<ToolSchemaSpec> specs = registeredToolSpecs();
    int full = 0;
    for (const ToolSchemaSpec& s : specs)
        if (dimensionHits(s.description) == 4) ++full;
    qInfo("四维齐全工具数: %d / %d", full, int(specs.size()));
    QVERIFY2(full >= int(specs.size()),
             qPrintable(QStringLiteral("四维覆盖率回退: %1 < %2")
                            .arg(full).arg(int(specs.size()))));
}

QTEST_MAIN(TestToolSchema)
#include "test_tool_schema.moc"
