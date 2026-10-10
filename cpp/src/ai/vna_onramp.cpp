// SPDX-License-Identifier: MIT
#include "vna_onramp.h"

#include "agent_tools.h"
#include "dsp/spectrum_engine.h"
#include "vna/vna_rf.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>

#include <cmath>

namespace mbdsdr {
namespace ai {

bool isVswrIntent(const QString& input) {
    static const QRegularExpression re(
        QString::fromUtf8("(驻波|驻波比|vswr|smith|天线.*(匹配|驻波)|回波)"),
        QRegularExpression::CaseInsensitiveOption);
    return re.match(input.trimmed()).hasMatch();
}

// ---- 统一 VNA 意图分类 -----------------------------------------------------
namespace {
struct CableVf { const char* name; const char* pat; double vf; };
const CableVf kCableTable[] = {
    {"RG-58",  "rg-?58",   0.66},
    {"RG-213", "rg-?213",  0.66},
    {"RG-8X",  "rg-?8x",   0.78},
    {"LMR-400","lmr-?400", 0.85},
};
}

std::optional<QPair<QString,double>> parseCableVf(const QString& input) {
    QString low = input.toLower();
    for (const CableVf& c : kCableTable) {
        if (low.contains(QRegularExpression(QString::fromLatin1(c.pat))))
            return QPair<QString,double>(QString::fromUtf8(c.name), c.vf);
    }
    return std::nullopt;
}

QString cableVfOptionsText() {
    QStringList items;
    for (const CableVf& c : kCableTable)
        items << QString::fromUtf8("%1 (vf≈%2)").arg(QString::fromUtf8(c.name)).arg(c.vf);
    return items.join(QStringLiteral("、"));
}

VnaIntent classifyVnaIntent(const QString& input) {
    QString low = input.toLower();
    if (low.contains(QRegularExpression(QString::fromUtf8("(谐振|晶体|晶振|晶振频|q值|esr|esr)"))))
        return VnaIntent::Resonance;
    if (low.contains(QRegularExpression(QString::fromUtf8("(电缆|多长|断了|断线|故障点|tdr)"))))
        return VnaIntent::Tdr;
    if (low.contains(QRegularExpression(QString::fromUtf8("(电感多大|电容多大|电感量|电容量|这个.*电感|这个.*电容)"))))
        return VnaIntent::Lc;
    return VnaIntent::Vswr;  // 默认驻波/天线匹配
}

bool isVnaOnrampIntent(const QString& input) {
    if (isVswrIntent(input)) return true;
    QString low = input.toLower();
    static const QRegularExpression re(
        QString::fromUtf8("(谐振|晶体|晶振|esr|q值|电缆|多长|断了|故障点|tdr|电感多大|电容多大|电感量|电容量)"),
        QRegularExpression::CaseInsensitiveOption);
    return re.match(low).hasMatch();
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

    VnaIntent kind = classifyVnaIntent(input);

    // ---- TDR 电缆：需用户指定电缆类型（速度因子），不硬编码假设 -------------
    if (kind == VnaIntent::Tdr) {
        auto cvf = parseCableVf(input);
        if (!cvf.has_value())
            return QString::fromUtf8(
                "请在问题里指定同轴电缆类型以取速度因子，例如“RG-58 电缆多长”。\n"
                "可选：") + cableVfOptionsText();
        QJsonObject tdrArgs; tdrArgs["velocity_factor"] = cvf->second;
        QJsonObject tdr = QJsonDocument::fromJson(
            executeTool("vna_tdr_cable", tdrArgs, engine, nullptr).toUtf8()).object();
        if (!tdr.value("valid").toBool(false))
            return QString::fromUtf8("未检出明显反射点（%1）。请确认已做 OSL 校准、电缆接 PORT1，"
                                     "并在更宽扫频上重测。").arg(tdr.value("note").toString());
        return QString::fromUtf8(
            "%1 TDR 分析（vf=%2）：\n- 估算电缆长度约 %3 m；\n- 首个反射（故障/开路/短路）约在 %4 m 处。")
            .arg(cvf->first).arg(cvf->second).arg(tdr.value("cable_length_m").toDouble(), 0, 'f', 2)
            .arg(tdr.value("distance_m").toDouble(), 0, 'f', 2);
    }

    // ---- 以下分析都需要一个中心频率 ----------------------------------------
    long tgtHz = tgt.has_value() ? (long)(tgt.value() * 1e6) : 0;

    // ---- 谐振/晶体：有标称频率则窄扫 ±10kHz；否则请用户给出标称 -------------
    if (kind == VnaIntent::Resonance) {
        if (!tgt.has_value())
            return QString::fromUtf8("请给出晶体标称频率，例如“8.000MHz 晶体谐振/Q 是多少”，"
                                     "以便在标称 ±10kHz 内窄扫。");
        long s0, s1; int pts;
        planSweep(tgtHz, s0, s1, pts, 20'000, 201);  // 窄 span 20kHz
        QJsonObject sw; sw["start_hz"]=(double)s0; sw["stop_hz"]=(double)s1; sw["points"]=pts;
        executeTool("set_vna_sweep", sw, engine, nullptr);
        QJsonObject rr = QJsonDocument::fromJson(
            executeTool("analyze_vna_resonance", QJsonObject(), engine, nullptr).toUtf8()).object();
        if (!rr.value("valid").toBool(false))
            return QString::fromUtf8("谐振分析无效（%1）。请确认已校准、扫频覆盖晶体标称点。")
                .arg(rr.value("note").toString());
        return QString::fromUtf8(
            "谐振分析：\n- 串联谐振 fr ≈ %1 MHz（Q≈%2，BW≈%3 Hz，ESR≈%4 Ω）；\n- 并联谐振 ≈ %5 MHz。")
            .arg(rr.value("series_fr_hz").toDouble()/1e6, 0, 'f', 4)
            .arg(rr.value("q").toDouble(), 0, 'f', 1)
            .arg(rr.value("bandwidth_hz").toDouble(), 0, 'f', 0)
            .arg(rr.value("esr").toDouble(), 0, 'f', 2)
            .arg(rr.value("parallel_fr_hz").toDouble()/1e6, 0, 'f', 4);
    }

    // ---- 默认：驻波/匹配（需目标频率） --------------------------------------
    if (!tgt.has_value())
        return QString::fromUtf8("请在问题里给出目标频率，例如：“438.5MHz 驻波是多少？”");

    // 2) set_vna_sweep 围绕目标频率；3) get_vna_data 取平行数组。
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

    // ---- L/C 测量：在目标频点由电抗派生 -----------------------------------
    if (kind == VnaIntent::Lc) {
        int j = nearestIndex(freqs, (double)tgtHz);
        if (j < 0 || j >= za.size() || j >= zb.size())
            return QString::fromUtf8("无扫频数据可派生 L/C。请确认已校准并有有效扫频。");
        double xs = zb.at(j).toDouble();
        double f = freqs.value(j, (double)tgtHz);
        double l = vna::reactanceToHenries(xs, f);
        double c = vna::reactanceToFarads(xs, f);
        if (l > 0)
            return QString::fromUtf8("在 %1 MHz（呈感性）：电感约 %2 nH。")
                .arg(f/1e6, 0, 'f', 4).arg(l*1e9, 0, 'f', 2);
        if (c > 0)
            return QString::fromUtf8("在 %1 MHz（呈容性）：电容约 %2 pF。")
                .arg(f/1e6, 0, 'f', 4).arg(c*1e12, 0, 'f', 2);
        return QString::fromUtf8("在该频点电抗接近 0（串联谐振），无法据此区分 L/C。");
    }

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
