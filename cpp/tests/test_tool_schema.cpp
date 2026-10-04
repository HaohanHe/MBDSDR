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
    QCOMPARE(specs.size(), 35);
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
        {"tune_frequency", "Tune the receiver to a center frequency in Hz."},
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
            "（地址 RIC/功能位/文本）。无解码结果时诚实返回空列表，不编造消息。")},
        {"get_m17_calls",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）信道已解码的 M17 呼叫/帧快照"
            "（源/目的呼号、类型、CRC 状态、语音帧诚实标注未解码）。"
            "无解码结果时诚实返回空列表。")},
        {"get_vor_radial",
         QString::fromUtf8(
            "只读：读取指定（默认当前选中）VOR 信道最新径向读数"
            "（radialDeg 方位、质量、莫尔斯识别码、锁定态）。"
            "未锁定时 locked=false，方位不可信并被显式标注，不编造方位。")},
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
            "enable 开关、port 端口、format 采样格式。属于写动作，手动模式下被拦截。")},
        {"get_network_audio_status",
         QString::fromUtf8(
            "只读：返回网络音频流状态（是否使能、端口、格式）。"
            "无状态时诚实返回 enabled=false，不编造端口。")},
        {"start_scan_link",
         QString::fromUtf8(
            "写入：启动扫描活动链路（扫描→命中→驻留→解码→录制），目标频率 target_freq_hz。"
            "属于写动作，手动模式下被拦截。")},
        {"stop_scan_link",
         QString::fromUtf8(
            "写入：停止扫描活动链路。属于写动作，手动模式下被拦截。")},
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
        {"list_bookmarks",
         QString::fromUtf8(
            "只读：列出书签（频率/名称/模式）。无书签时诚实返回空列表，不编造。")},
        {"add_bookmark",
         QString::fromUtf8(
            "写入：添加书签（freq_hz/name/mode）。属于写动作，手动模式下被拦截。")},
        {"tune_to_bookmark",
         QString::fromUtf8(
            "写入：调谐到指定下标书签的频率。属于写动作，手动模式下被拦截。")},
        {"delete_bookmark",
         QString::fromUtf8(
            "写入：删除指定下标书签。属于写动作，手动模式下被拦截。")},
        {"list_vfos",
         QString::fromUtf8(
            "只读：列出全部 VFO 信道（id/频率/带宽/模式/选中态）。")},
        {"add_vfo",
         QString::fromUtf8(
            "写入：新增一个 VFO 信道。属于写动作，手动模式下被拦截。")},
        {"switch_vfo",
         QString::fromUtf8(
            "写入：切换选中的 VFO 信道（index）。属于写动作，手动模式下被拦截。")},
        {"rename_vfo",
         QString::fromUtf8(
            "写入：重命名指定 VFO 信道（index/name）。属于写动作，手动模式下被拦截。")},
        {"list_recordings",
         QString::fromUtf8(
            "只读：扫描录制目录并列出已有录制文件。目录不存在或为空时诚实返回空列表。")},
        {"delete_recording",
         QString::fromUtf8(
            "写入：删除录制目录下指定名称的文件（仅限录制目录内）。"
            "属于写动作，手动模式下被拦截。")},
        {"export_recording",
         QString::fromUtf8(
            "写入：把录制目录下指定文件复制导出到 out_path。"
            "属于写动作，手动模式下被拦截。")},
        {"set_fft_params",
         QString::fromUtf8(
            "写入：设置频谱 FFT 参数（fft_size/window/average）。"
            "属于写动作，手动模式下被拦截。")},
        {"set_color_map",
         QString::fromUtf8(
            "写入：保存瀑布图色板文件路径到设置（headless 仅持久化偏好，重绘由 UI 持有）。"
            "属于写动作，手动模式下被拦截。")},
        {"get_spectrum_status",
         QString::fromUtf8(
            "只读：返回频谱当前参数（fft_size/window/average）真实值。")},
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

QTEST_MAIN(TestToolSchema)
#include "test_tool_schema.moc"
