// SPDX-License-Identifier: MIT
#include "vna_onramp.h"

#include "agent_tools.h"
#include "dsp/spectrum_engine.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>

namespace mbdsdr {
namespace ai {

bool isVswrIntent(const QString& input) {
    static const QRegularExpression re(
        QString::fromUtf8("(驻波|驻波比|vswr|smith|天线.*(匹配|驻波)|回波)"),
        QRegularExpression::CaseInsensitiveOption);
    return re.match(input.trimmed()).hasMatch();
}

std::optional<double> parseTargetMhz(const QString& input) {
    static const QRegularExpression re(
        R"((\d+(?:\.\d+)?)\s*(?:MHz|兆赫|兆周))");
    auto m = re.match(input);
    if (!m.hasMatch()) return std::nullopt;
    bool ok = false;
    double v = m.captured(1).toDouble(&ok);
    return ok ? std::optional<double>(v) : std::nullopt;
}

QString classifyVswr(double vswr) {
    if (vswr < kVswrGoodMax) return QStringLiteral("good");
    if (vswr <= kVswrOkMax)  return QStringLiteral("ok");
    return QStringLiteral("bad");
}

void planSweep(long targetHz, long& startHz, long& stopHz, int& points,
               long spanHz, int pointsHint) {
    long half = spanHz / 2;
    long s = targetHz - half;
    long t = targetHz + half;
    if (s < 0) s = 0;
    if (t <= s) t = s + half * 2;
    startHz = s; stopHz = t; points = pointsHint;
}

int nearestIndex(const QList<double>& freqs, double targetHz) {
    if (freqs.isEmpty()) return -1;
    int best = 0;
    double bd = std::abs(freqs[0] - targetHz);
    for (int i = 1; i < freqs.size(); ++i) {
        double d = std::abs(freqs[i] - targetHz);
        if (d < bd) { bd = d; best = i; }
    }
    return best;
}

VswrReport interpretVswr(bool connected, bool calibrated,
                         const std::optional<VswrReading>& reading) {
    if (!connected) {
        return VswrReport{
            QStringLiteral("disconnected"),
            QString::fromUtf8("未检测到 NanoVNA"),
            QString(),
            {
                QString::fromUtf8("请用 USB 线连接 NanoVNA（枚举 VID:PID 0483:5740），待串口设备出现。"),
                QString::fromUtf8("把待测天线接到 PORT 1（S11 反射口），端口 2 接匹配负载。"),
                QString::fromUtf8("Linux 下确认当前用户在 dialout/uucp 组并配置了 udev 权限后重新插拔。"),
            }};
    }
    if (!calibrated) {
        return VswrReport{
            QStringLiteral("uncalibrated"),
            QString::fromUtf8("已连接，但尚未校准"),
            QString(),
            {
                QString::fromUtf8("当前为未补偿的裸测量，VSWR 仅供参考。"),
                QString::fromUtf8("建议先做 OSL 校准：在 PORT 1 依次接开路(open)/短路(short)/负载(load)，执行 cal 后重测。"),
            }};
    }
    VswrReading r = reading.value_or(VswrReading{});
    QString rt = QString::fromUtf8("在 %1 MHz：VSWR %2，回波损耗 %3 dB")
                     .arg(r.targetHz / 1e6, 0, 'f', 3)
                     .arg(r.vswr, 0, 'f', 2)
                     .arg(r.returnLossDb, 0, 'f', 1);
    QString z = QString::fromUtf8("，阻抗 %4%5%+6j Ω")
                    .arg(r.zReal, 0, 'f', 1).arg(r.zImag, 0, 'f', 1);
    rt += z;

    QString band = classifyVswr(r.vswr);
    if (band == "good") {
        return VswrReport{
            QStringLiteral("good"), QString::fromUtf8("匹配良好"), rt,
            {
                QString::fromUtf8("VSWR %1 低于 %2：此频点匹配良好，可放心使用。")
                    .arg(r.vswr, 0, 'f', 2).arg(kVswrGoodMax),
                QString::fromUtf8("如需全段结论，可加大扫频 span 重测，观察整段是否都低于门限。"),
            }};
    }
    if (band == "ok") {
        return VswrReport{
            QStringLiteral("ok"), QString::fromUtf8("匹配可用"), rt,
            {
                QString::fromUtf8("VSWR %1 在 %2-%3：可用但非最佳，注意反射功率。")
                    .arg(r.vswr, 0, 'f', 2).arg(kVswrGoodMax).arg(kVswrOkMax),
                QString::fromUtf8("建议检查接头是否拧紧、天线是否靠近金属/地面、是否偏离谐振频点。"),
            }};
    }
    return VswrReport{
        QStringLiteral("bad"), QString::fromUtf8("驻波偏高，建议检查"), rt,
        {
            QString::fromUtf8("VSWR %1 高于 %2：反射较大，发射可能触发驻波保护。")
                .arg(r.vswr, 0, 'f', 2).arg(kVswrOkMax),
            QString::fromUtf8("排查：①接头与接地 ②是否未接天线/开路 ③重跑 OSL 校准 ④确认扫频覆盖谐振点。"),
        }};
}

QString renderReport(const VswrReport& r) {
    QString out = r.title;
    if (!r.readingsText.isEmpty()) out += QStringLiteral("\n") + r.readingsText;
    for (const QString& a : r.advice) out += QStringLiteral("\n- ") + a;
    return out;
}

QString runVswrOnramp(dsp::SpectrumEngine* engine, const QString& input) {
    if (!engine) return QString::fromUtf8("无引擎连接，无法测量驻波。");
    auto tgt = parseTargetMhz(input);
    if (!tgt.has_value())
        return QString::fromUtf8("请在问题里给出目标频率，例如：“438.5MHz 驻波是多少？”");

    // 1) get_vna_status（真实工具，留审计轨迹）。注意：该工具 JSON 的 "connected"
    // 字段会被 addSourceFields 覆盖为 SDR 源连接，故连通性以 VNA 客户端真实状态为准。
    executeTool("get_vna_status", QJsonObject(), engine, nullptr);
    vna::NanoVnaClient& vna = engine->vnaClient();
    bool connected = vna.isConnected();

    // 已连接则主动刷新一次 cal。
    bool calibrated = false;
    if (connected) {
        for (const QString& c : vna.readCalStatus())
            if (c.contains(QStringLiteral("cal'ed"))) calibrated = true;
    }

    if (!connected || !calibrated)
        return renderReport(interpretVswr(connected, calibrated, std::nullopt));

    // 2) set_vna_sweep 围绕目标频率；3) get_vna_data 取平行数组。
    long tgtHz = (long)(tgt.value() * 1e6);
    long s0, s1; int pts;
    planSweep(tgtHz, s0, s1, pts);
    QJsonObject sweepArgs;
    sweepArgs["start_hz"] = (double)s0; sweepArgs["stop_hz"] = (double)s1;
    sweepArgs["points"] = pts;
    executeTool("set_vna_sweep", sweepArgs, engine, nullptr);

    QJsonObject data = QJsonDocument::fromJson(
        executeTool("get_vna_data", QJsonObject(), engine, nullptr).toUtf8()).object();
    QJsonArray fa = data.value("frequencies").toArray();
    QJsonArray va = data.value("vswr").toArray();
    QJsonArray ra = data.value("return_loss_db").toArray();
    QJsonArray za = data.value("z_real").toArray();
    QJsonArray zb = data.value("z_imag").toArray();
    QList<double> freqs;
    for (const QJsonValue& v : fa) freqs.append(v.toDouble());
    int idx = nearestIndex(freqs, (double)tgtHz);
    if (idx < 0 || idx >= va.size())
        return renderReport(interpretVswr(true, true, std::nullopt));

    VswrReading r;
    r.targetHz = tgtHz;
    r.vswr = va.at(idx).toDouble();
    r.returnLossDb = ra.at(idx).toDouble();
    r.zReal = idx < za.size() ? za.at(idx).toDouble() : 0.0;
    r.zImag = idx < zb.size() ? zb.at(idx).toDouble() : 0.0;
    return renderReport(interpretVswr(true, true, r));
}

} // namespace ai
} // namespace mbdsdr
