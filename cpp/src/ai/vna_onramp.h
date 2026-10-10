// SPDX-License-Identifier: MIT
// NanoVNA "一句话测驻波" 引导与判定纯函数（产品内离线确定性路径）。
//
// 把 VSWR 经验阈值、四分支引导文案、围绕目标频率的扫频规划、读数解读全部收在
// 这里——无 Qt 控件、无串口、无 IO，仅依赖 QJsonObject/QString 解析由工具层喂入的
// 真实结果。产品路径面对真实串口，本模块不内置任何假数据；测试用自造回放 JSON。
#pragma once

#include <QString>
#include <QStringList>

#include <optional>

class QJsonObject;

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }
namespace ai {

// VSWR 经验门限（工程约定，非设备读出值）。
constexpr double kVswrGoodMax = 1.5;  // < 1.5 良好
constexpr double kVswrOkMax   = 2.0;  // 1.5..2.0 可用；> 2.0 建议检查

// 目标频点的派生读数（由 get_vna_data 平行数组取最近点后填入）。
struct VswrReading {
    double targetHz = 0.0;
    double vswr = 0.0;
    double returnLossDb = 0.0;
    double zReal = 0.0;
    double zImag = 0.0;
};

struct VswrReport {
    QString status;           // disconnected | uncalibrated | good | ok | bad
    QString title;
    QString readingsText;
    QStringList advice;
};

// "驻波/VSWR/天线驻波"意图识别。
bool isVswrIntent(const QString& input);

// 统一 VNA 意图：驻波/谐振晶体/TDR 电缆/L-C 测量。agent.cpp 只调这一个入口。
enum class VnaIntent { Vswr, Resonance, Tdr, Lc };
bool isVnaOnrampIntent(const QString& input);
VnaIntent classifyVnaIntent(const QString& input);

// 同轴线速度因子常量表（文档值）。未指定类型时列出选项、不硬编码假设。
// 返回 (vf, 规范名)；识别不到返回空。
std::optional<QPair<QString,double>> parseCableVf(const QString& input);
// 列出可选电缆类型与 vf（用于引导用户指定）。
QString cableVfOptionsText();

// 从问题里抓目标频率 MHz（如 "438.5MHz"）；无则返回空。仅解析用户输入，不硬编码默认。
std::optional<double> parseTargetMhz(const QString& input);

// VSWR 分档：good / ok / bad。
QString classifyVswr(double vswr);

// 围绕目标频率规划对称扫频：返回 (startHz, stopHz, points)，保证 start>=0、stop>start。
void planSweep(long targetHz, long& startHz, long& stopHz, int& points,
               long spanHz = 1'000'000, int pointsHint = 101);

// 在 freqs 平行数组里找离 targetHz 最近的下标；空数组返回 -1。
int nearestIndex(const QList<double>& freqs, double targetHz);

// 结构化解读（纯函数）。connected=false 给接线引导；connected 但未校准给 OSL 引导；
// 有读数则按 VSWR 分档。
VswrReport interpretVswr(bool connected, bool calibrated,
                         const std::optional<VswrReading>& reading);

// 把解读渲染成一段可直接回给用户的中文对话文本。
QString renderReport(const VswrReport& r);

// 端到端编排：解析目标频率 -> 真实调 get_vna_status/set_vna_sweep/get_vna_data ->
// 纯函数判定 -> 返回对话文本。未连接诚实空态，不内置假数据。测试可直接调用。
QString runVswrOnramp(dsp::SpectrumEngine* engine, const QString& input);

} // namespace ai
} // namespace mbdsdr
